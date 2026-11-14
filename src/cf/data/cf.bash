# cf — поиск команд по описанию задачи.
# Добавьте в ~/.bashrc: eval "$(command cf --install-shell)"

cf() {
    local cf_argument
    for cf_argument in "$@"; do
        case "$cf_argument" in
            --) break ;;
            --stats|--seed|--prepare-model|--version|--help|--install-shell|--print|-p|--copy|-c|--tmux|-t)
                command cf "$@"
                return $? ;;
        esac
    done

    if [[ $- != *i* ]]; then
        command cf "$@"
        return $?
    fi

    local cf_result cf_exit_status=0
    cf_result=$(command cf --print "$@" </dev/tty 2>/dev/tty) || cf_exit_status=$?
    if (( cf_exit_status != 0 )); then
        return "$cf_exit_status"
    fi
    if [[ -n "$cf_result" ]]; then
        printf '%s\n' "$cf_result"
        if [[ -o history ]]; then
            history -s -- "$cf_result"
        fi
    fi
    return "$cf_exit_status"
}

_cf_bash_widget() {
    local cf_original_line=$READLINE_LINE
    local cf_original_point=$READLINE_POINT
    local cf_query=$READLINE_LINE
    local cf_result cf_exit_status=0
    local cf_tty_state cf_read_status=0

    cf_tty_state=$(stty -g < /dev/tty) || return 0
    stty icanon echo icrnl < /dev/tty || return 0
    if [[ -z "$cf_query" ]]; then
        printf '\ncf> ' > /dev/tty
        IFS= read -r cf_query < /dev/tty || cf_read_status=$?
        if (( cf_read_status != 0 )) || [[ -z "$cf_query" ]]; then
            stty "$cf_tty_state" < /dev/tty || return 0
            READLINE_LINE=$cf_original_line
            READLINE_POINT=$cf_original_point
            return 0
        fi
    fi

    cf_result=$(command cf --print -- "$cf_query" </dev/tty 2>/dev/tty) || cf_exit_status=$?
    stty "$cf_tty_state" < /dev/tty || return 0
    if (( cf_exit_status == 0 )) && [[ -n "$cf_result" ]]; then
        READLINE_LINE=$cf_result
        READLINE_POINT=${#READLINE_LINE}
    else
        READLINE_LINE=$cf_original_line
        READLINE_POINT=$cf_original_point
    fi
    return 0
}

if [[ $- == *i* ]] && (( BASH_VERSINFO[0] >= 4 )); then
    bind -m emacs-standard -x '"\C-f":_cf_bash_widget'
    bind -m vi-insert -x '"\C-f":_cf_bash_widget'
fi
