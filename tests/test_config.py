"""Tests for cf.config module."""

from pathlib import Path
import os
import subprocess
import sys

from cf.config import (
    DATA_DIR, DB_DIR, DB_PATH, DEFAULT_BATCH_SIZE, EMBEDDING_DIM, MAX_TOKENS,
    MODEL_NAME, MODEL_REVISION, MODEL_SPEC, NUM_THREADS, ONNX_DIR,
)


class TestConfigConstants:
    def test_model_name_is_mini_retriever(self):
        assert MODEL_NAME == "sergeyzh/rubert-mini-retriever"

    def test_embedding_dim_matches_model(self):
        assert EMBEDDING_DIM == 312

    def test_data_dir_points_to_commands(self):
        assert DATA_DIR.name == "commands"
        assert DATA_DIR.parent.name == "data"

    def test_db_dir_under_local_share(self):
        environment = dict(os.environ, PATH="/usr/bin:/bin")
        environment.pop("CF_DB_DIR", None)
        program = "from pathlib import Path; from cf.config import DB_DIR; assert DB_DIR == Path.home()/'.local'/'share'/'cf-ru'"
        result = subprocess.run([sys.executable, "-c", program], env=environment, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def test_db_path_is_sqlite_file(self):
        assert DB_PATH.name == "cf.db"
        assert DB_PATH.parent == DB_DIR

    def test_onnx_dir_under_db_dir(self):
        assert ONNX_DIR == DB_DIR / "onnx"

    def test_seed_files_exist(self):
        json_files = list(DATA_DIR.glob("*.json"))
        assert len(json_files) >= 1, "Expected at least one seed JSON file"

    def test_model_settings_come_from_the_packaged_specification(self):
        assert MODEL_NAME == MODEL_SPEC["model"]
        assert MODEL_REVISION == MODEL_SPEC["revision"]
        assert MAX_TOKENS == MODEL_SPEC["max_tokens"] == 128
        assert len(MODEL_REVISION) == 40

    def test_cpu_and_batch_defaults(self):
        assert NUM_THREADS == 2
        assert DEFAULT_BATCH_SIZE == 32
