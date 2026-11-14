"""Повторное ранжирование шаблонов по намерению и покрытию аргументов."""

import json
import re
import shlex

from cf.arguments import bind_arguments, explicit_path_values, search_query


_WORD = re.compile(r"[a-zа-яё]+|[0-9]+", re.I)
_STOP = {
    "а", "без", "бы", "в", "во", "для", "до", "же", "за", "и", "из", "или", "к", "как", "ко",
    "ли", "на", "не", "но", "о", "об", "от", "по", "под", "при", "про", "с", "со", "у", "через",
    "что", "чтобы", "это", "the", "a", "an", "to", "of", "in", "with", "and", "or", "is",
}
_GENERIC = ("файл", "папк", "папок", "каталог", "архив", "текущ", "указан", "задан", "содержим")
_GROUPS = (
    ("unpack", ("распак", "разархив", "извлек", "извлеч", "разж")),
    ("pack", ("упак", "архивир", "сожм", "сжим", "сжат")),
    ("numbering", ("номер", "нумер", "пронумер")),
    ("size", ("размер", "объём", "объем", "занима", "занят", "мест")),
    ("lines", ("строк", "строч", "построч")),
    ("remove", ("удал", "убер", "убра", "стер", "стир", "сотр", "очист")),
    ("copy", ("скоп", "копир", "копи")),
    ("move", ("перемест", "перенес", "перенос")),
    ("rename", ("переимен",)),
    ("view", ("показ", "посмотр", "вывест", "вывед", "вывод", "отобраз", "прочит", "узна")),
    ("create", ("созда", "сдела")),
    ("search", ("найти", "найд", "поиск", "искать")),
    ("count", ("посчит", "счит", "количеств", "числ")),
)
_ACTIONS = {"unpack", "pack", "remove", "copy", "move", "rename", "view", "create", "search"}
_OPPOSITES = (
    ({"pack"}, {"unpack"}),
    ({"remove"}, {"copy", "move", "rename", "view", "create", "search"}),
    ({"copy"}, {"move", "rename"}),
)


def _term(word: str) -> str | None:
    word = word.lower().replace("ё", "е")
    if word in _STOP or len(word) < 2 or word.startswith(_GENERIC):
        return None
    for term, prefixes in _GROUPS:
        if word.startswith(prefixes):
            return term
    if word.isdigit():
        return word
    # Общие начала сохраняют совпадения падежей без зависимости от морфологической библиотеки.
    return word[:6] if len(word) > 6 else word


def _terms(text: str) -> tuple[set[str], set[str]]:
    positive, negative = set(), set()
    negated = 0
    for word in _WORD.findall(text.lower()):
        if word in {"без", "не", "without", "not"}:
            negated = 2
            continue
        term = _term(word)
        if term:
            (negative if negated else positive).add(term)
        if word not in _STOP:
            negated = max(0, negated - 1)
    return positive, negative


def _weight(term: str) -> float:
    if term in _ACTIONS:
        return 0.6
    if term in {"numbering", "size"}:
        return 1.4
    return 1.0


def _lexical(query: tuple[set[str], set[str]], texts: list[str]) -> tuple[float, float]:
    wanted, avoided = query
    positive, negative = set(), set()
    for text in texts:
        yes, no = _terms(text)
        positive.update(yes)
        negative.update(no)
    denominator = sum(_weight(term) for term in wanted)
    overlap = sum(_weight(term) for term in wanted & positive) / denominator if denominator else 0.0
    conflict = bool((wanted & (negative - positive)) or (avoided & (positive - negative)))
    for first, second in _OPPOSITES:
        if ((wanted & first and positive & second and not positive & first)
                or (wanted & second and positive & first and not positive & second)):
            conflict = True
    return overlap, float(conflict)


def _coverage(paths: tuple[str, ...], command: str) -> float:
    if not paths:
        return 1.0
    try:
        words = set(shlex.split(command)[1:])
    except ValueError:
        return 0.0
    return sum(("./" + path if path.startswith("-") else path) in words for path in paths) / len(paths)


def _row_key(row: dict) -> tuple:
    return (row["distance"], row.get("pattern_text", ""),
            json.dumps(row, ensure_ascii=False, sort_keys=True))


def rank_matches(query: str, rows: list[dict]) -> list[dict]:
    """Объединить формулировки и вернуть заполненные шаблоны в стабильном порядке."""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["command_template"], []).append(row)
    query_terms = _terms(search_query(query))
    paths = explicit_path_values(query)
    rerank = any(path.startswith(("/", "~/", "./", "../")) for path in paths)
    ranked = []
    for template, group in grouped.items():
        best = dict(min(group, key=_row_key))
        best["destructive"] = any(row.get("destructive", False) for row in group)
        arguments = best.get("arguments", {})
        command, missing = bind_arguments(query, template, arguments)
        best["filled_command"] = command
        best["missing_arguments"] = missing
        score = best["distance"]
        if rerank:
            texts = sorted({row.get(field, "") or "" for row in group
                            for field in ("pattern_text", "explanation")})
            overlap, conflict = _lexical(query_terms, texts)
            coverage = _coverage(paths, command)
            unresolved = len(missing) / len(arguments) if arguments else 0.0
            score += -0.28 * overlap + 0.32 * conflict
            score += 0.45 * (1.0 - coverage) + 0.12 * unresolved
        ranked.append((score, best["distance"], template, best))
    return [row for _, _, _, row in sorted(ranked, key=lambda item: item[:3])]
