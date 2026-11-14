from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from benchmarks import native_export, runner


class FixtureModel:
    def __init__(self, revision, dimensions=384):
        self.transformer = SimpleNamespace(auto_model=SimpleNamespace(config=SimpleNamespace(_commit_hash=revision)))
        self.dimensions = dimensions

    def __getitem__(self, index):
        return self.transformer

    def get_sentence_embedding_dimension(self):
        return self.dimensions


class NativeExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cf-native-export-', dir='/tmp')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.out = self.root / 'out'
        self.profile = {'model': 'all-MiniLM-L6-v2', 'revision': 'a' * 40,
                        'dimensions': 384, 'query_prefix': '', 'document_prefix': ''}
        self.profiles = self.root / 'profiles.json'
        runner.write_json(self.profiles, {'schema_version': 1, 'profiles': {'original': self.profile}})
        self.exported = []

    def arguments(self, *extra):
        return native_export.parser().parse_args(['--out', str(self.out), '--profiles', str(self.profiles), *extra])

    def fixture_worker(self, job):
        directory = Path(job['run_dir']) / 'onnx'
        model = FixtureModel(self.profile['revision'])

        def load(actual_job):
            self.assertEqual(actual_job['profile'], self.profile)
            snapshot = Path(job['hf_home']) / 'models--sentence-transformers--all-MiniLM-L6-v2/snapshots' / self.profile['revision']
            snapshot.mkdir(parents=True, exist_ok=True)
            return model

        def export():
            self.assertIs(legacy._model, model)
            self.exported.append(str(directory))
            directory.mkdir()
            (directory / 'model.onnx').write_bytes(b'fixture-original-token-encoder')
            (directory / 'tokenizer.json').write_bytes(b'fixture-tokenizer')
            return directory

        legacy = SimpleNamespace(ONNX_DIR=directory, export_onnx=export)
        verification = {'query_checks': [{'cosine_vs_st': 1.0}], 'graph_shapes': {'token_embeddings': ['batch', 'seq', 384]},
                        'intra_op_num_threads': 4, 'inter_op_num_threads': 1}
        with patch.dict(os.environ):
            return native_export.export_worker(job, model_loader=load, legacy_module=legacy,
                                               verifier=lambda *args: verification)

    def test_original_export_is_pinned_and_publishes_suite_compatible_info(self):
        with redirect_stdout(io.StringIO()):
            directory = native_export.export_native(self.arguments(), child_runner=self.fixture_worker)
        info = runner.read_json(directory.parent / 'info.json')
        self.assertEqual(info['status'], 'ready')
        self.assertEqual(info['resolved_revision'], self.profile['revision'])
        self.assertEqual(info['native_export_function'], 'benchmarks.legacy.embeddings.export_onnx')
        self.assertEqual(info['intra_op_num_threads'], 4)
        self.assertFalse(info['whole_corpus_seed_run'])
        self.assertFalse((directory.parent / 'cf.db').exists())
        self.assertEqual(Path(info['offline_main_alias']['path']).read_text(), self.profile['revision'])
        self.assertTrue(Path(info['offline_main_alias']['path']).is_relative_to(self.out))
        for name, digest in info['all_file_sha256'].items():
            self.assertEqual(digest, runner.file_hash(directory / name))
        latest = runner.read_json(self.out / 'native-original-onnx/latest.json')
        self.assertEqual(latest['onnx_dir'], str(directory))
        self.assertEqual(latest['info_path'], str(directory.parent / 'info.json'))

    def test_repeated_export_preserves_previous_native_artifacts(self):
        with redirect_stdout(io.StringIO()):
            first = native_export.export_native(self.arguments(), child_runner=self.fixture_worker)
            original = (first / 'model.onnx').read_bytes()
            second = native_export.export_native(self.arguments(), child_runner=self.fixture_worker)
        self.assertNotEqual(first, second)
        self.assertEqual((first / 'model.onnx').read_bytes(), original)

    def test_wrong_revision_never_reaches_the_legacy_exporter_or_alias(self):
        job = {'profile': self.profile, 'run_dir': str(self.root / 'job'),
               'hf_home': str(self.out / 'cache/huggingface'), 'download': False}
        legacy = SimpleNamespace(ONNX_DIR=Path(job['run_dir']) / 'onnx', export_onnx=Mock())
        with patch.dict(os.environ), self.assertRaisesRegex(ValueError, 'ревизия'):
            native_export.export_worker(job, model_loader=lambda _: FixtureModel('b' * 40), legacy_module=legacy)
        legacy.export_onnx.assert_not_called()
        self.assertFalse(Path(job['hf_home']).exists())

    def test_unpinned_profile_is_rejected_before_creating_output(self):
        runner.write_json(self.profiles, {'schema_version': 1, 'profiles': {'original': {**self.profile, 'revision': 'main'}}})
        with self.assertRaisesRegex(ValueError, 'SHA'):
            native_export.export_native(self.arguments(), child_runner=self.fixture_worker)
        self.assertFalse(self.out.exists())

    def test_environment_is_project_local_and_does_not_inherit_cf_settings(self):
        job = {'profile': self.profile, 'run_dir': str(self.root / 'job'),
               'hf_home': str(self.out / 'cache/huggingface'), 'download': False}
        with patch.dict(os.environ, {'CF_MODEL_DIR': 'unrelated-fixture', 'CF_DB_DIR': 'unrelated-fixture'}):
            environment = native_export.child_environment(job)
        self.assertNotIn('CF_MODEL_DIR', environment)
        self.assertEqual(environment['CF_DB_DIR'], job['run_dir'])
        self.assertEqual(environment['HF_HOME'], job['hf_home'])
        self.assertEqual(environment['SENTENCE_TRANSFORMERS_HOME'], job['hf_home'])
        self.assertEqual(environment['HF_HUB_OFFLINE'], '1')
        self.assertEqual(environment['PATH'], '/usr/bin:/bin')

    def test_model_loader_requests_exact_revision_and_cpu_without_downloads(self):
        job = {'profile': self.profile, 'hf_home': str(self.out / 'cache/huggingface'), 'download': False}
        constructor = Mock(return_value='fixture-model')
        torch = SimpleNamespace(set_num_threads=Mock(), set_num_interop_threads=Mock())
        with patch.dict(sys.modules, {'torch': torch, 'sentence_transformers': SimpleNamespace(SentenceTransformer=constructor)}):
            self.assertEqual(native_export._load_model(job), 'fixture-model')
        constructor.assert_called_once_with('all-MiniLM-L6-v2', revision='a' * 40, device='cpu',
                                            cache_folder=job['hf_home'], local_files_only=True, trust_remote_code=False)
        torch.set_num_threads.assert_called_once_with(4)

    def test_cli_module_does_not_import_training_libraries(self):
        program = "import sys; import benchmarks.native_export; assert not {'torch','sentence_transformers','onnxruntime'} & sys.modules.keys()"
        result = subprocess.run([sys.executable, '-c', program], cwd=runner.ROOT,
                                env={**os.environ, 'PATH': '/usr/bin:/bin'}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
