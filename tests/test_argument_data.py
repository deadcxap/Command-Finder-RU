"""Проверки разметки параметров в опубликованном словаре команд."""

import json
from pathlib import Path

import pytest

from cf.arguments import bind_arguments, validate_arguments


DATA = Path(__file__).parent.parent / "src/cf/data/commands"


def recipes():
    for source in sorted(DATA.glob("*.json")):
        for command in json.loads(source.read_text(encoding="utf-8"))["commands"]:
            for pattern in command["patterns"]:
                yield command["name"], pattern


def test_every_annotated_recipe_has_valid_parameters():
    covered = set()
    for name, pattern in recipes():
        if "arguments" in pattern:
            validate_arguments(pattern["command"], pattern["arguments"])
            covered.add(name)
    assert {"du", "stat", "file", "cp", "mv", "tar", "zip", "unzip", "gzip", "bzip2", "xz", "cat", "head", "tail"} <= covered


def test_equivalent_recipes_have_identical_parameter_roles():
    metadata = {}
    for _, pattern in recipes():
        previous = metadata.setdefault(pattern["command"], pattern.get("arguments", {}))
        assert previous == pattern.get("arguments", {}), pattern["command"]


@pytest.mark.parametrize(
    "template,query,expected",
    [
        ("du -sh -- <directory>", "посчитай размер папки ~/test", "du -sh -- ~/test"),
        ("du -sm -- <directory>", "размер папки ~/test в мегабайтах", "du -sm -- ~/test"),
        ("tar -czf <archive> -- <source>", "создай архив primer.tar.gz из папки ~/test", "tar -czf primer.tar.gz -- ~/test"),
        ("tar -xzf <archive> -C <destination>", "распакуй primer.tar.gz в папку ~/result", "tar -xzf primer.tar.gz -C ~/result"),
        ("cp -- <source> <destination>", "скопируй файл README.md в ~/backup", "cp -- README.md ~/backup"),
        ("mv -- <source> <destination>", "перемести файл README.md в ~/backup", "mv -- README.md ~/backup"),
        ("zip -r <archive> <source>", "создай архив primer.zip из папки ~/test", "zip -r primer.zip ~/test"),
        ("unzip <archive> -d <destination>", "распакуй primer.zip в папку ~/result", "unzip primer.zip -d ~/result"),
        ("cat -- <file>", "прочитай файл README.md", "cat -- README.md"),
    ],
)
def test_dictionary_recipes_fill_real_query_arguments(template, query, expected):
    pattern = next(pattern for _, pattern in recipes() if pattern["command"] == template)
    command, missing = bind_arguments(query, template, pattern["arguments"])
    assert command == expected
    assert missing == ()


def test_current_directory_and_wildcard_recipes_remain_applicable_without_arguments():
    indexed = {pattern["command"]: pattern for _, pattern in recipes()}
    for template in ("ls", "ls -lh", "tree", "eza -lah", "du -sh * | sort -rh | head -10", "mv *.txt target_dir/"):
        assert "arguments" not in indexed[template]
