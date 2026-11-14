from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from benchmarks import worker


@contextmanager
def fresh_legacy_modules():
    previous = {key: value for key, value in sys.modules.items() if key == "benchmarks.legacy" or key.startswith("benchmarks.legacy.")}
    for key in previous:
        sys.modules.pop(key)
    try:
        yield
    finally:
        for key in list(sys.modules):
            if key == "benchmarks.legacy" or key.startswith("benchmarks.legacy."):
                sys.modules.pop(key)
        sys.modules.update(previous)


class FixtureEncoder:
    def __init__(self, job):
        self.calls = Counter()
        self.import_ms = 0.0
        self.load_ms = 0.0
        self.metadata = {
            "model": "fixture",
            "dimensions": job["profile"]["dimensions"],
            "resolved_revision": "fixture-commit",
            "dtype": "float32",
            "encoder_backend": "fixture",
            "effective_max_tokens": job["max_tokens"],
            "effective_threads": job["threads"],
        }
        self.dimensions = job["profile"]["dimensions"]

    def encode_query(self, text):
        self.calls[text] += 1
        vector = np.zeros(self.dimensions, dtype=np.float32)
        if text in ("erase files", "delete files", "убрать файлы"):
            vector[1] = 1
        else:
            vector[0] = 1
        return vector

    def encode_batch(self, texts, batch_size=None):
        return np.array([self.encode_query(text) for text in texts], dtype=np.float32)


class FixtureModel:
    def __init__(self, dimensions=3):
        self.dimensions = dimensions
        self.max_seq_length = 512
        self.calls = []
        self.transformer = SimpleNamespace(config=SimpleNamespace(_commit_hash="resolved-test-revision"), dtype="float32", device="cpu")
        self.to = Mock(return_value=self)

    def __getitem__(self, index):
        return SimpleNamespace(auto_model=self.transformer)

    def get_sentence_embedding_dimension(self):
        return self.dimensions

    def encode(self, texts, **kwargs):
        self.calls.append((texts, kwargs))
        shape = (self.dimensions,) if isinstance(texts, str) else (len(texts), self.dimensions)
        values = np.zeros(shape, dtype=np.float32)
        values[..., 0] = 1
        return values


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cf-worker-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run_dir = self.root / "run"
        seed = self.run_dir / "seed"
        seed.mkdir(parents=True)
        (seed / "files.json").write_text(json.dumps({
            "category": "filesystem",
            "commands": [
                {
                    "name": "ls", "description": "list", "synopsis": "ls",
                    "patterns": [
                        {"type": "example", "text": "show files", "command": "ls"},
                        {"type": "example", "text": "another listing", "command": "ls"},
                    ],
                },
                {
                    "name": "rm", "description": "delete", "synopsis": "rm FILE",
                    "patterns": [
                        {"type": "example", "text": "erase files", "command": "rm file.txt", "destructive": True},
                    ],
                },
            ],
        }))
        self.job = {
            "profile_name": "fixture",
            "profile": {"model": "fixture", "revision": "test", "dimensions": 3, "query_prefix": "", "document_prefix": ""},
            "backend": "sentence-transformers",
            "corpus": "fixture",
            "run_dir": str(self.run_dir),
            "hf_home": str(self.root / "hf"),
            "languages": ["ru", "en"],
            "repeats": 2,
            "threads": 2,
            "batch_size": 2,
            "max_tokens": 128,
            "top_k": 7,
            "download": False,
            "seed": 10,
            "cases": [
                {
                    "id": "delete", "category": "filesystem",
                    "query_en": "delete files", "query_ru": "убрать файлы",
                    "expected_commands": ["rm file.txt"],
                },
                {
                    "id": "retain", "category": "filesystem",
                    "query_en": "do not delete files", "query_ru": "не удалять файлы",
                    "expected_commands": ["ls"], "forbidden_commands": ["rm file.txt"],
                    "tags": ["negation"],
                },
            ],
        }
        self.encoders = []

    def factory(self, job, embeddings):
        encoder = FixtureEncoder(job)
        self.encoders.append(encoder)
        return encoder

    def phase(self, phase, language=None, job=None):
        with patch.dict(os.environ), fresh_legacy_modules():
            return worker.run_phase(job or self.job, phase, language, encoder_factory=self.factory)

    def test_prepare_builds_real_sqlite_index_and_reports_vector_bytes(self):
        result = self.phase("prepare")
        self.assertEqual(result["stats"], {"commands": 2, "patterns": 3})
        self.assertEqual(result["vector_bytes"], 3 * 3 * 4)
        self.assertGreater(result["db_bytes"], 0)
        self.assertGreater(result["peak_rss_bytes"], 0)
        self.assertEqual(self.encoders[-1].calls["erase files"], 1)
        saved = json.loads((self.run_dir / "prepare.json").read_text())
        self.assertEqual(saved["model"]["dimensions"], 3)
        with sqlite3.connect(self.run_dir / "cf.db") as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM patterns").fetchone()[0], 3)

    def test_warm_uses_native_search_dedup_and_exposes_negation_cache_collision(self):
        self.phase("prepare")
        result = self.phase("warm")
        english = result["languages"]["en"]
        russian = result["languages"]["ru"]
        self.assertEqual(english["metrics"]["overall"]["hit@1"], 1.0)
        self.assertEqual(english["rankings"]["delete"], ["rm file.txt", "ls"])
        self.assertEqual(english["rankings"]["retain"], ["ls", "rm file.txt"])
        self.assertEqual(english["metrics"]["overall"]["forbidden_hit@1"], 0.0)
        self.assertEqual(english["latency_ms"]["uncached"]["count"], 4)
        self.assertEqual(english["latency_ms"]["cache_hit"]["count"], 4)
        mismatch = english["session_cache_mismatches"]
        self.assertEqual(len(mismatch), 1)
        self.assertEqual(mismatch[0]["id"], "retain")
        self.assertTrue(mismatch[0]["top1_changed"])
        self.assertEqual(russian["session_cache_mismatches"], [])
        self.assertEqual(english["repeat_ranking_mismatches"], [])
        self.assertEqual(self.encoders[-1].calls["do not delete files"], 4)
        self.assertEqual(self.encoders[-1].calls["delete files"], 6)

    def test_cold_clears_existing_cache_before_first_query(self):
        self.phase("prepare")
        wrong = np.array([1, 0, 0], dtype=np.float32).tobytes()
        with sqlite3.connect(self.run_dir / "cf.db") as connection:
            connection.execute("INSERT INTO query_cache VALUES (?, ?)", ("delete files", wrong))
            connection.commit()
        result = self.phase("cold", "en")
        self.assertEqual(result["ranking"][0], "rm file.txt")
        self.assertEqual(result["query_id"], "delete")
        self.assertGreater(result["startup_and_first_query_ms"], 0)
        self.assertTrue((self.run_dir / "cold-en.json").is_file())

    def test_cold_and_warm_force_offline_even_when_prepare_allows_downloads(self):
        job = {**self.job, "download": True}
        prepared = self.phase("prepare", job=job)
        self.assertTrue(prepared["download_allowed"])
        observed = []

        def factory(job, embeddings):
            observed.append((job["download"], os.environ["HF_HUB_OFFLINE"], os.environ["TRANSFORMERS_OFFLINE"]))
            return self.factory(job, embeddings)

        with patch.dict(os.environ), fresh_legacy_modules():
            cold = worker.run_phase(job, "cold", "en", encoder_factory=factory)
        with patch.dict(os.environ), fresh_legacy_modules():
            warm = worker.run_phase(job, "warm", encoder_factory=factory)
        self.assertEqual(observed, [(False, "1", "1"), (False, "1", "1")])
        self.assertFalse(cold["download_allowed"])
        self.assertFalse(warm["download_allowed"])
        self.assertTrue(job["download"])

    def test_cached_cold_uses_existing_vector_without_building_an_encoder(self):
        self.phase("prepare")
        self.phase("cold", "en")
        factory = Mock(side_effect=AssertionError("encoder must not load"))
        with patch.dict(os.environ), fresh_legacy_modules(), patch.object(worker, "_build_runtime", side_effect=AssertionError("runtime must not load")):
            result = worker.run_phase(self.job, "cached-cold", "en", encoder_factory=factory)
        factory.assert_not_called()
        self.assertEqual(result["ranking"][0], "rm file.txt")
        self.assertFalse(result["model"]["model_loaded"])
        self.assertEqual(result["model"]["encoder_backend"], "query-cache")
        self.assertGreater(result["startup_and_cached_query_ms"], 0)
        self.assertTrue((self.run_dir / "cached-cold-en.json").is_file())

    def test_cached_cold_missing_vector_fails_instead_of_loading_a_model(self):
        self.phase("prepare")
        factory = Mock(side_effect=AssertionError("encoder must not load"))
        with patch.dict(os.environ), fresh_legacy_modules(), self.assertRaisesRegex(ValueError, "requires the first query to be cached"):
            worker.run_phase(self.job, "cached-cold", "en", encoder_factory=factory)
        factory.assert_not_called()

    def test_cached_cold_fresh_process_imports_no_encoder_or_ml_modules(self):
        self.phase("prepare")
        self.phase("cold", "en")
        job_path = self.root / "job.json"
        job_path.write_text(json.dumps(self.job))
        code = (
            "import json,sys; from benchmarks.worker import run_phase; "
            "r=run_phase(json.load(open(sys.argv[1])), 'cached-cold', 'en'); "
            "assert not {'torch','sentence_transformers','onnxruntime','cf','benchmarks.legacy.embeddings'} & set(sys.modules); "
            "assert r['model']['model_loaded'] is False"
        )
        result = subprocess.run([sys.executable, "-c", code, str(job_path)], cwd=Path(__file__).parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_phases_preserve_all_production_source_files(self):
        root = Path(__file__).parents[1] / "src" / "cf"
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob("*") if path.is_file() and path.suffix in (".py", ".json")}
        self.phase("prepare")
        self.phase("cold", "ru")
        self.phase("warm")
        after = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in before}
        self.assertEqual(before, after)

    def test_prepare_required_for_queries(self):
        with self.assertRaisesRegex(ValueError, "prepare must build"):
            self.phase("warm")
        self.assertEqual(self.encoders, [])

    def test_project_backend_preserves_original_encoding_functions(self):
        job = {**self.job, "backend": "project", "profile_name": "original", "profile": {"model": "all-MiniLM-L6-v2", "dimensions": 384}}
        observed = {}

        def factory(job, embeddings):
            observed["encode_text"] = embeddings.encode_text
            observed["encode_batch"] = embeddings.encode_batch
            return self.factory(job, embeddings)

        with patch.dict(os.environ), fresh_legacy_modules():
            runtime = worker._build_runtime(worker.validate_job(job), encoder_factory=factory)
            self.assertIs(runtime.embeddings.encode_text, observed["encode_text"])
            self.assertIs(runtime.embeddings.encode_batch, observed["encode_batch"])
            self.assertIs(runtime.seed.encode_batch, observed["encode_batch"])
            self.assertEqual(runtime.embeddings.CACHE_DIR, self.run_dir / "embedding-cache")

    def test_profile_validation_rejects_unfair_or_incomplete_jobs(self):
        invalid = [
            {**self.job, "top_k": 3},
            {**self.job, "download": "yes"},
            {**self.job, "repeats": 0},
            {**self.job, "languages": ["ru", "ru"]},
            {**self.job, "cases": []},
            {**self.job, "run_dir": "relative"},
            {**self.job, "profile": None},
            {**self.job, "backend": "project"},
        ]
        for job in invalid:
            with self.subTest(job=job), self.assertRaises(ValueError):
                worker.validate_job(job)

    def test_normalized_host_path_is_rejected_before_filesystem_probes(self):
        job = {**self.job, "run_dir": "/tmp/../mnt/c/benchmark"}
        with patch.object(Path, "is_symlink", side_effect=AssertionError("host path must not be probed")):
            with self.assertRaisesRegex(ValueError, "outside mounted host"):
                worker.validate_job(job)

    def test_symlink_cache_roots_are_rejected_before_creating_directories(self):
        destination = self.root / "other"
        destination.mkdir()
        for name in ("torch", "xdg", "hf/hub"):
            with self.subTest(name=name):
                link = self.root / name
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(destination, target_is_directory=True)
                try:
                    job = {**self.job, "run_dir": str(self.root / "new-run")}
                    with self.assertRaisesRegex(ValueError, "symbolic link"):
                        worker._configure_environment(job)
                    self.assertFalse((self.root / "new-run").exists())
                finally:
                    link.unlink()

    def test_database_symlink_is_rejected_before_checking_its_target(self):
        target = self.root / "unrelated.db"
        target.write_bytes(b"not a benchmark database")
        (self.run_dir / "cf.db").symlink_to(target)
        with patch.object(Path, "is_file", side_effect=AssertionError("symlink target must not be checked")):
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                worker.run_phase(self.job, "cached-cold", "en")

    def test_sentence_transformer_adapter_passes_explicit_prompts_and_caches_documents(self):
        job = worker.validate_job(self.job)
        model = FixtureModel()
        encoder = worker.SentenceTransformerEncoder(model, job, {"resolved_revision": "first"})
        encoder.encode_query("test")
        self.assertEqual(model.calls[-1][1]["prompt"], "")
        texts = ["a", "b"]
        first = encoder.encode_batch(texts)
        second = encoder.encode_batch(texts)
        np.testing.assert_array_equal(first, second)
        self.assertEqual(len(model.calls), 2)
        prefixed = {**job, "profile": {**job["profile"], "document_prefix": "document: "}}
        worker.SentenceTransformerEncoder(model, prefixed, {"resolved_revision": "first"}).encode_batch(texts)
        self.assertEqual(model.calls[-1][1]["prompt"], "document: ")
        self.assertEqual(len(model.calls), 3)
        worker.SentenceTransformerEncoder(model, job, {"resolved_revision": "second"}).encode_batch(texts)
        self.assertEqual(len(model.calls), 4)

    def test_bad_cached_vector_dimensions_are_rejected(self):
        job = worker.validate_job(self.job)
        encoder = worker.SentenceTransformerEncoder(FixtureModel(), job, {})
        encoder.encode_batch(["a"])
        path = next((self.run_dir / "embedding-cache").glob("*.npy"))
        np.save(path, np.zeros((1, 2), dtype=np.float32))
        with self.assertRaisesRegex(ValueError, "invalid vectors"):
            encoder.encode_batch(["a"])

    def test_sentence_transformer_factory_is_cpu_offline_and_revision_pinned(self):
        job = worker.validate_job(self.job)
        job['profile']['config_kwargs'] = {'reference_compile': False}
        constructor = Mock(return_value=FixtureModel())
        fake_torch = SimpleNamespace(set_num_threads=Mock(), set_num_interop_threads=Mock())
        with patch.dict(sys.modules, {"torch": fake_torch, "sentence_transformers": SimpleNamespace(SentenceTransformer=constructor)}):
            encoder = worker._create_encoder(job, SimpleNamespace())
        kwargs = constructor.call_args.kwargs
        self.assertEqual(kwargs["device"], "cpu")
        self.assertTrue(kwargs["local_files_only"])
        self.assertFalse(kwargs["trust_remote_code"])
        self.assertEqual(kwargs["revision"], "test")
        self.assertEqual(kwargs['config_kwargs'], {'reference_compile': False})
        self.assertEqual(encoder.metadata['config_kwargs'], {'reference_compile': False})
        self.assertEqual(encoder.metadata["resolved_revision"], "resolved-test-revision")
        self.assertEqual(encoder.model.max_seq_length, 128)
        fake_torch.set_num_threads.assert_called_once_with(2)

    def test_original_pytorch_loader_moves_model_to_cpu_and_records_device(self):
        job = worker.validate_job({
            **self.job,
            "backend": "project",
            "profile_name": "original",
            "profile": {"model": "all-MiniLM-L6-v2", "dimensions": 384},
        })
        model = FixtureModel(dimensions=384)
        embeddings = SimpleNamespace(_onnx_available=lambda: False, _get_full_model=lambda: model)
        with patch.object(worker, "_torch_threads"):
            encoder = worker._create_encoder(job, embeddings)
        model.to.assert_called_once_with("cpu")
        self.assertEqual(encoder.metadata["device"], "cpu")

    def test_importing_worker_does_not_import_ml_libraries(self):
        command = [sys.executable, "-c", "import sys; import benchmarks.worker; assert not {'numpy', 'torch', 'sentence_transformers', 'cf', 'benchmarks.legacy.db'} & set(sys.modules)"]
        result = subprocess.run(command, cwd=Path(__file__).parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_percentiles_interpolate_and_empty_samples_remain_json_safe(self):
        result = worker._latencies([10, 20])
        self.assertEqual(result["p50"], 15)
        self.assertEqual(result["p95"], 19.5)
        self.assertIsNone(worker._latencies([])["p95"])
        json.dumps(worker._latencies([]), allow_nan=False)


if __name__ == "__main__":
    unittest.main()
