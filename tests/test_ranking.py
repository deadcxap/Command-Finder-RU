"""Поведение ранжирования при близких расстояниях и разных параметрах."""

from copy import deepcopy

from cf.ranking import rank_matches


def row(template, text, distance, arguments=None, destructive=False):
    return {"command_template": template, "pattern_text": text, "explanation": None,
            "command_name": "tool", "command_description": "Средство обработки данных",
            "synopsis": "tool [options]", "distance": distance,
            "arguments": arguments or {}, "destructive": destructive}


def test_dedup_preserves_distance_and_ors_destructive_from_every_description():
    rows = [row("tool <files>", "прочитать файлы", 0.4, {"files": "paths"}),
            row("tool <files>", "показать текст", 0.8, {"files": "paths"}, True)]
    original = deepcopy(rows)
    result = rank_matches("прочитай файл ./readme", rows)
    assert len(result) == 1
    assert result[0]["distance"] == 0.4
    assert result[0]["destructive"] is True
    assert result[0]["filled_command"] == "tool ./readme"
    assert result[0]["missing_arguments"] == ()
    assert rows == original


def test_all_descriptions_participate_even_when_the_closest_description_lacks_a_qualifier():
    rows = [row("tool --numbers <files>", "вывести текст", 0.7, {"files": "paths"}),
            row("tool --numbers <files>", "пронумеровать строки", 0.9, {"files": "paths"}),
            row("tool --plain <files>", "вывести строки", 0.65, {"files": "paths"})]
    ranked = rank_matches("покажи файл ./input с номерами строк", rows)
    assert ranked[0]["command_template"] == "tool --numbers <files>"
    assert ranked[0]["distance"] == 0.7


def test_input_order_does_not_change_dedup_or_ranking():
    rows = [row("a", "показать", 0.5), row("a", "вывести", 0.5, destructive=True),
            row("b", "показать", 0.5)]
    assert rank_matches("покажи", rows) == rank_matches("покажи", list(reversed(rows)))


def test_unbound_template_does_not_beat_a_command_covering_both_archive_and_destination():
    rows = [row("pack <archive> <source>", "упаковать архив", 0.3,
                {"archive": "archive", "source": "source"}),
            row("extract <archive>", "распаковать архив", 0.4, {"archive": "archive"}),
            row("extract <archive> --to <destination>", "извлечь архив в папку", 0.58,
                {"archive": "archive", "destination": "destination"})]
    ranked = rank_matches("распакуй archive.tar.gz в папку ~/restore", rows)
    assert ranked[0]["filled_command"] == "extract archive.tar.gz --to ~/restore"
    assert ranked[0]["missing_arguments"] == ()


def test_literal_quoted_tilde_and_option_paths_count_as_covered():
    rows = [row("tool <files>", "показать файлы", 0.6, {"files": "paths"}),
            row("tool static", "показать файлы", 0.5)]
    ranked = rank_matches('покажи файлы "~/My Files" -option', rows)
    assert ranked[0]["filled_command"] == "tool ~/'My Files' ./-option"


def test_complete_arguments_do_not_override_an_opposite_intent():
    rows = [row("erase <files>", "удалить файлы", 0.7, {"files": "paths"}, True),
            row("view <files>", "показать файл с номерами строк", 0.8, {"files": "paths"})]
    assert rank_matches("покажи файл ./input с номерами строк", rows)[0]["command_template"] == "view <files>"


def test_static_option_does_not_count_as_covering_a_filename_beginning_with_dash():
    rows = [row("tool -n ./other", "показать файл", 0.5),
            row("tool <files>", "показать файл", 0.6, {"files": "paths"})]
    assert rank_matches("покажи файлы -n ./other", rows)[0]["filled_command"] == "tool ./-n ./other"


def test_without_an_explicit_filesystem_path_lexical_matches_do_not_reorder_distance():
    rows = [row("a", "узнать формат", 0.2), row("b", "показать номера строк", 0.4)]
    for query in ("покажи номера строк", "покажи файл README.md с номерами строк"):
        assert [result["command_template"] for result in rank_matches(query, rows)] == ["a", "b"]


def test_without_paths_or_lexical_signal_distance_order_is_unchanged():
    rows = [row("b", "зебра", 0.5), row("a", "волк", 0.2)]
    ranked = rank_matches("задача", rows)
    assert [result["command_template"] for result in ranked] == ["a", "b"]
    assert [result["distance"] for result in ranked] == [0.2, 0.5]


def test_empty_candidate_pool():
    assert rank_matches("покажи ./input", []) == []
