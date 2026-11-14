"""Preparation workflow checks without model packages or downloads."""

import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmarks import prepare, runner


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cf-prepare-tests-')
        self.addCleanup(self.temporary.cleanup)
        self.out = Path(self.temporary.name) / 'artifacts'
        profile = {'model': 'org/fixture', 'revision': 'main', 'dimensions': 3,
                   'query_prefix': '', 'document_prefix': ''}
        self.inputs = {
            'profiles': {'first': copy.deepcopy(profile), 'second': copy.deepcopy(profile)},
            'sources': {'sample': {'category': 'sample', 'commands': [{
                'name': 'tool', 'description': 'Example', 'synopsis': 'tool',
                'patterns': [{'type': 'example', 'text': 'read text', 'command': 'tool --read'}],
            }]}},
            'aliases': {('sample', 0, 0): {'patterns_ru': ['прочитать текст', 'показать содержимое']}},
            'cases': [{'id': 'one', 'category': 'sample', 'query_ru': 'что внутри',
                       'query_en': 'what is inside', 'expected_commands': ['tool --read']}],
            'source_hashes': {'sample.json': 'source-hash'},
        }
        self.calls = []

    def arguments(self, *extra):
        return prepare.parser().parse_args(['--out', str(self.out), *extra])

    def child(self, job, phase, language=None):
        self.calls.append((copy.deepcopy(job), phase, language))
        persisted = runner.read_json(Path(job['run_dir']) / 'job.json')
        self.assertEqual(persisted, job)
        revision = ('a' if job['profile_name'] == 'first' else 'b') * 40
        metadata = {'resolved_revision': revision, 'dimensions': 3}
        if phase == 'prepare':
            records = [runner.read_json(p) for p in (Path(job['run_dir']) / 'seed').glob('*.json')]
            count = sum(len(c['patterns']) for d in records for c in d['commands'])
            return {'stats': {'commands': 1, 'patterns': count}, 'model': metadata}
        self.assertFalse(job['download'])
        self.assertEqual(job['profile']['revision'], revision)
        if phase == 'cold':
            return {'ranking': ['tool --read'], 'model': metadata}
        if phase == 'cached-cold':
            return {'ranking': ['tool --read'], 'model': {'model_loaded': False}}
        self.fail(f'Unexpected phase: {phase}')

    def run_preparation(self, args, child=None):
        with patch.object(runner, '_runtime_dependencies'), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            path = prepare.prepare_models(args, inputs=self.inputs, child_runner=child or self.child)
        return runner.read_json(path), path

    def test_prepares_multiple_corpora_pins_revision_and_smokes_without_network(self):
        manifest, path = self.run_preparation(self.arguments('--download', '--corpora', 'ru2', 'original'))
        self.assertEqual(manifest['status'], 'ready')
        self.assertEqual(manifest['ready_count'], 4)
        self.assertEqual(manifest['task_count'], 4)
        self.assertEqual(len(self.calls), 20)
        for name in ('first', 'second'):
            prepares = [job for job, phase, _ in self.calls if job['profile_name'] == name and phase == 'prepare']
            self.assertTrue(prepares[0]['download'])
            self.assertFalse(prepares[1]['download'])
            self.assertEqual(prepares[0]['profile']['revision'], 'main')
            self.assertEqual(prepares[1]['profile']['revision'], ('a' if name == 'first' else 'b') * 40)
        locked = runner.read_json(Path(manifest['pinned_profiles_path']))
        self.assertEqual(locked['profiles']['first']['revision'], 'a' * 40)
        self.assertEqual(locked['profiles']['second']['revision'], 'b' * 40)
        self.assertEqual(runner.read_json(self.out / 'prepared/latest.json')['manifest_path'], str(path))
        self.assertEqual({r['expected_patterns'] for r in manifest['tasks']}, {1, 2})
        self.assertTrue(all(r['profile']['revision'] != 'main' for r in manifest['tasks']))

    def test_repeated_preparation_does_not_overwrite_existing_indexes(self):
        args = self.arguments('--models', 'first')
        first, _ = self.run_preparation(args)
        previous = Path(first['tasks'][0]['run_dir'])
        sentinel = previous / 'preserve.txt'
        sentinel.write_text('keep prior preparation')
        second, _ = self.run_preparation(args)
        self.assertNotEqual(first['collection_id'], second['collection_id'])
        self.assertNotEqual(first['tasks'][0]['run_dir'], second['tasks'][0]['run_dir'])
        self.assertEqual(sentinel.read_text(), 'keep prior preparation')

    def test_partial_failure_is_saved_and_other_models_are_still_prepared(self):
        def failing(job, phase, language=None):
            if job['profile_name'] == 'first':
                raise RuntimeError('download unavailable')
            return self.child(job, phase, language)
        manifest, _ = self.run_preparation(self.arguments('--corpora', 'ru2', 'original'), failing)
        self.assertEqual(manifest['status'], 'partial')
        self.assertEqual([r['status'] for r in manifest['tasks']], ['failed', 'skipped', 'ready', 'ready'])
        self.assertEqual(manifest['ready_count'], 2)
        self.assertIn('download unavailable', manifest['tasks'][0]['error'])

    def test_unresolved_revision_is_not_marked_ready(self):
        def unresolved(job, phase, language=None):
            result = self.child(job, phase, language)
            result['model']['resolved_revision'] = None
            return result
        manifest, _ = self.run_preparation(self.arguments('--models', 'first'), unresolved)
        self.assertEqual(manifest['status'], 'failed')
        self.assertIn('immutable model revision', manifest['tasks'][0]['error'])
        self.assertEqual(len(self.calls), 1)

    def test_cache_disagreement_is_reported_as_failed(self):
        def disagreement(job, phase, language=None):
            result = self.child(job, phase, language)
            if phase == 'cached-cold':
                result['ranking'] = ['wrong-command']
            return result
        manifest, _ = self.run_preparation(self.arguments('--models', 'first'), disagreement)
        self.assertEqual(manifest['status'], 'failed')
        self.assertIn('cached and uncached smoke', manifest['tasks'][0]['error'])

    def test_offline_is_default_for_preparation_too(self):
        manifest, _ = self.run_preparation(self.arguments('--models', 'first'))
        self.assertEqual(manifest['status'], 'ready')
        self.assertFalse(any(job['download'] for job, _, _ in self.calls))

    def test_unknown_profile_fails_before_creating_artifacts(self):
        with self.assertRaisesRegex(ValueError, 'existing model profiles'):
            self.run_preparation(self.arguments('--models', 'missing'))
        self.assertFalse(self.out.exists())

    def test_inputs_are_not_mutated_when_profiles_are_pinned(self):
        before = copy.deepcopy(self.inputs)
        self.run_preparation(self.arguments('--models', 'first'))
        self.assertEqual(self.inputs, before)

    def test_existing_sha_revision_is_accepted_when_model_metadata_omits_it(self):
        self.assertEqual(prepare._revision({'resolved_revision': None}, {'revision': 'a' * 40}), 'a' * 40)
        with self.assertRaises(ValueError):
            prepare._revision({'resolved_revision': 'main'}, {'revision': 'main'})


if __name__ == '__main__':
    unittest.main()
