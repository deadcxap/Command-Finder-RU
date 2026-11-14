from dataclasses import dataclass

from cf.db import cache_query, get_cached_query, get_connection, init_db, search_similar

def _normalize_query(query: str) -> str:
    return "v2:" + query.strip()


@dataclass
class SearchResult:
    command_template: str
    pattern_text: str
    explanation: str | None
    command_name: str
    command_description: str
    synopsis: str
    distance: float
    destructive: bool = False


def search(query: str, top_k: int = 10, db_path=None) -> list[SearchResult]:
    from cf.config import DB_PATH
    db_path = db_path or DB_PATH

    conn = get_connection(db_path)
    try:
        init_db(conn)
        cache_key = _normalize_query(query)
        cached = get_cached_query(conn, cache_key)
        if cached is not None:
            embedding_bytes = cached
        else:
            from cf.embeddings import encode_text, to_bytes
            embedding = encode_text(query)
            embedding_bytes = to_bytes(embedding)
            cache_query(conn, cache_key, embedding_bytes)
        raw = search_similar(conn, embedding_bytes, top_k=top_k * 2)
    finally:
        conn.close()

    # Deduplicate: keep best (lowest distance) match per command_template.
    # OR the destructive flag across dupes so any destructive marker wins.
    seen = {}
    for r in raw:
        key = r["command_template"]
        prior = seen.get(key)
        if prior is None or r["distance"] < prior["distance"]:
            seen[key] = r
        if prior is not None and r.get("destructive"):
            seen[key]["destructive"] = True

    # Sort by distance, take top_k
    results = sorted(seen.values(), key=lambda r: r["distance"])[:top_k]

    return [
        SearchResult(
            command_template=r["command_template"],
            pattern_text=r["pattern_text"],
            explanation=r["explanation"],
            command_name=r["command_name"],
            command_description=r["command_description"],
            synopsis=r["synopsis"],
            distance=r["distance"],
            destructive=r.get("destructive", False),
        )
        for r in results
    ]
