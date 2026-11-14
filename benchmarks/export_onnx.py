"""Export pinned offline encoders to versioned FP32 and INT8 ONNX artifacts."""

import argparse
import copy
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from benchmarks import runner


def _save(path: Path, data: dict) -> None:
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex[:8])
    runner.write_json(temporary, data)
    temporary.replace(path)


def code_hashes() -> dict:
    return {
        name: runner.file_hash(runner.wsl_path(Path(__file__).parent / name))
        for name in ('export_onnx.py', 'onnx_export_worker.py', 'onnx_encoder.py')
    }


def child_environment(job: dict) -> dict:
    hf_home = runner.wsl_path(job['hf_home'])
    paths = {
        'HF_HOME': hf_home,
        'HF_HUB_CACHE': hf_home / 'hub',
        'SENTENCE_TRANSFORMERS_HOME': hf_home,
        'TORCH_HOME': hf_home.parent / 'torch',
        'XDG_CACHE_HOME': hf_home.parent / 'xdg',
    }
    environment = os.environ.copy()
    environment.update({name: str(runner.wsl_path(path)) for name, path in paths.items()})
    environment.update({
        'PATH': '/usr/bin:/bin',
        'HF_HUB_OFFLINE': '1',
        'TRANSFORMERS_OFFLINE': '1',
        'HF_HUB_DISABLE_TELEMETRY': '1',
        'HF_HUB_DISABLE_PROGRESS_BARS': '1',
        'TOKENIZERS_PARALLELISM': 'false',
        'CUDA_VISIBLE_DEVICES': '',
        'OMP_NUM_THREADS': str(job['threads']),
        'MKL_NUM_THREADS': str(job['threads']),
        'OPENBLAS_NUM_THREADS': str(job['threads']),
    })
    return environment


def run_child(job: dict) -> dict:
    directory = runner.wsl_path(job['run_dir'])
    job_path = runner.wsl_path(directory / 'job.json')
    result_path = runner.wsl_path(directory / 'result.json')
    log_path = runner.wsl_path(directory / 'child.log')
    command = [sys.executable, '-m', 'benchmarks.onnx_export_worker', '--job', str(job_path)]
    with log_path.open('w', encoding='utf-8') as log:
        result = subprocess.run(
            command, cwd=runner.ROOT, env=child_environment(job),
            stdout=log, stderr=subprocess.STDOUT, check=False,
        )
    if result.returncode != 0:
        detail = ''
        if result_path.is_file():
            failure = runner.read_json(result_path)
            if isinstance(failure, dict) and isinstance(failure.get('error'), str):
                detail = ': ' + failure['error']
        raise RuntimeError(f'ONNX worker exited with code {result.returncode}{detail}; see {log_path}')
    return runner.read_json(result_path)


def artifact_index(task: dict) -> list[dict]:
    artifacts = []
    for precision in ('fp32', 'int8'):
        directory = runner.wsl_path(Path(task['run_dir']) / precision)
        for name in ('model.onnx', 'tokenizer.json', 'metadata.json'):
            if not runner.wsl_path(directory / name).is_file():
                raise ValueError(f'The ready worker did not create {precision}/{name}')
        hashes = {str(path.relative_to(directory)): runner.file_hash(runner.wsl_path(path))
                  for path in sorted(directory.rglob('*')) if path.is_file()}
        artifacts.append({'profile': task['profile_name'], 'precision': precision,
                          'directory': str(directory), 'dimensions': task['profile']['dimensions'],
                          'model_revision': task['profile']['revision'], 'all_file_sha256': hashes})
    return artifacts


def write_ready_index(out: Path, manifest: dict, manifest_path: Path, profiles: dict) -> None:
    artifacts = [item for task in manifest['tasks'] if task['status'] == 'ready'
                 for item in task['artifacts']]
    exported = {item['profile'] for item in artifacts}
    complete = (manifest['status'] == 'ready' and len(artifacts) == 12
                and len(exported) == 6 and exported == set(profiles) and 'original' in exported)
    index = {
        'schema_version': 1, 'status': 'ready' if complete else 'partial' if artifacts else 'failed',
        'model_count': len(exported), 'artifact_count': len(artifacts),
        'manifest_path': str(manifest_path), 'pinned_profiles_path': manifest['profiles_path'],
        'profiles_sha256': manifest['profiles_sha256'], 'artifacts': artifacts,
        'max_tokens': manifest['max_tokens'], 'opset': manifest['opset'],
        'device': 'cpu', 'offline_export': True,
    }
    _save(runner.wsl_path(out / 'onnx-ready.json'), index)


def export_models(args: argparse.Namespace, *, profiles: dict | None = None, child_runner=None) -> Path:
    if sys.platform != 'linux' or sys.executable.lower().endswith('.exe'):
        raise ValueError('Use a Linux Python inside WSL')
    runner.wsl_path(Path(sys.executable).parent)
    profile_path = runner.wsl_path(args.profiles)
    profiles = runner.load_profiles(profile_path) if profiles is None else profiles
    models = list(args.models or profiles)
    if not models or len(models) != len(set(models)) or set(models) - set(profiles):
        raise ValueError('Select existing model profiles without duplicates')
    for name in models:
        revision = profiles[name].get('revision')
        if not isinstance(revision, str) or not re.fullmatch('[0-9a-fA-F]{40}', revision):
            raise ValueError(f'{name}: supply an immutable 40-character commit SHA in --profiles; download and pin models first')
    for name in ('threads', 'max_tokens', 'opset'):
        value = getattr(args, name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f'{name} must be a positive integer')
    if args.opset < 17:
        raise ValueError('opset must be at least 17')
    out = runner.wsl_path(args.out)
    onnx_root = runner.wsl_path(out / 'onnx')
    hf_home = runner.wsl_path(out / 'cache/huggingface')
    for name in ('torch', 'xdg'):
        runner.wsl_path(out / 'cache' / name)
    hashes = code_hashes()
    profile_hash = runner.file_hash(profile_path)
    collection_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    collection_directory = runner.wsl_path(onnx_root / 'collections' / collection_id)
    collection_directory.mkdir(parents=True)
    manifest_path = collection_directory / 'manifest.json'
    manifest = {
        'schema_version': 1,
        'status': 'exporting',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'collection_id': collection_id,
        'models': models,
        'variants': ['fp32', 'int8'],
        'profiles_path': str(profile_path),
        'profiles_sha256': profile_hash,
        'code_sha256': hashes,
        'hf_home': str(hf_home),
        'offline': True,
        'threads': args.threads,
        'max_tokens': args.max_tokens,
        'opset': args.opset,
        'tasks': [],
    }
    _save(manifest_path, manifest)
    _save(onnx_root / 'latest.json', {'manifest_path': str(manifest_path)})
    print(f'ONNX export manifest: {manifest_path}', flush=True)
    execute = child_runner or run_child
    for name in models:
        directory = runner.wsl_path(onnx_root / name / collection_id)
        directory.mkdir(parents=True)
        for variant in ('fp32', 'int8'):
            runner.wsl_path(directory / variant).mkdir()
        profile = copy.deepcopy(profiles[name])
        job = {
            'profile_name': name,
            'profile': profile,
            'run_dir': str(directory),
            'hf_home': str(hf_home),
            'threads': args.threads,
            'max_tokens': args.max_tokens,
            'opset': args.opset,
        }
        runner.write_json(directory / 'job.json', job)
        task = {'profile_name': name, 'profile': copy.deepcopy(profile), 'run_dir': str(directory), 'status': 'exporting'}
        manifest['tasks'].append(task)
        _save(manifest_path, manifest)
        print(f'Exporting {name}: FP32 and INT8...', flush=True)
        try:
            result = execute(job)
            task['result'] = result
            if not isinstance(result, dict) or result.get('status') != 'ready':
                raise ValueError('Worker did not report successful FP32 and INT8 validation')
            if hashes != code_hashes() or profile_hash != runner.file_hash(profile_path):
                raise ValueError('Export code or model profiles changed during preparation; repeat this export')
            task['artifacts'] = artifact_index(task)
            task['status'] = 'ready'
            print(f'Ready: {name}', flush=True)
        except Exception as error:
            task.update(status='failed', error=f'{type(error).__name__}: {error}')
            print(f'Failed: {name}: {error}', file=sys.stderr, flush=True)
        _save(manifest_path, manifest)
    ready = sum(task['status'] == 'ready' for task in manifest['tasks'])
    manifest.update(
        ready_count=ready,
        task_count=len(manifest['tasks']),
        status='ready' if ready == len(manifest['tasks']) else 'partial' if ready else 'failed',
        completed_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    _save(manifest_path, manifest)
    write_ready_index(out, manifest, manifest_path, profiles)
    print(f'Exported {ready}/{len(manifest["tasks"])} models. Status: {manifest["status"]}.', flush=True)
    return manifest_path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--profiles', default=str(runner.ROOT / 'benchmarks/pinned-profiles.json'))
    result.add_argument('--out', default=str(runner.ROOT / '.benchmarks'))
    result.add_argument('--models', nargs='+')
    result.add_argument('--threads', type=int, default=2)
    result.add_argument('--max-tokens', type=int, default=128)
    result.add_argument('--opset', type=int, default=17)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        path = export_models(args)
        return 0 if runner.read_json(path)['status'] == 'ready' else 1
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
        print(f'ONNX export error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
