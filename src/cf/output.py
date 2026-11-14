import os
import subprocess
import sys


def output_print(command: str) -> None:
    print(command)


def output_clipboard(command: str) -> None:
    try:
        subprocess.run(["pbcopy"], input=command.encode(), check=True)
        print(f"Скопировано в буфер обмена: {command}", file=sys.stderr)
    except FileNotFoundError:
        # Fallback for Linux
        try:
            subprocess.run(["xclip", "-selection", "clipboard"],
                           input=command.encode(), check=True)
            print(f"Скопировано в буфер обмена: {command}", file=sys.stderr)
        except FileNotFoundError:
            print("Утилиты буфера обмена не найдены (pbcopy/xclip). Команда:", file=sys.stderr)
            print(command)


def output_tmux(command: str) -> None:
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        print("Сеанс tmux не найден. Команда:", file=sys.stderr)
        print(command)
        return
    subprocess.run(["tmux", "send-keys", "-t", pane, "-l", command], check=True)
    print(f"Отправлено в панель tmux: {command}", file=sys.stderr)
