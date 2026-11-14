"""Run the prepared encoder matrix sequentially against the original application."""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
import uuid

from benchmarks import runner


def save(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + '.tmp')
    runner.write_json(temporary, value)
    temporary.replace(path)


def build_matrix(profiles: dict, artifacts: dict, native: Path) -> list[dict]:
    matrix = [
        {'id': 'original-native-en', 'profile': 'original', 'backend': 'project',
         'precision': 'native-onnx', 'corpus': 'original', 'role': 'baseline',
         'onnx_dir': str(native), 'project_initial_seed': True, 'threads': 4},
        {'id': 'original-fallback-en', 'profile': 'original', 'backend': 'project',
         'precision': 'pytorch', 'corpus': 'original', 'role': 'baseline', 'threads': 2},
    ]
    for backend, precision in (('sentence-transformers', 'pytorch'), ('onnx', 'fp32')):
        item = {'id': f'original-english-{precision}', 'profile': 'original',
                'backend': backend, 'precision': precision, 'corpus': 'original',
                'role': 'corpus-control', 'threads': 2}
        if backend == 'onnx':
            item['onnx_dir'] = artifacts[('original', 'fp32')]['directory']
        matrix.append(item)
    for name in profiles:
        for backend, precision in (('sentence-transformers', 'pytorch'), ('onnx', 'fp32'), ('onnx', 'int8')):
            item = {'id': f'{name}-ru2-{precision}', 'profile': name, 'backend': backend,
                    'precision': precision, 'corpus': 'ru2', 'role': 'controlled', 'threads': 2}
            if backend == 'onnx':
                item['onnx_dir'] = artifacts[(name, precision)]['directory']
            matrix.append(item)
    return matrix


def run_suite(args) -> Path:
    if sys.platform != 'linux' or sys.executable.lower().endswith('.exe'):
        raise ValueError('Use a Linux Python inside WSL')
    ready_path = runner.wsl_path(args.onnx_ready)
    ready = runner.read_json(ready_path)
    if ready['status'] != 'ready' or ready['artifact_count'] != 12:
        raise ValueError('Prepare all six FP32/8-bit encoders first')
    profile_path = runner.wsl_path(ready['pinned_profiles_path'])
    profiles = runner.load_profiles(profile_path)
    artifacts = {(x['profile'], x['precision']): x for x in ready['artifacts']}
    for item in artifacts.values():
        directory = runner.wsl_path(item['directory'])
        for name, expected in item['all_file_sha256'].items():
            if runner.file_hash(runner.wsl_path(directory / name)) != expected:
                raise ValueError(f'Prepared artifact changed: {item["profile"]}/{item["precision"]}/{name}')
    native = runner.wsl_path(args.native_onnx)
    native_info = runner.read_json(runner.wsl_path(native.parent / 'info.json'))
    out = runner.wsl_path(args.out)
    suite_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    suite_dir = runner.wsl_path(out / 'suites' / suite_id)
    suite_dir.mkdir(parents=True)
    manifest_path = suite_dir / 'manifest.json'
    baseline_profiles = suite_dir / 'baseline-profiles.json'
    baseline = {**profiles['original'], 'revision': 'main'}
    runner.write_json(baseline_profiles, {'schema_version': 1, 'profiles': {'original': baseline}})
    matrix = build_matrix(profiles, artifacts, native)
    manifest = {
        'schema_version': 1, 'status': 'running', 'suite_id': suite_id,
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'machine': runner.machine_info(), 'onnx_ready_path': str(ready_path),
        'profiles_path': str(profile_path), 'profiles_sha256': runner.file_hash(profile_path),
        'suite_code_sha256': runner.file_hash(Path(__file__).absolute()),
        'pipeline_sha256': runner.pipeline_hashes(), 'benchmark_sha256': runner.benchmark_hashes(),
        'native_baseline_info': native_info,
        'settings': {
            'primary_baseline_id': 'original-native-en', 'threads': 2,
            'native_original_threads': 4, 'repeats': args.repeats,
            'cold_repeats': args.cold_repeats, 'max_tokens': 128, 'batch_size': 32,
            'top_k': 7, 'languages': ['ru', 'en'], 'offline': True,
            'quality_in_scope_pairs': 90, 'out_of_scope_pairs': 6,
            'notes': [
                'All model measurements run sequentially on the same WSL CPU.',
                'Quality is evaluated on 90 in-scope tasks; latency includes all 96 cases.',
                'Controlled encoders use ru2: 3892 descriptions. Original English dictionary has 1946.',
                'Native baseline follows first CLI setup: ST256/batch128 seeding, then original ONNX128/fixed padding/four-thread query loader.',
                'Cold means a fresh process; Linux filesystem caches are not cleared.',
                'Cold medians use three fresh processes per language; cache-only medians are separate.',
                'Measures search core and process startup, without interactive terminal UI.',
            ],
        },
        'runs': [{**item, 'status': 'pending'} for item in matrix],
    }
    save(manifest_path, manifest)
    save(out / 'suites/latest.json', {'manifest_path': str(manifest_path)})
    print(f'Suite manifest: {manifest_path}', flush=True)
    for number, item in enumerate(manifest['runs'], 1):
        item['status'] = 'running'
        save(manifest_path, manifest)
        print(f'[{number}/{len(matrix)}] {item["id"]}', flush=True)
        argv = ['run', '--profile', item['profile'], '--backend', item['backend'],
                '--corpus', item['corpus'], '--languages', 'ru', 'en',
                '--profiles', str(baseline_profiles if item['role'] == 'baseline' else profile_path),
                '--out', str(out), '--threads', str(item['threads']),
                '--repeats', str(args.repeats), '--batch-size', '32', '--max-tokens', '128']
        if item.get('onnx_dir'):
            argv.extend(['--onnx-dir', item['onnx_dir']])
        if item.get('project_initial_seed'):
            argv.append('--project-initial-seed')
        run_args = runner.parser().parse_args(argv)
        try:
            inputs = runner.validate_inputs(runner.wsl_path(run_args.source), runner.wsl_path(run_args.review),
                                            runner.wsl_path(run_args.queries), runner.wsl_path(run_args.profiles))
            report_path = runner.run_benchmark(run_args, inputs)
            report = runner.read_json(report_path)
            job = report['request']
            report['cold_repeats'] = {language: [report['cold'][language]] for language in job['languages']}
            report['cached_cold_repeats'] = {language: [report['cached_cold'][language]] for language in job['languages']}
            for repeat in range(1, args.cold_repeats):
                for language in job['languages']:
                    print(f'Extra fresh process {repeat+1}/{args.cold_repeats}: {language}', flush=True)
                    cold = runner.run_child(job, 'cold', language)
                    cached = runner.run_child(job, 'cached-cold', language)
                    if cold['ranking'] != cached['ranking']:
                        raise ValueError('Fresh and cached query rankings differ')
                    report['cold_repeats'][language].append(cold)
                    report['cached_cold_repeats'][language].append(cached)
            save(report_path, report)
            if manifest['pipeline_sha256'] != runner.pipeline_hashes() or manifest['benchmark_sha256'] != runner.benchmark_hashes():
                raise ValueError('Measured application/benchmark code changed during the suite')
            item.update(status='ready', report_path=str(report_path))
        except Exception as error:
            item.update(status='failed', error=f'{type(error).__name__}: {error}')
            print(f'Failed: {item["id"]}: {error}', file=sys.stderr, flush=True)
        save(manifest_path, manifest)
    ready_count = sum(item['status'] == 'ready' for item in manifest['runs'])
    manifest.update(status='ready' if ready_count == len(matrix) else 'partial', ready_count=ready_count,
                    completed_at_utc=datetime.now(timezone.utc).isoformat())
    save(manifest_path, manifest)
    print(f'Suite complete: {ready_count}/{len(matrix)}. {manifest_path}', flush=True)
    return manifest_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--onnx-ready', default=str(runner.ROOT / '.benchmarks/onnx-ready.json'))
    parser.add_argument('--native-onnx', required=True)
    parser.add_argument('--out', default=str(runner.ROOT / '.benchmarks'))
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--cold-repeats', type=int, default=3)
    args = parser.parse_args(argv)
    if args.repeats < 1 or args.cold_repeats < 1:
        parser.error('Repeat counts must be positive')
    result = run_suite(args)
    return 0 if runner.read_json(result)['status'] == 'ready' else 1


if __name__ == '__main__':
    raise SystemExit(main())
