"""Tests for cf.selector module — command selection UI logic."""

from unittest.mock import MagicMock, mock_open, patch
from dataclasses import replace

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


@pytest.mark.parametrize("answer", [
    "y", "Y", "yes", "д", "да", "ДА", "да, подтверждаю", "подтверждаю", "ок", "конечно",
    "yes, proceed", "давай", "да, пожалуйста", "Yes, please!", "я подтверждаю", "yes, I confirm",
    "  да, конечно.  ", "yes, thank you", "да, согласен", "okay",
    "ага", "угу", "ладно", "хорошо", "продолжай", "sure", "absolutely", "ага, конечно",
])
def test_destructive_confirmation_accepts_short_and_full_affirmative_answers(sample_results, answer):
    from cf.selector import _confirm_destructive

    terminal = mock_open(read_data=answer + "\n")
    with patch("builtins.open", terminal):
        assert _confirm_destructive(sample_results[0]) is True
    prompt = terminal().write.call_args.args[0]
    assert "ОПАСНАЯ КОМАНДА" in prompt
    assert "[y/N]" in prompt


@pytest.mark.parametrize("answer", [
    "", "   ", "n", "N", "no", "нет", "не подтверждаю", "не уверен", "да нет", "да, но не надо",
    "yes, but no", "no, thanks", "не давай", "да?", "наверное", "пожалуйста", "thank you",
    "yesterday", "yesplease", "подтверждаюнет", "дада", "y/n", "yes/no", "surely not", "maybe yes",
    "да, не уверен", "да, отмена", "не согласен", "не согласна", "whatever", "yes; proceed",
])
def test_destructive_confirmation_rejects_default_negation_ambiguity_and_unknown_answers(sample_results, answer):
    from cf.selector import _confirm_destructive

    with patch("builtins.open", mock_open(read_data=answer + "\n")):
        assert _confirm_destructive(sample_results[0]) is False


def test_destructive_confirmation_rejects_unavailable_terminal(sample_results, capsys):
    from cf.selector import _confirm_destructive

    with patch("builtins.open", side_effect=OSError("Нет терминала")):
        assert _confirm_destructive(sample_results[0]) is False
    assert "терминал недоступен" in capsys.readouterr().err


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


@pytest.mark.parametrize("command", [
    "cat files_to_remove.txt | xargs rm",
    "du -sh * | sort -rh | head -10",
    "cat 'file|name.txt'",
    r"cat 'file\|name.txt'",
    r"cat 'file\\|name.txt' | sort",
    "test -d ./test || mkdir ./test",
])
def test_real_menu_keeps_full_pipelines_literal_pipes_and_preview_identity(sample_results, monkeypatch, command):
    from simple_term_menu import TerminalMenu
    from cf.selector import _select_with_menu

    monkeypatch.setenv("TERM", "xterm")
    results = [replace(sample_results[0], command_template=command, pattern_text="Первый результат"),
               replace(sample_results[0], command_template=command, pattern_text="Второй результат")]

    def inspect_menu(menu):
        assert menu._menu_entries[:2] == [_format_entry(result, False) for result in results]
        for index, result in enumerate(results):
            preview_argument = menu._preview_arguments[index]
            assert menu._preview_command(preview_argument) == _format_preview(result)
        assert menu._preview_command(menu._preview_arguments[2]) == ""
        return 1

    with patch.object(TerminalMenu, "_init_backspace_control_character"), \
            patch.object(TerminalMenu, "_add_missing_control_characters_for_keys"), \
            patch.object(TerminalMenu, "_init_terminal_codes"), \
            patch.object(TerminalMenu, "show", autospec=True, side_effect=inspect_menu):
        assert _select_with_menu(results, verbose=False, has_more=True) == command
