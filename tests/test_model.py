import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from cf import model
from tests.test_embeddings import encoder_artifact


@pytest.fixture
def prepared_source(encoder_artifact, tmp_path, monkeypatch):
    directory, metadata = encoder_artifact
    spec = copy.deepcopy(model.model_spec())
    spec.update({key: metadata[key] for key in (
        'model', 'revision', 'dimensions', 'max_tokens', 'precision',
        'query_prefix', 'document_prefix', 'normalization', 'pooling_in_graph',
    )})
    spec['reference_artifact'] = {name: model.file_hash(directory / name)
                                  for name in ('model.onnx', 'tokenizer.json')}
    monkeypatch.setattr(model, 'model_spec', lambda: copy.deepcopy(spec))
    target = tmp_path / 'installed' / 'onnx'
    monkeypatch.setattr(model, 'ONNX_DIR', target)
    return directory, metadata, spec, target


def snapshot(directory):
    return {str(path.relative_to(directory)): path.read_bytes()
            for path in directory.rglob('*') if path.is_file()}


def existing_installation(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    (target / 'notes.txt').write_text('Локальные заметки, не относящиеся к модели.\n')
    (target / 'custom').mkdir()
    (target / 'custom' / 'settings.json').write_text('{"retain": true}\n')


def test_import_known_local_artifact_checks_hashes_and_real_vectors(prepared_source):
    source, metadata, spec, target = prepared_source
    result = model.prepare_model(source=source)
    assert result == target
    assert model.verify_artifact(target) == metadata
    checksums = json.loads((target / 'checksums.json').read_text())
    assert checksums == spec['reference_artifact']
    assert model.file_hash(target / 'model.onnx') == model.file_hash(source / 'model.onnx')
    assert source.is_dir()
    assert not list(target.parent.glob('.cf-model-*'))


@pytest.mark.parametrize('name', ['model.onnx', 'tokenizer.json'])
def test_corrupted_artifact_is_rejected_before_installation(prepared_source, name):
    source, _, _, target = prepared_source
    with (source / name).open('ab') as handle:
        handle.write(b'corrupted')
    with pytest.raises(RuntimeError, match='сумм'):
        model.prepare_model(source=source)
    assert not target.exists()


@pytest.mark.parametrize('name', ['model.onnx', 'tokenizer.json', 'metadata.json'])
def test_missing_artifact_is_rejected_without_creating_target(prepared_source, name):
    source, _, _, target = prepared_source
    (source / name).unlink()
    with pytest.raises((OSError, RuntimeError, ValueError)):
        model.prepare_model(source=source)
    assert not target.exists()


@pytest.mark.parametrize('field,value', [
    ('dimensions', 384), ('revision', '0' * 40), ('precision', 'fp32'),
    ('max_tokens', 128), ('pooling_in_graph', False), ('document_prefix', 'passage: '),
])
def test_incompatible_metadata_is_rejected(prepared_source, field, value):
    source, metadata, _, target = prepared_source
    metadata[field] = value
    (source / 'metadata.json').write_text(json.dumps(metadata))
    with pytest.raises(RuntimeError, match=field):
        model.prepare_model(source=source)
    assert not target.exists()


def test_missing_checksum_entry_cannot_authorize_an_artifact(prepared_source):
    source, _, spec, target = prepared_source
    (source / 'checksums.json').write_text(json.dumps({'model.onnx': spec['reference_artifact']['model.onnx']}))
    with pytest.raises(RuntimeError, match='сумм'):
        model.prepare_model(source=source)
    assert not target.exists()


@pytest.mark.parametrize('contents', ['[]', 'null', '{not-json'])
def test_malformed_checksum_manifest_is_rejected(prepared_source, contents):
    source, _, _, target = prepared_source
    (source / 'checksums.json').write_text(contents)
    with pytest.raises(RuntimeError):
        model.prepare_model(source=source)
    assert not target.exists()


@pytest.mark.parametrize('contents', ['[]', 'null', '{not-json'])
def test_malformed_model_metadata_is_rejected(prepared_source, contents):
    source, _, _, target = prepared_source
    (source / 'metadata.json').write_text(contents)
    with pytest.raises(RuntimeError):
        model.prepare_model(source=source)
    assert not target.exists()


def test_valid_local_import_preserves_unrelated_existing_files(prepared_source):
    source, _, _, target = prepared_source
    existing_installation(source, target)
    retained = {name: data for name, data in snapshot(target).items()
                if name in ('notes.txt', 'custom/settings.json')}
    model.prepare_model(force=True, source=source)
    assert model.verify_artifact(target)
    for name, data in retained.items():
        assert (target / name).read_bytes() == data
    assert not list(target.parent.glob('onnx.backup-*'))


def test_failed_vector_validation_keeps_old_installation_and_unrelated_files(prepared_source, monkeypatch):
    source, _, _, target = prepared_source
    existing_installation(source, target)
    before = snapshot(target)

    def reject_vectors(directory):
        assert directory != target
        assert (directory / 'model.onnx').is_file()
        raise RuntimeError('Векторы не прошли проверку')

    monkeypatch.setattr(model, '_check_vectors', reject_vectors)
    with pytest.raises(RuntimeError, match='проверку'):
        model.prepare_model(force=True, source=source)
    assert snapshot(target) == before
    assert not list(target.parent.glob('.cf-model-*'))
    assert not list(target.parent.glob('onnx.backup-*'))


def test_failed_build_does_not_replace_existing_model(prepared_source, monkeypatch):
    source, _, _, target = prepared_source
    existing_installation(source, target)
    before = snapshot(target)

    def reject_build(directory, cache):
        (directory / 'model.onnx').write_bytes(b'incomplete export')
        raise RuntimeError('Ошибка экспорта')

    monkeypatch.setattr(model, '_build', reject_build)
    with pytest.raises(RuntimeError, match='экспорта'):
        model.prepare_model(force=True)
    assert snapshot(target) == before
    assert not list(target.parent.glob('.cf-model-*'))


def test_failed_atomic_install_restores_old_directory(prepared_source, monkeypatch):
    source, _, _, target = prepared_source
    existing_installation(source, target)
    before = snapshot(target)
    original_rename = Path.rename

    def fail_staged_rename(path, destination):
        if path.name == 'ready':
            raise OSError('Не удалось установить подготовленный каталог')
        return original_rename(path, destination)

    monkeypatch.setattr(Path, 'rename', fail_staged_rename)
    with pytest.raises(OSError, match='каталог'):
        model.prepare_model(force=True, source=source)
    assert snapshot(target) == before
    assert not list(target.parent.glob('onnx.backup-*'))


def test_existing_valid_model_is_reused_without_build_or_onnx_session(prepared_source, monkeypatch):
    source, _, _, target = prepared_source
    existing_installation(source, target)
    monkeypatch.setattr(model, '_build', lambda *args: pytest.fail('готовая модель не должна пересобираться'))
    monkeypatch.setattr(model, '_check_vectors', lambda *args: pytest.fail('проверка хешей не должна запускать модель'))
    assert model.prepare_model() == target


def test_importing_from_target_is_validation_only_even_with_force(prepared_source, monkeypatch):
    source, _, _, target = prepared_source
    existing_installation(source, target)
    before = snapshot(target)
    monkeypatch.setattr(model, '_build', lambda *args: pytest.fail('сборка не нужна'))
    monkeypatch.setattr(model, '_check_vectors', lambda *args: pytest.fail('каталог уже установлен'))
    assert model.prepare_model(force=True, source=target) == target
    assert snapshot(target) == before


def test_reusing_prepared_model_does_not_import_training_libraries(prepared_source):
    source, _, spec, target = prepared_source
    existing_installation(source, target)
    program = '''
import json, sys
from pathlib import Path
from cf import embeddings, model
spec = json.loads(sys.argv[2])
model.model_spec = lambda: spec
model.ONNX_DIR = Path(sys.argv[1])
embeddings.MAX_TOKENS = spec['max_tokens']
def fail(*args):
    raise AssertionError('готовая модель не должна пересобираться или загружаться')
model._build = model._check_vectors = fail
assert model.prepare_model() == model.ONNX_DIR
assert not {'torch', 'transformers', 'sentence_transformers', 'onnxruntime'} & sys.modules.keys()
'''
    environment = {
        'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'PYTHONNOUSERSITE': '1',
        'CF_MODEL': spec['model'], 'CF_MODEL_REVISION': spec['revision'],
        'CF_EMBEDDING_DIM': str(spec['dimensions']), 'CF_DB_DIR': str(target.parent),
    }
    result = subprocess.run([sys.executable, '-c', program, str(target), json.dumps(spec)],
                            cwd=Path(__file__).resolve().parents[1], env=environment,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
