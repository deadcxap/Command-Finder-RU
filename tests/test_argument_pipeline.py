"""Параметры проходят из JSON через индекс, поиск и вывод CLI."""

import json
from unittest.mock import patch

import numpy as np
import pytest
from typer.testing import CliRunner

from cf.cli import app
from cf.config import EMBEDDING_DIM
from cf.db import get_connection, init_db
from cf.search import search
from cf.seed import load_seed_files, seed_database


@pytest.mark.parametrize("template,arguments,query,expected", [
    ("du -sh -- <directory>", {"directory": "path"},
     "посчитай размер папки ~/test", "du -sh -- ~/test"),
    ("tar -czf <archive> -- <source>", {"archive": "archive", "source": "source"},
     "создай архив primer.tar.gz из папки ~/test", "tar -czf primer.tar.gz -- ~/test"),
    ("cp -r -- <source> <destination>", {"source": "source", "destination": "destination"},
     'скопируй папку "./мои файлы" в ./backup', "cp -r -- './мои файлы' ./backup"),
])
def test_json_to_cli_with_arguments(tmp_path, monkeypatch, template, arguments, query, expected):
    data = tmp_path / "commands"
    data.mkdir()
    (data / "test.json").write_text(json.dumps({
        "category": "test", "commands": [{
            "name": "test", "synopsis": "test", "description": "Тест",
            "patterns": [{"type": "example", "text": "Описание задачи",
                          "command": template, "arguments": arguments}],
        }],
    }), encoding="utf-8")
    database = tmp_path / "cf.db"
    monkeypatch.setattr("cf.seed.load_seed_files", lambda: load_seed_files(data))
    monkeypatch.setattr("cf.config.DB_PATH", database)
    monkeypatch.setattr("cf.db.get_connection", lambda db_path=database: get_connection(db_path))
    monkeypatch.setattr("cf.db.ONNX_DIR", tmp_path / "model")
    monkeypatch.setattr("cf.seed._onnx_available", lambda: False)
    vector = np.ones(EMBEDDING_DIM, dtype=np.float32) / np.sqrt(EMBEDDING_DIM)
    with patch("cf.seed.encode_batch", return_value=vector.reshape(1, -1)):
        assert seed_database(database, force=True) == {"commands": 1, "patterns": 1}
    with patch("cf.embeddings.encode_text", return_value=vector):
        result = CliRunner().invoke(app, ["--print", query])
        assert result.exit_code == 0, result.output
        assert result.stdout.strip() == expected
        first = search(query, db_path=database)[0]
        assert first.command_template == template
        assert first.command == expected
        assert first.missing_arguments == ()
        # Подстановка относится к запросу; в базе остаётся исходный шаблон.
        other = search(query.replace("~/test", "~/other"), db_path=database)[0]
        assert other.command == expected.replace("~/test", "~/other")
    conn = get_connection(database)
    try:
        row = conn.execute("SELECT command_template, arguments FROM patterns").fetchone()
        assert row[0] == template
        assert json.loads(row[1]) == arguments
    finally:
        conn.close()


def test_old_index_without_arguments_remains_searchable(populated_db, embedding_bytes):
    path, conn = populated_db
    conn.execute("ALTER TABLE patterns DROP COLUMN arguments")
    conn.commit()
    init_db(conn)
    assert "arguments" in {r[1] for r in conn.execute("PRAGMA table_info(patterns)")}
    from cf.db import cache_query
    from cf.search import _normalize_query
    cache_query(conn, _normalize_query("покажи файлы"), embedding_bytes(0))
    assert search("покажи файлы", db_path=path)
