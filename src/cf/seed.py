import json
import sys

import numpy as np
from pathlib import Path

from cf.config import DATA_DIR, EMBEDDING_DIM
from cf.db import (
    bulk_load_pragmas,
    get_connection,
    init_db,
    get_stats,
    reset_index,
    store_index_identity,
    insert_commands_batch,
    insert_embeddings_batch,
    insert_patterns_batch,
    restore_pragmas,
)
from cf.embeddings import _onnx_available, encode_batch, model_fingerprint, to_bytes


def load_seed_files(data_dir: Path = DATA_DIR) -> list[dict]:
    files = sorted(data_dir.glob("*.json"))
    if not files:
        print(f"Файлы словаря не найдены: {data_dir}", file=sys.stderr)
        return []
    all_data = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
            all_data.append(data)
        print(f"  Загружен {f.name}: {len(data['commands'])} команд", file=sys.stderr)
    return all_data


def seed_database(db_path=None, force: bool = False) -> dict:
    from cf.config import DB_PATH
    db_path = db_path or DB_PATH
    conn = get_connection(db_path)
    try:
        if not force:
            init_db(conn)
            existing = get_stats(conn)
            if existing["commands"] or existing["patterns"]:
                print("База уже заполнена. Для перестроения выполните cf --seed --force.", file=sys.stderr)
                return existing

        print("Загрузка словаря...", file=sys.stderr)
        all_data = load_seed_files()
        if not all_data:
            print("Словарь пуст; существующая база не изменена.", file=sys.stderr)
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            return get_stats(conn) if {"commands", "patterns"}.issubset(tables) else {"commands": 0, "patterns": 0}

        cmd_rows = []
        cmd_seen = {}
        pat_meta = []
        for category_data in all_data:
            category = category_data["category"]
            for cmd in category_data["commands"]:
                key = (category, cmd["name"])
                if key not in cmd_seen:
                    cmd_seen[key] = len(cmd_rows)
                    cmd_rows.append((cmd["name"], category, cmd["synopsis"], cmd["description"]))
                cmd_idx = cmd_seen[key]
                for pat in cmd["patterns"]:
                    pat_meta.append((cmd_idx, pat["type"], pat["text"], pat["command"],
                                     pat.get("explanation"), 1 if pat.get("destructive") else 0))

        texts = [pattern[2] for pattern in pat_meta]
        print(f"Кодирование описаний: {len(texts)}...", file=sys.stderr)
        fingerprint = model_fingerprint() if _onnx_available() else None
        vectors = np.asarray(encode_batch(texts), dtype=np.float32)
        if vectors.shape != (len(texts), EMBEDDING_DIM) or not np.isfinite(vectors).all():
            raise ValueError("Модель вернула неверные векторы; существующая база не изменена")
        if fingerprint is not None and model_fingerprint() != fingerprint:
            raise RuntimeError("Модель изменилась во время индексации; повторите cf --seed --force")

        print("Пакетная запись в базу...", file=sys.stderr)
        bulk_load_pragmas(conn)
        try:
            conn.execute("BEGIN IMMEDIATE")
            if force:
                reset_index(conn)
            cmd_ids = insert_commands_batch(conn, cmd_rows)
            pat_rows = [(cmd_ids[ci], ptype, text, template, explanation, destructive)
                        for ci, ptype, text, template, explanation, destructive in pat_meta]
            pat_ids = insert_patterns_batch(conn, pat_rows)
            insert_embeddings_batch(conn, [(pat_id, to_bytes(vectors[i])) for i, pat_id in enumerate(pat_ids)])
            store_index_identity(conn, fingerprint)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            restore_pragmas(conn)

        stats = {"commands": len(cmd_rows), "patterns": len(pat_meta)}
        print(f"Готово: {stats['commands']} команд, {stats['patterns']} описаний.", file=sys.stderr)
        return stats
    finally:
        conn.close()
