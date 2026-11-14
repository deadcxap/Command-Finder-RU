#!/usr/bin/env bash
set -euo pipefail
PYTHON_BIN="$(command -v "${CF_PYTHON:-python3}" || true)"
if [[ -z "$PYTHON_BIN" || "$PYTHON_BIN" == /mnt/* || "$PYTHON_BIN" == *.exe ]]; then
    printf '[cf-ru] Нужен Python, установленный внутри Linux/WSL.\n' >&2
    exit 1
fi
CF_UNINSTALL_SCRIPT="${BASH_SOURCE[0]}" "$PYTHON_BIN" - "$@" <<'PY'
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys


def local_path(value):
    path = Path(value).expanduser().absolute()
    for part in reversed((path, *path.parents)):
        if str(part).startswith('/mnt/') or re.match(r'^[A-Za-z]:[\\/]', str(part)):
            raise ValueError(f'Нужен путь внутри Linux/WSL: {path}')
        if part.is_symlink():
            raise ValueError(f'Укажите путь без символических ссылок: {part}')
    return path


def main():
    parser = argparse.ArgumentParser(description='Удаление управляемой установки Command Finder RU.')
    parser.add_argument('--purge', action='store_true', help='Также удалить модель, базу и кеш запросов')
    args = parser.parse_args()
    script_dir = local_path(str(Path(os.environ['CF_UNINSTALL_SCRIPT']).absolute().parent))
    default = script_dir if (script_dir / 'install.json').is_file() else Path.home() / '.local/share/cf-ru'
    prefix = local_path(os.environ.get('CF_INSTALL_DIR', str(default)))
    marker = prefix / 'install.json'
    if not marker.is_file() or marker.is_symlink() or json.loads(marker.read_text()).get('installer') != 'command-finder-ru-release-install-v1':
        raise ValueError(f'В {prefix} не найдена управляемая установка; файлы сохранены.')
    bashrc = local_path(os.environ.get('CF_BASHRC', str(Path.home() / '.bashrc')))
    link = local_path(str(Path.home() / '.local/bin')) / 'cf'
    with (prefix / '.install.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Другой установщик работает с этим каталогом.') from None
        line = 'source ' + shlex.quote(str(prefix / 'cf.bash'))
        if bashrc.is_file():
            text = bashrc.read_text()
            lines = text.splitlines(keepends=True)
            filtered = ''.join(item for item in lines if item.rstrip('\r\n') not in (line, '# cf-ru: ' + line))
            if filtered != text:
                shutil.copyfile(bashrc, str(bashrc) + '.bak.cf-ru')
                bashrc.write_text(filtered)
        if link.is_symlink() and os.readlink(link) == str(prefix / 'cf'):
            link.unlink()
        for name in ('current', 'cf', 'cf.bash'):
            path = prefix / name
            if path.is_file() or path.is_symlink():
                path.unlink()
        if args.purge:
            shutil.rmtree(prefix)
            print('[cf-ru] Установка, модель и база удалены.')
        else:
            releases = local_path(str(prefix / 'releases'))
            if releases.is_dir():
                for release in releases.iterdir():
                    if release.is_dir() and not release.is_symlink():
                        venv = release / 'venv'
                        if venv.is_symlink():
                            venv.unlink()
                        elif venv.is_dir():
                            shutil.rmtree(venv)
            print(f'[cf-ru] Приложение удалено. Модель и база сохранены в {prefix}.')
            print(f'[cf-ru] Для полного удаления: bash {shlex.quote(str(prefix / "uninstall.sh"))} --purge')


try:
    main()
except (OSError, ValueError, TypeError) as error:
    print(f'[cf-ru] Ошибка удаления: {error}', file=sys.stderr)
    sys.exit(1)
PY
