"""Input integrity and corpus-comparison checks without model downloads."""

import copy
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from benchmarks import runner


class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.sources = {'test': {
            'category': 'test',
            'commands': [{'name': 'tool', 'description': 'Example', 'synopsis': 'tool', 'patterns': [
                {'type': 'example', 'text': 'read contents', 'command': 'tool --read', 'destructive': False},
                {'type': 'example', 'text': 'remove contents', 'command': 'tool --delete', 'destructive': True},
            ]}],
        }}
        self.aliases = {
            ('test', 0, 0): {'patterns_ru': ['прочитать содержимое', 'показать данные']},
            ('test', 0, 1): {'patterns_ru': ['удалить содержимое', 'стереть данные']},
        }

    def test_ablation_changes_only_descriptions_and_pattern_count(self):
        original = copy.deepcopy(self.sources)
        expected = {'original': 2, 'ru1': 2, 'ru2': 4, 'mixed': 6}
        for mode, count in expected.items():
            with self.subTest(mode=mode):
                output = runner.build_corpus(self.sources, self.aliases, mode)
                patterns = output['test']['commands'][0]['patterns']
                self.assertEqual(len(patterns), count)
                for pattern in patterns:
                    source = next(p for p in original['test']['commands'][0]['patterns'] if p['command'] == pattern['command'])
                    self.assertEqual({k: v for k, v in pattern.items() if k != 'text'},
                                     {k: v for k, v in source.items() if k != 'text'})
        self.assertEqual(self.sources, original)

    def test_original_corpus_remains_byte_equivalent_as_json(self):
        self.assertEqual(runner.build_corpus(self.sources, self.aliases, 'original'), self.sources)

    def test_ru1_is_subset_of_ru2_for_each_scenario(self):
        one = runner.build_corpus(self.sources, self.aliases, 'ru1')['test']['commands'][0]['patterns']
        two = runner.build_corpus(self.sources, self.aliases, 'ru2')['test']['commands'][0]['patterns']
        self.assertEqual(one, two[::2])


class IntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = runner.validate_inputs(runner.DEFAULT_SOURCE, runner.DEFAULT_REVIEW,
                                            runner.DEFAULT_QUERIES, runner.DEFAULT_PROFILES)

    def test_real_dataset_is_balanced_and_held_out(self):
        summary = self.inputs['summary']
        self.assertEqual(summary['in_scope_cases'], 90)
        self.assertEqual(summary['out_of_scope_cases'], 6)
        self.assertEqual(summary['exact_query_leakage'], 0)
        self.assertEqual(len(self.inputs['cases']), 96)
        self.assertEqual(set(summary['categories'].values()), {6})

    def test_full_source_is_represented_in_russian_corpora(self):
        for mode, count in (('original', 1946), ('ru1', 1946), ('ru2', 3892), ('mixed', 5838)):
            with self.subTest(mode=mode):
                corpus = runner.build_corpus(self.inputs['sources'], self.inputs['aliases'], mode)
                self.assertEqual(sum(len(c['patterns']) for data in corpus.values() for c in data['commands']), count)
                self.assertEqual({p['command'] for data in corpus.values() for c in data['commands'] for p in c['patterns']},
                                 {p['command'] for p in self.inputs['scenarios'].values()})

    def test_query_copied_from_index_is_rejected(self):
        data = runner.read_json(runner.DEFAULT_QUERIES)
        data['cases'][0]['query_ru'] = next(iter(self.inputs['aliases'].values()))['patterns_ru'][0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queries.json'
            runner.write_json(path, data)
            with self.assertRaisesRegex(ValueError, 'copies an indexed pattern'):
                runner.validate_inputs(runner.DEFAULT_SOURCE, runner.DEFAULT_REVIEW, path, runner.DEFAULT_PROFILES)

    def test_stale_translation_manifest_is_rejected(self):
        review = runner.read_json(runner.DEFAULT_REVIEW)
        name = next(iter(review['source_sha256']))
        review['source_sha256'][name] = '0' * 64
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'review.json'
            runner.write_json(path, review)
            with self.assertRaisesRegex(ValueError, 'stale'):
                runner.load_review(path, self.inputs['scenarios'], runner.DEFAULT_SOURCE)

    def test_a_missing_review_scenario_is_rejected(self):
        review = runner.read_json(runner.DEFAULT_REVIEW)
        review['scenarios'].pop()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'review.json'
            runner.write_json(path, review)
            with self.assertRaisesRegex(ValueError, 'cover every'):
                runner.load_review(path, self.inputs['scenarios'], runner.DEFAULT_SOURCE)

    def test_profile_prefixes_preserve_model_protocol(self):
        profiles = self.inputs['profiles']
        self.assertEqual(profiles['e5-small']['query_prefix'], profiles['e5-small']['document_prefix'])
        self.assertEqual(profiles['user2-small']['query_prefix'], 'search_query: ')
        self.assertEqual(profiles['user2-small']['document_prefix'], 'search_document: ')

    def test_native_environment_is_isolated_from_existing_cf_configuration(self):
        job = {
            'run_dir': '/tmp/cf-run', 'hf_home': '/tmp/cf-models',
            'profile': {'model': 'all-MiniLM-L6-v2', 'dimensions': 384},
            'threads': 2, 'download': False, 'seed': 42,
        }
        with patch.dict('os.environ', {'CF_DB_DIR': '/tmp/production', 'CF_DB_NAME': 'production.db',
                                      'CF_MODEL': 'other-model', 'HF_HOME': '/tmp/other-models'}):
            environment = runner.child_environment(job)
        self.assertEqual(environment['CF_DB_DIR'], '/tmp/cf-run')
        self.assertEqual(environment['CF_DB_NAME'], 'cf.db')
        self.assertEqual(environment['CF_MODEL'], 'all-MiniLM-L6-v2')
        self.assertEqual(environment['HF_HOME'], '/tmp/cf-models')
        self.assertEqual(environment['HF_HUB_OFFLINE'], '1')

    def test_wsl_paths_reject_mounted_host_before_access(self):
        with patch.object(Path, 'is_symlink', side_effect=AssertionError('must reject before filesystem access')):
            for value in ('/mnt/z/forbidden', 'Z:\\forbidden', '/tmp/../mnt/z/forbidden'):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'Linux path'):
                    runner.wsl_path(value)

    def test_nested_cache_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'cache').symlink_to(base / 'other-cache', target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'symbolic link'):
                runner.wsl_path(base / 'cache/huggingface')

    def test_comparison_rejects_different_evaluation_sets(self):
        with self.assertRaisesRegex(ValueError, 'different query datasets'):
            runner.print_comparison([{'dataset_sha256': 'a'}, {'dataset_sha256': 'b'}])

    def test_validation_cli_needs_no_runtime_dependencies(self):
        output = io.StringIO()
        with patch('importlib.util.find_spec', side_effect=AssertionError('no runtime import needed')):
            with redirect_stdout(output):
                self.assertEqual(runner.main(['validate']), 0)
        self.assertEqual(json.loads(output.getvalue())['case_count'], 96)

    def test_comparison_distinguishes_actual_onnx_and_torch_backends(self):
        from benchmarks.metrics import evaluate

        metrics = evaluate([{'id': 'one', 'category': 'test', 'expected_commands': ['ok']}], {'one': ['ok']})
        report = {
            'dataset_sha256': 'same', 'pipeline_sha256': {}, 'source_sha256': {},
            'benchmark_sha256': {}, 'machine': {'cpu_count': 2},
            'request': {'profile_name': 'original', 'backend': 'project', 'corpus': 'original',
                        'threads': 4, 'top_k': 7, 'max_tokens': 128},
            'cold': {'ru': {'full_process_wall_ms': 10, 'peak_rss_bytes': 1024}},
            'cached_cold': {'ru': {'full_process_wall_ms': 2}},
            'warm': {
                'peak_rss_bytes': 1024, 'library_versions': {},
                'model': {'encoder_backend': 'onnx', 'effective_max_tokens': 128, 'effective_threads': 4},
                'languages': {'ru': {'metrics': metrics, 'latency_ms': {
                    'uncached': {'p50': 1, 'p95': 2}, 'cache_hit': {'p50': 0.5},
                }}},
            },
        }
        other = copy.deepcopy(report)
        other['warm']['model']['encoder_backend'] = 'pytorch'
        output = io.StringIO()
        with redirect_stdout(output):
            runner.print_comparison([report, other])
        self.assertIn('Measurement conditions differ', output.getvalue())
        self.assertIn('cold cache ms', output.getvalue())


class OnnxBackendTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cf-onnx-backend-tests-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.artifact = self.root / 'artifact'
        self.artifact.mkdir()
        self.profile = {'model': 'org/fixture', 'revision': 'a' * 40, 'dimensions': 3,
                        'query_prefix': 'q ', 'document_prefix': 'd '}
        self.metadata = {**self.profile, 'max_tokens': 128, 'precision': 'int8',
                         'pooling_in_graph': True, 'normalization': 'l2',
                         'quantization': {'method': 'dynamic', 'weight_type': 'QInt8'}}
        self.save_metadata()
        (self.artifact / 'model.onnx').write_bytes(b'fixture graph')
        (self.artifact / 'tokenizer.json').write_text('{}')

    def save_metadata(self):
        runner.write_json(self.artifact / 'metadata.json', self.metadata)

    def arguments(self, *extra):
        return runner.parser().parse_args(['run', '--profile', 'fixture', '--backend', 'onnx',
                                          '--onnx-dir', str(self.artifact), '--out', str(self.root / 'out'), *extra])

    def test_onnx_dependencies_do_not_require_training_libraries(self):
        requested = []
        with patch.object(importlib.util, 'find_spec', side_effect=lambda name: requested.append(name) or object()):
            runner._runtime_dependencies('onnx', True)
        self.assertEqual(requested, ['numpy', 'sqlite_vec', 'onnxruntime', 'tokenizers'])

    def test_artifact_metadata_must_match_profile_and_limits(self):
        result = runner.onnx_artifact_metadata(self.artifact, self.profile, 128)
        self.assertEqual(result['precision'], 'int8')
        for field, wrong in (('revision', 'b' * 40), ('model', 'other'), ('dimensions', 384),
                             ('query_prefix', ''), ('document_prefix', ''), ('max_tokens', 64),
                             ('precision', 'fp16'), ('pooling_in_graph', False), ('normalization', 'none')):
            with self.subTest(field=field):
                saved = self.metadata[field]
                self.metadata[field] = wrong
                self.save_metadata()
                with self.assertRaises(ValueError):
                    runner.onnx_artifact_metadata(self.artifact, self.profile, 128)
                self.metadata[field] = saved
        self.save_metadata()
        with self.assertRaisesRegex(ValueError, 'pinned'):
            runner.onnx_artifact_metadata(self.artifact, {**self.profile, 'revision': 'main'}, 128)

    def test_onnx_controller_copies_and_hashes_artifacts_with_two_threads(self):
        (self.artifact / 'weights.data').write_bytes(b'external weights')
        inputs = {
            'profiles': {'fixture': self.profile}, 'cases': [], 'source_hashes': {},
            'sources': {'sample': {'category': 'sample', 'commands': [{
                'name': 'tool', 'patterns': [{'text': 'alpha', 'command': 'tool'}],
            }]}}, 'aliases': {},
        }
        jobs = []

        def child(job, phase, language=None):
            jobs.append(copy.deepcopy(job))
            return {'phase': phase}

        with patch.object(runner, '_runtime_dependencies'), patch.object(runner, 'run_child', side_effect=child), \
                patch.object(runner, 'print_comparison'), redirect_stdout(io.StringIO()):
            path = runner.run_benchmark(self.arguments('--download'), inputs)
        report = runner.read_json(path)
        job = report['request']
        self.assertEqual(job['backend'], 'onnx')
        self.assertEqual(job['threads'], 2)
        self.assertFalse(job['download'])
        self.assertTrue(jobs)
        self.assertEqual(set(report['onnx_assets_sha256']), {'metadata.json', 'model.onnx', 'tokenizer.json', 'weights.data'})
        self.assertEqual((path.parent / 'onnx/weights.data').read_bytes(), b'external weights')

    def test_native_project_loader_rejects_complete_sentence_graph(self):
        args = self.arguments('--profile', 'original', '--backend', 'project')
        original = {'model': 'all-MiniLM-L6-v2', 'revision': 'main', 'dimensions': 384,
                    'query_prefix': '', 'document_prefix': ''}
        with patch.object(runner, '_runtime_dependencies'), self.assertRaisesRegex(ValueError, 'Use --backend onnx'):
            runner.run_benchmark(args, {'profiles': {'original': original}})

    def test_native_initial_setup_installs_onnx_only_after_seeding(self):
        (self.artifact / 'metadata.json').unlink()
        original = {'model': 'all-MiniLM-L6-v2', 'revision': 'main', 'dimensions': 384,
                    'query_prefix': '', 'document_prefix': ''}
        inputs = {'profiles': {'original': original}, 'cases': [], 'source_hashes': {},
                  'sources': {'sample': {'category': 'sample', 'commands': [{
                      'name': 'tool', 'patterns': [{'text': 'alpha', 'command': 'tool'}],
                  }]}}, 'aliases': {}}
        phases = []

        def child(job, phase, language=None):
            available = (Path(job['run_dir']) / 'onnx/model.onnx').is_file()
            self.assertEqual(available, phase != 'prepare')
            self.assertTrue(job['project_initial_seed'])
            phases.append(phase)
            return {'phase': phase}

        args = self.arguments('--profile', 'original', '--backend', 'project', '--project-initial-seed')
        with patch.object(runner, '_runtime_dependencies'), patch.object(runner, 'run_child', side_effect=child), \
                patch.object(runner, 'print_comparison'), redirect_stdout(io.StringIO()):
            path = runner.run_benchmark(args, inputs)
        report = runner.read_json(path)
        self.assertEqual(report['request']['threads'], 4)
        self.assertEqual(phases[0], 'prepare')
        self.assertIn('warm', phases)

    def test_onnx_backend_requires_an_artifact_directory(self):
        args = self.arguments()
        args.onnx_dir = None
        with self.assertRaisesRegex(ValueError, 'requires --onnx-dir'):
            runner.run_benchmark(args, {'profiles': {'fixture': self.profile}})


@unittest.skipUnless(all(importlib.util.find_spec(name) is not None for name in ('numpy', 'onnx', 'onnxruntime', 'tokenizers', 'sqlite_vec')),
                     'ONNX integration test dependencies are not installed')
class OnnxWorkerIntegrationTests(unittest.TestCase):
    def test_tiny_real_encoder_seeds_and_queries_without_training_libraries(self):
        import onnx
        from onnx import helper
        from benchmarks import test_onnx_encoder

        fixture = test_onnx_encoder.OnnxEncoderTests('test_query_and_document_prefixes_change_tokens')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root = fixture.directory
        run_dir = root / 'run'
        artifact = run_dir / 'onnx'
        artifact.mkdir(parents=True)
        for name in ('model.onnx', 'tokenizer.json'):
            shutil.copyfile(root / name, artifact / name)
        metadata = {**fixture.metadata, 'model': 'fixture', 'revision': 'a' * 40,
                    'precision': 'int8', 'pooling_in_graph': True, 'normalization': 'l2',
                    'quantization': {'method': 'dynamic'}}
        runner.write_json(artifact / 'metadata.json', metadata)
        model = onnx.load(artifact / 'model.onnx')
        model.graph.node[-1].output[0] = 'features'
        model.graph.node.extend([
            helper.make_node('ReduceL2', ['features'], ['norm'], axes=[1], keepdims=1),
            helper.make_node('Div', ['features', 'norm'], ['sentence_embedding']),
        ])
        onnx.save(model, artifact / 'model.onnx')
        seed = run_dir / 'seed'
        seed.mkdir()
        runner.write_json(seed / 'sample.json', {'category': 'sample', 'commands': [{
            'name': 'tool', 'description': 'fixture', 'synopsis': 'tool', 'patterns': [
                {'type': 'example', 'text': 'alpha', 'command': 'tool --alpha'},
                {'type': 'example', 'text': 'beta gamma', 'command': 'tool --beta'},
            ],
        }]})
        job = {
            'profile_name': 'fixture',
            'profile': {key: metadata[key] for key in ('model', 'revision', 'dimensions', 'query_prefix', 'document_prefix')},
            'backend': 'onnx', 'corpus': 'fixture', 'run_dir': str(run_dir), 'hf_home': str(root / 'cache'),
            'threads': 1, 'batch_size': 1, 'max_tokens': 6, 'top_k': 7, 'download': False,
            'cases': [{'id': 'one', 'category': 'sample', 'query_en': 'alpha', 'query_ru': 'alpha',
                       'expected_commands': ['tool --alpha']}],
        }
        runner.write_json(run_dir / 'job.json', job)
        program = (
            "import json,sys; from benchmarks.worker import run_phase; "
            "job=json.load(open(sys.argv[1])); "
            "p=run_phase(job,'prepare'); c=run_phase(job,'cold','en'); "
            "k=run_phase(job,'cached-cold','en'); "
            "assert p['stats']=={'commands':1,'patterns':2}; "
            "assert p['model']['dtype']=='float32' and p['model']['weight_precision']=='int8'; "
            "assert p['model']['artifact_metadata']['quantization']['method']=='dynamic'; "
            "assert p['model']['resolved_revision']=='a'*40; "
            "assert c['ranking'] and c['ranking']==k['ranking']; "
            "assert not k['model']['model_loaded']; "
            "assert not {'torch','transformers','sentence_transformers'} & sys.modules.keys()"
        )
        result = subprocess.run([sys.executable, '-c', program, str(run_dir / 'job.json')],
                                cwd=runner.ROOT, env={**os.environ, 'PATH': '/usr/bin:/bin'},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
