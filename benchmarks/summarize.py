"""Build a standalone comparison from completed benchmark reports without loading models."""

import argparse
import csv
from datetime import datetime, timezone
import html
import json
from pathlib import Path
from statistics import median

from benchmarks import runner


MIB = 1024 ** 2
READY = {'ready', 'complete', 'completed'}
QUALITY_KEYS = ('hit@1', 'hit@3', 'hit@7', 'mrr@7', 'forbidden_hit@3')
CSV_COLUMNS = (
    'run_id', 'role', 'profile', 'backend', 'precision', 'corpus', 'language',
    'model_revision', 'threads', 'max_tokens', 'batch_size',
    'case_count', 'out_of_scope_count', 'forbidden_case_count',
    *QUALITY_KEYS,
    'uncached_p50_ms', 'uncached_p95_ms', 'cached_p50_ms', 'cold_wall_ms',
    'cached_cold_wall_ms', 'inference_peak_mib', 'warm_peak_mib', 'cold_peak_mib',
    'prepare_peak_mib', 'cached_cold_peak_mib', 'index_build_ms', 'model_bytes',
    'database_bytes', 'vector_bytes', 'repeat_ranking_mismatch_count',
    'session_cache_mismatch_count', 'cold_sample_count', 'cached_cold_sample_count',
    'primary_delta_hit3_pp', 'report_path',
)


def _optional_mib(phase: dict) -> float | None:
    value = phase.get('peak_rss_bytes')
    return value / MIB if value is not None else None


def _samples(report: dict, phase: str, language: str) -> list[dict]:
    samples = report.get(phase + '_repeats', {}).get(language)
    return samples if samples else [report.get(phase, {}).get(language, {})]


def _median_wall(samples: list[dict]) -> float | None:
    values = [sample['full_process_wall_ms'] for sample in samples if sample.get('full_process_wall_ms') is not None]
    return median(values) if values else None


def _max_mib(samples: list[dict]) -> float | None:
    return max((value for value in map(_optional_mib, samples) if value is not None), default=None)


def _row(entry: dict, report: dict, language: str, path: Path) -> dict:
    warm = report['warm']
    result = warm['languages'][language]
    metrics = result['metrics']
    model = warm.get('model', {})
    cold = _samples(report, 'cold', language)
    cached = _samples(report, 'cached_cold', language)
    preparation = report['prepare']
    peaks = [_optional_mib(warm), _max_mib(cold)]
    return {
        'run_id': entry['id'], 'role': entry['role'], 'profile': entry['profile'],
        'backend': entry['backend'], 'precision': entry['precision'],
        'corpus': entry['corpus'], 'language': language,
        'model_revision': model.get('resolved_revision'),
        'threads': model.get('effective_threads', report['request'].get('threads')),
        'max_tokens': model.get('effective_max_tokens', report['request'].get('max_tokens')),
        'batch_size': model.get('effective_batch_size', report['request'].get('batch_size')),
        'case_count': metrics['overall']['case_count'],
        'out_of_scope_count': metrics['out_of_scope']['case_count'],
        'forbidden_case_count': metrics['overall']['forbidden_case_count'],
        **{key: metrics['overall'][key] for key in QUALITY_KEYS},
        'uncached_p50_ms': result['latency_ms']['uncached']['p50'],
        'uncached_p95_ms': result['latency_ms']['uncached']['p95'],
        'cached_p50_ms': result['latency_ms']['cache_hit']['p50'],
        'cold_wall_ms': _median_wall(cold),
        'cached_cold_wall_ms': _median_wall(cached),
        'cold_sample_count': sum(bool(sample) for sample in cold),
        'cached_cold_sample_count': sum(bool(sample) for sample in cached),
        'inference_peak_mib': max((value for value in peaks if value is not None), default=None),
        'warm_peak_mib': _optional_mib(warm), 'cold_peak_mib': _max_mib(cold),
        'prepare_peak_mib': _optional_mib(preparation),
        'cached_cold_peak_mib': _max_mib(cached),
        'index_build_ms': preparation['index_build_ms'],
        'model_bytes': model.get('onnx_bytes'),
        'database_bytes': preparation.get('db_bytes'), 'vector_bytes': preparation.get('vector_bytes'),
        'repeat_ranking_mismatch_count': len(result.get('repeat_ranking_mismatches', [])),
        'session_cache_mismatch_count': len(result.get('session_cache_mismatches', [])),
        'by_category': metrics.get('by_category', {}),
        'by_tag': metrics.get('by_tag', {}),
        'macro_by_category': metrics.get('macro_by_category', {}),
        'session_cache_mismatches': result.get('session_cache_mismatches', []),
        'report_path': str(path),
    }


def _paired(candidate: dict, reference: dict, language: str) -> dict:
    left = candidate['warm']['languages'][language]['metrics']['per_case']
    right = reference['warm']['languages'][language]['metrics']['per_case']
    if set(left) != set(right):
        raise ValueError('Reports have different case identifiers')
    gained, lost, changed_top1, changed_ranking = [], [], [], []
    for case_id, row in left.items():
        baseline = right[case_id]
        if row['out_of_scope'] != baseline['out_of_scope']:
            raise ValueError('Reports disagree on out-of-scope case labels')
        if row['ranking'] != baseline['ranking']:
            changed_ranking.append(case_id)
        if row['ranking'][:1] != baseline['ranking'][:1]:
            changed_top1.append(case_id)
        if not row['out_of_scope']:
            if row['hit@3'] > baseline['hit@3']:
                gained.append(case_id)
            elif row['hit@3'] < baseline['hit@3']:
                lost.append(case_id)
    return {
        'gained_hit3_case_ids': gained, 'lost_hit3_case_ids': lost,
        'changed_top1_case_ids': changed_top1,
        'changed_ranking_case_ids': changed_ranking,
    }


def _comparison(candidate: dict, baseline: dict, candidate_report: dict, baseline_report: dict) -> dict:
    result = {
        'run_id': candidate['run_id'], 'reference_run_id': baseline['run_id'],
        'language': candidate['language'],
        'quality_delta': {
            key: candidate[key] - baseline[key] if candidate[key] is not None and baseline[key] is not None else None
            for key in QUALITY_KEYS
        },
        'uncached_speedup': baseline['uncached_p50_ms'] / candidate['uncached_p50_ms']
        if candidate['uncached_p50_ms'] and baseline['uncached_p50_ms'] is not None else None,
        'cold_speedup': baseline['cold_wall_ms'] / candidate['cold_wall_ms']
        if candidate['cold_wall_ms'] and baseline['cold_wall_ms'] is not None else None,
        'inference_memory_ratio': candidate['inference_peak_mib'] / baseline['inference_peak_mib']
        if baseline['inference_peak_mib'] and candidate['inference_peak_mib'] is not None else None,
    }
    result.update(_paired(candidate_report, baseline_report, candidate['language']))
    return result


def _number(value, *, percentage=False) -> str:
    if value is None:
        return '—'
    if isinstance(value, int) and not percentage:
        return str(value)
    return f'{value * 100:.1f}%' if percentage else f'{value:.2f}'


def _table(rows: list[dict], columns: list[tuple[str, str, bool]]) -> str:
    head = ''.join(f'<th>{html.escape(label)}</th>' for _, label, _ in columns)
    body = []
    for row in rows:
        cells = []
        for key, _, percentage in columns:
            value = row.get(key)
            text = _number(value, percentage=percentage) if isinstance(value, (int, float)) or value is None else str(value)
            cells.append(f'<td>{html.escape(text)}</td>')
        body.append('<tr>' + ''.join(cells) + '</tr>')
    return '<div class="scroll"><table><thead><tr>' + head + '</tr></thead><tbody>' + ''.join(body) + '</tbody></table></div>'


def _scatter(rows: list[dict], language: str, metric: str, axis: str) -> str:
    points = [row for row in rows if row['language'] == language and row[metric] is not None and row['hit@3'] is not None]
    if not points:
        return ''
    maximum = max(row[metric] for row in points) * 1.1 or 1
    shapes = []
    for row in points:
        x = 56 + row[metric] / maximum * 700
        y = 275 - row['hit@3'] * 230
        color = '#e07a18' if row['role'] == 'baseline' else '#147eae' if row['precision'] == 'int8' else '#77828c'
        label = f"{row['run_id']}: Hit@3 {row['hit@3'] * 100:.1f}%, {axis} {row[metric]:.2f}"
        shapes.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="5" fill="{color}"><title>{html.escape(label)}</title></circle>')
    return f'''<svg viewBox="0 0 820 330" role="img" aria-label="Качество поиска и пиковая память">
<path d="M56 35V275H785" fill="none" stroke="#9ca3af"/>
<text x="8" y="45">100%</text><text x="28" y="280">0%</text>
<text x="54" y="303">0</text><text x="710" y="303">{maximum:.2f}</text>
<text x="240" y="325">{html.escape(axis)} →</text>
{''.join(shapes)}</svg>'''


def _html(summary: dict) -> str:
    rows = summary['rows']
    columns = [('run_id', 'Конфигурация', False), ('corpus', 'Словарь', False),
               ('hit@1', 'Hit@1', True), ('hit@3', 'Hit@3', True), ('hit@7', 'Hit@7', True),
               ('mrr@7', 'MRR@7', False), ('forbidden_hit@3', 'Запрет@3 ↓', True),
               ('primary_delta_hit3_pp', 'Δ Hit@3, п.п.', False),
               ('uncached_p50_ms', 'Поиск p50, мс', False), ('uncached_p95_ms', 'p95, мс', False),
               ('cold_wall_ms', 'Новый процесс, мс', False), ('inference_peak_mib', 'Память поиска, МиБ', False)]
    sections = []
    for language, title in (('ru', 'Русские запросы'), ('en', 'Английские запросы')):
        subset = [row for row in rows if row['language'] == language]
        if not subset:
            continue
        sections.append(f'<h2>{title}</h2>' + _table(subset, columns))
        sections.append('<p class="muted">Оранжевые точки — исходный проект, синие — 8-битные варианты; наведите курсор для подробностей. Лучше — выше и левее.</p>' + _scatter(rows, language, 'uncached_p50_ms', 'Поиск без кеша p50, мс') + _scatter(rows, language, 'inference_peak_mib', 'Пиковая память процесса поиска, МиБ'))
    resources = [('run_id', 'Конфигурация', False), ('corpus', 'Словарь', False),
                 ('cached_p50_ms', 'Повторный запрос, мс', False),
                 ('cached_cold_wall_ms', 'Новый процесс с кешем, мс', False),
                 ('cached_cold_peak_mib', 'Память с кешем, МиБ', False),
                 ('prepare_peak_mib', 'Память индексации, МиБ', False),
                 ('index_build_ms', 'Индексация, мс', False), ('model_mib', 'Граф, МиБ', False)]
    first_language = {}
    for row in rows:
        if row['run_id'] not in first_language or row['language'] == 'ru':
            first_language[row['run_id']] = {**row, 'model_mib': row['model_bytes'] / MIB if row['model_bytes'] is not None else None}
    sections.append('<h2>Кеш и подготовка</h2><p>В этой таблице кеш показан для русского языка, если он присутствует в прогоне. Полные разрезы сохранены в CSV и JSON.</p>' + _table(list(first_language.values()), resources))
    sections.append('<h2>Фактические настройки конфигураций</h2><p>Исходный backend сохраняет собственные настройки. Сравнение с ним показывает эффект замены всего пути поиска; для выделения эффекта модели следует сравнивать строки с одинаковыми backend, числом потоков и пределом длины.</p>' + _table(list(first_language.values()), [
        ('run_id', 'Конфигурация', False), ('backend', 'Backend', False),
        ('precision', 'Веса', False), ('threads', 'Потоки', False),
        ('max_tokens', 'Предел токенов', False), ('batch_size', 'Пакет индексации', False),
        ('model_revision', 'Ревизия модели', False)]))
    failures = summary['failed_runs']
    if failures:
        sections.append('<h2>Незавершённые запуски</h2><pre>' + html.escape(json.dumps(failures, ensure_ascii=False, indent=2)) + '</pre>')
    quant = summary['quantization_comparisons']
    if quant:
        quant_rows = []
        for comparison in quant:
            quant_rows.append({'run_id': comparison['run_id'], 'language': comparison['language'],
                               'delta_hit3_pp': comparison['quality_delta']['hit@3'] * 100,
                               'gained': len(comparison['gained_hit3_case_ids']), 'lost': len(comparison['lost_hit3_case_ids']),
                               'uncached_speedup': comparison['uncached_speedup'],
                               'inference_memory_ratio': comparison['inference_memory_ratio']})
        sections.append('<h2>Эффект квантования относительно ONNX FP32</h2>' + _table(quant_rows, [
            ('run_id', 'Конфигурация', False), ('language', 'Язык', False), ('delta_hit3_pp', 'Δ Hit@3, п.п.', False),
            ('gained', 'Новых попаданий', False), ('lost', 'Потерянных попаданий', False),
            ('uncached_speedup', 'Ускорение поиска, ×', False), ('inference_memory_ratio', 'Память / FP32', False)]))
    settings = html.escape(json.dumps(summary['settings'], ensure_ascii=False, indent=2))
    machines = html.escape(json.dumps(summary['machines'], ensure_ascii=False, indent=2))
    baseline_ids = ', '.join(summary['baseline_ids']) or 'не указаны'
    first = rows[0]
    primary = html.escape(summary.get('primary_baseline_id') or 'не указан')
    return f'''<!doctype html>
<html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Сравнение моделей поиска команд</title>
<style>body{{font:15px system-ui,sans-serif;color:#142330;background:#f7f9fb;max-width:1500px;margin:32px auto;padding:0 20px}}h1,h2{{line-height:1.2}}h2{{margin-top:34px}}p{{max-width:1000px;line-height:1.6}}.scroll{{overflow:auto;border:1px solid #d9e0e6;border-radius:8px;background:white}}table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;white-space:nowrap}}th,td{{padding:9px 12px;border-bottom:1px solid #e5e9ed;text-align:right}}th:first-child,td:first-child{{text-align:left}}thead{{background:#edf3f8}}tr:hover{{background:#f0f7fc}}.muted{{color:#546575}}svg{{width:min(100%,820px);background:white;border-radius:8px}}svg text{{font:12px system-ui;fill:#435461}}pre{{background:#eaf0f5;padding:16px;overflow:auto;border-radius:8px}}.notice{{padding:12px 16px;border-left:4px solid #147eae;background:#eaf4fb}}</style>
<h1>Сравнение моделей поиска команд</h1>
<p class="notice">Сравнение по {summary['run_count']} завершённым конфигурациям. Качество измеряется отдельно для русского и английского языка; выше Hit и MRR — лучше, ниже задержки и память — лучше.</p>
<p>Исходные конфигурации: {html.escape(baseline_ids)}. Основной контроль для дельт: <code>{primary}</code>. Исходный проект использует исправленный английский словарь. Контролируемые сравнения используют одинаковый русский словарь ru2, если в строке не указано другое. Изменение словаря и смена модели дают разные эффекты; это учитывается отдельными контрольными запусками.</p>
<p>Для каждого языка размечено {first['case_count']} задач в словаре и {first['out_of_scope_count']} запросов вне области словаря. Hit@K — доля задач, где допустимая команда попала в первые K результатов. Запрет@3 считается только по задачам с размеченными запрещёнными командами. Запросы вне области словаря вынесены из оценок качества; исходный поиск не умеет автоматически отказываться от ответа. Задержка поиска включает кодирование запроса, SQLite и устранение повторов команд; интерфейс выбора не измеряется.</p>
<p>«Новый процесс» включает загрузку модели и показан медианой повторов, но файловый кеш Linux не очищался. Кешированный запрос измеряется отдельно. Память — максимум пикового RSS повторов, а не размер весов; память индексации показана отдельно. Прогоны одной машины нельзя напрямую переносить на другое оборудование. Разница backend, числа потоков и длины текста отражена в условиях.</p>
{''.join(sections)}
<h2>Условия и воспроизводимость</h2><pre>{settings}</pre>
<details><summary>Оборудование и среда измерения</summary><pre>{machines}</pre></details>
<p>Источник: <code>{html.escape(summary['manifest_path'])}</code>. SHA набора запросов: <code>{html.escape(summary['dataset_sha256'])}</code>.</p>
<p><a href="summary.csv">Таблица CSV</a> · <a href="summary.json">Полные данные JSON</a></p>
</html>'''


def summarize_suite(manifest_path: str | Path, output_dir: str | Path) -> Path:
    manifest_path = runner.wsl_path(manifest_path)
    output_dir = runner.wsl_path(output_dir)
    manifest = runner.read_json(manifest_path)
    if manifest.get('status') not in READY | {'partial', 'failed'}:
        raise ValueError('Wait for the suite to finish before summarizing it')
    ready = [entry for entry in manifest['runs'] if entry.get('status') in READY]
    if not ready:
        raise ValueError('The suite has no completed reports')
    ids = [entry['id'] for entry in ready]
    if len(set(ids)) != len(ids):
        raise ValueError('Suite run IDs must be unique')
    reports, rows = {}, []
    for entry in ready:
        path = runner.wsl_path(entry['report_path'])
        report = runner.read_json(path)
        request = report['request']
        for entry_key, request_key in (('profile', 'profile_name'), ('backend', 'backend'), ('corpus', 'corpus')):
            if entry[entry_key] != request[request_key]:
                raise ValueError(f"Suite entry {entry['id']} does not match report {request_key}")
        precision = report['warm'].get('model', {}).get('weight_precision')
        if precision is not None and precision != entry['precision']:
            raise ValueError(f"Suite entry {entry['id']} does not match report weight precision")
        reports[entry['id']] = report
        for language in report['warm']['languages']:
            rows.append(_row(entry, report, language, path))
    hashes = {report['dataset_sha256'] for report in reports.values()}
    if len(hashes) != 1:
        raise ValueError('Reports use different query datasets; rerun on identical cases')
    case_sets = {tuple(sorted(report['request']['cases'][i]['id'] for i in range(len(report['request']['cases']))))
                 for report in reports.values()}
    if len(case_sets) != 1:
        raise ValueError('Reports contain different query case identifiers')
    for key in ('source_sha256', 'pipeline_sha256', 'benchmark_sha256'):
        signatures = {runner.digest(report.get(key)) for report in reports.values()}
        if len(signatures) != 1:
            raise ValueError(f'Reports differ in {key}; use a consistent benchmark and source dictionary')
    revisions = {}
    for row in rows:
        if row['model_revision']:
            revisions.setdefault(row['profile'], set()).add(row['model_revision'])
    if any(len(values) > 1 for values in revisions.values()):
        raise ValueError('Model variants use different resolved revisions')
    reference_rows = [row for row in rows if row['role'] == 'baseline']
    baseline_comparisons = []
    quantization_comparisons = []
    for row in rows:
        for reference in reference_rows:
            if row['run_id'] != reference['run_id'] and row['language'] == reference['language']:
                baseline_comparisons.append(_comparison(row, reference, reports[row['run_id']], reports[reference['run_id']]))
        if row['backend'] == 'onnx' and row['precision'] == 'int8':
            matches = [reference for reference in rows if reference['backend'] == 'onnx'
                       and reference['precision'] == 'fp32' and reference['profile'] == row['profile']
                       and reference['corpus'] == row['corpus'] and reference['language'] == row['language']]
            if len(matches) > 1:
                raise ValueError('Ambiguous FP32 reference for an INT8 run')
            if matches:
                reference = matches[0]
                quantization_comparisons.append(_comparison(row, reference, reports[row['run_id']], reports[reference['run_id']]))
    primary = manifest.get('settings', {}).get('primary_baseline_id')
    if primary is not None and primary not in {row['run_id'] for row in reference_rows}:
        raise ValueError('The selected primary baseline has no completed report')
    for row in rows:
        match = next((reference for reference in reference_rows if reference['run_id'] == primary
                      and reference['language'] == row['language']), None)
        row['primary_delta_hit3_pp'] = (row['hit@3'] - match['hit@3']) * 100 if match else None
    summary = {
        'schema_version': 1, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'manifest_path': str(manifest_path), 'manifest_sha256': runner.file_hash(manifest_path),
        'status': manifest['status'], 'run_count': len(ready),
        'baseline_ids': list(dict.fromkeys(row['run_id'] for row in reference_rows)),
        'primary_baseline_id': primary,
        'dataset_sha256': next(iter(hashes)), 'settings': manifest.get('settings', {}),
        'machines': list({runner.digest(report.get('machine')): report.get('machine')
                          for report in reports.values()}.values()),
        'rows': rows, 'baseline_comparisons': baseline_comparisons,
        'quantization_comparisons': quantization_comparisons,
        'failed_runs': [entry for entry in manifest['runs'] if entry.get('status') not in READY],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = runner.wsl_path(output_dir / 'summary.json')
    runner.write_json(summary_path, summary)
    with runner.wsl_path(output_dir / 'summary.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    runner.wsl_path(output_dir / 'report.html').write_text(_html(summary), encoding='utf-8')
    return summary_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        print(summarize_suite(args.manifest, args.out))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f'Summary error: {error}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
