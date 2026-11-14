"""Извлечение явно указанных путей и заполнение параметров команд."""

from dataclasses import dataclass
import re
import shlex


_PLACEHOLDER = re.compile(r"<([a-z][a-z0-9_]*)>")
_ROLES = {"path", "source", "destination", "archive"}
_TOKENS = re.compile(r'''"((?:\\.|[^"\\\n])*)"|'([^'\n]*)'|«([^»\n]*)»|([^\s"'«»]+)''')
_ARCHIVE = re.compile(r"\.(?:tar(?:\.(?:gz|bz2|xz|zst))?|tgz|tbz2?|txz|zip|7z|rar|gz|bz2|xz|zst)$", re.I)
_NOUN = re.compile(r"^(?:папк[а-яё]*|каталог[а-яё]*|файл[а-яё]*|архив[а-яё]*)$", re.I)
_FILE = re.compile(r"^[^\s.]+(?:\.[^\s.]+)+$")
_FORMATS = {"tar", "tar.gz", "tar.bz2", "tar.xz", "tar.zst", "tgz", "tbz", "tbz2", "txz",
            "zip", "7z", "rar", "gz", "bz2", "xz", "zst"}
_NON_VALUES = {
    "в", "из", "на", "с", "со", "для", "и", "или", "без", "по", "не", "а", "но",
    "целиком", "полностью", "подробно", "рекурсивно", "кратко", "быстро", "здесь", "тут", "там",
    "весь", "всю", "все", "всё", "всех", "всеми", "каждый", "каждую", "размер", "размеры",
    "текущий", "текущую", "текущей", "текущего", "текущем", "родительский", "родительскую",
    "содержимое", "содержимым", "содержимого", "сжатие", "сжатием", "сжатия", "создай", "покажи",
    "найди", "удали", "посчитай", "скопируй", "перемести", "распакуй", "упакуй", "прочитай",
    "построчно", "включая", "исключая", "сначала", "потом", "затем", "рядом", "только", "также",
    "вместе", "отдельно", "скрытые", "обычные", "большие", "маленькие", "размером", "старше",
    "младше", "больше", "меньше", "крупнее", "крупные", "глубиной", "пожалуйста",
}


def validate_arguments(template: str, arguments: dict[str, str]) -> None:
    if not isinstance(arguments, dict):
        raise ValueError("Поле arguments должно быть объектом с ролями параметров")
    if not arguments:
        return
    if any(not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name)
           or not isinstance(role, str) or role not in _ROLES
           for name, role in arguments.items()):
        raise ValueError("Недопустимое имя или роль параметра команды")
    if len(set(arguments.values())) != len(arguments):
        raise ValueError("Роли параметров команды должны быть различными")
    matches = list(_PLACEHOLDER.finditer(template))
    if ({match[1] for match in matches} != set(arguments)
            or set(re.findall(r"<[^<>\s]+>", template)) != {match[0] for match in matches}):
        raise ValueError("Параметры arguments должны совпадать с параметрами <name> команды")
    for match in matches:
        before, after = template[:match.start()], template[match.end():]
        if (before and not before[-1].isspace()) or (after and not after[0].isspace()):
            raise ValueError("Параметр команды должен занимать отдельное слово без кавычек")
        # Проверяем также кавычки, открытые в предыдущем слове шаблона.
        try:
            shlex.split(before)
        except ValueError as error:
            raise ValueError("Параметр команды не может находиться внутри кавычек") from error


@dataclass(frozen=True)
class _Candidate:
    value: str
    hint: str | None
    archive: bool
    start: int
    end: int
    searchable: bool


def _candidates(query: str) -> list[_Candidate]:
    tokens = list(_TOKENS.finditer(query))
    position = 0
    for token in tokens:
        if query[position:token.start()].strip():
            return []
        position = token.end()
    if query[position:].strip():
        return []
    candidates = []
    for index, token in enumerate(tokens):
        quoted = token[4] is None
        value = next(group for group in token.groups() if group is not None)
        if token[1] is not None:
            value = re.sub(r'\\(["\\])', r'\1', value)
        if not quoted and value.endswith(","):
            return []
        gap = query[candidates[-1].end:token.start()].lower().split() if candidates else []
        coordinated = bool(gap and gap[0] in {"и", "или"}
                           and (len(gap) == 1 or (len(gap) == 2 and _NOUN.fullmatch(gap[1]))))
        adjacent = bool(candidates and not gap)
        preceding = [item.group(0).lower().strip(",:;") for item in tokens[max(0, index - 3):index]]
        noun = preceding[-1] if preceding and _NOUN.fullmatch(preceding[-1]) else None
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            continue
        explicit = value in {"~", ".", ".."} or "/" in value or _FILE.fullmatch(value)
        if not (quoted or explicit or noun or coordinated):
            continue
        if not quoted and (value.lower() in _NON_VALUES | _FORMATS or _NOUN.fullmatch(value)):
            continue
        context = preceding[:-1] if noun else preceding
        preposition = context[-1] if context else None
        hint = None
        if preposition in {"из", "с", "со"}:
            hint = "source"
        elif preposition in {"в", "во"}:
            hint = "destination"
        elif noun and noun.startswith("архив"):
            hint = "archive"
        if hint is None and (coordinated or adjacent):
            hint = candidates[-1].hint
        archive = bool(_ARCHIVE.search(value)) or bool(noun and noun.startswith("архив"))
        searchable = (value in {"~", ".", ".."} or value.startswith(("/", "./", "../", "~/"))
                      or bool(noun) or hint is not None or archive)
        candidates.append(_Candidate(value, hint, archive, token.start(), token.end(), searchable))
    return candidates


def search_query(query: str) -> str:
    """Убрать явно указанные пути из текста, сохранив формат архива."""
    if any(ord(char) < 32 or ord(char) == 127 for char in query):
        return query
    replacements = []
    for candidate in _candidates(query):
        if not candidate.searchable:
            continue
        if any(char in candidate.value for char in "*?[]"):
            if "/" not in candidate.value:
                continue
            replacements.append((candidate.start, candidate.end, candidate.value.rsplit("/", 1)[1]))
            continue
        extension = _ARCHIVE.search(candidate.value)
        replacement = extension[0][1:].lower() if extension else ""
        replacements.append((candidate.start, candidate.end, replacement))
    normalized = query
    for start, end, replacement in reversed(replacements):
        normalized = normalized[:start] + replacement + normalized[end:]
    return " ".join(normalized.split()) if replacements else query


def _quote_path(value: str) -> str:
    if value == "~":
        return "~"
    if value.startswith("~/"):
        suffix = value[2:]
        return "~/" + (shlex.quote(suffix) if suffix else "")
    if value.startswith("-"):
        value = "./" + value
    return shlex.quote(value)


def bind_arguments(query: str, template: str,
                   arguments: dict[str, str]) -> tuple[str, tuple[str, ...]]:
    validate_arguments(template, arguments)
    if not arguments:
        return template, ()
    candidates = [] if any(ord(char) < 32 or ord(char) == 127 for char in query) else _candidates(query)
    values: dict[str, str] = {}
    used: set[int] = set()

    def choose(role: str, indexes: list[int]) -> None:
        if indexes and len({candidates[index].value for index in indexes}) == 1:
            values[role] = candidates[indexes[0]].value
            used.update(indexes)

    roles = set(arguments.values())
    if roles == {"path"}:
        choose("path", list(range(len(candidates))))
    else:
        if "archive" in roles:
            choose("archive", [index for index, candidate in enumerate(candidates)
                               if (candidate.archive or candidate.hint == "archive")
                               and not ("destination" in roles and candidate.hint == "destination")
                               and not ("source" in roles and candidate.hint == "source")])
        for role in ("source", "destination"):
            if role in roles:
                choose(role, [index for index, candidate in enumerate(candidates)
                              if index not in used and candidate.hint == role])
        for role in ("source", "destination", "path"):
            if role not in roles or role in values:
                continue
            remaining = [index for index, candidate in enumerate(candidates)
                         if index not in used and candidate.hint in (None, role)]
            if len(roles) == 1 or values or role == "source":
                choose(role, remaining)

    unused = [candidate for index, candidate in enumerate(candidates) if index not in used
              and candidate.hint is None
              and not ("archive" in roles and "archive" not in values and candidate.archive)]
    if unused:
        for role in ("source", "destination", "path"):
            values.pop(role, None)

    def substitute(match: re.Match) -> str:
        role = arguments[match[1]]
        return _quote_path(values[role]) if role in values else match[0]

    command = _PLACEHOLDER.sub(substitute, template)
    missing = tuple(name for name, role in arguments.items() if role not in values)
    return command, missing
