"""Prepare versioned model indexes and check fresh-process offline retrieval."""

import argparse
import copy
from datetime import datetime, timezone
from pathlib import Path
import sys
import uuid

from benchmarks import runner


def _save(manifest: dict, manifest_path: Path, pinned: dict, pinned_path: Path) -> None:
    for path, data in ((manifest_path, manifest), (pinned_path, {'schema_version': 1, 'profiles': pinned})):
        temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex[:8])
        runner.write_json(temporary, data)
        temporary.replace(path)


def _revision(metadata: dict, profile: dict) -> str:
    value = metadata.get('resolved_revision')
    if not value and len(profile['revision']) == 40:
        value = profile['revision']
    if not isinstance(value, str) or len(value) != 40 or any(c not in '0123456789abcdef' for c in value.lower()):
        raise ValueError('The encoder did not identify an immutable model revision; specify a full model commit SHA')
    return value


def prepare_models(args: argparse.Namespace, *, inputs: dict | None = None, child_runner=None) -> Path:
    if sys.platform != 'linux' or sys.executable.lower().endswith('.exe'):
        raise ValueError('Use a Linux Python inside WSL')
    inputs = inputs or runner.validate_inputs(
        runner.wsl_path(args.source), runner.wsl_path(args.review),
        runner.wsl_path(args.queries), runner.wsl_path(args.profiles),
    )
    models = list(args.models or inputs['profiles'])
    if not models or len(models) != len(set(models)) or set(models) - set(inputs['profiles']):
        raise ValueError('Select existing model profiles without duplicates')
    if not args.corpora or len(args.corpora) != len(set(args.corpora)) or set(args.corpora) - set(runner.CORPORA):
        raise ValueError('Select existing corpora without duplicates')
    for name in ('threads', 'batch_size', 'max_tokens'):
        if getattr(args, name) < 1:
            raise ValueError(f'{name} must be positive')
    runner._runtime_dependencies('sentence-transformers', False)
    execute = child_runner or runner.run_child
    out = runner.wsl_path(args.out)
    prepared_root = runner.wsl_path(out / 'prepared')
    hf_home = runner.wsl_path(out / 'cache/huggingface')
    runner.wsl_path(out / 'cache/torch')
    runner.wsl_path(out / 'cache/xdg')
    collection_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    collection_dir = runner.wsl_path(prepared_root / 'collections' / collection_id)
    collection_dir.mkdir(parents=True)
    manifest_path = collection_dir / 'manifest.json'
    pinned_path = collection_dir / 'pinned-profiles.json'
    manifest = {
        'schema_version': 1,
        'status': 'preparing',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'collection_id': collection_id,
        'models': models,
        'corpora': list(args.corpora),
        'backend': 'sentence-transformers',
        'source_sha256': inputs['source_hashes'],
        'review_sha256': runner.file_hash(runner.wsl_path(args.review)),
        'dataset_sha256': runner.file_hash(runner.wsl_path(args.queries)),
        'profiles_sha256': runner.file_hash(runner.wsl_path(args.profiles)),
        'prepare_sha256': runner.file_hash(Path(__file__).absolute()),
        'hf_home': str(hf_home),
        'pinned_profiles_path': str(pinned_path),
        'tasks': [],
    }
    pinned = {}
    _save(manifest, manifest_path, pinned, pinned_path)
    runner.write_json(prepared_root / 'latest.json', {'manifest_path': str(manifest_path)})
    print(f'Preparation manifest: {manifest_path}', flush=True)

    for name in models:
        profile = copy.deepcopy(inputs['profiles'][name])
        network_allowed = args.download
        profile_failed = False
        for corpus_name in args.corpora:
            entry = {'profile_name': name, 'corpus': corpus_name, 'status': 'preparing'}
            manifest['tasks'].append(entry)
            if profile_failed:
                entry.update(status='skipped', error='The preceding corpus for this model failed')
                _save(manifest, manifest_path, pinned, pinned_path)
                continue
            run_dir = runner.wsl_path(prepared_root / name / collection_id / corpus_name)
            run_dir.mkdir(parents=True)
            entry['run_dir'] = str(run_dir)
            seed = runner.wsl_path(run_dir / 'seed')
            seed.mkdir()
            corpus = runner.build_corpus(inputs['sources'], inputs['aliases'], corpus_name)
            for category, data in corpus.items():
                runner.write_json(seed / f'{category}.json', data)
            expected_count = sum(len(command['patterns']) for data in corpus.values() for command in data['commands'])
            pipeline = runner.pipeline_hashes()
            benchmark = runner.benchmark_hashes()
            entry.update(
                corpus_sha256=runner.digest(corpus),
                pipeline_sha256=pipeline,
                benchmark_sha256=benchmark,
                expected_patterns=expected_count,
            )
            job = {
                'schema_version': 1,
                'profile_name': name,
                'profile': copy.deepcopy(profile),
                'backend': 'sentence-transformers',
                'corpus': corpus_name,
                'run_dir': str(run_dir),
                'hf_home': str(hf_home),
                'cases': inputs['cases'],
                'languages': ['ru', 'en'],
                'repeats': 1,
                'threads': args.threads,
                'batch_size': args.batch_size,
                'max_tokens': args.max_tokens,
                'top_k': 7,
                'download': network_allowed,
                'seed': 42,
            }
            runner.write_json(run_dir / 'job.json', job)
            _save(manifest, manifest_path, pinned, pinned_path)
            print(f'Preparing {name}/{corpus_name} ({expected_count} descriptions)...', flush=True)
            try:
                preparation = execute(job, 'prepare')
                entry['prepare'] = preparation
                if preparation['stats']['patterns'] != expected_count:
                    raise ValueError('The prepared index has an unexpected pattern count')
                if preparation['model']['dimensions'] != profile['dimensions']:
                    raise ValueError('The prepared model does not match its embedding dimension')
                revision = _revision(preparation['model'], profile)
                if name in pinned and pinned[name]['revision'] != revision:
                    raise ValueError('A model revision changed between preparation corpora')
                profile['revision'] = revision
                pinned[name] = copy.deepcopy(profile)
                job['profile'] = copy.deepcopy(profile)
                job['download'] = False
                network_allowed = False
                runner.write_json(run_dir / 'job.json', job)
                entry['profile'] = copy.deepcopy(profile)
                entry['cold'], entry['cached_cold'] = {}, {}
                _save(manifest, manifest_path, pinned, pinned_path)
                for language in ('ru', 'en'):
                    cold = execute(job, 'cold', language)
                    cached = execute(job, 'cached-cold', language)
                    if not cold['ranking'] or cold['ranking'] != cached['ranking']:
                        raise ValueError(f'{language}: cached and uncached smoke results differ or are empty')
                    if _revision(cold['model'], profile) != revision:
                        raise ValueError('Offline smoke loaded a different model revision')
                    if cached['model'].get('model_loaded') is not False:
                        raise ValueError('Cached smoke unexpectedly loaded the model')
                    entry['cold'][language] = cold
                    entry['cached_cold'][language] = cached
                if pipeline != runner.pipeline_hashes() or benchmark != runner.benchmark_hashes():
                    raise ValueError('Application or benchmark code changed during preparation; repeat this task')
                entry['status'] = 'ready'
                print(f'Ready: {name}/{corpus_name}, revision {revision}', flush=True)
            except Exception as error:
                entry.update(status='failed', error=f'{type(error).__name__}: {error}')
                profile_failed = True
                print(f'Failed: {name}/{corpus_name}: {error}', file=sys.stderr, flush=True)
            _save(manifest, manifest_path, pinned, pinned_path)

    ready = [entry for entry in manifest['tasks'] if entry['status'] == 'ready']
    manifest['ready_count'] = len(ready)
    manifest['task_count'] = len(manifest['tasks'])
    manifest['status'] = 'ready' if len(ready) == len(manifest['tasks']) else 'partial' if ready else 'failed'
    manifest['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
    _save(manifest, manifest_path, pinned, pinned_path)
    print(f'Prepared {len(ready)}/{len(manifest["tasks"])} indexes. Status: {manifest["status"]}.', flush=True)
    print(f'Pinned profiles: {pinned_path}', flush=True)
    return manifest_path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--out', default=str(runner.ROOT / '.benchmarks'))
    result.add_argument('--profiles', default=str(runner.DEFAULT_PROFILES))
    result.add_argument('--source', default=str(runner.DEFAULT_SOURCE))
    result.add_argument('--review', default=str(runner.DEFAULT_REVIEW))
    result.add_argument('--queries', default=str(runner.DEFAULT_QUERIES))
    result.add_argument('--models', nargs='+')
    result.add_argument('--corpora', nargs='+', choices=runner.CORPORA, default=['ru2'])
    result.add_argument('--download', action='store_true')
    result.add_argument('--threads', type=int, default=2)
    result.add_argument('--batch-size', type=int, default=32)
    result.add_argument('--max-tokens', type=int, default=128)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        path = prepare_models(args)
        return 0 if runner.read_json(path)['status'] == 'ready' else 1
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
        print(f'Preparation error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
