"""Пути, роли аргументов и буквальная передача значений в Bash."""

import subprocess

import pytest

from cf.arguments import bind_arguments, search_query, validate_arguments


@pytest.mark.parametrize("query, expected", [
    ("посчитай размер папки ~/test", "du -sh ~/test"),
    ("покажи размер /var/log", "du -sh /var/log"),
    ("размер ./test", "du -sh ./test"),
    ("размер ../test", "du -sh ../test"),
    ("размер папки test", "du -sh test"),
    ("размер каталога Test", "du -sh Test"),
    ("размер файла README.md", "du -sh README.md"),
    ('размер папки "~/Мои документы"', "du -sh ~/'Мои документы'"),
    ("размер папки «Мои документы»", "du -sh 'Мои документы'"),
    ("размер папки '~'", "du -sh ~"),
    ("размер папки -rf", "du -sh ./-rf"),
    ("размер папки .", "du -sh ."),
    ("размер папки ~/", "du -sh ~/"),
])
def test_single_path(query, expected):
    assert bind_arguments(query, "du -sh <directory>", {"directory": "path"}) == (expected, ())


@pytest.mark.parametrize("query", ["размер папки", "размер ~/one и ~/two", "размер «a» и «b»"])
def test_ambiguous_or_missing_path(query):
    assert bind_arguments(query, "du -sh <directory>", {"directory": "path"}) == (
        "du -sh <directory>", ("directory",))


@pytest.mark.parametrize("query, expected, missing", [
    ("создай архив primer.tar.gz из папки ~/test", "tar -czf primer.tar.gz ~/test", ()),
    ("из папки ~/test создай архив primer.tar.gz", "tar -czf primer.tar.gz ~/test", ()),
    ("создай архив «primer copy.tar.gz» из папки «~/Мои документы»",
     "tar -czf 'primer copy.tar.gz' ~/'Мои документы'", ()),
    ("упакуй папку test в архив primer.tar.gz", "tar -czf primer.tar.gz test", ()),
    ("создай архив primer.tar.gz", "tar -czf primer.tar.gz <source>", ("source",)),
    ("создай архив из папки ~/test", "tar -czf <archive> ~/test", ("archive",)),
    ("создай архив a.tar.gz или b.tar.gz из папки ~/test", "tar -czf <archive> ~/test", ("archive",)),
    ("создай архив a.tar.gz из папки ~/source.zip", "tar -czf a.tar.gz ~/source.zip", ()),
    ("создай архив tar.gz из папки ~/test", "tar -czf <archive> ~/test", ("archive",)),
])
def test_create_archive(query, expected, missing):
    assert bind_arguments(query, "tar -czf <archive> <source>",
                          {"archive": "archive", "source": "source"}) == (expected, missing)


@pytest.mark.parametrize("query, expected, missing", [
    ("скопируй ~/one в ~/two", "cp -r ~/one ~/two", ()),
    ("скопируй папку one в папку two", "cp -r one two", ()),
    ("скопируй файл a.zip в папку ~/two", "cp -r a.zip ~/two", ()),
    ("скопируй «one x» в «two x»", "cp -r 'one x' 'two x'", ()),
    ("скопируй в папку ~/two", "cp -r <source> ~/two", ("source",)),
    ("скопируй ~/one", "cp -r ~/one <destination>", ("destination",)),
    ("скопируй ~/one ~/two", "cp -r <source> <destination>", ("source", "destination")),
])
def test_source_and_destination(query, expected, missing):
    assert bind_arguments(query, "cp -r <source> <destination>",
                          {"source": "source", "destination": "destination"}) == (expected, missing)


def test_extract_archive():
    assert bind_arguments("распакуй primer.tar.gz в папку ~/test", "tar -xzf <archive> -C <directory>",
                          {"archive": "archive", "directory": "destination"}) == (
        "tar -xzf primer.tar.gz -C ~/test", ())


def test_destination_that_looks_like_an_archive_is_not_the_input():
    assert bind_arguments("распакуй a.tar.gz в папку ~/result.zip", "tar -xzf <archive> -C <directory>",
                          {"archive": "archive", "directory": "destination"}) == (
        "tar -xzf a.tar.gz -C ~/result.zip", ())


def test_same_parameter_can_appear_twice():
    assert bind_arguments("проверь файл a.txt", "tool <file> && tool <file>", {"file": "path"}) == (
        "tool a.txt && tool a.txt", ())


def test_unmarked_template_is_preserved():
    assert bind_arguments("любой ~/test", "echo <file>", {}) == ("echo <file>", ())


@pytest.mark.parametrize("template, arguments", [
    ("tool <file>", None),
    ("tool <file>", []),
    ("tool <file>", {"file": "unknown"}),
    ("tool <file>", {"file": None}),
    ("tool <file>", {1: "path"}),
    ("tool <file>", {"bad-name": "path"}),
    ("tool <file>", {"other": "path"}),
    ("tool <file> <other>", {"file": "path"}),
    ("tool <file> <bad-name>", {"file": "path"}),
    ("tool <file> <other>", {"file": "path", "other": "path"}),
    ("tool --file=<file>", {"file": "path"}),
    ("tool '<file>'", {"file": "path"}),
    ('tool "prefix <file> suffix"', {"file": "path"}),
])
def test_invalid_metadata(template, arguments):
    with pytest.raises(ValueError):
        validate_arguments(template, arguments)


@pytest.mark.parametrize("value", [
    "a b", "a;printf hacked", "$(printf hacked)", "`printf hacked`", "$HOME", "*", "a|b",
    "a&b", "a>b", "it's mine", 'a"b', "a\\b", "a(b)", "-rf", "~/a b", "~/a'b", "~/a$HOME",
])
def test_bash_receives_a_single_literal_argument(value):
    opening, closing = ('"', '"') if '"' not in value and "\\" not in value else ("«", "»")
    rendered, missing = bind_arguments(f"размер папки {opening}{value}{closing}", "<path>", {"path": "path"})
    assert not missing
    result = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c", "printf '%s\\0' " + rendered],
                            capture_output=True, check=True, env={"HOME": "/tmp/cf-argument-home"})
    expected = "/tmp/cf-argument-home/" + value[2:] if value.startswith("~/") else value
    if expected.startswith("-"):
        expected = "./" + expected
    assert result.stdout == expected.encode() + b"\0"


@pytest.mark.parametrize("control", ["\x00", "\t", "\n", "\r", "\x1b", "\x7f"])
def test_control_characters_are_not_inserted(control):
    command, missing = bind_arguments(f'размер папки "a{control}b"', "du <path>", {"path": "path"})
    assert command == "du <path>"
    assert missing == ("path",)


@pytest.mark.parametrize("query", ['размер папки "hello', "размер папки 'hello", "размер папки «hello"])
def test_unclosed_quotes_do_not_guess_a_partial_filename(query):
    assert bind_arguments(query, "du <path>", {"path": "path"}) == ("du <path>", ("path",))


def test_escaped_double_quote_is_literal():
    assert bind_arguments(r'прочитай файл "a\"b.txt"', "cat <file>", {"file": "path"}) == (
        "cat 'a\"b.txt'", ())


@pytest.mark.parametrize("query", [
    "покажи файл целиком", "размер папки рекурсивно", "покажи файл полностью", "создай архив быстро",
    "покажи файл построчно", "размер папки включая скрытые файлы",
])
def test_task_modifiers_are_not_filenames(query):
    assert bind_arguments(query, "tool <path>", {"path": "path"}) == ("tool <path>", ("path",))


def test_quoted_generic_word_can_be_a_filename():
    assert bind_arguments('прочитай файл "файл"', "cat <path>", {"path": "path"}) == ("cat 'файл'", ())


@pytest.mark.parametrize("query, expected", [
    ("скопируй файл README.md в ~/backup", "скопируй файл в"),
    ("посчитай размер папки ~/test", "посчитай размер папки"),
    ("создай архив primer.tar.gz из папки ~/test", "создай архив tar.gz из папки"),
    ("распакуй ARCHIVE.TAR.BZ2 в папку ./test", "распакуй tar.bz2 в папку"),
    ("размер папки «~/Мои документы»", "размер папки"),
    ("размер папки test", "размер папки"),
    ("найди файлы *.log", "найди файлы *.log"),
    ("найди файлы ~/logs/*.log", "найди файлы *.log"),
    ("установи node.js", "установи node.js"),
    ("создай архив tar.gz", "создай архив tar.gz"),
    ("создай архив tar.bz2", "создай архив tar.bz2"),
    ("создай архив tar.xz", "создай архив tar.xz"),
    ("создай архив tgz", "создай архив tgz"),
    ("найди текст 'error' в файле ~/log", "найди текст 'error' в файле"),
    ("покажи ввод/вывод", "покажи ввод/вывод"),
    ("проверь TCP/IP", "проверь TCP/IP"),
    ("размер папки logs/app", "размер папки"),
    ("покажи файл целиком", "покажи файл целиком"),
    ("размер папки рекурсивно", "размер папки рекурсивно"),
    ('прочитай файл "a\nname"', 'прочитай файл "a\nname"'),
    ('размер папки "hello', 'размер папки "hello'),
    ("не удаляй ~/test", "не удаляй"),
])
def test_search_query_only_removes_explicit_path_values(query, expected):
    assert search_query(query) == expected


@pytest.mark.parametrize("query", [
    "скопируй из ~/one и ~/two в ~/dest",
    "скопируй из ~/one или ~/two в ~/dest",
    "скопируй из папки ~/one и папки ~/two в ~/dest",
    "скопируй из папки one и two в ~/dest",
    "скопируй из ~/one ~/two в ~/dest",
])
def test_multiple_sources_leave_source_unfilled(query):
    assert bind_arguments(query, "cp <source> <destination>",
                          {"source": "source", "destination": "destination"}) == (
        "cp <source> ~/dest", ("source",))


@pytest.mark.parametrize("query", [
    "скопируй из ~/one в ~/dest1 и ~/dest2",
    "скопируй из ~/one в ~/dest1 или ~/dest2",
    "скопируй из ~/one в папку ~/dest1 и папку ~/dest2",
    "скопируй из ~/one в ~/dest1 ~/dest2",
])
def test_multiple_destinations_leave_destination_unfilled(query):
    assert bind_arguments(query, "cp <source> <destination>",
                          {"source": "source", "destination": "destination"}) == (
        "cp ~/one <destination>", ("destination",))


@pytest.mark.parametrize("query", [
    "создай архив backup.tar.gz из ~/one и ~/two",
    "создай архив backup.tar.gz из папки ~/one и папки ~/two",
    "создай архив backup.tar.gz из ~/one ~/two",
])
def test_multiple_archive_sources_do_not_silently_pick_one(query):
    assert bind_arguments(query, "tar -czf <archive> <source>",
                          {"archive": "archive", "source": "source"}) == (
        "tar -czf backup.tar.gz <source>", ("source",))


def test_unassigned_extra_path_prevents_claiming_a_complete_copy_command():
    assert bind_arguments("скопируй из ~/one также ~/two в ~/dest", "cp <source> <destination>",
                          {"source": "source", "destination": "destination"}) == (
        "cp <source> <destination>", ("source", "destination"))


@pytest.mark.parametrize("query", ["размер папки пожалуйста", "размер папки ~/test, пожалуйста"])
def test_politeness_and_ambiguous_comma_are_not_filenames(query):
    assert bind_arguments(query, "du <path>", {"path": "path"}) == ("du <path>", ("path",))


def test_quoted_comma_is_a_literal_part_of_filename():
    assert bind_arguments('размер папки "~/test," пожалуйста', "du <path>", {"path": "path"}) == (
        "du ~/test,", ())


def test_comma_separated_sources_do_not_drop_the_first_path():
    assert bind_arguments("скопируй из ~/one, ~/two в ~/dest", "cp <source> <destination>",
                          {"source": "source", "destination": "destination"}) == (
        "cp <source> <destination>", ("source", "destination"))
