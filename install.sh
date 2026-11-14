#!/usr/bin/env bash
set -euo pipefail
PYTHON_BIN="$(command -v "${CF_PYTHON:-python3}" || true)"
if [[ -z "$PYTHON_BIN" || "$PYTHON_BIN" == /mnt/* || "$PYTHON_BIN" == *.exe ]]; then
    printf '[cf-ru] Нужен Python 3.12 или 3.13, установленный внутри Linux/WSL.\n' >&2
    exit 1
fi
CF_INSTALL_PYTHON="$PYTHON_BIN" "$PYTHON_BIN" - "$@" <<'PY'
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlparse
from urllib.request import urlopen

TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-rc\.([1-9][0-9]*))?")
FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
DIGEST = re.compile(r"[a-f0-9]{64}")
MODEL_FILES = {"model.onnx", "tokenizer.json", "metadata.json", "checksums.json",
               "tokenizer_config.json", "special_tokens_map.json", "vocab.txt"}
MODEL_REQUIRED = {"model.onnx", "tokenizer.json", "metadata.json", "checksums.json"}
MARKER = 'command-finder-ru-release-install-v1'
CURRENT_STEP = None
TOTAL_STEPS = 8


def info(message):
    print(f'[cf-ru] {message}', file=sys.stderr, flush=True)


def step(number, message):
    global CURRENT_STEP
    CURRENT_STEP = f'[{number}/{TOTAL_STEPS}] {message}'
    info(CURRENT_STEP)


def size_text(size):
    for unit in ('Б', 'КиБ', 'МиБ', 'ГиБ'):
        if size < 1024 or unit == 'ГиБ':
            return f'{size:.1f} {unit}'
        size /= 1024


def fail(message):
    raise ValueError(message)


def local_path(value):
    path = Path(value).expanduser().absolute()
    for part in reversed((path, *path.parents)):
        if str(part).startswith('/mnt/') or re.match(r'^[A-Za-z]:[\\/]', str(part)):
            fail(f'Нужен путь внутри Linux/WSL: {path}')
        if part.is_symlink():
            fail(f'Укажите путь без символических ссылок: {part}')
    return path


def download(url, path):
    name = url.rsplit('/', 1)[-1]
    info(f'Скачивание {name}…')
    with urlopen(url, timeout=60) as response, path.open('wb') as output:
        header = response.headers.get('Content-Length', '')
        total = int(header) if header.isdigit() and int(header) > 0 else None
        received = 0
        last_update = time.monotonic()
        interactive = sys.stderr.isatty()
        active_line = False

        def show(final=False):
            nonlocal active_line
            amount = size_text(received)
            if total:
                percent = min(100, int(received * 100 / total))
                amount = f'{percent:3d}% · {amount} / {size_text(total)}'
            label = name
            if interactive:
                available = max(12, shutil.get_terminal_size().columns - len(amount) - 12)
                if len(label) > available:
                    label = label[:available - 9] + '…' + label[-8:]
            text = f'[cf-ru] {label}: {amount}'
            if interactive:
                print('\r\033[2K' + text, file=sys.stderr, end='\n' if final else '', flush=True)
                active_line = not final
            else:
                print(text, file=sys.stderr, flush=True)

        show()
        read = getattr(response, 'read1', response.read)
        try:
            while block := read(64 * 1024):
                output.write(block)
                received += len(block)
                now = time.monotonic()
                if now - last_update >= (0.2 if interactive else 5):
                    show()
                    last_update = now
            if total and received != total:
                fail(f'Загрузка {name} неполна: {size_text(received)} из {size_text(total)}')
            show(final=True)
        finally:
            if active_line:
                print(file=sys.stderr, flush=True)


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def checked_file(value):
    if not isinstance(value, str) or not FILE.fullmatch(value):
        fail('Недопустимое имя файла в релизе')
    return value


def read_sums(path):
    sums = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9][A-Za-z0-9_.-]*)', line)
        if not match or match[2] in sums:
            fail('Повреждён SHA256SUMS релиза')
        sums[match[2]] = match[1]
    return sums


def check(path, digest):
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest) or sha256(path) != digest:
        fail(f'Не совпадает SHA-256 файла {path.name}')


def extract(archive, target, allowed, required):
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        names = [member.name for member in members]
        if (len(names) != len(set(names)) or not required.issubset(names)
                or any(name not in allowed for name in names)
                or any(not member.isfile() or member.size > 1024 ** 3 for member in members)):
            fail(f'Недопустимое содержимое архива {archive.name}')
        for member in members:
            with source.extractfile(member) as stream, (target / member.name).open('wb') as output:
                shutil.copyfileobj(stream, output, length=1024 * 1024)


def write_atomic(path, content, mode=0o644):
    temporary = path.with_name(path.name + '.new')
    temporary.write_text(content, encoding='utf-8')
    temporary.chmod(mode)
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description='Установка готового релиза Command Finder RU без сборки модели.')
    parser.add_argument('--version', help='Тег vX.Y.Z или vX.Y.Z-rc.N; по умолчанию последний стабильный релиз')
    parser.add_argument('--shell', action='store_true', help='Подключить Bash-интеграцию в ~/.bashrc')
    args = parser.parse_args()
    started = time.monotonic()
    step(1, 'Проверка системы и каталогов')
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        fail('Автоматическая установка поддерживает только Linux/WSL x86_64.')
    if sys.version_info[:2] not in ((3, 12), (3, 13)):
        fail('Нужен Python 3.12 или 3.13 с модулями venv и pip. Выберите его через CF_PYTHON.')
    if args.version and not TAG.fullmatch(args.version):
        fail('Версия должна иметь вид vX.Y.Z или vX.Y.Z-rc.N.')
    base = os.environ.get('CF_RELEASE_BASE_URL', 'https://github.com/deadcxap/Command-Finder-RU/releases').rstrip('/')
    url = urlparse(base)
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in ('127.0.0.1', 'localhost', '::1')):
        fail('Источник релизов должен использовать HTTPS (HTTP разрешён только для localhost).')
    prefix = local_path(os.environ.get('CF_INSTALL_DIR', str(Path.home() / '.local/share/cf-ru')))
    if prefix == Path.home() or prefix in Path.home().parents or prefix == Path('/'):
        fail('Выберите отдельный каталог для установки.')
    link_dir = local_path(str(Path.home() / '.local/bin'))
    link = link_dir / 'cf'
    bashrc = local_path(os.environ.get('CF_BASHRC', str(Path.home() / '.bashrc'))) if args.shell else None
    shell_line = 'source ' + shlex.quote(str(prefix / 'cf.bash'))
    shell_text = None
    if bashrc:
        if bashrc.exists() and not bashrc.is_file():
            fail(f'Настройки Bash должны быть обычным файлом: {bashrc}')
        shell_text = bashrc.read_text() if bashrc.exists() else ''
        bashrc.parent.mkdir(parents=True, exist_ok=True)
        if not os.access(bashrc if bashrc.exists() else bashrc.parent, os.W_OK):
            fail(f'Нет прав на изменение настроек Bash: {bashrc}')
    marker = prefix / 'install.json'
    if marker.exists():
        if marker.is_symlink() or json.loads(marker.read_text()).get('installer') != MARKER:
            fail(f'Каталог {prefix} принадлежит другой установке.')
    elif prefix.is_dir() and any(prefix.iterdir()):
        fail(f'Каталог {prefix} не пуст и не принадлежит релизному установщику. Выберите отдельный CF_INSTALL_DIR.')
    if link.is_symlink():
        if os.readlink(link) != str(prefix / 'cf'):
            fail(f'{link} указывает на другую установку; сначала удалите её прежним установщиком.')
    elif link.exists():
        fail(f'{link} уже существует; файл сохранён.')
    for name in ('releases', 'models'):
        local_path(str(prefix / name))
    if (prefix / 'current').exists() and not (prefix / 'current').is_symlink():
        fail('current должен быть ссылкой управляемой установки.')

    release_dir = None
    activated = False
    link_dir.mkdir(parents=True, exist_ok=True)
    prefix.mkdir(parents=True, exist_ok=True)
    with (prefix / '.install.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail('Другой установщик уже работает с этим каталогом.')
        if not marker.exists():
            write_atomic(marker, json.dumps({'installer': MARKER}) + '\n')
        try:
            with tempfile.TemporaryDirectory(prefix='.install-', dir=prefix) as temporary:
                work = Path(temporary)
                manifest_path = work / 'release.json'
                step(2, 'Выбор и проверка релиза')
                discovery = f'{base}/download/{args.version}' if args.version else f'{base}/latest/download'
                download(f'{discovery}/release.json', manifest_path)
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                tag = manifest.get('tag')
                match = TAG.fullmatch(tag) if isinstance(tag, str) else None
                if not match or (args.version and tag != args.version) or (not args.version and match[4]):
                    fail('Манифест содержит неверный тег или Pre-release вместо стабильного релиза.')
                release_url = f'{base}/download/{tag}'
                download(f'{release_url}/SHA256SUMS', work / 'SHA256SUMS')
                sums = read_sums(work / 'SHA256SUMS')
                check(manifest_path, sums.get('release.json'))
                version = '.'.join(match.groups()[:3]) + (f'rc{match[4]}' if match[4] else '')
                if (manifest.get('schema_version') != 1 or manifest.get('version') != version
                        or manifest.get('platform') != 'linux-x86_64'
                        or f'{sys.version_info.major}.{sys.version_info.minor}' not in manifest.get('python_versions', [])):
                    fail('Релиз несовместим с платформой, Python или форматом установщика.')
                info(f'Версия: {tag}. Каталог установки: {prefix}')

                def asset(name, digest=None):
                    name = checked_file(name)
                    expected = sums.get(name)
                    if digest is not None and digest != expected:
                        fail(f'Манифест и SHA256SUMS расходятся для {name}')
                    path = work / name
                    download(f'{release_url}/{name}', path)
                    check(path, expected)
                    return path

                step(3, 'Скачивание приложения и базы')
                wheel = asset(manifest['wheel'], manifest['wheel_sha256'])
                requirements = asset(manifest['requirements']['file'], manifest['requirements']['sha256'])
                database = asset(manifest['database']['file'], manifest['database']['sha256'])
                uninstaller = asset('uninstall.sh')
                step(4, 'Подготовка модели')
                model_info = manifest['model']
                identity = model_info['identity']
                if not isinstance(identity, str) or not DIGEST.fullmatch(identity):
                    fail('Неверный идентификатор модели в релизе.')
                models = prefix / 'models'
                models.mkdir(exist_ok=True)
                model_dir = local_path(str(models / identity))
                if model_dir.exists():
                    if (model_dir / '.archive-sha256').read_text().strip() != model_info['sha256']:
                        fail('Сохранённая модель не соответствует релизу.')
                    info('Готовая модель уже установлена; повторное скачивание не требуется.')
                else:
                    archive = asset(model_info['file'], model_info['sha256'])
                    staged_model = Path(tempfile.mkdtemp(prefix='.model-', dir=models))
                    try:
                        info('Распаковка модели…')
                        extract(archive, staged_model, MODEL_FILES, MODEL_REQUIRED)
                        (staged_model / '.archive-sha256').write_text(model_info['sha256'] + '\n')
                        staged_model.rename(model_dir)
                    finally:
                        if staged_model.exists():
                            shutil.rmtree(staged_model)

                releases = prefix / 'releases'
                releases.mkdir(exist_ok=True)
                release_dir = Path(tempfile.mkdtemp(prefix=tag + '.', dir=releases))
                data = release_dir / 'data'
                data.mkdir()
                info('Распаковка готовой базы команд…')
                extract(database, data, {'cf.db'}, {'cf.db'})
                (release_dir / 'model').symlink_to(model_dir, target_is_directory=True)
                python = release_dir / 'venv/bin/python'
                environment = os.environ.copy()
                environment.pop('PYTHONPATH', None)
                environment.pop('PYTHONHOME', None)
                environment['PYTHONNOUSERSITE'] = '1'
                for key in tuple(environment):
                    if key.startswith('CF_'):
                        environment.pop(key)
                environment.update(CF_DB_DIR=str(data), CF_MODEL_DIR=str(model_dir),
                                   HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TMPDIR=str(work))
                step(5, 'Создание Python-окружения')
                subprocess.run([os.environ['CF_INSTALL_PYTHON'], '-m', 'venv', str(release_dir / 'venv')], check=True, env=environment)
                step(6, 'Установка приложения и зависимостей')
                info('Готовые пакеты загружаются из PyPI; ниже вывод pip.')
                subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                                '--no-cache-dir', '--only-binary=:all:', '-r', str(requirements), str(wheel)],
                               check=True, env=environment)
                step(7, 'Проверка модели и поиска')
                subprocess.run([str(python), '-c', f'''from cf import __version__
assert __version__ == {version!r}, 'Версия установленного приложения не совпадает с релизом'
from cf.model import verify_artifact
from cf.config import ONNX_DIR
from cf.db import get_connection, init_db, get_stats
from cf.search import search
verify_artifact(ONNX_DIR)
conn = get_connection()
try:
    init_db(conn)
    stats = get_stats(conn)
    assert stats['commands'] > 0 and stats['patterns'] > 0, 'Пустой индекс'
finally:
    conn.close()
assert search('покажи скрытые файлы', top_k=3), 'Поиск не вернул результаты'
'''], check=True, env=environment)
                step(8, 'Подключение команды и очистка')
                shutil.copyfile(manifest_path, release_dir / 'release.json')
                root = shlex.quote(str(prefix))
                launcher = f'''#!/usr/bin/env bash
set -euo pipefail
CF_RELEASE_DIR=$(readlink -f -- {root}/current)
export CF_DB_DIR="${{CF_DB_DIR:-$CF_RELEASE_DIR/data}}"
export CF_MODEL_DIR="${{CF_MODEL_DIR:-$CF_RELEASE_DIR/model}}"
exec "$CF_RELEASE_DIR/venv/bin/cf" "$@"
'''
                user_bin = shlex.quote(str(link_dir))
                shell = f'''case ":$PATH:" in
    *:{user_bin}:*) ;;
    *) export PATH={user_bin}:"$PATH" ;;
esac
eval "$({shlex.quote(str(prefix / 'cf'))} --install-shell)"
'''
                write_atomic(prefix / 'cf', launcher, 0o755)
                write_atomic(prefix / 'cf.bash', shell)
                write_atomic(prefix / 'uninstall.sh', uninstaller.read_text(), 0o755)
                write_atomic(marker, json.dumps({'installer': MARKER, 'tag': tag}) + '\n')
                current_new = prefix / 'current.new'
                current_new.unlink(missing_ok=True)
                current_new.symlink_to(release_dir, target_is_directory=True)
                current_new.replace(prefix / 'current')
                activated = True
                if not link.is_symlink():
                    link.symlink_to(prefix / 'cf')
                if bashrc:
                    if shell_line not in shell_text.splitlines():
                        with bashrc.open('a') as output:
                            output.write('\n# cf-ru: ' + shell_line + '\n' + shell_line + '\n')
                    info(f'Интеграция подключена в {bashrc}. Откройте новую сессию Bash.')
                for previous in releases.iterdir():
                    if previous != release_dir and previous.is_dir() and not previous.is_symlink():
                        shutil.rmtree(previous)
                for previous in models.iterdir():
                    if previous != model_dir and previous.is_dir() and not previous.is_symlink():
                        shutil.rmtree(previous)
                info(f'Установлен {tag}. Команда: {link}')
                info(f'Все {TOTAL_STEPS} шагов завершены за {time.monotonic() - started:.1f} с.')
                if not args.shell and str(link_dir) not in os.environ.get('PATH', '').split(os.pathsep):
                    info('Добавьте в ~/.bashrc: export PATH="$HOME/.local/bin:$PATH"')
                if not args.shell:
                    info('Для Ctrl+F добавьте в ~/.bashrc: source ' + shlex.quote(str(prefix / 'cf.bash')))
        finally:
            if release_dir is not None and not activated:
                shutil.rmtree(release_dir, ignore_errors=True)


try:
    main()
except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.CalledProcessError) as error:
    context = f' на шаге {CURRENT_STEP}' if CURRENT_STEP else ''
    print(f'[cf-ru] Ошибка установки{context}: {error}', file=sys.stderr)
    sys.exit(1)
PY
