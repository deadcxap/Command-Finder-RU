import sqlite3
from pathlib import Path
from typing import Annotated, Optional

import typer

from cf import __version__

app = typer.Typer(
    name="cf",
    help="Поиск консольных команд по описанию задачи.",
    add_completion=False,
    no_args_is_help=True,
)


def version_callback(value: bool):
    if value:
        print(f"cf {__version__}")
        raise typer.Exit()


def _run_or_error(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (RuntimeError, ValueError, OSError, sqlite3.Error) as error:
        typer.echo(f"Ошибка: {error}", err=True)
        raise typer.Exit(1) from None


def _database_stats(detailed=False):
    from cf.db import get_connection, get_stats, get_detailed_stats, init_db

    conn = get_connection()
    try:
        init_db(conn)
        return get_detailed_stats(conn) if detailed else get_stats(conn)
    finally:
        conn.close()


@app.command()
def main(
    query: Annotated[Optional[list[str]], typer.Argument(help="Описание задачи на русском или английском языке")] = None,
    print_mode: Annotated[bool, typer.Option("--print", "-p", help="Вывести выбранную команду в stdout")] = False,
    copy: Annotated[bool, typer.Option("--copy", "-c", help="Скопировать выбранную команду в буфер обмена")] = False,
    tmux: Annotated[bool, typer.Option("--tmux", "-t", help="Передать команду в текущую панель tmux")] = False,
    top: Annotated[Optional[int], typer.Option("--top", min=1, help="Число результатов (по умолчанию 7, переменная CF_TOP)")] = None,
    seed: Annotated[bool, typer.Option("--seed", help="Подготовить модель и построить индекс команд")] = False,
    prepare: Annotated[bool, typer.Option("--prepare-model", help="Подготовить рабочую 8-битную ONNX-модель")] = False,
    model_source: Annotated[Optional[Path], typer.Option("--model-source", help="Импортировать готовую модель из каталога")] = None,
    force: Annotated[bool, typer.Option("--force", help="Пересоздать индекс; с --prepare-model пересобрать модель")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Показать расстояния и сведения о поиске")] = False,
    stats: Annotated[bool, typer.Option("--stats", help="Показать статистику базы команд")] = False,
    install_shell: Annotated[bool, typer.Option("--install-shell", help="Напечатать скрипт интеграции с Bash")] = False,
    version: Annotated[bool, typer.Option("--version", callback=version_callback, is_eager=True, help="Показать версию")] = False,
):
    """Поиск консольных команд по описанию задачи."""
    if install_shell:
        _print_shell_instructions()
        raise typer.Exit()

    if model_source is not None and not prepare:
        raise typer.BadParameter("--model-source используется вместе с --prepare-model")

    if prepare:
        try:
            from cf.model import prepare_model
            directory = prepare_model(force=force, source=model_source)
        except (RuntimeError, ValueError, OSError) as error:
            typer.echo(f"Ошибка подготовки модели: {error}", err=True)
            raise typer.Exit(1) from None
        typer.echo(f"Модель готова: {directory}", err=True)
        if not seed:
            raise typer.Exit()

    if seed:
        try:
            _do_seed(force)
        except (RuntimeError, ValueError, OSError) as error:
            typer.echo(f"Ошибка подготовки базы: {error}", err=True)
            raise typer.Exit(1) from None
        raise typer.Exit()

    if stats:
        _show_stats()
        raise typer.Exit()

    if not query:
        typer.echo("Укажите задачу. Например: cf 'найди большие файлы'", err=True)
        raise typer.Exit(1)

    query_str = " ".join(query)

    if top is None:
        from cf.config import DEFAULT_TOP_K
        top = DEFAULT_TOP_K

    from cf.config import DB_PATH
    if not DB_PATH.exists():
        typer.echo("База команд не найдена. Сначала выполните cf --seed.", err=True)
        raise typer.Exit(1)

    stats = _run_or_error(_database_stats)

    if stats["patterns"] == 0:
        typer.echo("База команд пуста. Выполните cf --seed.", err=True)
        raise typer.Exit(1)

    if verbose:
        typer.echo(f"База: {stats['commands']} команд, {stats['patterns']} формулировок", err=True)
        typer.echo(f"Запрос: {query_str}", err=True)

    from cf.search import search
    from cf.selector import SHOW_MORE, select_command

    current_top = top
    from cf.config import DEFAULT_MAX_TOP_K
    max_top = DEFAULT_MAX_TOP_K

    while True:
        results = _run_or_error(search, query_str, top_k=current_top)

        if not results:
            typer.echo("Подходящие команды не найдены.", err=True)
            raise typer.Exit(1)

        has_more = current_top < max_top and len(results) >= current_top
        selected = select_command(results, verbose=verbose, has_more=has_more)

        if selected is SHOW_MORE:
            current_top = min(current_top + 10, max_top)
            continue

        if not selected:
            raise typer.Exit(0)

        break

    from cf.output import output_clipboard, output_print, output_tmux

    if copy:
        output_clipboard(selected)
    elif tmux:
        output_tmux(selected)
    else:
        output_print(selected)


def _show_stats():
    from cf.config import DB_PATH
    if not DB_PATH.exists():
        typer.echo("База команд не найдена. Сначала выполните cf --seed.", err=True)
        raise typer.Exit(1)

    s = _run_or_error(_database_stats, detailed=True)

    typer.echo(f"База:           {DB_PATH}")
    typer.echo(f"Категории:      {s['categories']}")
    typer.echo(f"Команды:        {s['commands']}")
    typer.echo(f"Формулировки:   {s['patterns']}")
    typer.echo(f"Векторы:        {s['embeddings']}")
    typer.echo(f"Размерность:    {s['embedding_dim']}")
    typer.echo(f"Запросы в кеше: {s['cached_queries']}")
    typer.echo("")

    if s["by_category"]:
        name_w = max(len(r["category"]) for r in s["by_category"])
        typer.echo(f"  {'Категория':<{name_w}}  Команды  Формулировки")
        typer.echo(f"  {'-' * name_w}  --------  --------")
        for r in s["by_category"]:
            typer.echo(f"  {r['category']:<{name_w}}  {r['commands']:>8}  {r['patterns']:>8}")


def _do_seed(force: bool):
    from cf.model import prepare_model
    from cf.seed import seed_database

    typer.echo("Подготовка модели и индекса команд…", err=True)
    prepare_model()
    stats = seed_database(force=force)
    typer.echo(f"База готова: {stats['commands']} команд, {stats['patterns']} формулировок.", err=True)


def _print_shell_instructions():
    from importlib.resources import files

    print((files("cf") / "data/cf.bash").read_text(encoding="utf-8"), end="")
