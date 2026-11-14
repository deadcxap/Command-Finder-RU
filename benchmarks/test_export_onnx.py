"""Offline ONNX orchestration checks without model packages or downloads."""

import copy
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from benchmarks import export_onnx, runner


class ExportControllerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cf-onnx-controller-', dir='/tmp')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.out = self.root / 'artifacts'
        self.profile_path = self.root / 'profiles.json'
        profile = {'model': 'org/fixture', 'revision': 'a' * 40, 'dimensions': 3,
                   'query_prefix': '', 'document_prefix': ''}
        self.profiles = {'first': copy.deepcopy(profile), 'second': copy.deepcopy(profile)}
        self.profiles['second']['revision'] = 'b' * 40
        runner.write_json(self.profile_path, {'schema_version': 1, 'profiles': self.profiles})
        self.hashes = {'export_onnx.py': 'controller', 'onnx_export_worker.py': 'worker', 'onnx_encoder.py': 'runtime'}
        self.calls = []

    def arguments(self, *extra):
        return export_onnx.parser().parse_args([
            '--out', str(self.out), '--profiles', str(self.profile_path), *extra,
        ])

    def child(self, job):
        self.calls.append(copy.deepcopy(job))
        directory = Path(job['run_dir'])
        self.assertEqual(runner.read_json(directory / 'job.json'), job)
        self.assertTrue((directory / 'fp32').is_dir())
        self.assertTrue((directory / 'int8').is_dir())
        self.assertEqual(job['profile'], self.profiles[job['profile_name']])
        for precision in ('fp32', 'int8'):
            for name in ('model.onnx', 'tokenizer.json', 'metadata.json'):
                (directory / precision / name).write_bytes(f'{job["profile_name"]}/{precision}/{name}'.encode())
        return {'status': 'ready', 'resolved_revision': job['profile']['revision'],
                'validation': {'parity': True}, 'fp32': str(directory / 'fp32'), 'int8': str(directory / 'int8')}

    def run_export(self, args=None, child=None, hashes=None):
        with patch.object(export_onnx, 'code_hashes', side_effect=hashes or (lambda: copy.deepcopy(self.hashes))), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            path = export_onnx.export_models(args or self.arguments(), child_runner=child or self.child)
        return runner.read_json(path), path

    def test_each_pinned_model_is_exported_and_results_are_preserved(self):
        manifest, path = self.run_export()
        self.assertEqual(manifest['status'], 'ready')
        self.assertEqual(manifest['ready_count'], 2)
        self.assertEqual(manifest['task_count'], 2)
        self.assertEqual([j['profile_name'] for j in self.calls], ['first', 'second'])
        self.assertTrue(manifest['offline'])
        self.assertEqual(manifest['variants'], ['fp32', 'int8'])
        self.assertEqual(manifest['profiles_sha256'], runner.file_hash(self.profile_path))
        self.assertEqual(manifest['code_sha256'], self.hashes)
        self.assertEqual(manifest['tasks'][0]['result']['validation'], {'parity': True})
        self.assertEqual(runner.read_json(self.out / 'onnx/latest.json')['manifest_path'], str(path))

    def test_selected_models_and_export_configuration_reach_the_worker(self):
        args = self.arguments('--models', 'second', '--threads', '1', '--max-tokens', '64', '--opset', '18')
        manifest, _ = self.run_export(args)
        self.assertEqual(manifest['models'], ['second'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual({k: self.calls[0][k] for k in ('threads', 'max_tokens', 'opset')},
                         {'threads': 1, 'max_tokens': 64, 'opset': 18})

    def test_full_ready_index_contains_all_twelve_hashed_artifacts(self):
        self.profiles = runner.load_profiles(runner.ROOT / 'benchmarks/pinned-profiles.json')
        runner.write_json(self.profile_path, {'schema_version': 1, 'profiles': self.profiles})
        manifest, path = self.run_export()
        index = runner.read_json(self.out / 'onnx-ready.json')
        self.assertEqual(index['status'], 'ready')
        self.assertEqual(index['artifact_count'], 12)
        self.assertEqual(index['model_count'], 6)
        self.assertEqual(index['pinned_profiles_path'], str(self.profile_path))
        self.assertEqual(index['manifest_path'], str(path))
        self.assertEqual({(item['profile'], item['precision']) for item in index['artifacts']},
                         {(name, precision) for name in self.profiles for precision in ('fp32', 'int8')})
        for item in index['artifacts']:
            self.assertEqual(set(item['all_file_sha256']), {'model.onnx', 'tokenizer.json', 'metadata.json'})
            for name, digest in item['all_file_sha256'].items():
                self.assertEqual(digest, runner.file_hash(Path(item['directory']) / name))

    def test_selected_subset_is_not_published_as_a_full_suite(self):
        self.run_export(self.arguments('--models', 'first'))
        index = runner.read_json(self.out / 'onnx-ready.json')
        self.assertEqual(index['status'], 'partial')
        self.assertEqual(index['artifact_count'], 2)

    def test_missing_artifact_cannot_be_published_as_ready(self):
        def missing(job):
            result = self.child(job)
            (Path(job['run_dir']) / 'int8/metadata.json').unlink()
            return result
        manifest, _ = self.run_export(self.arguments('--models', 'first'), child=missing)
        self.assertEqual(manifest['status'], 'failed')
        self.assertEqual(runner.read_json(self.out / 'onnx-ready.json')['status'], 'failed')

    def test_default_profiles_are_part_of_the_repository(self):
        path = Path(export_onnx.parser().parse_args([]).profiles)
        self.assertEqual(path, runner.ROOT / 'benchmarks/pinned-profiles.json')
        self.assertTrue(path.is_file())

    def test_unpinned_model_is_rejected_before_creating_artifacts(self):
        self.profiles['first']['revision'] = 'main'
        runner.write_json(self.profile_path, {'schema_version': 1, 'profiles': self.profiles})
        with self.assertRaisesRegex(ValueError, 'immutable 40-character commit SHA'):
            self.run_export()
        self.assertFalse(self.out.exists())

    def test_failure_is_preserved_and_other_models_continue(self):
        def failing(job):
            if job['profile_name'] == 'first':
                raise RuntimeError('INT8 validation failed')
            return self.child(job)
        manifest, _ = self.run_export(child=failing)
        self.assertEqual(manifest['status'], 'partial')
        self.assertEqual(manifest['ready_count'], 1)
        self.assertEqual([t['status'] for t in manifest['tasks']], ['failed', 'ready'])
        self.assertIn('INT8 validation failed', manifest['tasks'][0]['error'])

    def test_worker_not_ready_result_is_never_marked_successful(self):
        manifest, _ = self.run_export(
            self.arguments('--models', 'first'),
            child=lambda _: {'status': 'failed', 'error': 'bad vectors'},
        )
        self.assertEqual(manifest['status'], 'failed')
        self.assertEqual(manifest['ready_count'], 0)
        self.assertEqual(manifest['tasks'][0]['result']['error'], 'bad vectors')

    def test_repeat_preserves_previous_collection_and_artifacts(self):
        args = self.arguments('--models', 'first')
        previous, previous_path = self.run_export(args)
        sentinel = Path(previous['tasks'][0]['run_dir']) / 'fp32/model.onnx'
        sentinel.write_bytes(b'preserve earlier model')
        current, _ = self.run_export(args)
        self.assertNotEqual(previous['collection_id'], current['collection_id'])
        self.assertEqual(sentinel.read_bytes(), b'preserve earlier model')
        self.assertEqual(runner.read_json(previous_path)['status'], 'ready')

    def test_code_change_during_export_invalidates_success(self):
        changed = dict(self.hashes, **{'onnx_encoder.py': 'changed'})
        manifest, _ = self.run_export(self.arguments('--models', 'first'), hashes=[self.hashes, changed])
        self.assertEqual(manifest['status'], 'failed')
        self.assertIn('changed during preparation', manifest['tasks'][0]['error'])

    def test_unknown_duplicate_and_invalid_configuration_fail_before_export(self):
        for options in (('--models', 'missing'), ('--models', 'first', 'first'),
                        ('--threads', '0'), ('--max-tokens', '0'), ('--opset', '16')):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.run_export(self.arguments(*options))
        self.assertFalse(self.out.exists())

    def test_child_is_launched_without_shell_and_with_project_offline_cache(self):
        directory = self.root / 'worker'
        directory.mkdir()
        job = {'run_dir': str(directory), 'hf_home': str(self.out / 'cache/huggingface'), 'threads': 2}
        runner.write_json(directory / 'job.json', job)

        def child(command, **kwargs):
            self.assertEqual(command[1:4], ['-m', 'benchmarks.onnx_export_worker', '--job'])
            self.assertEqual(command[4], str(directory / 'job.json'))
            self.assertEqual(kwargs['cwd'], runner.ROOT)
            self.assertNotIn('shell', kwargs)
            environment = kwargs['env']
            for name in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE'):
                self.assertEqual(environment[name], '1')
            self.assertEqual(environment['HF_HOME'], job['hf_home'])
            self.assertEqual(environment['PATH'], '/usr/bin:/bin')
            self.assertEqual(environment['CUDA_VISIBLE_DEVICES'], '')
            runner.write_json(directory / 'result.json', {'status': 'ready', 'fixture': True})
            return SimpleNamespace(returncode=0)

        with patch.object(export_onnx.subprocess, 'run', side_effect=child):
            result = export_onnx.run_child(job)
        self.assertEqual(result, {'status': 'ready', 'fixture': True})
        self.assertTrue((directory / 'child.log').is_file())

    def test_child_nonzero_exit_cannot_be_hidden_by_ready_result_file(self):
        directory = self.root / 'failed-worker'
        directory.mkdir()
        runner.write_json(directory / 'job.json', {})
        runner.write_json(directory / 'result.json', {'status': 'ready'})
        job = {'run_dir': str(directory), 'hf_home': str(self.out / 'cache/huggingface'), 'threads': 2}
        with patch.object(export_onnx.subprocess, 'run', return_value=SimpleNamespace(returncode=3)), \
                self.assertRaisesRegex(RuntimeError, 'exited with code 3'):
            export_onnx.run_child(job)


if __name__ == '__main__':
    unittest.main()
