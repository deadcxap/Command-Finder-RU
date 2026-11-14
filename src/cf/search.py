from dataclasses import dataclass

from cf.arguments import search_query, unsupported_path_constraint
from cf.db import cache_query, get_cached_query, get_connection, init_db, search_similar
from cf.ranking import rank_matches

_CANDIDATE_POOL = 200

def _normalize_query(query: str) -> str:
    return "v5:" + query.strip()


class UnsupportedQueryError(ValueError):
    """Запрос содержит ограничения, которые поиск не может надёжно сохранить."""


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
    filled_command: str | None = None
    missing_arguments: tuple[str, ...] = ()

    @property
    def command(self) -> str:
        return self.filled_command if self.filled_command is not None else self.command_template


def search(query: str, top_k: int = 10, db_path=None) -> list[SearchResult]:
    constraint_error = unsupported_path_constraint(query)
    if constraint_error:
        raise UnsupportedQueryError(constraint_error)
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
            embedding = encode_text(search_query(query))
            embedding_bytes = to_bytes(embedding)
            cache_query(conn, cache_key, embedding_bytes)
        raw = search_similar(conn, embedding_bytes, top_k=max(_CANDIDATE_POOL, top_k * 2))
    finally:
        conn.close()

    results = rank_matches(query, raw)[:top_k]

    matches = []
    for r in results:
        matches.append(SearchResult(
            command_template=r["command_template"],
            pattern_text=r["pattern_text"],
            explanation=r["explanation"],
            command_name=r["command_name"],
            command_description=r["command_description"],
            synopsis=r["synopsis"],
            distance=r["distance"],
            destructive=r.get("destructive", False),
            filled_command=r["filled_command"],
            missing_arguments=r["missing_arguments"],
        ))
    return matches
