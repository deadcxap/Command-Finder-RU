import json
import re
import sqlite3
from pathlib import Path

import sqlite_vec

from cf.config import DB_PATH, EMBEDDING_DIM, MAX_TOKENS, MODEL_NAME, MODEL_PRECISION, MODEL_REVISION, MODEL_SPEC, ONNX_DIR


def get_connection(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def index_identity() -> dict:
    return {"model": MODEL_NAME, "revision": MODEL_REVISION, "dimensions": EMBEDDING_DIM,
            "precision": MODEL_PRECISION, "max_tokens": MAX_TOKENS,
            "quantization": MODEL_SPEC["quantization"], "query_prefix": MODEL_SPEC["query_prefix"],
            "document_prefix": MODEL_SPEC["document_prefix"]}


def _incompatible_index() -> RuntimeError:
    return RuntimeError("База создана для другой модели или размерности. Выполните cf --seed --force.")


def _model_checksums() -> dict | None:
    try:
        value = json.loads((ONNX_DIR / "checksums.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError("Повреждены контрольные суммы модели. Выполните cf --prepare-model --force.") from error
    if not isinstance(value, dict):
        raise ValueError("Контрольные суммы модели должны содержать JSON-объект")
    return value


def _check_index_compatibility(conn: sqlite3.Connection) -> None:
    existing = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'pattern_embeddings'").fetchone()
    if existing:
        dimension = re.search(r"embedding\s+FLOAT\s*\[\s*(\d+)\s*\]", existing[0] or "", re.IGNORECASE)
        if dimension is None or int(dimension.group(1)) != EMBEDDING_DIM:
            raise _incompatible_index()
    marked = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'index_metadata'").fetchone()
    if marked:
        try:
            row = conn.execute("SELECT value FROM index_metadata WHERE key = 'model_identity'").fetchone()
            if row and json.loads(row[0]) != index_identity():
                raise _incompatible_index()
            saved_checksums = conn.execute("SELECT value FROM index_metadata WHERE key = 'model_checksums'").fetchone()
            if saved_checksums:
                current = _model_checksums()
                if current is not None and json.loads(saved_checksums[0]) != current:
                    raise ValueError("Файлы модели изменились. Выполните cf --seed --force.")
        except (sqlite3.DatabaseError, json.JSONDecodeError, TypeError) as error:
            raise _incompatible_index() from error


def init_db(conn: sqlite3.Connection, *, commit: bool = True) -> None:
    _check_index_compatibility(conn)
    schema = f"""
        CREATE TABLE IF NOT EXISTS commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            synopsis TEXT NOT NULL,
            description TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            command_id INTEGER NOT NULL REFERENCES commands(id) ON DELETE CASCADE,
            pattern_type TEXT NOT NULL,
            text TEXT NOT NULL,
            command_template TEXT NOT NULL,
            explanation TEXT,
            destructive INTEGER NOT NULL DEFAULT 0,
            arguments TEXT NOT NULL DEFAULT '{{}}'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS pattern_embeddings USING vec0(
            pattern_id INTEGER PRIMARY KEY,
            embedding FLOAT[{EMBEDDING_DIM}]
        );

        CREATE INDEX IF NOT EXISTS idx_patterns_command_id
            ON patterns(command_id);

        CREATE TABLE IF NOT EXISTS query_cache (
            query_text TEXT PRIMARY KEY,
            embedding BLOB NOT NULL
        );

        CREATE TABLE IF NOT EXISTS index_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """
    for statement in schema.split(";"):
        if statement.strip():
            conn.execute(statement)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(patterns)")}
    if "destructive" not in cols:
        conn.execute(
            "ALTER TABLE patterns ADD COLUMN destructive INTEGER NOT NULL DEFAULT 0"
        )
    if "arguments" not in cols:
        conn.execute("ALTER TABLE patterns ADD COLUMN arguments TEXT NOT NULL DEFAULT '{}'")
    if commit:
        conn.commit()


def store_index_identity(conn: sqlite3.Connection, fingerprint: str | None = None) -> None:
    conn.execute("INSERT OR REPLACE INTO index_metadata (key, value) VALUES ('model_identity', ?)",
                 (json.dumps(index_identity(), sort_keys=True),))
    if fingerprint is not None:
        conn.execute("INSERT OR REPLACE INTO index_metadata (key, value) VALUES ('model_fingerprint', ?)",
                     (fingerprint,))
    checksums = _model_checksums()
    if checksums is not None:
        conn.execute("INSERT OR REPLACE INTO index_metadata (key, value) VALUES ('model_checksums', ?)",
                     (json.dumps(checksums, sort_keys=True, separators=(",", ":")),))


def reset_index(conn: sqlite3.Connection) -> None:
    for table in ("pattern_embeddings", "patterns", "commands", "query_cache", "index_metadata"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    init_db(conn, commit=False)


def get_cached_query(conn: sqlite3.Connection, query: str) -> bytes | None:
    row = conn.execute(
        "SELECT embedding FROM query_cache WHERE query_text = ?", (query,)
    ).fetchone()
    if row and isinstance(row[0], bytes) and len(row[0]) == EMBEDDING_DIM * 4:
        return row[0]
    return None


def cache_query(conn: sqlite3.Connection, query: str, embedding_bytes: bytes) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO query_cache (query_text, embedding) VALUES (?, ?)",
        (query, embedding_bytes),
    )
    conn.commit()


def bulk_load_pragmas(conn: sqlite3.Connection) -> None:
    """Tune PRAGMAs for fast bulk inserts. Call before seeding."""
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-64000")  # 64MB cache


def restore_pragmas(conn: sqlite3.Connection) -> None:
    """Restore safe PRAGMAs after bulk load."""
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=DEFAULT")
    conn.execute("PRAGMA cache_size=-2000")


def insert_commands_batch(conn: sqlite3.Connection,
                          rows: list[tuple]) -> list[int]:
    """Batch insert commands. rows: [(name, category, synopsis, description), ...]"""
    conn.executemany(
        "INSERT INTO commands (name, category, synopsis, description) VALUES (?, ?, ?, ?)",
        rows,
    )
    # Retrieve the auto-generated IDs for the batch
    last_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return list(range(last_id - len(rows) + 1, last_id + 1))


def insert_patterns_batch(conn: sqlite3.Connection,
                          rows: list[tuple]) -> list[int]:
    """Пакетная запись шаблонов с JSON-описанием аргументов последним полем."""
    conn.executemany(
        "INSERT INTO patterns (command_id, pattern_type, text, command_template, explanation, destructive, arguments) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    last_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return list(range(last_id - len(rows) + 1, last_id + 1))


def insert_embeddings_batch(conn: sqlite3.Connection,
                            rows: list[tuple]) -> None:
    """Batch insert embeddings. rows: [(pattern_id, embedding_bytes), ...]"""
    conn.executemany(
        "INSERT INTO pattern_embeddings (pattern_id, embedding) VALUES (?, ?)",
        rows,
    )


def search_similar(conn: sqlite3.Connection, query_embedding_bytes: bytes,
                   top_k: int = 10) -> list[dict]:
    rows = conn.execute("""
        SELECT
            p.command_template,
            p.text,
            p.explanation,
            c.name,
            c.description,
            c.synopsis,
            pe.distance,
            p.destructive,
            p.arguments
        FROM pattern_embeddings pe
        JOIN patterns p ON p.id = pe.pattern_id
        JOIN commands c ON c.id = p.command_id
        WHERE pe.embedding MATCH ?
          AND k = ?
        ORDER BY pe.distance
    """, (query_embedding_bytes, top_k)).fetchall()

    return [
        {
            "command_template": r[0],
            "pattern_text": r[1],
            "explanation": r[2],
            "command_name": r[3],
            "command_description": r[4],
            "synopsis": r[5],
            "distance": r[6],
            "destructive": bool(r[7]),
            "arguments": json.loads(r[8]),
        }
        for r in rows
    ]


def get_stats(conn: sqlite3.Connection) -> dict:
    cmds = conn.execute("SELECT COUNT(*) FROM commands").fetchone()[0]
    pats = conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0]
    return {"commands": cmds, "patterns": pats}


def get_detailed_stats(conn: sqlite3.Connection) -> dict:
    cmds = conn.execute("SELECT COUNT(*) FROM commands").fetchone()[0]
    pats = conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0]
    cats = conn.execute("SELECT COUNT(DISTINCT category) FROM commands").fetchone()[0]
    cached = conn.execute("SELECT COUNT(*) FROM query_cache").fetchone()[0]
    embeds = conn.execute("SELECT COUNT(*) FROM pattern_embeddings").fetchone()[0]

    by_category = conn.execute("""
        SELECT c.category,
               COUNT(DISTINCT c.id) AS commands,
               COUNT(p.id) AS patterns
        FROM commands c
        LEFT JOIN patterns p ON p.command_id = c.id
        GROUP BY c.category
        ORDER BY c.category
    """).fetchall()

    return {
        "commands": cmds,
        "patterns": pats,
        "categories": cats,
        "cached_queries": cached,
        "embeddings": embeds,
        "embedding_dim": EMBEDDING_DIM,
        "by_category": [
            {"category": r[0], "commands": r[1], "patterns": r[2]}
            for r in by_category
        ],
    }


def clear_all(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM pattern_embeddings")
    conn.execute("DELETE FROM patterns")
    conn.execute("DELETE FROM commands")
    conn.execute("DELETE FROM query_cache")
    conn.execute("DELETE FROM index_metadata")
    conn.commit()
