"""Пути, роли аргументов и буквальная передача значений в Bash."""

import subprocess

import pytest

from cf.arguments import bind_arguments, explicit_path_values, search_query, unsupported_path_constraint, validate_arguments


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


@pytest.mark.parametrize("query", [
    "Собираюсь стереть file.txt, но сначала обязательно спроси меня ещё раз.",
    "удали файл ./old.log, но запроси подтверждение",
])
def test_requesting_confirmation_is_not_an_exclusion_from_a_file_list(query):
    from cf.arguments import unsupported_path_constraint

    assert unsupported_path_constraint(query) is None


def test_requesting_confirmation_does_not_hide_an_actual_exclusion():
    from cf.arguments import unsupported_path_constraint

    assert unsupported_path_constraint("удали файлы ./old.log ./live.log, но спроси и не трогай ./live.log")


def test_plural_directory_list_drops_only_the_argument_connector_for_search():
    query = "посчитай размер папок /var/log/nginx и /var/log/postgresql"
    assert search_query(query) == "посчитай размер папок"
    assert bind_arguments(query, "du -sh -- <directories>", {"directories": "paths"}) == (
        "du -sh -- /var/log/nginx /var/log/postgresql", ())


def test_plural_genitive_directory_noun_introduces_bare_paths():
    query = "посчитай размер папок nginx и postgresql"
    assert search_query(query) == "посчитай размер папок"
    assert bind_arguments(query, "du -sh -- <directories>", {"directories": "paths"}) == (
        "du -sh -- nginx postgresql", ())


def test_argument_connectors_do_not_remove_conjunctions_between_instructions():
    query = "прочитай файл /tmp/a.txt и покажи файл /tmp/b.txt"
    assert search_query(query) == "прочитай файл и покажи файл"
    assert search_query("удали файлы a.txt или b.txt") == "удали файлы или"


@pytest.mark.parametrize("instruction", ["с номерами строк", "нумерованными строками"])
def test_line_number_instruction_is_preserved_and_is_not_a_path(instruction):
    query = "покажи файл /etc/nginx/nginx.conf " + instruction
    assert search_query(query) == "покажи файл " + instruction
    assert bind_arguments(query, "cat -n -- <files>", {"files": "paths"}) == (
        "cat -n -- /etc/nginx/nginx.conf", ())


def test_archive_extraction_with_absolute_paths_preserves_format_and_roles():
    query = "распакуй архив /tmp/myapp-backup.tar.gz в папку /srv/restore"
    assert search_query(query) == "распакуй архив tar.gz в папку"
    assert bind_arguments(query, "tar -xzf <archive> -C <directory>",
                          {"archive": "archive", "directory": "destination"}) == (
        "tar -xzf /tmp/myapp-backup.tar.gz -C /srv/restore", ())


@pytest.mark.parametrize("query", [
    "удали файлы /tmp/old.log /tmp/current.log, кроме /tmp/current.log",
    'удали файлы "/tmp/old.log" "/tmp/current.log," кроме "/tmp/current.log"',
    "удали файлы /tmp/a.log /tmp/b.log или /tmp/c.log",
    "удали /tmp/a.log /tmp/b.log, но не /tmp/b.log",
    "удали файлы a.txt b.txt исключив b.txt",
    "удали файлы a.txt b.txt сохранив b.txt",
    "не удалять файлы",
    "не удаляй /tmp/current.log",
    "rm a.txt b.txt except b.txt",
    "rmdir /tmp/a /tmp/b except /tmp/b",
])
def test_unsupported_deletion_list_constraints_have_a_user_explanation(query):
    message = unsupported_path_constraint(query)
    assert message is not None
    assert "не поддерживается" in message
    assert "Укажите только пути" in message


@pytest.mark.parametrize("query", [
    "копируй без перезаписи",
    "скопируй файл /tmp/a в /tmp/b без перезаписи",
    "создай архив без удаления источника",
    "создай архив /tmp/backup.tar.gz из папки /tmp/source без удаления источника",
    "заморозить процесс, не завершать",
    "удали файлы /tmp/old.log /tmp/current.log",
    'удали файлы "кроме" "не" "или"',
    "удали файлы /tmp/not/a.txt ./кроме /tmp/не.txt",
    'удали файлы "a,b.txt" "c.txt"',
    "удали процесс, но не замораживай",
    "прочитай файл a.txt без номеров строк",
])
def test_path_constraint_guard_does_not_apply_to_other_tasks_or_literal_filenames(query):
    assert unsupported_path_constraint(query) is None


def test_explicit_path_values_are_literal_unique_and_in_query_order():
    assert explicit_path_values('удали файлы "~/My Files" -rf ./a.txt «~/My Files»') == (
        "~/My Files", "-rf", "./a.txt")
    assert explicit_path_values("удали файлы a.txt b.txt кроме c.txt") == ("a.txt", "b.txt", "c.txt")


def test_explicit_path_values_reject_malformed_quotes_and_control_characters():
    for query in ['прочитай "~/hello', 'прочитай "~/hello\nworld"', "прочитай ./file\x00.txt"]:
        assert explicit_path_values(query) == ()


@pytest.mark.parametrize("instruction", ["при выводе", "после сортировки", "перед выводом", "во время просмотра", "до обработки"])
def test_instruction_prepositions_end_inherited_filename_context(instruction):
    query = "пронумеруй все строки файла /etc/ssh/sshd_config " + instruction
    assert bind_arguments(query, "cat -n -- <files>", {"files": "paths"}) == (
        "cat -n -- /etc/ssh/sshd_config", ())
    assert search_query(query) == "пронумеруй все строки файла " + instruction
    assert explicit_path_values(query) == ("/etc/ssh/sshd_config",)


@pytest.mark.parametrize("query", [
    "Перед git clean хочу только прикинуть список удаляемых неотслеживаемых файлов; ничего пока не стирать.",
    "Убери nginx через APT, но его конфигурационные файлы пока сохрани.",
    "В этой ветке каталогов нужны совсем пустые папки; удалять их не требуется.",
    "Найди файлы /tmp/a.log /tmp/b.log, не удаляй их.",
    "Прикинь список удаляемых файлов; ничего не стирать.",
    "Посчитай размер файлов /tmp/a.log /tmp/b.log, но не удаляй.",
    "Удали пакет nginx через apt-get, но сохрани конфигурационные файлы.",
    "Удали процесс, но не трогай файлы.",
])
def test_deletion_constraint_guard_requires_primary_filesystem_removal_intent(query):
    assert unsupported_path_constraint(query) is None
