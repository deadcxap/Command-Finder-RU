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
    assert {"du", "stat", "file", "cp", "mv", "tar", "zip", "unzip", "gzip", "bzip2", "xz", "cat", "head", "tail", "rm", "rmdir"} <= covered


def test_equivalent_recipes_have_identical_parameter_roles():
    metadata = {}
    for _, pattern in recipes():
        previous = metadata.setdefault(pattern["command"], pattern.get("arguments", {}))
        assert previous == pattern.get("arguments", {}), pattern["command"]


@pytest.mark.parametrize(
    "template,query,expected",
    [
        ("du -sh -- <directories>", "посчитай размер папки ~/test", "du -sh -- ~/test"),
        ("du -sm -- <directories>", "размер папки ~/test в мегабайтах", "du -sm -- ~/test"),
        ("tar -czf <archive> -- <source>", "создай архив primer.tar.gz из папки ~/test", "tar -czf primer.tar.gz -- ~/test"),
        ("tar -xzf <archive> -C <destination>", "распакуй primer.tar.gz в папку ~/result", "tar -xzf primer.tar.gz -C ~/result"),
        ("cp -- <source> <destination>", "скопируй файл README.md в ~/backup", "cp -- README.md ~/backup"),
        ("mv -- <source> <destination>", "перемести файл README.md в ~/backup", "mv -- README.md ~/backup"),
        ("zip -r <archive> <source>", "создай архив primer.zip из папки ~/test", "zip -r primer.zip ~/test"),
        ("unzip <archive> -d <destination>", "распакуй primer.zip в папку ~/result", "unzip primer.zip -d ~/result"),
        ("cat -- <files>", "прочитай файл README.md", "cat -- README.md"),
        ("rm -- <files>", "удалить файлы test.tar.gz large_file.dat", "rm -- test.tar.gz large_file.dat"),
        ("rm -i -- <files>", "удали файлы «test one.tar.gz» ~/data/large_file.dat с подтверждением", "rm -i -- 'test one.tar.gz' ~/data/large_file.dat"),
        ("rm -rf -- <directories>", "удали папки ~/first и «~/вторая папка»", "rm -rf -- ~/first ~/'вторая папка'"),
        ("rmdir -- <directories>", "удали пустые папки ~/first и ~/second", "rmdir -- ~/first ~/second"),
    ],
)
def test_dictionary_recipes_fill_real_query_arguments(template, query, expected):
    pattern = next(pattern for _, pattern in recipes() if pattern["command"] == template)
    command, missing = bind_arguments(query, template, pattern["arguments"])
    assert command == expected
    assert missing == ()


def test_current_directory_and_wildcard_recipes_remain_applicable_without_arguments():
    indexed = {pattern["command"]: pattern for _, pattern in recipes()}
    for template in ("ls", "ls -lh", "tree", "eza -lah", "du -sh * | sort -rh | head -10", "mv *.txt target_dir/", "rm *.log"):
        assert "arguments" not in indexed[template]


@pytest.mark.parametrize("template,expected", [
    ("du -sh -- <directories>", "du -sh -- 'one folder' ~/two"),
    ("stat -- <files>", "stat -- 'one folder' ~/two"),
    ("file -- <files>", "file -- 'one folder' ~/two"),
    ("cat -- <files>", "cat -- 'one folder' ~/two"),
    ("head -- <files>", "head -- 'one folder' ~/two"),
    ("tail -- <files>", "tail -- 'one folder' ~/two"),
    ("gzip -- <files>", "gzip -- 'one folder' ~/two"),
    ("bzip2 -- <files>", "bzip2 -- 'one folder' ~/two"),
    ("xz -9 -- <files>", "xz -9 -- 'one folder' ~/two"),
])
def test_multiple_operand_tools_receive_every_path(template, expected):
    pattern = next(pattern for _, pattern in recipes() if pattern["command"] == template)
    assert set(pattern["arguments"].values()) == {"paths"}
    assert bind_arguments('обработай файлы «one folder» ~/two', template, pattern["arguments"]) == (expected, ())


def test_deletion_recipes_preserve_destructive_confirmation():
    annotated = set()
    for name, pattern in recipes():
        if name in {"rm", "rmdir"}:
            assert pattern.get("destructive") is True, pattern["command"]
            if "arguments" in pattern:
                assert set(pattern["arguments"].values()) == {"paths"}
                annotated.add(pattern["command"])
    assert {
        "rm -- <files>", "rm -i -- <files>", "rm -rf -- <directories>", "rm -d -- <directories>",
        "rmdir -- <directories>", "rmdir -p -- <directories>",
        "rmdir --ignore-fail-on-non-empty -- <directories>",
    } <= annotated


def test_deletion_with_no_paths_keeps_a_parameter_for_editing():
    pattern = next(pattern for _, pattern in recipes() if pattern["command"] == "rm -- <files>")
    assert bind_arguments("удали файлы", pattern["command"], pattern["arguments"]) == (
        "rm -- <files>", ("files",))


def test_deletion_from_a_list_requires_confirmation_without_treating_list_as_operand():
    patterns = [pattern for _, pattern in recipes() if pattern["command"] == "cat files_to_remove.txt | xargs rm"]
    assert len(patterns) == 2
    assert all(pattern.get("destructive") is True and "arguments" not in pattern for pattern in patterns)
