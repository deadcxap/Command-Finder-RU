"""Tests for cf.seed module — seed file loading and database population."""

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from cf.config import EMBEDDING_DIM
from cf.seed import load_seed_files


@pytest.fixture(autouse=True)
def no_installed_model_is_needed_for_mock_encoding(monkeypatch, tmp_path):
    monkeypatch.setattr("cf.seed._onnx_available", lambda: False)
    monkeypatch.setattr("cf.db.ONNX_DIR", tmp_path / "unused_model")


def old_index(tmp_path, dimension=384):
    from cf.db import get_connection, init_db, insert_commands_batch, insert_patterns_batch
    path = tmp_path / "old.db"
    conn = get_connection(path)
    init_db(conn)
    cmd_id = insert_commands_batch(conn, [("old", "test", "old", "Старая команда")])[0]
    pat_id = insert_patterns_batch(conn, [(cmd_id, "example", "старое описание", "old", None, 0)])[0]
    conn.execute("DROP TABLE pattern_embeddings")
    conn.execute(f"CREATE VIRTUAL TABLE pattern_embeddings USING vec0(pattern_id INTEGER PRIMARY KEY, embedding FLOAT[{dimension}])")
    vector = np.ones(dimension, dtype=np.float32)
    vector /= np.linalg.norm(vector)
    conn.execute("INSERT INTO pattern_embeddings(pattern_id, embedding) VALUES (?, ?)", (pat_id, vector.tobytes()))
    conn.execute("INSERT INTO query_cache(query_text, embedding) VALUES ('old-cache', ?)", (vector.tobytes(),))
    conn.commit()
    conn.close()
    return path


class TestLoadSeedFiles:
    def test_loads_json_files(self, seed_json_file):
        result = load_seed_files(seed_json_file)
        assert len(result) == 1
        assert result[0]["category"] == "test"

    def test_returns_empty_for_missing_dir(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        result = load_seed_files(empty_dir)
        assert result == []

    def test_loads_multiple_files(self, tmp_path):
        data_dir = tmp_path / "commands"
        data_dir.mkdir()
        for name in ("a.json", "b.json"):
            with open(data_dir / name, "w") as f:
                json.dump({
                    "category": name.split(".")[0],
                    "commands": [{
                        "name": "test",
                        "synopsis": "test",
                        "description": "test",
                        "patterns": [],
                    }],
                }, f)
        result = load_seed_files(data_dir)
        assert len(result) == 2

    def test_preserves_command_structure(self, seed_json_file):
        result = load_seed_files(seed_json_file)
        cmds = result[0]["commands"]
        assert len(cmds) == 2
        assert cmds[0]["name"] == "ls"
        assert len(cmds[0]["patterns"]) == 2


class TestSeedDatabase:
    @patch("cf.seed.encode_batch")
    @patch("cf.seed.load_seed_files")
    def test_seed_inserts_correct_counts(self, mock_load, mock_encode, tmp_db, sample_seed_data):
        db_path, conn = tmp_db
        conn.close()

        mock_load.return_value = [sample_seed_data]
        mock_encode.return_value = np.random.randn(3, EMBEDDING_DIM).astype(np.float32)

        from cf.seed import seed_database
        stats = seed_database(db_path=db_path)

        assert stats["commands"] == 2
        assert stats["patterns"] == 3

    @patch("cf.seed.encode_batch")
    @patch("cf.seed.load_seed_files")
    def test_force_clears_before_insert(self, mock_load, mock_encode, tmp_db, sample_seed_data):
        db_path, conn = tmp_db
        from cf.db import insert_commands_batch
        insert_commands_batch(conn, [("old", "cat", "old", "Old command")])
        conn.commit()
        conn.close()

        mock_load.return_value = [sample_seed_data]
        mock_encode.return_value = np.random.randn(3, EMBEDDING_DIM).astype(np.float32)

        from cf.seed import seed_database
        stats = seed_database(db_path=db_path, force=True)

        assert stats["commands"] == 2

    @patch("cf.seed.load_seed_files", return_value=[])
    def test_returns_zeros_when_no_seed_files(self, mock_load, tmp_db):
        db_path, conn = tmp_db
        conn.close()

        from cf.seed import seed_database
        stats = seed_database(db_path=db_path)
        assert stats == {"commands": 0, "patterns": 0}

    @patch("cf.seed.encode_batch")
    @patch("cf.seed.load_seed_files")
    def test_deduplicates_commands_by_category_and_name(self, mock_load, mock_encode, tmp_path):
        data = {
            "category": "fs",
            "commands": [
                {
                    "name": "ls",
                    "synopsis": "ls",
                    "description": "List",
                    "patterns": [
                        {"type": "ex", "text": "pat1", "command": "ls -la"},
                    ],
                },
                {
                    "name": "ls",
                    "synopsis": "ls",
                    "description": "List",
                    "patterns": [
                        {"type": "ex", "text": "pat2", "command": "ls -a"},
                    ],
                },
            ],
        }

        mock_load.return_value = [data]
        mock_encode.return_value = np.random.randn(2, EMBEDDING_DIM).astype(np.float32)

        db_path = tmp_path / "test.db"
        from cf.seed import seed_database
        stats = seed_database(db_path=db_path)

        assert stats["commands"] == 1
        assert stats["patterns"] == 2

    @patch("cf.seed.encode_batch")
    @patch("cf.seed.load_seed_files")
    def test_repeated_nonforce_seed_keeps_counts_and_skips_encoding(self, mock_load, mock_encode, tmp_path, sample_seed_data):
        from cf.seed import seed_database
        from cf.db import get_connection, get_stats
        path = tmp_path / "test.db"
        mock_load.return_value = [sample_seed_data]
        mock_encode.return_value = np.zeros((3, EMBEDDING_DIM), dtype=np.float32)
        expected = seed_database(path)
        mock_load.side_effect = AssertionError("existing index should be retained")
        mock_encode.side_effect = AssertionError("existing index should not be encoded")
        assert seed_database(path) == expected == {"commands": 2, "patterns": 3}
        conn = get_connection(path)
        assert get_stats(conn) == expected
        assert conn.execute("SELECT COUNT(*) FROM pattern_embeddings").fetchone()[0] == 3
        conn.close()

    @patch("cf.seed.encode_batch")
    @patch("cf.seed.load_seed_files")
    def test_force_rebuilds_384_dimension_index_and_clears_query_cache(self, mock_load, mock_encode, tmp_path, sample_seed_data):
        from cf.seed import seed_database
        from cf.db import get_connection, init_db, index_identity
        path = old_index(tmp_path)
        mock_load.return_value = [sample_seed_data]
        mock_encode.return_value = np.zeros((3, EMBEDDING_DIM), dtype=np.float32)
        assert seed_database(path, force=True) == {"commands": 2, "patterns": 3}
        conn = get_connection(path)
        init_db(conn)
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='pattern_embeddings'").fetchone()[0]
        assert f"FLOAT[{EMBEDDING_DIM}]" in sql
        assert conn.execute("SELECT COUNT(*) FROM query_cache").fetchone()[0] == 0
        marker = conn.execute("SELECT value FROM index_metadata WHERE key='model_identity'").fetchone()[0]
        assert json.loads(marker) == index_identity()
        conn.close()

    @patch("cf.seed.encode_batch", side_effect=RuntimeError("encoder unavailable"))
    @patch("cf.seed.load_seed_files")
    def test_failed_encoding_preserves_old_index_and_cache(self, mock_load, mock_encode, tmp_path, sample_seed_data):
        from cf.seed import seed_database
        from cf.db import get_connection
        path = old_index(tmp_path)
        mock_load.return_value = [sample_seed_data]
        with pytest.raises(RuntimeError, match="encoder unavailable"):
            seed_database(path, force=True)
        conn = get_connection(path)
        assert conn.execute("SELECT name FROM commands").fetchall() == [("old",)]
        assert conn.execute("SELECT COUNT(*) FROM pattern_embeddings").fetchone()[0] == 1
        assert conn.execute("SELECT query_text FROM query_cache").fetchall() == [("old-cache",)]
        assert "FLOAT[384]" in conn.execute("SELECT sql FROM sqlite_master WHERE name='pattern_embeddings'").fetchone()[0]
        conn.close()

    @patch("cf.seed.insert_patterns_batch", side_effect=sqlite3.IntegrityError("insert failed"))
    @patch("cf.seed.encode_batch")
    @patch("cf.seed.load_seed_files")
    def test_failed_insert_rolls_back_schema_and_old_data(self, mock_load, mock_encode, mock_insert, tmp_path, sample_seed_data):
        from cf.seed import seed_database
        from cf.db import get_connection
        path = old_index(tmp_path)
        mock_load.return_value = [sample_seed_data]
        mock_encode.return_value = np.zeros((3, EMBEDDING_DIM), dtype=np.float32)
        with pytest.raises(sqlite3.IntegrityError, match="insert failed"):
            seed_database(path, force=True)
        conn = get_connection(path)
        assert conn.execute("SELECT name FROM commands").fetchall() == [("old",)]
        assert conn.execute("SELECT COUNT(*) FROM pattern_embeddings").fetchone()[0] == 1
        assert "FLOAT[384]" in conn.execute("SELECT sql FROM sqlite_master WHERE name='pattern_embeddings'").fetchone()[0]
        conn.close()
