import sys

from cf.search import SearchResult

# Маркер выбора пункта «Показать ещё».
SHOW_MORE = object()

DESTRUCTIVE_MARK = "⚠ "

_AFFIRMATIVE_WORDS = {
    "y", "yes", "д", "да", "подтверждаю", "согласен", "согласна",
    "ок", "ok", "okay", "конечно", "давай", "confirm", "proceed",
    "ага", "угу", "ладно", "хорошо", "продолжай", "sure", "absolutely",
}
_CONFIRMATION_FILLERS = {"пожалуйста", "please", "спасибо", "thanks", "thank", "you", "я", "i"}


def _is_confirmation(answer: str) -> bool:
    words = set(answer.casefold().translate(str.maketrans({",": " ", ".": " ", "!": " "})).split())
    return bool(words & _AFFIRMATIVE_WORDS) and words <= _AFFIRMATIVE_WORDS | _CONFIRMATION_FILLERS


def _confirm_destructive(r: SearchResult) -> bool:
    """Запросить подтверждение через /dev/tty для опасной команды."""
    msg = (
        f"\n⚠  ОПАСНАЯ КОМАНДА\n"
        f"   {r.command}\n"
        f"   Команда может удалить данные, завершить процессы или переписать историю.\n"
        f"   Продолжить? [y/N]: "
    )
    try:
        with open("/dev/tty", "w", encoding="utf-8") as tty_out, \
                open("/dev/tty", "r", encoding="utf-8") as tty_in:
            tty_out.write(msg)
            tty_out.flush()
            answer = tty_in.readline()
    except OSError:
        print("Не удалось запросить подтверждение: терминал недоступен.", file=sys.stderr)
        return False
    return _is_confirmation(answer)


def select_command(results: list[SearchResult], verbose: bool = False,
                   non_interactive: bool = False, has_more: bool = True) -> str | None:
    if not results:
        print("Подходящие команды не найдены.", file=sys.stderr)
        return None

    # Non-interactive mode: return top result
    if non_interactive or not sys.stdin.isatty():
        return results[0].command

    try:
        from simple_term_menu import TerminalMenu
        return _select_with_menu(results, verbose, has_more)
    except (ImportError, OSError):
        # OSError when /dev/tty is not available
        return _select_with_input(results, verbose, has_more)


def _format_entry(r: SearchResult, verbose: bool) -> str:
    score = f" ({r.distance:.3f})" if verbose else ""
    mark = DESTRUCTIVE_MARK if r.destructive else ""
    return f"{mark}{r.command}  [{r.command_name}]{score}"


def _format_preview(r: SearchResult) -> str:
    lines = [
        f"Команда: {r.command_name}",
        f"Синтаксис: {r.synopsis}",
        "",
        f"Запрос: {r.pattern_text}",
        f"Шаблон: {r.command_template}",
    ]
    if r.command != r.command_template:
        lines.extend(["", f"Подстановка: {r.command}"])
    if r.missing_arguments:
        lines.extend(["", "Заполните параметры: " + ", ".join(f"<{name}>" for name in r.missing_arguments)])
    if r.explanation:
        lines.extend(["", f"Пояснение: {r.explanation}"])
    if r.destructive:
        lines.extend(["", "⚠ Опасная команда: перед выбором потребуется подтверждение"])
    return "\n".join(lines)


def _select_with_menu(results: list[SearchResult], verbose: bool,
                      has_more: bool) -> str | None:
    from simple_term_menu import TerminalMenu

    entries = [_format_entry(r, verbose).replace("|", r"\|") + f"|{i}"
               for i, r in enumerate(results)]
    if has_more:
        entries.append("[+] Показать ещё...|more")

    previews = {str(i): _format_preview(r) for i, r in enumerate(results)}
    menu = TerminalMenu(
        entries,
        title="Выберите команду (q — отмена):",
        preview_command=lambda index: previews.get(index, ""),
        preview_size=0.4,
    )
    chosen = menu.show()
    if chosen is None:
        return None
    # "Show more" is the last entry
    if has_more and chosen == len(results):
        return SHOW_MORE
    selected = results[chosen]
    if selected.destructive and not _confirm_destructive(selected):
        return None
    return selected.command


def _select_with_input(results: list[SearchResult], verbose: bool,
                       has_more: bool) -> str | None:
    """Текстовый выбор, если simple-term-menu недоступен."""
    print("\nПодходящие команды:\n", file=sys.stderr)
    for i, r in enumerate(results, 1):
        score = f"  (расстояние: {r.distance:.3f})" if verbose else ""
        mark = DESTRUCTIVE_MARK if r.destructive else ""
        print(f"  {i}) {mark}{r.command}", file=sys.stderr)
        print(f"     {r.pattern_text} [{r.command_name}]{score}", file=sys.stderr)
        if r.explanation:
            print(f"     {r.explanation}", file=sys.stderr)
        if r.missing_arguments:
            print("     Заполните параметры: " + ", ".join(f"<{name}>" for name in r.missing_arguments), file=sys.stderr)
        print(file=sys.stderr)

    if has_more:
        print("  +) Показать ещё...", file=sys.stderr)
        print(file=sys.stderr)

    try:
        choice = input("Выберите [1-{}{}] (q — отмена): ".format(
            len(results), "/+" if has_more else ""))
    except (EOFError, KeyboardInterrupt):
        return None

    if choice.strip().lower() in ("q", ""):
        return None
    if has_more and choice.strip() == "+":
        return SHOW_MORE
    try:
        idx = int(choice.strip()) - 1
        if 0 <= idx < len(results):
            selected = results[idx]
            if selected.destructive and not _confirm_destructive(selected):
                return None
            return selected.command
    except ValueError:
        pass

    print("Недопустимый выбор.", file=sys.stderr)
    return None
