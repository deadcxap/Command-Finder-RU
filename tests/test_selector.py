"""Tests for cf.selector module — command selection UI logic."""

from unittest.mock import MagicMock, mock_open, patch

import pytest

from cf.search import SearchResult
from cf.selector import _format_entry, _format_preview, select_command


class TestFormatEntry:
    def test_basic_format(self, sample_results):
        entry = _format_entry(sample_results[0], verbose=False)
        assert "ls -la" in entry
        assert "[ls]" in entry
        assert "0.123" not in entry

    def test_verbose_includes_score(self, sample_results):
        entry = _format_entry(sample_results[0], verbose=True)
        assert "0.123" in entry

    def test_includes_command_name(self, sample_results):
        entry = _format_entry(sample_results[2], verbose=False)
        assert "[find]" in entry


class TestFormatPreview:
    def test_includes_all_fields(self, sample_results):
        preview = _format_preview(sample_results[0])
        assert "Команда: ls" in preview
        assert "Синтаксис: ls [options] [file...]" in preview
        assert "Запрос: list files in a directory" in preview
        assert "Шаблон: ls -la" in preview
        assert "Пояснение: Show all files with details" in preview

    def test_omits_explanation_when_none(self, sample_results):
        preview = _format_preview(sample_results[2])
        assert "Пояснение" not in preview


class TestSelectCommand:
    def test_returns_none_for_empty_results(self):
        assert select_command([], verbose=False) is None

    def test_non_interactive_returns_first(self, sample_results):
        result = select_command(sample_results, verbose=False, non_interactive=True)
        assert result == "ls -la"

    @patch("sys.stdin")
    def test_non_tty_returns_first(self, mock_stdin, sample_results):
        mock_stdin.isatty.return_value = False
        result = select_command(sample_results, verbose=False)
        assert result == "ls -la"

    @patch("cf.selector._select_with_input", return_value="ls -la")
    @patch("sys.stdin")
    def test_falls_back_to_input_on_import_error(self, mock_stdin, mock_input, sample_results):
        mock_stdin.isatty.return_value = True

        with patch.dict("sys.modules", {"simple_term_menu": None}):
            # Force ImportError by making the import fail inside select_command
            with patch("cf.selector._select_with_menu", side_effect=ImportError):
                result = select_command(sample_results, verbose=False)
                mock_input.assert_called_once()


class TestSelectWithInput:
    def test_valid_selection(self, sample_results):
        from cf.selector import _select_with_input

        with patch("builtins.input", return_value="2"):
            result = _select_with_input(sample_results, verbose=False, has_more=False)
        assert result == "ls -a"

    def test_quit_returns_none(self, sample_results):
        from cf.selector import _select_with_input

        with patch("builtins.input", return_value="q"):
            result = _select_with_input(sample_results, verbose=False, has_more=False)
        assert result is None

    def test_empty_input_returns_none(self, sample_results):
        from cf.selector import _select_with_input

        with patch("builtins.input", return_value=""):
            result = _select_with_input(sample_results, verbose=False, has_more=False)
        assert result is None

    def test_invalid_number_returns_none(self, sample_results):
        from cf.selector import _select_with_input

        with patch("builtins.input", return_value="999"):
            result = _select_with_input(sample_results, verbose=False, has_more=False)
        assert result is None

    def test_non_numeric_returns_none(self, sample_results):
        from cf.selector import _select_with_input

        with patch("builtins.input", return_value="abc"):
            result = _select_with_input(sample_results, verbose=False, has_more=False)
        assert result is None

    def test_eof_returns_none(self, sample_results):
        from cf.selector import _select_with_input

        with patch("builtins.input", side_effect=EOFError):
            result = _select_with_input(sample_results, verbose=False, has_more=False)
        assert result is None

    def test_keyboard_interrupt_returns_none(self, sample_results):
        from cf.selector import _select_with_input

        with patch("builtins.input", side_effect=KeyboardInterrupt):
            result = _select_with_input(sample_results, verbose=False, has_more=False)
        assert result is None

    def test_first_selection(self, sample_results):
        from cf.selector import _select_with_input

        with patch("builtins.input", return_value="1"):
            result = _select_with_input(sample_results, verbose=False, has_more=False)
        assert result == "ls -la"

    def test_last_selection(self, sample_results):
        from cf.selector import _select_with_input

        with patch("builtins.input", return_value="3"):
            result = _select_with_input(sample_results, verbose=False, has_more=False)
        assert result == "find . -name '*.txt'"


@pytest.mark.parametrize("answer,accepted", [("да\n", True), ("ДА\n", True), ("yes\n", True), ("нет\n", False)])
def test_destructive_confirmation_accepts_explicit_russian_or_english_yes(sample_results, answer, accepted):
    from cf.selector import _confirm_destructive

    terminal = mock_open(read_data=answer)
    with patch("builtins.open", terminal):
        assert _confirm_destructive(sample_results[0]) is accepted
    assert "ОПАСНАЯ КОМАНДА" in terminal().write.call_args.args[0]


def test_filled_command_is_shown_selected_and_confirmed(sample_results):
    from cf.selector import _select_with_input

    result = sample_results[0]
    result.command_template = "mv -- <source> <destination>"
    result.filled_command = "mv -- './мои файлы' ./backup"
    result.destructive = True
    assert result.command in _format_entry(result, verbose=False)
    preview = _format_preview(result)
    assert "Шаблон: mv -- <source> <destination>" in preview
    assert "Подстановка: " + result.command in preview
    terminal = mock_open(read_data="да\n")
    with patch("builtins.input", return_value="1"), patch("builtins.open", terminal):
        selected = _select_with_input([result], verbose=False, has_more=False)
    assert selected == result.command
    assert result.command in terminal().write.call_args.args[0]
    assert select_command([result], non_interactive=True) == result.command


def test_missing_arguments_are_visible_in_both_menus(sample_results, capsys):
    from cf.selector import _select_with_input

    result = sample_results[0]
    result.missing_arguments = ("source", "destination")
    assert "Заполните параметры: <source>, <destination>" in _format_preview(result)
    with patch("builtins.input", return_value="q"):
        _select_with_input([result], verbose=False, has_more=False)
    assert "Заполните параметры: <source>, <destination>" in capsys.readouterr().err
