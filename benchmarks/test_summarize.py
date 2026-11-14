import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.metrics import evaluate
from benchmarks.summarize import summarize_suite


CASES = [
    {'id': 'one', 'category': 'files', 'expected_commands': ['right1'], 'forbidden_commands': ['wrong']},
    {'id': 'two', 'category': 'files', 'expected_commands': ['right2']},
    {'id': 'outside', 'category': 'out_of_scope', 'expected_commands': []},
]


def _report(profile, backend, corpus, precision, rankings):
    languages = {}
    for language in ('ru', 'en'):
        languages[language] = {
            'metrics': evaluate(CASES, rankings),
            'latency_ms': {'uncached': {'p50': 4.0, 'p95': 9.0}, 'cache_hit': {'p50': 0.2}},
            'session_cache_mismatches': [], 'repeat_ranking_mismatches': [],
        }
    cold = {'full_process_wall_ms': 99, 'peak_rss_bytes': 99 * 1024**2}
    cold_samples = [{'full_process_wall_ms': wall, 'peak_rss_bytes': mib * 1024**2}
                    for wall, mib in ((10, 10), (20, 50), (100, 30))]
    cached_samples = [{'full_process_wall_ms': wall, 'peak_rss_bytes': 5 * 1024**2} for wall in (9, 3, 6)]
    return {
        'dataset_sha256': 'shared-dataset', 'source_sha256': {'dict': 'shared-source'},
        'pipeline_sha256': {'search.py': 'shared-code'}, 'benchmark_sha256': {'worker.py': 'shared-code'},
        'request': {'profile_name': profile, 'backend': backend, 'corpus': corpus, 'cases': CASES},
        'warm': {'languages': languages, 'peak_rss_bytes': 40 * 1024**2,
                 'model': {'resolved_revision': 'a' * 40, 'weight_precision': precision, 'onnx_bytes': 123}},
        'prepare': {'index_build_ms': 125, 'peak_rss_bytes': 75 * 1024**2, 'db_bytes': 1000, 'vector_bytes': 32},
        'cold': {'ru': cold, 'en': cold}, 'cached_cold': {'ru': cold, 'en': cold},
        'cold_repeats': {'ru': cold_samples, 'en': cold_samples},
        'cached_cold_repeats': {'ru': cached_samples, 'en': cached_samples},
    }


class SummarizeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.reports = {
            'baseline': _report('original', 'project', 'original', 'native-onnx',
                                {'one': ['wrong', 'right1'], 'two': ['miss'], 'outside': ['wrong']}),
            'fp32': _report('tiny', 'onnx', 'ru2', 'fp32',
                            {'one': ['right1'], 'two': ['right2'], 'outside': ['wrong']}),
            'int8': _report('tiny', 'onnx', 'ru2', 'int8',
                            {'one': ['wrong'], 'two': ['right2'], 'outside': ['wrong']}),
        }
        self.manifest = {'status': 'ready', 'settings': {'primary_baseline_id': 'baseline'}, 'runs': []}
        for run_id, report in self.reports.items():
            request = report['request']
            path = self.root / (run_id + '.json')
            path.write_text(json.dumps(report))
            self.manifest['runs'].append({
                'id': run_id, 'profile': request['profile_name'], 'backend': request['backend'],
                'precision': report['warm']['model']['weight_precision'], 'corpus': request['corpus'],
                'role': 'baseline' if run_id == 'baseline' else 'controlled',
                'report_path': str(path), 'status': 'ready',
            })
        self.manifest_path = self.root / 'manifest.json'

    def _run(self):
        self.manifest_path.write_text(json.dumps(self.manifest))
        return summarize_suite(self.manifest_path, self.root / 'output')

    def test_metrics_denominators_medians_memory_and_quantization_pairing(self):
        result = json.loads(self._run().read_text())
        self.assertEqual(result['run_count'], 3)
        rows = {(row['run_id'], row['language']): row for row in result['rows']}
        row = rows['fp32', 'ru']
        self.assertEqual(row['case_count'], 2)
        self.assertEqual(row['out_of_scope_count'], 1)
        self.assertEqual(row['forbidden_case_count'], 1)
        self.assertEqual(row['hit@3'], 1.0)
        self.assertEqual(row['cold_wall_ms'], 20)
        self.assertEqual(row['cached_cold_wall_ms'], 6)
        self.assertEqual(row['inference_peak_mib'], 50)
        self.assertEqual(row['prepare_peak_mib'], 75)
        self.assertEqual(row['primary_delta_hit3_pp'], 50)
        quant = next(comparison for comparison in result['quantization_comparisons'] if comparison['language'] == 'ru')
        self.assertEqual(quant['reference_run_id'], 'fp32')
        self.assertEqual(quant['quality_delta']['hit@3'], -0.5)
        self.assertEqual(quant['lost_hit3_case_ids'], ['one'])
        self.assertEqual(quant['gained_hit3_case_ids'], [])
        self.assertEqual(quant['changed_top1_case_ids'], ['one'])

    def test_csv_contains_all_languages_and_html_is_standalone(self):
        self._run()
        with (self.root / 'output/summary.csv').open(newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 6)
        self.assertEqual({row['language'] for row in rows}, {'ru', 'en'})
        page = (self.root / 'output/report.html').read_text()
        self.assertIn('<svg ', page)
        self.assertIn('2 задач', page)
        self.assertNotIn('https://', page)
        self.assertNotIn('<script', page)

    def test_different_datasets_and_sources_are_rejected(self):
        for field in ('dataset_sha256', 'source_sha256', 'pipeline_sha256', 'benchmark_sha256'):
            with self.subTest(field=field):
                report = copy.deepcopy(self.reports['int8'])
                report[field] = 'different'
                (self.root / 'int8.json').write_text(json.dumps(report))
                with self.assertRaises(ValueError):
                    self._run()
                (self.root / 'int8.json').write_text(json.dumps(self.reports['int8']))

    def test_run_manifest_mismatch_is_rejected(self):
        self.manifest['runs'][1]['corpus'] = 'original'
        with self.assertRaisesRegex(ValueError, 'does not match report'):
            self._run()

    def test_unfinished_suite_is_rejected(self):
        self.manifest['status'] = 'running'
        with self.assertRaisesRegex(ValueError, 'Wait for the suite'):
            self._run()

    def test_primary_baseline_must_exist(self):
        self.manifest['settings']['primary_baseline_id'] = 'absent'
        with self.assertRaisesRegex(ValueError, 'primary baseline'):
            self._run()

    def test_report_text_is_escaped(self):
        self.manifest['runs'][1]['id'] = '<script>bad</script>'
        self._run()
        page = (self.root / 'output/report.html').read_text()
        self.assertIn('&lt;script&gt;bad&lt;/script&gt;', page)
        self.assertNotIn('<script>', page)

    def test_partial_suite_preserves_failures(self):
        self.manifest['status'] = 'partial'
        self.manifest['runs'].append({'id': 'failed', 'status': 'failed', 'error': 'CPU limit'})
        summary = json.loads(self._run().read_text())
        self.assertEqual(summary['run_count'], 3)
        self.assertEqual(summary['failed_runs'][0]['error'], 'CPU limit')

    def test_different_variant_revisions_are_rejected(self):
        report = copy.deepcopy(self.reports['int8'])
        report['warm']['model']['resolved_revision'] = 'b' * 40
        (self.root / 'int8.json').write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'different resolved revisions'):
            self._run()


if __name__ == '__main__':
    unittest.main()
