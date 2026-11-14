"""Validate benchmark inputs and orchestrate isolated CPU measurements."""

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from benchmarks.metrics import evaluate


ROOT = Path(__file__).absolute().parent.parent
DEFAULT_SOURCE = ROOT / 'benchmarks/data/original'
DEFAULT_REVIEW = ROOT / 'benchmarks/data/russian-variants.json'
DEFAULT_QUERIES = ROOT / 'benchmarks/queries.json'
DEFAULT_PROFILES = ROOT / 'benchmarks/profiles.json'
CORPORA = ('original', 'ru1', 'ru2', 'mixed')


def wsl_path(value: str | Path) -> Path:
    raw = os.fspath(value)
    if re.match(r'^[A-Za-z]:[\\/]|^\\\\|^/mnt/[A-Za-z](?:/|$)', raw):
        raise ValueError('Use a Linux path inside WSL, outside mounted host drives')
    path = Path(os.path.abspath(raw))
    if re.match(r'^/mnt/[A-Za-z](?:/|$)', str(path)):
        raise ValueError('Use a Linux path inside WSL, outside mounted host drives')
    for part in reversed((path, *path.parents)):
        if part.is_symlink():
            raise ValueError(f'Supply a direct WSL path instead of a symbolic link: {part}')
    return path


def read_json(path: Path) -> dict:
    with path.open(encoding='utf-8') as handle:
        return json.load(handle)


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def digest(data: object) -> str:
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def file_hash(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def normalized_text(text: str) -> str:
    text = unicodedata.normalize('NFKC', text).casefold().replace('ё', 'е')
    return ' '.join(re.sub(r'[^\w\s]', ' ', text).split())


def load_sources(source_dir: Path) -> tuple[dict, dict, dict]:
    sources, scenarios, hashes = {}, {}, {}
    for path in sorted(source_dir.glob('*.json')):
        data = read_json(wsl_path(path))
        category = data['category']
        if category != path.stem or category in sources:
            raise ValueError(f'Invalid or repeated source category: {path.name}')
        sources[category] = data
        hashes[path.name] = file_hash(path)
        for ci, command in enumerate(data['commands']):
            for pi, pattern in enumerate(command['patterns']):
                scenarios[(category, ci, pi)] = pattern
    if not scenarios:
        raise ValueError('The source dictionary is empty')
    return sources, scenarios, hashes


def load_review(path: Path, scenarios: dict, source_dir: Path) -> tuple[dict, dict]:
    review = read_json(path)
    if review.get('language') != 'ru':
        raise ValueError('Expected a Russian review dictionary')
    for name, expected in review.get('source_sha256', {}).items():
        source = source_dir / Path(name).name
        if not source.is_file() or file_hash(source) != expected:
            raise ValueError(f'Russian review is stale for {source.name}; regenerate it first')
    aliases = {}
    for row in review['scenarios']:
        key = (row['category'], row['source_command_index'], row['source_pattern_index'])
        if key not in scenarios or key in aliases:
            raise ValueError(f'Unknown or repeated review scenario: {key}')
        pattern = scenarios[key]
        if row['command'] != pattern['command'] or row['source_text'] != pattern['text']:
            raise ValueError(f'Russian review no longer matches source: {key}')
        if row['destructive'] != bool(pattern.get('destructive', False)):
            raise ValueError(f'Russian review changes destructive metadata: {key}')
        phrases = row['patterns_ru']
        if not isinstance(phrases, list) or len(phrases) != 2 or any(
            not isinstance(p, str) or not p.strip() or not re.search('[А-Яа-яЁё]', p) for p in phrases
        ) or len({normalized_text(p) for p in phrases}) != 2:
            raise ValueError(f'Expected two different Russian phrases: {key}')
        aliases[key] = row
    if set(aliases) != set(scenarios):
        raise ValueError('Russian review must cover every source scenario exactly once')
    return review, aliases


def load_profiles(path: Path) -> dict:
    data = read_json(path)
    if data.get('schema_version') != 1 or not isinstance(data.get('profiles'), dict):
        raise ValueError('Unsupported profiles schema')
    for name, profile in data['profiles'].items():
        if not re.fullmatch('[a-z0-9][a-z0-9_-]*', name):
            raise ValueError(f'Invalid profile name: {name}')
        for field in ('model', 'revision'):
            if not isinstance(profile.get(field), str) or not profile[field].strip():
                raise ValueError(f'{name}: {field} must be a nonempty string')
        model = profile['model']
        if model.startswith('/') or re.match(r'^[A-Za-z]:|^\\\\', model):
            wsl_path(model)
        if not isinstance(profile.get('dimensions'), int) or isinstance(profile['dimensions'], bool) or profile['dimensions'] < 1:
            raise ValueError(f'{name}: invalid embedding dimensions')
        for field in ('query_prefix', 'document_prefix'):
            if not isinstance(profile.get(field), str):
                raise ValueError(f'{name}: {field} must be a string')
        config_kwargs = profile.get('config_kwargs', {})
        if not isinstance(config_kwargs, dict) or any(not isinstance(key, str) for key in config_kwargs):
            raise ValueError(f'{name}: config_kwargs must be an object with string keys')
    return data['profiles']


def validate_inputs(source_dir: Path, review_path: Path, queries_path: Path, profiles_path: Path) -> dict:
    sources, scenarios, hashes = load_sources(source_dir)
    review, aliases = load_review(review_path, scenarios, source_dir)
    profiles = load_profiles(profiles_path)
    query_file = read_json(queries_path)
    if query_file.get('schema_version') != 1:
        raise ValueError('Unsupported query schema')
    cases = query_file['cases']
    evaluate(cases, {})
    if not cases:
        raise ValueError('No evaluation cases supplied')
    all_commands = {p['command'] for p in scenarios.values()}
    broken = {r['command'] for r in aliases.values() if r.get('notes_ru', '').startswith('Исходный шаблон требует исправления:')}
    indexed_texts = {normalized_text(p['text']) for p in scenarios.values()}
    indexed_texts.update(normalized_text(text) for r in aliases.values() for text in r['patterns_ru'])
    seen_queries = set()
    for case in cases:
        if case['category'] not in sources and case['category'] != 'out_of_scope':
            raise ValueError(f"{case['id']}: unknown category")
        for language in ('ru', 'en'):
            query = case.get('query_' + language)
            if not isinstance(query, str) or not query.strip():
                raise ValueError(f"{case['id']}: missing {language} query")
            key = (language, normalized_text(query))
            if not key[1] or key in seen_queries:
                raise ValueError(f"{case['id']}: repeated or empty normalized query")
            seen_queries.add(key)
            if key[1] in indexed_texts:
                raise ValueError(f"{case['id']}: evaluation query copies an indexed pattern")
        for field in ('expected_commands', 'forbidden_commands'):
            if set(case.get(field, [])) - all_commands:
                raise ValueError(f"{case['id']}: unknown command in {field}")
        if set(case['expected_commands']) & broken:
            raise ValueError(f"{case['id']}: an expected command has a known broken source template")
    return {
        'sources': sources, 'scenarios': scenarios, 'aliases': aliases,
        'cases': cases, 'profiles': profiles, 'source_hashes': hashes,
        'summary': {
            'source_commands': sum(len(s['commands']) for s in sources.values()),
            'source_scenarios': len(scenarios), 'russian_phrases': 2 * len(aliases),
            'case_count': len(cases),
            'in_scope_cases': sum(bool(c['expected_commands']) for c in cases),
            'out_of_scope_cases': sum(not c['expected_commands'] for c in cases),
            'categories': dict(sorted(Counter(c['category'] for c in cases).items())),
            'exact_query_leakage': 0,
            'known_broken_source_templates': len(broken),
        },
    }


def build_corpus(sources: dict, aliases: dict, mode: str) -> dict:
    if mode not in CORPORA:
        raise ValueError(f'Unknown corpus: {mode}')
    output = copy.deepcopy(sources)
    for category, data in output.items():
        for ci, command in enumerate(data['commands']):
            patterns = []
            for pi, original in enumerate(command['patterns']):
                if mode in ('original', 'mixed'):
                    patterns.append(copy.deepcopy(original))
                if mode != 'original':
                    phrases = aliases[(category, ci, pi)]['patterns_ru']
                    for text in phrases[:1] if mode == 'ru1' else phrases:
                        patterns.append({**original, 'text': text})
            command['patterns'] = patterns
    return output


def pipeline_hashes() -> dict:
    return {name: file_hash(ROOT / 'benchmarks/legacy' / name) for name in (
        'config.py', 'db.py', 'embeddings.py', 'search.py', 'seed.py',
    )}


def benchmark_hashes() -> dict:
    return {name: file_hash(ROOT / 'benchmarks' / name) for name in (
        'runner.py', 'worker.py', 'metrics.py', 'onnx_encoder.py',
    )}


def machine_info() -> dict:
    cpu_model = None
    try:
        with Path('/proc/cpuinfo').open(encoding='utf-8') as handle:
            for line in handle:
                if line.startswith('model name'):
                    cpu_model = line.partition(':')[2].strip()
                    break
    except OSError:
        pass
    return {
        'platform': platform.platform(), 'python': sys.version,
        'cpu_count': os.cpu_count(), 'cpu_model': cpu_model,
        'memory_total_bytes': os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE'),
        'cpu_affinity': sorted(os.sched_getaffinity(0)),
    }


def child_environment(job: dict) -> dict:
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith(('CF_', 'HF_', 'HUGGINGFACE_', 'TRANSFORMERS_')):
            environment.pop(key)
    run_dir = job['run_dir']
    environment.update({
        'PATH': '/usr/local/bin:/usr/bin:/bin',
        'PYTHONPATH': str(ROOT / 'src') + os.pathsep + str(ROOT),
        'CF_MODEL': job['profile']['model'],
        'CF_EMBEDDING_DIM': str(job['profile']['dimensions']),
        'CF_DB_DIR': run_dir, 'CF_DB_NAME': 'cf.db',
        'CF_DATA_DIR': str(Path(run_dir) / 'seed'),
        'HF_HOME': job['hf_home'],
        'HF_HUB_CACHE': str(Path(job['hf_home']) / 'hub'),
        'HF_HUB_OFFLINE': '0' if job['download'] else '1',
        'HF_HUB_DISABLE_TELEMETRY': '1',
        'TORCH_HOME': str(Path(job['hf_home']).parent / 'torch'),
        'XDG_CACHE_HOME': str(Path(job['hf_home']).parent / 'xdg'),
        'TOKENIZERS_PARALLELISM': 'false',
        'OMP_NUM_THREADS': str(job['threads']), 'MKL_NUM_THREADS': str(job['threads']),
        'PYTHONHASHSEED': str(job['seed']),
    })
    return environment


def run_child(job: dict, phase: str, language: str | None = None) -> dict:
    run_dir = Path(job['run_dir'])
    name = phase if language is None else f'{phase}-{language}'
    command = [sys.executable, '-m', 'benchmarks.worker', '--job', str(run_dir / 'job.json'), '--phase', phase]
    if language is not None:
        command += ['--language', language]
    started = time.perf_counter()
    environment_job = {**job, 'download': job['download'] if phase == 'prepare' else False}
    with (run_dir / f'{name}.log').open('w', encoding='utf-8') as log:
        result = subprocess.run(command, cwd=ROOT, env=child_environment(environment_job), stdout=log, stderr=subprocess.STDOUT)
    elapsed = (time.perf_counter() - started) * 1000
    if result.returncode:
        raise RuntimeError(f'{name} failed; see {run_dir / (name + ".log")}')
    data = read_json(run_dir / f'{name}.json')
    data['full_process_wall_ms'] = elapsed
    return data


def _runtime_dependencies(backend: str, onnx: bool) -> None:
    required = ['numpy', 'sqlite_vec']
    required += ['onnxruntime', 'tokenizers'] if backend == 'onnx' or backend == 'project' and onnx else ['sentence_transformers', 'torch']
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    if missing:
        raise ValueError('Missing benchmark runtime dependencies: ' + ', '.join(missing) + '. Install the project in a WSL virtual environment; validation and metric tests need no model packages.')


def onnx_artifact_metadata(directory: Path, profile: dict, max_tokens: int) -> dict:
    metadata = read_json(wsl_path(directory / 'metadata.json'))
    if not isinstance(metadata, dict):
        raise ValueError('ONNX metadata must be an object')
    revision = profile.get('revision', '')
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('The ONNX backend requires a pinned 40-character model revision in the profile')
    expected = {key: profile[key] for key in ('model', 'revision', 'dimensions', 'query_prefix', 'document_prefix')}
    expected['max_tokens'] = max_tokens
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise ValueError(f'ONNX metadata.{key} does not match the requested profile or limits')
    if metadata.get('precision') not in ('fp32', 'int8'):
        raise ValueError('ONNX metadata.precision must be fp32 or int8')
    if metadata.get('pooling_in_graph') is not True or metadata.get('normalization') != 'l2':
        raise ValueError('The ONNX backend requires pooling and L2 normalization in the exported graph')
    return metadata


def run_benchmark(args: argparse.Namespace, inputs: dict) -> Path:
    if sys.platform != 'linux' or sys.executable.lower().endswith('.exe'):
        raise ValueError('Run this benchmark with a Linux Python inside WSL')
    if args.profile not in inputs['profiles']:
        raise ValueError(f'Unknown model profile: {args.profile}')
    profile = copy.deepcopy(inputs['profiles'][args.profile])
    backend = args.backend or ('project' if args.profile == 'original' else 'sentence-transformers')
    if args.revision:
        profile['revision'] = args.revision
    if backend == 'project' and (
        args.profile != 'original' or profile['revision'] != 'main'
        or profile['model'] not in ('all-MiniLM-L6-v2', 'sentence-transformers/all-MiniLM-L6-v2')
        or profile['dimensions'] != 384 or profile['query_prefix'] or profile['document_prefix']
        or profile.get('config_kwargs')
    ):
        raise ValueError('The project backend preserves the original loader; use profile original/revision main, or the controlled sentence-transformers backend')
    if args.onnx_dir and backend not in ('project', 'onnx'):
        raise ValueError('--onnx-dir requires backend project or onnx')
    if backend == 'onnx' and not args.onnx_dir:
        raise ValueError('The ONNX backend requires --onnx-dir with a complete sentence encoder')
    initial_seed = getattr(args, 'project_initial_seed', False)
    if initial_seed and (backend != 'project' or not args.onnx_dir):
        raise ValueError('--project-initial-seed requires the project backend and --onnx-dir')
    _runtime_dependencies(backend, bool(args.onnx_dir))
    if initial_seed:
        _runtime_dependencies('sentence-transformers', False)
    threads = args.threads or (4 if backend == 'project' and args.onnx_dir else 2)
    source_assets, asset_hashes = None, {}
    if args.onnx_dir:
        source_assets = wsl_path(args.onnx_dir)
        for path in source_assets.rglob('*'):
            wsl_path(path)
        if not (source_assets / 'model.onnx').is_file() or not (source_assets / 'tokenizer.json').is_file():
            raise ValueError('--onnx-dir must contain model.onnx and tokenizer.json')
        if backend == 'onnx':
            onnx_artifact_metadata(source_assets, profile, args.max_tokens)
        elif (source_assets / 'metadata.json').is_file() and read_json(source_assets / 'metadata.json').get('pooling_in_graph') is True:
            raise ValueError('Use --backend onnx for complete sentence encoders; the original project loader expects token embeddings')
        asset_hashes = {str(p.relative_to(source_assets)): file_hash(p) for p in source_assets.rglob('*') if p.is_file()}
    out = wsl_path(args.out)
    hf_home = wsl_path(out / 'cache/huggingface')
    wsl_path(out / 'cache/torch')
    wsl_path(out / 'cache/xdg')
    out.mkdir(parents=True, exist_ok=True)
    corpus = build_corpus(inputs['sources'], inputs['aliases'], args.corpus)
    pipeline, benchmark = pipeline_hashes(), benchmark_hashes()
    fingerprint = digest({
        'profile': profile, 'backend': backend, 'corpus': corpus,
        'dataset': inputs['cases'], 'pipeline': pipeline, 'benchmark': benchmark,
        'languages': args.languages, 'max_tokens': args.max_tokens,
        'threads': threads, 'repeats': args.repeats, 'top_k': args.top_k,
        'batch_size': args.batch_size, 'seed': args.seed, 'onnx_assets': asset_hashes,
        'project_initial_seed': initial_seed,
    })
    run_dir = out / 'runs' / f'{args.profile}-{args.corpus}-{fingerprint[:10]}-{uuid.uuid4().hex[:8]}'
    run_dir.mkdir(parents=True)
    seed_dir = run_dir / 'seed'
    seed_dir.mkdir()
    for category, data in corpus.items():
        write_json(seed_dir / f'{category}.json', data)
    if source_assets is not None and not initial_seed:
        shutil.copytree(source_assets, run_dir / 'onnx')
    job = {
        'schema_version': 1, 'profile_name': args.profile, 'profile': profile,
        'backend': backend, 'corpus': args.corpus,
        'run_dir': str(run_dir), 'hf_home': str(hf_home),
        'cases': inputs['cases'], 'languages': args.languages,
        'repeats': args.repeats, 'threads': threads,
        'batch_size': args.batch_size, 'max_tokens': args.max_tokens,
        'top_k': args.top_k, 'download': args.download if backend != 'onnx' else False, 'seed': args.seed,
        'project_initial_seed': initial_seed,
    }
    write_json(run_dir / 'job.json', job)
    print(f'Run: {run_dir}', flush=True)
    print('Preparing model and index...', flush=True)
    prepared = run_child(job, 'prepare')
    if initial_seed:
        shutil.copytree(source_assets, run_dir / 'onnx')
    cold, cached_cold = {}, {}
    for language in args.languages:
        print(f'Fresh-process query: {language}', flush=True)
        cold[language] = run_child(job, 'cold', language)
        print(f'Fresh-process cached query: {language}', flush=True)
        cached_cold[language] = run_child(job, 'cached-cold', language)
    print('Measuring uncached and cached retrieval...', flush=True)
    warm = run_child(job, 'warm')
    if pipeline != pipeline_hashes() or benchmark != benchmark_hashes():
        raise RuntimeError('Application or benchmark code changed during measurement; repeat this run')
    report = {
        'schema_version': 1, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'fingerprint': fingerprint, 'dataset_sha256': file_hash(wsl_path(args.queries)),
        'review_sha256': file_hash(wsl_path(args.review)),
        'source_sha256': inputs['source_hashes'], 'pipeline_sha256': pipeline,
        'benchmark_sha256': benchmark,
        'machine': machine_info(),
        'request': job, 'onnx_assets_sha256': asset_hashes,
        'prepare': prepared, 'cold': cold, 'cached_cold': cached_cold, 'warm': warm,
    }
    report_path = run_dir / 'report.json'
    write_json(report_path, report)
    print_comparison([report])
    print(f'Report: {report_path}')
    return report_path


def print_comparison(reports: list[dict]) -> None:
    datasets = {r['dataset_sha256'] for r in reports}
    if len(datasets) != 1:
        raise ValueError('Reports use different query datasets; rerun on the same cases')
    headers = ('profile', 'backend', 'weights', 'corpus', 'lang', 'Hit@1', 'Hit@3', 'MRR@7', 'bad@3', 'p50 ms', 'p95 ms', 'cache ms', 'cold ms', 'cold cache ms', 'inference MiB')
    rows = [headers]
    for report in reports:
        job = report['request']
        for language, result in report['warm']['languages'].items():
            overall = result['metrics']['overall']
            latency = result['latency_ms']
            fmt = lambda value: 'n/a' if value is None else f'{value:.3f}'
            rows.append((
                job['profile_name'], job['backend'], report['warm'].get('model', {}).get('weight_precision', 'n/a'), job['corpus'], language,
                fmt(overall['hit@1']), fmt(overall['hit@3']), fmt(overall['mrr@7']),
                fmt(overall['forbidden_hit@3']),
                fmt(latency['uncached']['p50']), fmt(latency['uncached']['p95']),
                fmt(latency['cache_hit']['p50']),
                fmt(report['cold'][language]['full_process_wall_ms']),
                fmt(report.get('cached_cold', {}).get(language, {}).get('full_process_wall_ms')),
                fmt(max(report['warm']['peak_rss_bytes'], report['cold'][language]['peak_rss_bytes']) / 1024**2),
            ))
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(headers))]
    for row in rows:
        print('  '.join(str(value).ljust(width) for value, width in zip(row, widths)))
    signatures = {
        digest({'pipeline': r['pipeline_sha256'], 'source': r['source_sha256'],
                'benchmark': r.get('benchmark_sha256'),
                'threads': r['request']['threads'], 'top_k': r['request']['top_k'],
                'backend': r['request']['backend'], 'max_tokens': r['request']['max_tokens'],
                'effective_max_tokens': r['warm'].get('model', {}).get('effective_max_tokens'),
                'effective_threads': r['warm'].get('model', {}).get('effective_threads'),
                'encoder_backend': r['warm'].get('model', {}).get('encoder_backend'),
                'dtype': r['warm'].get('model', {}).get('dtype'),
                'weight_precision': r['warm'].get('model', {}).get('weight_precision'),
                'versions': r['warm'].get('library_versions'),
                'machine': r['machine']})
        for r in reports
    }
    if len(signatures) > 1:
        print('Measurement conditions differ (backend, limits, threads, machine or pipeline); inspect report metadata before attributing latency changes to the model.')


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest='command', required=True)
    for name in ('validate', 'run'):
        sub = commands.add_parser(name)
        sub.add_argument('--source', default=str(DEFAULT_SOURCE))
        sub.add_argument('--review', default=str(DEFAULT_REVIEW))
        sub.add_argument('--queries', default=str(DEFAULT_QUERIES))
        sub.add_argument('--profiles', default=str(DEFAULT_PROFILES))
        if name == 'run':
            sub.add_argument('--profile', required=True)
            sub.add_argument('--corpus', choices=CORPORA, default='original')
            sub.add_argument('--backend', choices=('project', 'sentence-transformers', 'onnx'))
            sub.add_argument('--languages', nargs='+', choices=('ru', 'en'), default=['ru', 'en'])
            sub.add_argument('--out', default=str(ROOT / '.benchmarks'))
            sub.add_argument('--revision')
            sub.add_argument('--onnx-dir')
            sub.add_argument('--project-initial-seed', action='store_true',
                             help='Seed without ONNX, then install native ONNX assets before querying, as on first CLI setup')
            sub.add_argument('--download', action='store_true')
            sub.add_argument('--threads', type=int)
            sub.add_argument('--repeats', type=int, default=3)
            sub.add_argument('--batch-size', type=int, default=32)
            sub.add_argument('--max-tokens', type=int, default=128)
            sub.add_argument('--top-k', type=int, default=7)
            sub.add_argument('--seed', type=int, default=42)
    profiles = commands.add_parser('profiles')
    profiles.add_argument('--profiles', default=str(DEFAULT_PROFILES))
    compare = commands.add_parser('compare')
    compare.add_argument('reports', nargs='+')
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == 'profiles':
            print(json.dumps(load_profiles(wsl_path(args.profiles)), ensure_ascii=False, indent=2))
        elif args.command == 'compare':
            reports = [read_json(wsl_path(p)) for p in args.reports]
            print_comparison(reports)
        else:
            inputs = validate_inputs(wsl_path(args.source), wsl_path(args.review), wsl_path(args.queries), wsl_path(args.profiles))
            if args.command == 'validate':
                print(json.dumps(inputs['summary'], ensure_ascii=False, indent=2))
            else:
                for name in ('repeats', 'batch_size', 'max_tokens', 'top_k'):
                    if getattr(args, name) < 1:
                        raise ValueError(f'{name} must be positive')
                if args.top_k < 7:
                    raise ValueError('top_k must be at least 7 for the reported metrics')
                if args.threads is not None and args.threads < 1:
                    raise ValueError('threads must be positive')
                if len(set(args.languages)) != len(args.languages):
                    raise ValueError('languages must be distinct')
                if args.seed < 0 or args.seed > 4294967295:
                    raise ValueError('seed must be between 0 and 4294967295')
                run_benchmark(args, inputs)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
        print(f'Benchmark error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
