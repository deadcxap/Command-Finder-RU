"""Tests for cf.search module — query normalization, dedup, search pipeline."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from cf.config import EMBEDDING_DIM
from cf.search import SearchResult, _normalize_query, search


class TestNormalizeQuery:
    def test_preserves_negation(self):
        assert _normalize_query("delete files") != _normalize_query("do not delete files")
        assert _normalize_query("удали файлы") != _normalize_query("не удали файлы")

    def test_preserves_order(self):
        assert _normalize_query("copy a into b") != _normalize_query("copy b into a")

    def test_preserves_filename_case(self):
        assert _normalize_query("read README.md") != _normalize_query("read readme.md")

    def test_preserves_internal_whitespace(self):
        assert _normalize_query("найди 'a  b'") != _normalize_query("найди 'a b'")

    def test_strips_only_outer_whitespace(self):
        assert _normalize_query("  найди файл\n") == _normalize_query("найди файл")

    def test_does_not_reuse_old_normalized_keys(self):
        assert _normalize_query("delete files") == "v4:delete files"

    def test_empty_string(self):
        assert _normalize_query("") == "v4:"


class TestSearchResult:
    def test_dataclass_fields(self):
        r = SearchResult(
            command_template="ls -la",
            pattern_text="list files",
            explanation="Shows all",
            command_name="ls",
            command_description="List directory",
            synopsis="ls [opts]",
            distance=0.5,
        )
        assert r.command_template == "ls -la"
        assert r.distance == 0.5

    def test_optional_explanation(self):
        r = SearchResult(
            command_template="pwd",
            pattern_text="current dir",
            explanation=None,
            command_name="pwd",
            command_description="Print working directory",
            synopsis="pwd",
            distance=0.1,
        )
        assert r.explanation is None


class TestSearch:
    @pytest.mark.parametrize("query,semantic", [
        ("скопируй файл README.md в папку ~/backup", "скопируй файл в папку"),
        ("удалить файлы test.tar.gz large_file.dat", "удалить файлы"),
    ])
    def test_argument_search_ignores_old_cached_vectors(self, populated_db, embedding_bytes, random_embedding, query, semantic):
        from cf.db import cache_query

        path, conn = populated_db
        cache_query(conn, "v3:" + query, embedding_bytes(1))
        with patch("cf.embeddings.encode_text", return_value=random_embedding(2)) as encode:
            first = search(query, db_path=path)
            repeated = search(query, db_path=path)
        encode.assert_called_once_with(semantic)
        assert first == repeated

    @pytest.mark.parametrize("first,second", [("delete files", "do not delete files"),
                                            ("copy a into b", "copy b into a"),
                                            ("read README.md", "read readme.md"),
                                            ("найди 'a  b'", "найди 'a b'")])
    def test_distinct_intents_are_encoded_separately_in_the_real_cache(self, populated_db, random_embedding, first, second):
        path, conn = populated_db
        with patch("cf.embeddings.encode_text", side_effect=[random_embedding(1), random_embedding(2)]) as encode:
            initial = search(first, db_path=path)
            search(second, db_path=path)
            repeated = search(first, db_path=path)
        assert encode.call_count == 2
        assert repeated == initial
        keys = {row[0] for row in conn.execute("SELECT query_text FROM query_cache")}
        assert keys == {_normalize_query(first), _normalize_query(second)}

    def test_cached_search_does_not_load_or_require_the_model(self, populated_db, embedding_bytes):
        from cf.db import cache_query, store_index_identity
        path, conn = populated_db
        store_index_identity(conn)
        cache_query(conn, _normalize_query("покажи файлы"), embedding_bytes(1))
        with patch("cf.embeddings.encode_text", side_effect=AssertionError("cache hit must not encode")), \
                patch("cf.embeddings.model_fingerprint", side_effect=AssertionError("cache hit must not read model weights")):
            assert search("покажи файлы", db_path=path)

    @patch("cf.search.get_connection")
    @patch("cf.search.init_db")
    @patch("cf.search.get_cached_query", return_value=None)
    @patch("cf.search.cache_query")
    @patch("cf.search.search_similar")
    def test_deduplicates_by_template(
        self, mock_search_sim, mock_cache_q, mock_get_cache, mock_init, mock_conn
    ):
        mock_connection = MagicMock()
        mock_conn.return_value = mock_connection

        mock_search_sim.return_value = [
            {
                "command_template": "ls -la",
                "pattern_text": "list all files",
                "explanation": "first match",
                "command_name": "ls",
                "command_description": "List",
                "synopsis": "ls",
                "distance": 0.1,
            },
            {
                "command_template": "ls -la",
                "pattern_text": "show all files",
                "explanation": "duplicate template",
                "command_name": "ls",
                "command_description": "List",
                "synopsis": "ls",
                "distance": 0.2,
            },
            {
                "command_template": "ls -a",
                "pattern_text": "hidden files",
                "explanation": None,
                "command_name": "ls",
                "command_description": "List",
                "synopsis": "ls",
                "distance": 0.3,
            },
        ]

        with patch("cf.embeddings.encode_text") as mock_encode, \
             patch("cf.embeddings.to_bytes") as mock_to_bytes:
            mock_encode.return_value = np.zeros(EMBEDDING_DIM, dtype=np.float32)
            mock_to_bytes.return_value = b"\x00" * (EMBEDDING_DIM * 4)

            results = search("list files", top_k=10, db_path="/fake/path")

        assert len(results) == 2
        templates = [r.command_template for r in results]
        assert "ls -la" in templates
        assert "ls -a" in templates

    @patch("cf.search.get_connection")
    @patch("cf.search.init_db")
    @patch("cf.search.get_cached_query", return_value=None)
    @patch("cf.search.cache_query")
    @patch("cf.search.search_similar")
    def test_keeps_lowest_distance_on_dedup(
        self, mock_search_sim, mock_cache_q, mock_get_cache, mock_init, mock_conn
    ):
        mock_conn.return_value = MagicMock()

        mock_search_sim.return_value = [
            {
                "command_template": "ls -la",
                "pattern_text": "second match (worse)",
                "explanation": None,
                "command_name": "ls",
                "command_description": "List",
                "synopsis": "ls",
                "distance": 0.5,
            },
            {
                "command_template": "ls -la",
                "pattern_text": "first match (better)",
                "explanation": None,
                "command_name": "ls",
                "command_description": "List",
                "synopsis": "ls",
                "distance": 0.1,
            },
        ]

        with patch("cf.embeddings.encode_text") as mock_encode, \
             patch("cf.embeddings.to_bytes") as mock_to_bytes:
            mock_encode.return_value = np.zeros(EMBEDDING_DIM, dtype=np.float32)
            mock_to_bytes.return_value = b"\x00" * (EMBEDDING_DIM * 4)

            results = search("list files", top_k=10, db_path="/fake/path")

        assert len(results) == 1
        assert results[0].distance == 0.1
        assert results[0].pattern_text == "first match (better)"

    @patch("cf.search.get_connection")
    @patch("cf.search.init_db")
    @patch("cf.search.get_cached_query")
    @patch("cf.search.search_similar", return_value=[])
    def test_uses_cached_embedding(
        self, mock_search_sim, mock_get_cache, mock_init, mock_conn
    ):
        mock_conn.return_value = MagicMock()
        cached_bytes = b"\x01" * (EMBEDDING_DIM * 4)
        mock_get_cache.return_value = cached_bytes

        results = search("test query", db_path="/fake/path")

        mock_search_sim.assert_called_once()
        call_args = mock_search_sim.call_args
        assert call_args[0][1] == cached_bytes

    @patch("cf.search.get_connection")
    @patch("cf.search.init_db")
    @patch("cf.search.get_cached_query", return_value=None)
    @patch("cf.search.cache_query")
    @patch("cf.search.search_similar")
    def test_respects_top_k(
        self, mock_search_sim, mock_cache_q, mock_get_cache, mock_init, mock_conn
    ):
        mock_conn.return_value = MagicMock()

        mock_search_sim.return_value = [
            {
                "command_template": f"cmd{i}",
                "pattern_text": f"pattern {i}",
                "explanation": None,
                "command_name": f"cmd{i}",
                "command_description": f"desc {i}",
                "synopsis": f"cmd{i}",
                "distance": float(i) / 10,
            }
            for i in range(10)
        ]

        with patch("cf.embeddings.encode_text") as mock_encode, \
             patch("cf.embeddings.to_bytes") as mock_to_bytes:
            mock_encode.return_value = np.zeros(EMBEDDING_DIM, dtype=np.float32)
            mock_to_bytes.return_value = b"\x00" * (EMBEDDING_DIM * 4)

            results = search("test", top_k=3, db_path="/fake/path")

        assert len(results) == 3

    @patch("cf.search.get_connection")
    @patch("cf.search.init_db")
    @patch("cf.search.get_cached_query", return_value=None)
    @patch("cf.search.cache_query")
    @patch("cf.search.search_similar", return_value=[])
    def test_empty_results(
        self, mock_search_sim, mock_cache_q, mock_get_cache, mock_init, mock_conn
    ):
        mock_conn.return_value = MagicMock()

        with patch("cf.embeddings.encode_text") as mock_encode, \
             patch("cf.embeddings.to_bytes") as mock_to_bytes:
            mock_encode.return_value = np.zeros(EMBEDDING_DIM, dtype=np.float32)
            mock_to_bytes.return_value = b"\x00" * (EMBEDDING_DIM * 4)

            results = search("gibberish query xyz", db_path="/fake/path")

        assert results == []
