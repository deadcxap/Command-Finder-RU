#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
SHELL_FILE="$PROJECT_DIR/shell/cf.zsh"
ZSHRC="${ZDOTDIR:-$HOME}/.zshrc"
LINK_PATH="$HOME/.local/bin/cf"
DEFAULT_DATA_DIR="$HOME/.local/share/cf-ru"
PURGE=false

info() { printf '[cf-ru] %s\n' "$*"; }
error() { printf '[cf-ru] Ошибка: %s\n' "$*" >&2; exit 1; }

for argument in "$@"; do
    case "$argument" in
        --purge) PURGE=true ;;
        --help|-h)
            printf 'Удаление Command Finder RU\n\nИспользование: ./uninstall.sh [--purge]\n  --purge  также удалить стандартный каталог ~/.local/share/cf-ru\n'
            exit 0 ;;
        *) error "Неизвестный параметр: $argument" ;;
    esac
done

case "$PROJECT_DIR" in
    /mnt/[a-zA-Z]/*) error 'Запускайте удаление из файловой системы Linux/WSL.' ;;
esac
case "$ZSHRC" in
    /mnt/*) error 'Файл .zshrc должен находиться в файловой системе Linux/WSL.' ;;
esac

if [[ -L "$LINK_PATH" ]]; then
    if [[ "$(readlink "$LINK_PATH")" == "$VENV_DIR/bin/cf" ]]; then
        rm -- "$LINK_PATH"
        info "Удалена ссылка $LINK_PATH"
    else
        info "Ссылка $LINK_PATH относится к другому проекту и сохранена."
    fi
elif [[ -e "$LINK_PATH" ]]; then
    info "Файл $LINK_PATH сохранён: это не ссылка установки."
fi

printf -v SOURCE_LINE 'source %q' "$SHELL_FILE"
MARKER_LINE="# cf-ru: $SOURCE_LINE"
OLD_SOURCE_LINE="source $SHELL_FILE"
if [[ -f "$ZSHRC" ]] && { grep -Fqx -- "$SOURCE_LINE" "$ZSHRC" || grep -Fqx -- "$OLD_SOURCE_LINE" "$ZSHRC"; }; then
    cp -- "$ZSHRC" "$ZSHRC.bak.cf-ru"
    TEMP_RC="$(mktemp "$ZSHRC.cf-ru.XXXXXX")"
    if grep -Fvx -e "$SOURCE_LINE" -e "$OLD_SOURCE_LINE" -e "$MARKER_LINE" \
        "$ZSHRC.bak.cf-ru" > "$TEMP_RC"; then
        :
    else
        FILTER_STATUS=$?
        [[ "$FILTER_STATUS" -eq 1 ]] || error 'Не удалось обновить .zshrc; резервная копия сохранена.'
    fi
    cat "$TEMP_RC" > "$ZSHRC"
    rm -- "$TEMP_RC"
    info "Интеграция удалена; резервная копия: $ZSHRC.bak.cf-ru"
fi

if [[ -d "$VENV_DIR" || -L "$VENV_DIR" ]]; then
    rm -rf -- "$VENV_DIR"
    info 'Рабочее окружение удалено.'
fi

if "$PURGE"; then
    if [[ -d "$DEFAULT_DATA_DIR" || -L "$DEFAULT_DATA_DIR" ]]; then
        rm -rf -- "$DEFAULT_DATA_DIR"
        info "Стандартные данные удалены: $DEFAULT_DATA_DIR"
    fi
else
    info "Данные сохранены: $DEFAULT_DATA_DIR"
fi
info 'Пользовательские CF_DB_DIR и CF_MODEL_DIR при необходимости удалите вручную.'
info 'Исходники проекта сохранены. Повторная установка: ./install.sh'
