"""Подготовка закреплённого штатного ONNX исходного проекта для сравнения."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from benchmarks import runner


DEFAULT_PROFILES = runner.ROOT / 'benchmarks/pinned-profiles.json'


def child_environment(job: dict) -> dict:
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(('CF_', 'HF_', 'HUGGINGFACE_', 'TRANSFORMERS_'))}
    cache = runner.wsl_path(job['hf_home'])
    paths = {'HF_HOME': cache, 'HF_HUB_CACHE': cache / 'hub',
             'SENTENCE_TRANSFORMERS_HOME': cache, 'TORCH_HOME': cache.parent / 'torch',
             'XDG_CACHE_HOME': cache.parent / 'xdg'}
    environment.update({name: str(runner.wsl_path(path)) for name, path in paths.items()})
    environment.update({
        'CF_MODEL': job['profile']['model'], 'CF_EMBEDDING_DIM': '384',
        'CF_DB_DIR': str(runner.wsl_path(job['run_dir'])), 'CF_DB_NAME': 'cf.db',
        'CF_DATA_DIR': str(runner.DEFAULT_SOURCE), 'PATH': '/usr/bin:/bin',
        'HF_HUB_OFFLINE': '0' if job['download'] else '1',
        'TRANSFORMERS_OFFLINE': '0' if job['download'] else '1',
        'HF_HUB_DISABLE_TELEMETRY': '1', 'HF_HUB_DISABLE_PROGRESS_BARS': '1',
        'TOKENIZERS_PARALLELISM': 'false', 'CUDA_VISIBLE_DEVICES': '',
        'OMP_NUM_THREADS': '4', 'MKL_NUM_THREADS': '4', 'OPENBLAS_NUM_THREADS': '4',
    })
    return environment


def validate_profile(profile: dict) -> None:
    if (profile.get('model') not in ('all-MiniLM-L6-v2', 'sentence-transformers/all-MiniLM-L6-v2')
            or profile.get('dimensions') != 384 or profile.get('query_prefix')
            or profile.get('document_prefix') or profile.get('config_kwargs')):
        raise ValueError('Для штатного экспорта нужен исходный MiniLM, 384 измерения и пустые префиксы')
    if not isinstance(profile.get('revision'), str) or not re.fullmatch('[0-9a-f]{40}', profile['revision']):
        raise ValueError('Закрепите полную SHA-ревизию исходной модели')


def _load_model(job: dict):
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    return SentenceTransformer(job['profile']['model'], revision=job['profile']['revision'],
                               device='cpu', cache_folder=job['hf_home'],
                               local_files_only=not job['download'], trust_remote_code=False)


def _pin_offline_main(job: dict, revision: str) -> dict:
    model = job['profile']['model']
    repo_id = model if '/' in model else 'sentence-transformers/' + model
    repository = runner.wsl_path(Path(job['hf_home']) / ('models--' + repo_id.replace('/', '--')))
    if not runner.wsl_path(repository / 'snapshots' / revision).is_dir():
        raise ValueError('Закреплённый снимок не найден в изолированном кеше проекта')
    refs = runner.wsl_path(repository / 'refs')
    refs.mkdir(parents=True, exist_ok=True)
    target = runner.wsl_path(refs / 'main')
    temporary = refs / ('main.tmp-' + uuid.uuid4().hex[:8])
    temporary.write_text(revision, encoding='utf-8')
    temporary.replace(target)
    return {'repo_id': repo_id, 'revision': revision, 'path': str(target)}


def _verify_export(legacy, model, directory: Path) -> dict:
    import numpy as np
    import onnx

    graph = onnx.load(str(directory / 'model.onnx'), load_external_data=False)
    if any(value.data_location == onnx.TensorProto.EXTERNAL for value in graph.graph.initializer):
        raise ValueError('Штатный ONNX должен содержать веса в одном файле')
    onnx.checker.check_model(graph)
    session, _ = legacy._get_onnx()
    options = session.get_session_options()
    if options.intra_op_num_threads != 4 or options.inter_op_num_threads != 1:
        raise ValueError('Штатный загрузчик должен использовать четыре потока')
    outputs = session.get_outputs()
    if len(outputs) != 1 or outputs[0].name != 'token_embeddings' or len(outputs[0].shape) != 3 or outputs[0].shape[-1] != 384:
        raise ValueError('Ожидается исходный выход token_embeddings [batch, seq, 384]')
    shapes = {value.name: value.shape for value in session.get_inputs() + outputs}
    if any(isinstance(shape[axis], int) for shape in shapes.values() for axis in (0, 1)):
        raise ValueError('Оси batch и seq должны быть динамическими')
    checks = []
    for text in ('найди большие файлы в текущем каталоге', 'find large files in the current directory'):
        actual = legacy._encode_onnx(text)
        expected = np.asarray(model.encode(text, normalize_embeddings=True), dtype=np.float32)
        if actual.shape != (384,) or not np.isfinite(actual).all() or not np.isclose(np.linalg.norm(actual), 1, atol=2e-5):
            raise ValueError('Штатный загрузчик вернул некорректный вектор')
        cosine = float(np.dot(actual, expected) / np.linalg.norm(expected))
        error = float(np.max(np.abs(actual - expected)))
        if cosine < 0.99999 or error > 1e-4:
            raise ValueError('Выход штатного ONNX не совпадает с исходным энкодером')
        checks.append({'text': text, 'shape': list(actual.shape), 'finite': True,
                       'norm': float(np.linalg.norm(actual)), 'cosine_vs_st': cosine, 'max_abs_error': error})
    return {'graph_shapes': shapes, 'query_checks': checks,
            'intra_op_num_threads': 4, 'inter_op_num_threads': 1}


def export_worker(job: dict, *, model_loader=None, legacy_module=None, verifier=None) -> dict:
    validate_profile(job['profile'])
    directory = runner.wsl_path(job['run_dir']) / 'onnx'
    runner.wsl_path(job['hf_home'])
    os.environ.update(child_environment(job))
    model = (model_loader or _load_model)(job)
    revision = getattr(model[0].auto_model.config, '_commit_hash', None)
    if revision != job['profile']['revision'] or model.get_sentence_embedding_dimension() != 384:
        raise ValueError('Загрузилась другая ревизия или размерность исходной модели')
    if legacy_module is None:
        from benchmarks.legacy import embeddings as legacy_module
    if Path(legacy_module.ONNX_DIR) != directory:
        raise ValueError('Запустите экспорт в новом процессе с изолированными настройками')
    legacy_module._model = model
    exported = Path(legacy_module.export_onnx())
    if exported != directory:
        raise ValueError('Исходный экспорт сохранил модель вне изолированного каталога')
    for name in ('model.onnx', 'tokenizer.json'):
        if not runner.wsl_path(directory / name).is_file():
            raise ValueError(f'Исходный экспорт не создал {name}')
    verification = (verifier or _verify_export)(legacy_module, model, directory)
    alias = _pin_offline_main(job, revision)
    hashes = {str(path.relative_to(directory)): runner.file_hash(runner.wsl_path(path))
              for path in sorted(directory.rglob('*')) if path.is_file()}
    info = {
        'schema_version': 1, 'status': 'ready', 'artifact_type': 'native-project-token-encoder',
        'model': job['profile']['model'], 'requested_revision': 'main', 'resolved_revision': revision,
        'native_export_function': 'benchmarks.legacy.embeddings.export_onnx',
        'onnx_dir': str(directory), 'device': 'cpu', 'precision': 'fp32',
        'download_allowed': job['download'], 'max_tokens': 128, 'whole_corpus_seed_run': False,
        'offline_main_alias': alias, 'all_file_sha256': hashes,
        'model_bytes': (directory / 'model.onnx').stat().st_size,
        **verification,
    }
    runner.write_json(directory.parent / 'info.json', info)
    return info


def run_child(job: dict) -> dict:
    directory = runner.wsl_path(job['run_dir'])
    command = [sys.executable, '-m', 'benchmarks.native_export', '--job', str(directory / 'job.json')]
    log_path = directory / 'export.log'
    with log_path.open('w', encoding='utf-8') as log:
        result = subprocess.run(command, cwd=runner.ROOT, env=child_environment(job),
                                stdout=log, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RuntimeError(f'Ошибка штатного экспорта; журнал: {log_path}')
    return runner.read_json(directory / 'info.json')


def export_native(args, *, child_runner=None) -> Path:
    if sys.platform != 'linux' or sys.executable.lower().endswith('.exe'):
        raise ValueError('Используйте Linux Python в WSL')
    runner.wsl_path(Path(sys.executable).parent)
    profile_path = runner.wsl_path(args.profiles)
    profile = runner.load_profiles(profile_path)['original']
    validate_profile(profile)
    out = runner.wsl_path(args.out)
    identifier = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    directory = runner.wsl_path(out / 'native-original-onnx' / identifier)
    directory.mkdir(parents=True)
    job = {'schema_version': 1, 'profile': profile, 'run_dir': str(directory),
           'hf_home': str(runner.wsl_path(out / 'cache/huggingface')), 'download': args.download}
    runner.write_json(directory / 'job.json', job)
    info = (child_runner or run_child)(job)
    if info.get('status') != 'ready' or info.get('resolved_revision') != profile['revision']:
        raise ValueError('Штатный экспорт не подтвердил закреплённую ревизию')
    for name in ('model.onnx', 'tokenizer.json'):
        if not runner.wsl_path(directory / 'onnx' / name).is_file():
            raise ValueError('Штатный экспорт не создал все необходимые файлы')
    runner.write_json(out / 'native-original-onnx/latest.json',
                      {'info_path': str(directory / 'info.json'), 'onnx_dir': str(directory / 'onnx')})
    print(f'Штатный ONNX готов: {directory / "onnx"}', flush=True)
    return directory / 'onnx'


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--out', default=str(runner.ROOT / '.benchmarks'))
    result.add_argument('--profiles', default=str(DEFAULT_PROFILES))
    result.add_argument('--download', action='store_true')
    result.add_argument('--job', help=argparse.SUPPRESS)
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    job = None
    try:
        if args.job:
            job = runner.read_json(runner.wsl_path(args.job))
            export_worker(job)
        else:
            export_native(args)
        return 0
    except Exception as error:
        if args.job:
            import traceback
            traceback.print_exc()
            if isinstance(job, dict) and 'run_dir' in job:
                runner.write_json(runner.wsl_path(job['run_dir']) / 'info.json',
                                  {'status': 'failed', 'error': f'{type(error).__name__}: {error}'})
        else:
            print(f'Ошибка подготовки исходной модели: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
