"""Подготовка проверенного комплекта релиза без инструментов разработки на хосте."""

import argparse
import ast
from contextlib import closing
import gzip
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tarfile
import tempfile
import tomllib
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


ROOT = Path(__file__).resolve().parent.parent
TAG_PATTERN = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-rc\.([1-9]\d*))?", re.ASCII)
MODEL_FILES = (
    "checksums.json", "metadata.json", "model.onnx", "special_tokens_map.json",
    "tokenizer.json", "tokenizer_config.json", "vocab.txt",
)
REQUIRED_MODEL_FILES = {"checksums.json", "metadata.json", "model.onnx", "tokenizer.json"}
PROBES = (
    "найди большие файлы в текущем каталоге",
    "покажи изменения в git",
    "найди файлы по имени",
    "заморозить процесс, не завершать",
)
EXPORT_DISTRIBUTIONS = ("torch", "sentence-transformers", "transformers", "onnx", "onnxruntime", "tokenizers")


def file_hash(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def model_build_key(root: Path = ROOT) -> str:
    versions = {}
    for name in EXPORT_DISTRIBUTIONS:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    sources = {name: file_hash(root / name) for name in
               ("src/cf/data/model.json", "src/cf/model.py", "src/cf/embeddings.py")}
    return hashlib.sha256(json.dumps({"sources": sources, "versions": versions}, sort_keys=True).encode()).hexdigest()


def restore_model(base_url: str, destination: Path, root: Path = ROOT) -> bool:
    url = urlparse(base_url)
    if url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost", "::1")):
        raise ValueError("Для получения модели нужен HTTPS или локальный HTTP")
    if destination.exists():
        raise ValueError("Каталог восстановления модели уже существует")
    base_url = base_url.rstrip("/")
    try:
        with urlopen(base_url + "/latest/download/release.json", timeout=60) as response:
            raw = response.read(1024 * 1024 + 1)
    except HTTPError as error:
        if error.code == 404:
            print("Опубликованной модели пока нет; модель будет собрана в CI.")
            return False
        raise RuntimeError(f"Не удалось получить манифест модели: HTTP {error.code}") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError(f"Не удалось получить манифест модели: {error}") from error
    if len(raw) > 1024 * 1024:
        raise ValueError("Манифест модели слишком велик")
    manifest = json.loads(raw)
    if manifest.get("model_build_key") != model_build_key(root):
        print("Параметры экспорта изменились; модель будет собрана в CI.")
        return False
    tag = manifest.get("tag", "")
    model = manifest["model"]
    filename = model["file"]
    if not TAG_PATTERN.fullmatch(tag) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", filename):
        raise ValueError("Манифест содержит недопустимый тег или имя модели")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".restore-model-", dir=destination.parent) as temporary:
        temporary = Path(temporary)
        archive_path = temporary / "model.tar.gz"
        with urlopen(f"{base_url}/download/{tag}/{filename}", timeout=60) as response, archive_path.open("wb") as output:
            shutil.copyfileobj(response, output)
        if file_hash(archive_path) != model["sha256"]:
            raise ValueError("Контрольная сумма опубликованной модели не совпадает")
        staged = temporary / "ready"
        staged.mkdir()
        with tarfile.open(archive_path, "r:gz") as archive:
            entries = archive.getmembers()
            names = [entry.name for entry in entries]
            if (len(names) != len(set(names)) or REQUIRED_MODEL_FILES - set(names)
                    or any(name not in MODEL_FILES for name in names)
                    or any(not entry.isfile() or entry.size > 1024 ** 3 for entry in entries)):
                raise ValueError("Недопустимое содержимое опубликованного архива модели")
            for entry in entries:
                with archive.extractfile(entry) as source, (staged / entry.name).open("wb") as target:
                    shutil.copyfileobj(source, target)
        from cf.model import verify_artifact
        verify_artifact(staged)
        staged.rename(destination)
    print(f"Модель из {tag} восстановлена без повторного экспорта.")
    return True


def validate_tag(tag: str, root: Path = ROOT) -> dict:
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise ValueError("Тег должен иметь вид vX.Y.Z или vX.Y.Z-rc.N")
    expected = Version(tag[1:])
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    tree = ast.parse((root / "src/cf/__init__.py").read_text(encoding="utf-8"))
    runtime = next((ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "__version__"
                            for target in node.targets)), None)
    if project["project"]["version"] != str(expected) or runtime != str(expected):
        raise ValueError("Версии тега, pyproject.toml и cf.__version__ должны совпадать в каноническом формате Python")
    return {"tag": tag, "version": str(expected), "prerelease": match.group(4) is not None}


def runtime_requirements() -> list[str]:
    """Читает только дерево зависимостей приложения, даже в окружении сборки."""
    pending = [metadata.distribution("command-finder-ru")]
    resolved = {}
    while pending:
        distribution = pending.pop()
        name = canonicalize_name(distribution.metadata["Name"])
        if name in resolved:
            continue
        resolved[name] = distribution.version
        for raw in distribution.requires or ():
            requirement = Requirement(raw)
            if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
                continue
            if requirement.url or requirement.extras:
                raise ValueError(f"Зависимость требует отдельного правила закрепления: {raw}")
            dependency = metadata.distribution(requirement.name)
            if dependency.version not in requirement.specifier:
                raise ValueError(f"Установленная зависимость не соответствует {raw}")
            pending.append(dependency)
    resolved.pop("command-finder-ru")
    return [f"{name}=={version}" for name, version in sorted(resolved.items())]


def archive_files(destination: Path, files: dict[str, Path]) -> None:
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for name, path in sorted(files.items()):
                    if not path.is_file() or path.is_symlink():
                        raise ValueError(f"Ожидается обычный файл: {path}")
                    entry = tarfile.TarInfo(name)
                    entry.size = path.stat().st_size
                    entry.mode = 0o644
                    with path.open("rb") as source:
                        archive.addfile(entry, source)


def export_database(source: Path, destination: Path) -> dict:
    import sqlite_vec

    if destination.exists() or source.resolve() == destination.resolve():
        raise ValueError("Для снимка базы нужен новый выходной файл")
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(destination)) as snapshot:
            original.backup(snapshot)
            snapshot.enable_load_extension(True)
            sqlite_vec.load(snapshot)
            snapshot.enable_load_extension(False)
            snapshot.execute("DELETE FROM query_cache")
            snapshot.commit()
            snapshot.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            snapshot.execute("PRAGMA journal_mode=DELETE")
            if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Проверка целостности базы не пройдена")
            counts = {name: snapshot.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                      for name in ("commands", "patterns", "pattern_embeddings")}
            if not counts["commands"] or not counts["patterns"] or counts["patterns"] != counts["pattern_embeddings"]:
                raise ValueError("В базе отсутствуют команды или векторы описаний")
    return counts


def verify_runtime() -> dict:
    from cf.config import DB_PATH, ONNX_DIR
    from cf.db import get_connection, init_db, get_detailed_stats
    from cf.model import verify_artifact
    from cf.search import search

    verify_artifact(ONNX_DIR)
    connection = get_connection(DB_PATH)
    try:
        init_db(connection)
        stats = get_detailed_stats(connection)
    finally:
        connection.close()
    if not stats["commands"] or not stats["patterns"] or stats["patterns"] != stats["embeddings"]:
        raise ValueError("Готовый индекс не содержит полного набора векторов")
    results = {}
    for query in PROBES:
        found = search(query, top_k=3)
        if not found or any(not result.command_template for result in found):
            raise ValueError(f"Поиск не вернул команды: {query}")
        results[query] = [result.command_template for result in found]
    return {"commands": stats["commands"], "patterns": stats["patterns"], "queries": results}


def package_release(tag: str, wheel: Path, model_dir: Path, database: Path,
                    requirements: Path, output: Path, root: Path = ROOT) -> dict:
    info = validate_tag(tag, root)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Каталог комплекта релиза должен быть пустым")
    if not wheel.name.endswith("-py3-none-any.whl"):
        raise ValueError("Ожидается универсальный wheel приложения")
    if wheel.name != f"command_finder_ru-{info['version']}-py3-none-any.whl":
        raise ValueError("Версия wheel не соответствует тегу")
    lines = requirements.read_text(encoding="utf-8").splitlines()
    if not lines or any(not re.fullmatch(r"[a-z0-9][a-z0-9._-]*==[^\s;]+", line) for line in lines):
        raise ValueError("Зависимости релиза должны иметь точные версии name==version")
    if not any(line.startswith("sqlite-vec==") for line in lines):
        raise ValueError("В зависимостях не закреплена версия sqlite-vec")
    files = {name: model_dir / name for name in MODEL_FILES if (model_dir / name).exists()}
    if REQUIRED_MODEL_FILES - files.keys():
        raise ValueError("Комплект модели неполон")
    identity = hashlib.sha256(json.dumps({name: file_hash(path) for name, path in sorted(files.items())},
                                         sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    model_archive = output / f"cf-model-{identity}.tar.gz"
    archive_files(model_archive, files)
    snapshot = output / "cf.db"
    counts = export_database(database, snapshot)
    data_archive = output / f"cf-data-{tag}.tar.gz"
    archive_files(data_archive, {"cf.db": snapshot})
    snapshot.unlink()
    shutil.copyfile(wheel, output / wheel.name)
    lock = output / "requirements-runtime.txt"
    shutil.copyfile(requirements, lock)
    for name in ("install.sh", "uninstall.sh"):
        shutil.copyfile(root / name, output / name)
    manifest = {
        "schema_version": 1, **info,
        "platform": "linux-x86_64", "python_versions": ["3.12", "3.13"],
        "wheel": wheel.name, "wheel_sha256": file_hash(output / wheel.name),
        "model": {"file": model_archive.name, "sha256": file_hash(model_archive), "identity": identity},
        "database": {"file": data_archive.name, "sha256": file_hash(data_archive), "counts": counts},
        "requirements": {"file": lock.name, "sha256": file_hash(lock)},
        "model_files": {name: file_hash(path) for name, path in sorted(files.items())},
        "model_build_key": model_build_key(root),
    }
    (output / "release.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assets = sorted(path for path in output.iterdir() if path.is_file())
    (output / "SHA256SUMS").write_text("".join(f"{file_hash(path)}  {path.name}\n" for path in assets), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-tag")
    validate.add_argument("--tag", required=True)
    validate.add_argument("--github-output", type=Path)
    lock = commands.add_parser("lock")
    lock.add_argument("--out", required=True, type=Path)
    commands.add_parser("verify-runtime")
    restore = commands.add_parser("restore-model")
    restore.add_argument("--base-url", required=True)
    restore.add_argument("--out", type=Path, required=True)
    package = commands.add_parser("package")
    package.add_argument("--tag", required=True)
    for name in ("wheel", "model-dir", "db", "requirements", "out"):
        package.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "validate-tag":
        result = validate_tag(args.tag)
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as handle:
                handle.write(f"version={result['version']}\nprerelease={str(result['prerelease']).lower()}\n")
        print(json.dumps(result))
    elif args.command == "lock":
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("\n".join(runtime_requirements()) + "\n", encoding="utf-8")
    elif args.command == "verify-runtime":
        print(json.dumps(verify_runtime(), ensure_ascii=False))
    elif args.command == "restore-model":
        restore_model(args.base_url, args.out)
    else:
        print(json.dumps(package_release(args.tag, args.wheel, args.model_dir, args.db, args.requirements, args.out)))


if __name__ == "__main__":
    main()
