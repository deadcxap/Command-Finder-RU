import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tarfile
from urllib.error import HTTPError

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/release.py"
SPEC = importlib.util.spec_from_file_location("release_tools", SCRIPT)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def version_root(tmp_path, version="1.2.3", runtime=None):
    (tmp_path / "src/cf").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n')
    (tmp_path / "src/cf/__init__.py").write_text(f'__version__ = "{runtime or version}"\n')
    (tmp_path / "src/cf/data").mkdir()
    for name in ("data/model.json", "model.py", "embeddings.py"):
        (tmp_path / "src/cf" / name).write_text("source " + name)
    return tmp_path


@pytest.mark.parametrize("tag", ["main", "v1.2", "v1.2.3-beta.1", "v1.2.3-rc", "v1.2.3-rc.01", "v1.2.3-rc.0",
                                  "v01.2.3", "v1.2.3.extra", "v1.2.3\n", "v1.2.3;echo bad"])
def test_reject_non_release_tags(tmp_path, tag):
    root = version_root(tmp_path)
    with pytest.raises(ValueError, match="Тег"):
        release.validate_tag(tag, root)


@pytest.mark.parametrize("tag,version,prerelease", [("v1.2.3", "1.2.3", False),
                                                  ("v1.2.3-rc.1", "1.2.3rc1", True)])
def test_tag_matches_both_python_versions(tmp_path, tag, version, prerelease):
    root = version_root(tmp_path, version)
    assert release.validate_tag(tag, root) == {"tag": tag, "version": version, "prerelease": prerelease}


def test_tag_rejects_runtime_version_mismatch(tmp_path):
    root = version_root(tmp_path, runtime="1.2.2")
    with pytest.raises(ValueError, match="совпадать"):
        release.validate_tag("v1.2.3", root)


def test_release_candidate_requires_canonical_python_version(tmp_path):
    root = version_root(tmp_path, version="1.2.3-rc.1")
    with pytest.raises(ValueError, match="каноническом"):
        release.validate_tag("v1.2.3-rc.1", root)


def test_runtime_lock_traverses_only_default_dependencies(monkeypatch):
    class Distribution:
        def __init__(self, name, version, requires=()):
            self.metadata = {"Name": name}
            self.version = version
            self.requires = requires

    distributions = {
        "command-finder-ru": Distribution("command-finder-ru", "1.2.3", ["one>=1", 'torch; extra == "export"']),
        "one": Distribution("one", "2.0", ['Two_Name>=3', 'windows-only; sys_platform == "win32"']),
        "Two_Name": Distribution("Two_Name", "3.1", ["one>=1"]),
    }
    monkeypatch.setattr(release.metadata, "distribution", distributions.__getitem__)
    assert release.runtime_requirements() == ["one==2.0", "two-name==3.1"]


def test_database_snapshot_is_complete_without_source_mutation(populated_db, tmp_path):
    source, connection = populated_db
    connection.execute("INSERT INTO query_cache VALUES (?, ?)", ("private query", b"cached"))
    connection.commit()
    output = tmp_path / "snapshot.db"
    counts = release.export_database(source, output)
    assert counts == {"commands": 2, "patterns": 3, "pattern_embeddings": 3}
    assert connection.execute("SELECT COUNT(*) FROM query_cache").fetchone()[0] == 1
    with sqlite3.connect(output) as snapshot:
        assert snapshot.execute("SELECT COUNT(*) FROM query_cache").fetchone()[0] == 0
        assert snapshot.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert snapshot.execute("SELECT COUNT(*) FROM patterns").fetchone()[0] == 3
    assert not Path(str(output) + "-wal").exists()


def test_database_export_rejects_empty_index(tmp_db, tmp_path):
    source, _ = tmp_db
    with pytest.raises(ValueError, match="отсутствуют"):
        release.export_database(source, tmp_path / "empty.db")


def test_archives_are_deterministic_and_reject_symlinks(tmp_path):
    value = tmp_path / "value"
    value.write_bytes(b"release model")
    release.archive_files(tmp_path / "one.tar.gz", {"value": value})
    value.touch()
    release.archive_files(tmp_path / "two.tar.gz", {"value": value})
    assert (tmp_path / "one.tar.gz").read_bytes() == (tmp_path / "two.tar.gz").read_bytes()
    link = tmp_path / "link"
    link.symlink_to(value)
    with pytest.raises(ValueError, match="обычный файл"):
        release.archive_files(tmp_path / "bad.tar.gz", {"value": link})


def test_release_assets_have_matching_hashes_and_no_caches(populated_db, tmp_path):
    root = version_root(tmp_path / "repo")
    for name in ("install.sh", "uninstall.sh"):
        (root / name).write_text("#!/bin/bash\n")
    model = tmp_path / "model"
    model.mkdir()
    for name in release.REQUIRED_MODEL_FILES:
        (model / name).write_text("dummy " + name)
    (model / "cache.npy").write_bytes(b"not needed")
    wheel = tmp_path / "command_finder_ru-1.2.3-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    requirements = tmp_path / "lock.txt"
    requirements.write_text("sqlite-vec==0.1.9\n")
    output = tmp_path / "assets"
    manifest = release.package_release("v1.2.3", wheel, model, populated_db[0], requirements, output, root)
    assert manifest["version"] == "1.2.3"
    assert manifest["prerelease"] is False
    assert manifest["platform"] == "linux-x86_64"
    assert json.loads((output / "release.json").read_text()) == manifest
    with tarfile.open(output / manifest["model"]["file"]) as archive:
        assert set(archive.getnames()) == release.REQUIRED_MODEL_FILES
    with tarfile.open(output / manifest["database"]["file"]) as archive:
        assert archive.getnames() == ["cf.db"]
    for line in (output / "SHA256SUMS").read_text().splitlines():
        expected, name = line.split("  ")
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == expected
    assert not (output / "cf.db").exists()


def test_workflow_has_no_branch_or_manual_release_trigger():
    text = (SCRIPT.parent.parent / ".github/workflows/release.yml").read_text()
    trigger = text.split("permissions:")[0]
    assert "tags:" in trigger
    assert "branches:" not in trigger
    assert "workflow_dispatch:" not in trigger
    assert "pull_request:" not in trigger


def test_model_key_includes_export_versions_and_sources_but_not_app_version(tmp_path, monkeypatch):
    root = version_root(tmp_path)
    monkeypatch.setattr(release.metadata, "version", lambda name: "1.0")
    original = release.model_build_key(root)
    (root / "pyproject.toml").write_text('[project]\nversion="1.2.4"\n')
    assert release.model_build_key(root) == original
    monkeypatch.setattr(release.metadata, "version", lambda name: "2.0" if name == "onnxruntime" else "1.0")
    assert release.model_build_key(root) != original
    monkeypatch.setattr(release.metadata, "version", lambda name: "1.0")
    (root / "src/cf/model.py").write_text("changed exporter")
    assert release.model_build_key(root) != original


def test_restore_absent_release_does_not_fetch_other_files(tmp_path, monkeypatch):
    calls = []
    def missing(url, **kwargs):
        calls.append(url)
        raise HTTPError(url, 404, "Not found", {}, None)
    monkeypatch.setattr(release, "urlopen", missing)
    assert release.restore_model("https://example.invalid/releases", tmp_path / "model") is False
    assert len(calls) == 1
    assert not (tmp_path / "model").exists()


def test_restore_changed_export_does_not_download_model(tmp_path, monkeypatch):
    root = version_root(tmp_path / "repo")
    calls = []
    def stale(url, **kwargs):
        calls.append(url)
        return io.BytesIO(json.dumps({"model_build_key": "old"}).encode())
    monkeypatch.setattr(release, "urlopen", stale)
    assert release.restore_model("https://example.invalid/releases", tmp_path / "model", root) is False
    assert len(calls) == 1


@pytest.mark.parametrize("malicious", [False, True])
def test_restore_verifies_archive_before_reusing_model(tmp_path, monkeypatch, malicious):
    root = version_root(tmp_path / "repo")
    model = tmp_path / "source-model"
    model.mkdir()
    for name in release.REQUIRED_MODEL_FILES:
        (model / name).write_text("dummy " + name)
    archive = tmp_path / "model.tar.gz"
    if malicious:
        with tarfile.open(archive, "w:gz") as output:
            entry = tarfile.TarInfo("../escape")
            entry.size = 1
            output.addfile(entry, io.BytesIO(b"x"))
    else:
        release.archive_files(archive, {name: model / name for name in release.REQUIRED_MODEL_FILES})
    manifest = {"model_build_key": release.model_build_key(root), "tag": "v1.2.3",
                "model": {"file": "model.tar.gz", "sha256": release.file_hash(archive)}}
    def fetch(url, **kwargs):
        return io.BytesIO(json.dumps(manifest).encode() if url.endswith("release.json") else archive.read_bytes())
    monkeypatch.setattr(release, "urlopen", fetch)
    checked = []
    monkeypatch.setattr("cf.model.verify_artifact", lambda directory: checked.append(directory))
    destination = tmp_path / "restored"
    if malicious:
        with pytest.raises(ValueError, match="содержимое"):
            release.restore_model("https://example.invalid/releases", destination, root)
        assert not checked
        assert not destination.exists()
    else:
        assert release.restore_model("https://example.invalid/releases", destination, root)
        assert checked
        assert {path.name for path in destination.iterdir()} == release.REQUIRED_MODEL_FILES
