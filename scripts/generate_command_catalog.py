"""Сформировать каталог поддерживаемых команд из JSON-словаря."""

import argparse
import html
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "cf" / "data" / "commands"
OUTPUT = ROOT / "docs" / "commands.md"
CATEGORIES = (
    ("filesystem", "Файлы и каталоги"),
    ("text_processing", "Обработка текста"),
    ("search", "Поиск"),
    ("networking", "Сеть"),
    ("system", "Система"),
    ("process", "Процессы"),
    ("permissions", "Права и пользователи"),
    ("compression", "Архивы и сжатие"),
    ("disk", "Диски"),
    ("git", "Git"),
    ("docker", "Docker и Kubernetes"),
    ("package_managers", "Менеджеры пакетов"),
    ("perl_oneliners", "Однострочные команды Perl"),
    ("macos", "macOS"),
    ("misc", "Другие инструменты"),
)


def number(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def cell(value: str) -> str:
    return html.escape(" ".join(value.split()), quote=False).replace("|", r"\|")


def code(value: str) -> str:
    value = " ".join(value.splitlines())
    fence = "`" * (max((len(run) for run in re.findall(r"`+", value)), default=0) + 1)
    padding = " " if value.startswith(("`", " ")) or value.endswith(("`", " ")) else ""
    return f"{fence}{padding}{value}{padding}{fence}".replace("|", r"\|")


def load_categories() -> dict[str, list[dict]]:
    categories = {}
    for path in sorted(DATA.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["category"] != path.stem:
            raise ValueError(f"Категория не совпадает с именем файла: {path.name}")
        groups = {}
        for command in data["commands"]:
            if command["name"] not in groups:
                groups[command["name"]] = {**command, "patterns": []}
            groups[command["name"]]["patterns"].extend(command["patterns"])
        categories[path.stem] = list(groups.values())
    unknown = set(categories) - {category for category, _ in CATEGORIES}
    if unknown:
        raise ValueError("Добавьте русское название категории в CATEGORIES: " + ", ".join(sorted(unknown)))
    return categories


def render() -> str:
    categories = load_categories()
    commands = [command for groups in categories.values() for command in groups]
    patterns = [pattern for command in commands for pattern in command["patterns"]]
    templates = {pattern["command"] for pattern in patterns}
    automatic = {pattern["command"] for pattern in patterns if pattern.get("arguments")}
    lines = [
        "# Каталог поддерживаемых команд",
        "",
        f"В словаре **{number(len(commands))} команды и группы команд**, "
        f"**{number(len(patterns))} формулировки задач** в **{len(categories)} категориях**. "
        f"Они ссылаются на **{number(len(templates))} разных строк шаблонов**.",
        "",
        "Одна строка таблицы соответствует записи словаря, а не обязательно отдельной программе. "
        "Например, `git commit` — подкоманда Git, а `perl text substitution` — тематическая группа "
        "рецептов Perl. Одна утилита может встречаться в разных категориях для разных задач.",
        "",
        "Столбец «Автоподстановка» показывает число размеченных шаблонов относительно всех шаблонов "
        "этой записи. «—» означает, что автоматической подстановки аргументов нет; найденный шаблон "
        "можно отредактировать вручную. Даже при наличии разметки она поддерживается не во всех "
        f"вариантах команды: всего размечено **{len(automatic)} шаблонов**. "
        "Синтаксис в таблице описывает утилиту в целом; конкретные варианты и примеры берутся из словаря.",
        "",
        "Примеры под каждой категорией приведены из словаря. Метка ⚠ у примера соответствует "
        "признаку `destructive`: меню запрашивает подтверждение. Часть утилит относится к конкретной "
        "ОС или устанавливается отдельно. Назначение, разбор аргументов и ограничения поиска описаны "
        "в [README](../README.md#использование).",
        "",
        "## Категории",
        "",
        "| Категория | Команд и групп | Формулировок |",
        "|---|---:|---:|",
    ]
    for category, title in CATEGORIES:
        if category in categories:
            groups = categories[category]
            count = sum(len(command["patterns"]) for command in groups)
            lines.append(f"| [{title}](#{category}) | {len(groups)} | {number(count)} |")
    for category, title in CATEGORIES:
        if category not in categories:
            continue
        groups = categories[category]
        lines.extend([
            "", f'<a id="{category}"></a>', "", f"## {title}", "",
            f"Источник: [{category}.json](../src/cf/data/commands/{category}.json).", "",
            "| Команда / группа | Что помогает сделать | Синтаксис | Автоподстановка |",
            "|---|---|---|---|",
        ])
        for command in groups:
            unique = {pattern["command"] for pattern in command["patterns"]}
            supported = {pattern["command"] for pattern in command["patterns"] if pattern.get("arguments")}
            coverage = f"{len(supported)} из {len(unique)}" if supported else "—"
            lines.append(f"| {code(command['name'])} | {cell(command['description'])} | "
                         f"{code(command['synopsis'])} | {coverage} |")
        examples = [pattern for command in groups for pattern in command["patterns"]]
        example = min(enumerate(examples), key=lambda item: (
            bool(item[1].get("destructive")), not bool(item[1].get("arguments")),
            "\n" in item[1]["command"], item[0],
        ))[1]
        mark = "⚠ " if example.get("destructive") else ""
        lines.extend(["", f"Пример запроса: {code(example['text'])}.", "",
                      f"{mark}Шаблон:", "", "```bash", example["command"], "```"])
    lines.extend([
        "", "## Обновление каталога", "",
        "Каталог формируется из `src/cf/data/commands/*.json`. После изменения словаря выполните "
        "из корня проекта:", "", "```bash",
        ".venv/bin/python scripts/generate_command_catalog.py",
        ".venv/bin/python scripts/generate_command_catalog.py --check", "```", "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Проверить актуальность каталога без записи")
    args = parser.parse_args()
    content = render()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != content:
            print("Каталог устарел. Выполните: python scripts/generate_command_catalog.py")
            return 1
        print("Каталог соответствует JSON-словарю.")
        return 0
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"Каталог записан: {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
