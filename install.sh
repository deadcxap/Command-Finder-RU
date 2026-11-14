#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
SHELL_FILE="$PROJECT_DIR/shell/cf.bash"
BASHRC="${CF_BASHRC:-$HOME/.bashrc}"
LINK_DIR="$HOME/.local/bin"
LINK_PATH="$LINK_DIR/cf"
BUILD_DIR=""
WITH_SHELL=false

info() { printf '[cf-ru] %s\n' "$*"; }
error() { printf '[cf-ru] Ошибка: %s\n' "$*" >&2; exit 1; }
cleanup() {
    if [[ -n "$BUILD_DIR" && -d "$BUILD_DIR" ]]; then
        rm -rf -- "$BUILD_DIR"
    fi
}
trap cleanup EXIT

for argument in "$@"; do
    case "$argument" in
        --shell) WITH_SHELL=true ;;
        --help|-h)
            printf 'Установка Command Finder RU\n\nИспользование: ./install.sh [--shell]\n  --shell  добавить интеграцию в .bashrc\n'
            exit 0 ;;
        *) error "Неизвестный параметр: $argument" ;;
    esac
done

if "$WITH_SHELL" && (( BASH_VERSINFO[0] < 4 )); then
    error 'Для интеграции нужен Bash 4 или новее. Запустите установщик современным Bash или без --shell.'
fi

case "$PROJECT_DIR" in
    /mnt/[a-zA-Z]/*) error "Разместите проект в файловой системе Linux, например в ~/projects." ;;
esac

PYTHON_BIN="$(command -v "${CF_PYTHON:-python3}" || true)"
[[ -n "$PYTHON_BIN" ]] || error 'Нужен Python 3.12 или новее.'
case "$PYTHON_BIN" in
    /mnt/*|*.exe) error 'Используйте Python, установленный в Linux/WSL или macOS.' ;;
esac
"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) and sys.platform in ("linux", "darwin") else 1)' \
    || error 'Нужен Python 3.12+ для Linux/WSL или macOS.'

if [[ -L "$LINK_PATH" ]]; then
    [[ "$(readlink "$LINK_PATH")" == "$VENV_DIR/bin/cf" ]] \
        || error "$LINK_PATH указывает на другой проект. Освободите это имя или установите вручную."
elif [[ -e "$LINK_PATH" ]]; then
    error "$LINK_PATH уже существует. Установщик сохраняет этот файл; используйте ручную установку."
fi
[[ ! -L "$VENV_DIR" ]] || error 'Каталог .venv должен быть обычным каталогом внутри проекта.'

info 'Подготовка рабочего окружения...'
if [[ ! -d "$VENV_DIR" ]]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -e "$PROJECT_DIR"
MODEL_DIR="$("$VENV_DIR/bin/python" -c 'from cf.config import ONNX_DIR; print(ONNX_DIR)')"

if "$VENV_DIR/bin/python" -c 'from cf.config import ONNX_DIR; from cf.model import verify_artifact; verify_artifact(ONNX_DIR)' \
    >/dev/null 2>&1; then
    info 'Проверка готовой локальной модели...'
    "$VENV_DIR/bin/cf" --prepare-model
else
    "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] in ((3, 12), (3, 13)) else 1)' \
        || error 'Для сборки закреплённой модели используйте CF_PYTHON=python3.12 ./install.sh либо готовый ONNX-артефакт.'
    info 'Первоначальная сборка модели: нужны интернет и свободное место для CPU-зависимостей.'
    BUILD_DIR="$(mktemp -d "$PROJECT_DIR/.model-build.XXXXXX")"
    "$PYTHON_BIN" -m venv "$BUILD_DIR"
    export PIP_CACHE_DIR="$BUILD_DIR/pip-cache"
    if [[ "$(uname -s)" == Linux ]]; then
        "$BUILD_DIR/bin/python" -m pip install --disable-pip-version-check 'torch==2.6.0' \
            --index-url https://download.pytorch.org/whl/cpu
    else
        "$BUILD_DIR/bin/python" -m pip install --disable-pip-version-check 'torch==2.6.0'
    fi
    "$BUILD_DIR/bin/python" -m pip install --disable-pip-version-check -e "${PROJECT_DIR}[export]"
    HF_HOME="$BUILD_DIR/model-cache" \
    HF_HUB_CACHE="$BUILD_DIR/model-cache/hub" \
    SENTENCE_TRANSFORMERS_HOME="$BUILD_DIR/model-cache" \
    XDG_CACHE_HOME="$BUILD_DIR/cache" \
        "$BUILD_DIR/bin/cf" --prepare-model
    cleanup
    BUILD_DIR=""
fi

info 'Индексация русского словаря...'
"$VENV_DIR/bin/cf" --seed

mkdir -p "$LINK_DIR"
if [[ ! -L "$LINK_PATH" ]]; then
    ln -s "$VENV_DIR/bin/cf" "$LINK_PATH"
fi
info "Команда доступна по пути $LINK_PATH"

case ":$PATH:" in
    *":$LINK_DIR:"*) ;;
    *) info 'Добавьте в профиль оболочки: export PATH="$HOME/.local/bin:$PATH"' ;;
esac

printf -v SOURCE_LINE 'source %q' "$SHELL_FILE"
MARKER_LINE="# cf-ru: $SOURCE_LINE"
if "$WITH_SHELL"; then
    case "$BASHRC" in
        /mnt/*) error 'Файл .bashrc должен находиться в файловой системе Linux/WSL.' ;;
    esac
    mkdir -p "$(dirname "$BASHRC")"
    if [[ ! -f "$BASHRC" ]] || ! grep -Fqx -- "$SOURCE_LINE" "$BASHRC"; then
        printf '\n%s\n%s\n' "$MARKER_LINE" "$SOURCE_LINE" >> "$BASHRC"
    fi
    info "Интеграция подключена в $BASHRC. Перезапустите терминал."
else
    info "Для интеграции Bash добавьте в .bashrc: $SOURCE_LINE"
fi

info 'Установка завершена. Пример: cf "найди большие файлы"'
