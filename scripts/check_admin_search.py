"""Проверка реального локального поиска по фиксированным административным запросам."""

import argparse
import json
from pathlib import Path
import sys


FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "admin_queries.json"
GROUPS = ("reference", "held_out", "constraints")


def load_cases(path: Path) -> tuple[int, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("top_k"), int) or data["top_k"] < 1:
        raise ValueError("Неверная версия набора или число результатов top_k")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Набор должен содержать непустой список cases")
    ids = set()
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get("id"), str)
                or not case["id"] or case["id"] in ids
                or case.get("group") not in GROUPS
                or not isinstance(case.get("query"), str) or not case["query"].strip()):
            raise ValueError("Неверный запрос, группа или повторяющийся id в наборе")
        ids.add(case["id"])
        commands, error = case.get("expected_commands"), case.get("expected_error")
        if commands is not None:
            if error is not None or not isinstance(commands, list) or not commands or any(
                    not isinstance(command, str) or not command for command in commands):
                raise ValueError(f"{case['id']}: нужен непустой список expected_commands без expected_error")
        elif (not isinstance(error, dict) or error.get("type") != "UnsupportedQueryError"
              or not isinstance(error.get("contains"), str) or not error["contains"]):
            raise ValueError(f"{case['id']}: нужны expected_commands или описание UnsupportedQueryError")
    return data["top_k"], cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=FIXTURES, help="Путь к фиксированному набору JSON")
    parser.add_argument("--db", type=Path, help="Отдельная база; по умолчанию база приложения CF_DB_DIR")
    parser.add_argument("--group", choices=GROUPS, action="append", help="Проверить выбранные группы")
    args = parser.parse_args(argv)
    try:
        top_k, cases = load_cases(args.fixtures)
    except (OSError, ValueError, TypeError) as error:
        parser.error(str(error))
    if args.group:
        cases = [case for case in cases if case["group"] in args.group]
    if not cases:
        parser.error("В выбранных группах нет запросов")

    from cf.search import UnsupportedQueryError, search

    passed = 0
    group_counts = {group: [0, 0] for group in GROUPS if any(case["group"] == group for case in cases)}
    for case in cases:
        expected_error = case.get("expected_error")
        commands = case.get("expected_commands", [])
        try:
            results = search(case["query"], top_k=top_k, db_path=args.db)
        except UnsupportedQueryError as error:
            success = (expected_error is not None
                       and type(error).__name__ == expected_error["type"]
                       and expected_error["contains"] in str(error))
            actual = f"{type(error).__name__}: {error}"
        except Exception as error:
            success = False
            actual = f"{type(error).__name__}: {error}"
        else:
            actual = results[0].command if results else "<нет результатов>"
            success = expected_error is None and bool(results) and actual in commands
        label = "PASS" if success else "FAIL"
        print(f"{label} {case['id']}: {case['query']}")
        print(f"  top: {actual}")
        if not success:
            expected = (f"{expected_error['type']} содержит {expected_error['contains']!r}"
                        if expected_error else " или ".join(commands))
            print(f"  ожидалось: {expected}")
        passed += success
        group_counts[case["group"]][0] += success
        group_counts[case["group"]][1] += 1
    print("; ".join(f"{group}: {counts[0]}/{counts[1]}" for group, counts in group_counts.items()))
    print(f"Всего: {passed}/{len(cases)}; найденные команды не исполнялись.")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
