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


@pytest.mark.parametrize("query, expected", [
    ("удалить файлы test.tar.gz large_file.dat", "rm -- test.tar.gz large_file.dat"),
    ("удали файлы foo bar", "rm -- foo bar"),
    ("удали файлы foo и bar", "rm -- foo bar"),
    ("удали файл foo и файл bar", "rm -- foo bar"),
    ("удали файл a.txt файл b.txt", "rm -- a.txt b.txt"),
    ("удали папки ~/a ./b ../c", "rm -- ~/a ./b ../c"),
    ("удали файлы 'a b.txt' «~/My Files» -rf", "rm -- 'a b.txt' ~/'My Files' ./-rf"),
    ('удали файлы "не" ./not/a.txt', "rm -- 'не' ./not/a.txt"),
    ('удали файлы "или" "кроме" "без"', "rm -- 'или' 'кроме' 'без'"),
    ("удали файлы not/a.txt b.txt", "rm -- not/a.txt b.txt"),
    ("удали файлы foo foo", "rm -- foo foo"),
    ("удали файл tar.gz", "rm -- tar.gz"),
    ("удали файл .tar.gz", "rm -- .tar.gz"),
])
def test_path_list_keeps_every_filename_in_original_order(query, expected):
    assert bind_arguments(query, "rm -- <files>", {"files": "paths"}) == (expected, ())


@pytest.mark.parametrize("query", [
    "удали a.txt или b.txt", "удали a.txt либо b.txt", "удали a.txt кроме b.txt",
    "удали a.txt но не b.txt", "не удаляй a.txt", "удали файлы foo без bar",
    "удали файлы foo исключая bar", "удали a.txt за исключением b.txt",
    "удали файлы foo вместо bar", "удали a.txt, кроме b.txt", "удали файлы",
    "delete a.txt or b.txt", "delete a.txt except b.txt", "do not delete a.txt",
    "delete a.txt but keep b.txt", "delete a.txt without b.txt",
])
def test_path_list_is_not_filled_for_alternatives_negations_or_exclusions(query):
    assert bind_arguments(query, "rm -- <files>", {"files": "paths"}) == (
        "rm -- <files>", ("files",))


@pytest.mark.parametrize("query", ["прочитай файлы a.txt b.txt", "прочитай файлы foo bar"])
def test_scalar_path_does_not_take_the_first_item_of_a_list(query):
    assert bind_arguments(query, "cat <file>", {"file": "path"}) == ("cat <file>", ("file",))


@pytest.mark.parametrize("arguments", [
    {"files": "paths", "destination": "destination"},
    {"files": "paths", "file": "path"},
    {"files": "paths", "archive": "archive"},
    {"files": "paths", "more": "paths"},
])
def test_path_list_role_cannot_be_mixed_with_other_parameters(arguments):
    template = "tool " + " ".join(f"<{name}>" for name in arguments)
    with pytest.raises(ValueError):
        validate_arguments(template, arguments)


def test_bash_receives_list_values_as_separate_literal_arguments():
    values = ["a b.txt", "$(printf hacked)", "~/My Files", "-rf", "не", "a;printf hacked", "it's mine"]
    query = "удали файлы " + " ".join("«" + value + "»" for value in values)
    rendered, missing = bind_arguments(query, "<files>", {"files": "paths"})
    assert not missing
    result = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c", "printf '%s\\0' " + rendered],
                            capture_output=True, check=True, env={"HOME": "/tmp/cf-argument-home"})
    expected = ["/tmp/cf-argument-home/" + value[2:] if value.startswith("~/")
                else "./" + value if value.startswith("-") else value for value in values]
    assert result.stdout.split(b"\0") == [value.encode() for value in expected] + [b""]


@pytest.mark.parametrize("query, expected", [
    ("удалить файлы test.tar.gz large_file.dat", "удалить файлы"),
    ("удали файлы foo bar", "удали файлы"),
    ("удали a.tar.gz b.txt", "удали"),
    ("удали файл tar.gz", "удали файл"),
    ("удали файл .tar.gz", "удали файл"),
    ("прочитай файл test.tar.gz", "прочитай файл"),
    ("скопируй архив test.tar.gz в папку ~/backup", "скопируй архив в папку"),
    ("размер архива test.tar.gz", "размер архива"),
    ("создай файл test.tar.gz", "создай файл"),
    ("упакуй папку ~/test в архив primer.tar.gz", "упакуй папку в архив tar.gz"),
    ("создай архив удали.tar.gz из папки ~/test", "создай архив tar.gz из папки"),
    ("создай архив скопируй.tar.gz из папки ~/test", "создай архив tar.gz из папки"),
    ("создай архив ./удали/backup.tar.gz из папки ~/test", "создай архив tar.gz из папки"),
    ("сделай архив primer.tar.gz из папки ~/test", "сделай архив tar.gz из папки"),
    ("покажи содержимое архива backup.tar.gz", "покажи содержимое архива tar.gz"),
    ("создай архив .tar.gz", "создай архив .tar.gz"),
    ("создай архив tar.gz", "создай архив tar.gz"),
    ("установи node.js", "установи node.js"),
    ("покажи TCP/IP и ввод/вывод", "покажи TCP/IP и ввод/вывод"),
    ("найди текст 'error' в файле ~/log", "найди текст 'error' в файле"),
])
def test_search_query_preserves_archive_formats_only_for_archive_operations(query, expected):
    assert search_query(query) == expected


@pytest.mark.parametrize("modifier", ["принудительно", "навсегда", "безопасно", "окончательно", "немедленно"])
def test_operational_modifiers_are_not_list_paths_and_remain_in_semantic_query(modifier):
    query = "удали файлы a.txt b.txt " + modifier
    assert bind_arguments(query, "rm -- <files>", {"files": "paths"}) == ("rm -- a.txt b.txt", ())
    assert search_query(query) == "удали файлы " + modifier


@pytest.mark.parametrize("constraint", [
    "исключив", "исключая", "исключить", "сохранив", "сохраняя", "сохрани", "оставив", "оставляя",
    "оставь", "помимо", "заисключением", "за исключением",
])
def test_exclusions_and_preservation_requests_leave_the_entire_path_list_unfilled(constraint):
    query = f"удали файлы a.txt b.txt {constraint} c.txt"
    assert bind_arguments(query, "rm -- <files>", {"files": "paths"}) == ("rm -- <files>", ("files",))
    assert search_query(query) == "удали файлы " + constraint


def test_constraint_words_and_modifiers_are_literal_when_quoted_or_inside_paths():
    query = 'удали файлы "исключив" "сохраняя" "оставь" "принудительно" ./оставь /tmp/сохрани исключив.txt'
    assert bind_arguments(query, "rm -- <files>", {"files": "paths"}) == (
        "rm -- 'исключив' 'сохраняя' 'оставь' 'принудительно' './оставь' '/tmp/сохрани' 'исключив.txt'", ())
