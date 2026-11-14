import errno
from importlib.resources import files
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = files('cf') / 'data' / 'cf.bash'
DRIVER = r'''
import errno, fcntl, os, pty, select, signal, struct, termios
pid, master = pty.fork()
if pid == 0:
    os.execv('/bin/bash', ['/bin/bash', '--noprofile', '--norc', '-i'])
fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 140, 0, 0))
def stop(signum, frame):
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    raise SystemExit(1)
signal.signal(signal.SIGTERM, stop)
try:
    while True:
        readers, _, _ = select.select([0, master], [], [])
        if 0 in readers:
            data = os.read(0, 65536)
            if not data:
                stop(None, None)
            os.write(master, data)
        if master in readers:
            try:
                data = os.read(master, 65536)
            except OSError as error:
                if error.errno == errno.EIO:
                    break
                raise
            if not data:
                break
            os.write(1, data)
finally:
    os.close(master)
    os.waitpid(pid, 0)
'''
PROGRAM = 'source "$1"; shift; cf "$@"; exit "$?"'
PROMPT = b'__CF_TEST_PROMPT__ '
DONE = b'__CF_MOCK_DONE__'
SNAPSHOT = b'\x18\x14'


class BashTerminal:
    def __init__(self, environment, mode='emacs'):
        self.environment = environment
        self.process = subprocess.Popen(
            [sys.executable, '-c', DRIVER], env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self.pending = b''
        self.output = bytearray()
        self.expect(PROMPT)
        setup = (f'set -o {mode}; '.encode() + b'''source "$CF_BASH_SCRIPT"; '''
                 b'''_cf_test_snapshot() { printf '%s\\0%s\\0' "$READLINE_LINE" "$READLINE_POINT" > "$CF_TEST_BUFFER"; READLINE_LINE=; READLINE_POINT=0; }; '''
                 b'''bind -m emacs-standard -x '"\\C-x\\C-t":_cf_test_snapshot'; '''
                 b'''bind -m vi-insert -x '"\\C-x\\C-t":_cf_test_snapshot'\n''')
        self.send(setup)
        self.expect(PROMPT)

    def send(self, value):
        self.process.stdin.write(value)
        self.process.stdin.flush()

    def expect(self, token, timeout=5):
        deadline = time.monotonic() + timeout
        while token not in self.pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                raise AssertionError(f'Bash не вывел {token!r}: {self.pending!r}')
            value = os.read(self.process.stdout.fileno(), 65536)
            if not value:
                raise AssertionError(f'Bash завершился до {token!r}: {self.pending!r}')
            self.output.extend(value)
            self.pending += value
        before, self.pending = self.pending.split(token, 1)
        return before

    def snapshot(self):
        path = Path(self.environment['CF_TEST_BUFFER'])
        path.unlink(missing_ok=True)
        self.send(SNAPSHOT)
        deadline = time.monotonic() + 5
        while True:
            try:
                snapshot = path.read_bytes()
            except FileNotFoundError:
                snapshot = b''
            if snapshot.count(b'\0') == 2 and snapshot.endswith(b'\0'):
                break
            if time.monotonic() > deadline:
                raise AssertionError('Readline не выполнил контрольную привязку клавиши')
            time.sleep(0.01)
        fields = snapshot.decode().split('\0')
        return fields[0], int(fields[1])

    def close(self):
        if self.process.poll() is None:
            try:
                self.send(b'\x03\x15exit\n')
                self.process.communicate(timeout=2)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                self.process.terminate()
                self.process.communicate(timeout=2)


class BashShellTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cf-bash-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.arguments_path = self.directory / 'arguments'
        self.buffer_path = self.directory / 'buffer'
        self.sentinel = self.directory / 'executed'
        self.result = 'printf "готово 🚀" > "$CF_TEST_EXECUTED"'
        self.environment = {
            'PATH': f'{self.directory}:/usr/bin:/bin', 'LANG': 'C.UTF-8', 'TERM': 'xterm',
            'PS1': PROMPT.decode(), 'HISTFILE': '/dev/null', 'INPUTRC': '/dev/null',
            'CF_BASH_SCRIPT': str(SCRIPT), 'CF_TEST_BUFFER': str(self.buffer_path),
            'CF_TEST_ARGUMENTS': str(self.arguments_path), 'CF_TEST_EXECUTED': str(self.sentinel),
            'CF_TEST_RESULT': self.result, 'CF_TEST_STATUS': '0', 'CF_TEST_MENU': '0',
            'CF_TEST_REAL_MENU': '0', 'CF_TEST_DESTRUCTIVE': '0', 'CF_TEST_PYTHON': sys.executable,
            'CF_TEST_SELECTOR': str(self.directory / 'real_selector.py'),
        }
        (self.directory / 'real_selector.py').write_text('''import os, sys
from cf.search import SearchResult
from cf.selector import select_command
value = select_command([SearchResult(os.environ['CF_TEST_RESULT'], 'тестовая задача', None,
                                    'printf', 'Пример', 'printf', 0.0,
                                    destructive=os.environ['CF_TEST_DESTRUCTIVE']=='1')], has_more=False)
if value is not None:
    print(value)
print('__CF_MOCK_DONE__', file=sys.stderr)
''')
        executable = self.directory / 'cf'
        executable.write_text('''#!/bin/bash
printf '%s\\0' "$@" > "$CF_TEST_ARGUMENTS"
if [[ $CF_TEST_REAL_MENU == 1 ]]; then
    exec "$CF_TEST_PYTHON" "$CF_TEST_SELECTOR"
fi
if [[ $CF_TEST_MENU == 1 ]]; then
    printf '__CF_MOCK_MENU__' >&2
    IFS= read -r -n 1 cf_test_key
    if [[ $cf_test_key == $'\\e' ]]; then
        printf '__CF_MOCK_DONE__' >&2
        exit 0
    fi
fi
printf '%s\\n' "$CF_TEST_RESULT"
printf '__CF_MOCK_DONE__' >&2
exit "$CF_TEST_STATUS"
''')
        executable.chmod(0o755)

    def arguments(self):
        return self.arguments_path.read_bytes().decode().split('\0')[:-1]

    def terminal(self, mode='emacs', **changes):
        terminal = BashTerminal({**self.environment, **changes}, mode)
        self.addCleanup(terminal.close)
        return terminal

    def test_checkout_loader_uses_the_packaged_bash_script(self):
        loader = Path(__file__).parents[1] / 'shell/cf.bash'
        self.assertEqual(loader.read_text().splitlines()[-1], 'eval "$(command cf --install-shell)"')
        self.assertNotIn('_cf_bash_widget()', loader.read_text())

    def test_service_and_output_options_keep_stdout_and_exit_status(self):
        for flag in ('--stats', '--seed', '--prepare-model', '--version', '--help', '--install-shell',
                     '--print', '-p', '--copy', '-c', '--tmux', '-t'):
            with self.subTest(flag=flag):
                result = subprocess.run(
                    ['/bin/bash', '--noprofile', '--norc', '-c', PROGRAM, 'test', str(SCRIPT), flag],
                    env={**self.environment, 'CF_TEST_STATUS': '7'}, capture_output=True, text=True, timeout=5,
                )
                self.assertEqual(result.returncode, 7)
                self.assertEqual(result.stdout, self.result + '\n')
                self.assertEqual(self.arguments(), [flag])

    def test_noninteractive_default_forwards_query_without_a_terminal(self):
        result = subprocess.run(
            ['/bin/bash', '--noprofile', '--norc', '-c', PROGRAM, 'test', str(SCRIPT), 'покажи файлы'],
            env=self.environment, capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, self.result + '\n')
        self.assertEqual(self.arguments(), ['покажи файлы'])

    def test_ctrl_f_replaces_unicode_line_and_places_cursor_at_end_without_execution(self):
        terminal = self.terminal()
        terminal.send('покажи скрытые файлы'.encode() + b'\x06')
        terminal.expect(DONE)
        self.assertEqual(terminal.snapshot(), (self.result, len(self.result)))
        self.assertEqual(self.arguments(), ['--print', '--', 'покажи скрытые файлы'])
        self.assertFalse(self.sentinel.exists())

    def test_ctrl_f_works_in_vi_insertion_mode(self):
        terminal = self.terminal(mode='vi')
        terminal.send('найди файлы'.encode() + b'\x06')
        terminal.expect(DONE)
        self.assertEqual(terminal.snapshot(), (self.result, len(self.result)))
        self.assertFalse(self.sentinel.exists())

    def test_cursor_uses_byte_positions_in_the_c_locale(self):
        terminal = self.terminal(LC_ALL='C')
        terminal.send(b'show files\x06')
        terminal.expect(DONE)
        self.assertEqual(terminal.snapshot(), (self.result, len(self.result.encode())))
        self.assertFalse(self.sentinel.exists())

    def test_empty_line_accepts_query_with_enter_before_opening_the_menu(self):
        terminal = self.terminal(CF_TEST_MENU='1')
        terminal.send(b'\x06')
        terminal.expect(b'cf> ')
        terminal.send('покажи файлы'.encode() + b'\r')
        terminal.expect(b'__CF_MOCK_MENU__')
        terminal.send(b'\r')
        terminal.expect(DONE)
        self.assertEqual(terminal.snapshot(), (self.result, len(self.result)))
        self.assertEqual(self.arguments(), ['--print', '--', 'покажи файлы'])
        self.assertFalse(self.sentinel.exists())

    def test_cancelled_selection_restores_line_and_cursor(self):
        terminal = self.terminal(CF_TEST_MENU='1')
        query = 'покажи файлы'
        terminal.send(query.encode() + b'\x02\x02\x06')
        terminal.expect(b'__CF_MOCK_MENU__')
        terminal.send(b'\x1b')
        terminal.expect(DONE)
        self.assertEqual(terminal.snapshot(), (query, len(query) - 2))

    def test_failed_search_restores_line_and_cursor(self):
        terminal = self.terminal(CF_TEST_STATUS='7')
        query = 'покажи файлы'
        terminal.send(query.encode() + b'\x02\x06')
        terminal.expect(DONE)
        self.assertEqual(terminal.snapshot(), (query, len(query) - 1))

    def test_failed_key_binding_does_not_close_a_shell_with_errexit(self):
        terminal = self.terminal(CF_TEST_STATUS='7')
        terminal.send(b'set -e\n')
        terminal.expect(PROMPT)
        query = 'покажи файлы'
        terminal.send(query.encode() + b'\x06')
        terminal.expect(DONE)
        self.assertEqual(terminal.snapshot(), (query, len(query)))
        terminal.send(b"printf '__SHELL_ALIVE__\\n'\r")
        terminal.expect(b'__SHELL_ALIVE__\r\n')
        terminal.expect(PROMPT)

    def test_empty_query_cancel_does_not_call_cli(self):
        terminal = self.terminal()
        terminal.send(b'\x06')
        terminal.expect(b'cf> ')
        terminal.send(b'\r')
        self.assertEqual(terminal.snapshot(), ('', 0))
        self.assertFalse(self.arguments_path.exists())

    def test_escape_in_menu_after_empty_prompt_leaves_the_outer_line_empty(self):
        terminal = self.terminal(CF_TEST_MENU='1')
        terminal.send(b'\x06')
        terminal.expect(b'cf> ')
        terminal.send('покажи файлы'.encode() + b'\r')
        terminal.expect(b'__CF_MOCK_MENU__')
        terminal.send(b'\x1b')
        terminal.expect(DONE)
        self.assertEqual(terminal.snapshot(), ('', 0))
        self.assertFalse(self.sentinel.exists())

    def test_ctrl_c_in_empty_query_restores_normal_readline_and_keeps_widget_usable(self):
        terminal = self.terminal()
        terminal.send(b'\x06')
        terminal.expect(b'cf> ')
        terminal.send(b'\x03')
        terminal.expect(PROMPT)
        terminal.send(b"printf '__AFTER_CANCEL__\\n'\r")
        terminal.expect(b'__AFTER_CANCEL__\r\n')
        terminal.expect(PROMPT)
        self.assertFalse(self.arguments_path.exists())
        terminal.send('покажи файлы'.encode() + b'\x06')
        terminal.expect(DONE)
        self.assertEqual(terminal.snapshot(), (self.result, len(self.result)))
        self.assertFalse(self.sentinel.exists())

    def test_real_python_selector_accepts_enter_without_executing_the_selected_command(self):
        terminal = self.terminal(CF_TEST_REAL_MENU='1')
        terminal.send(b'\x06')
        terminal.expect(b'cf> ')
        terminal.send('покажи файлы'.encode() + b'\r')
        terminal.expect('Выберите команду'.encode())
        terminal.send(b'\r')
        terminal.expect(DONE)
        self.assertEqual(terminal.snapshot(), (self.result, len(self.result)))
        self.assertFalse(self.sentinel.exists())

    def test_real_destructive_confirmation_accepts_russian_answer_with_enter(self):
        terminal = self.terminal(CF_TEST_REAL_MENU='1', CF_TEST_DESTRUCTIVE='1')
        terminal.send('покажи файлы'.encode() + b'\x06')
        terminal.expect('Выберите команду'.encode())
        terminal.send(b'\r')
        terminal.expect('Для подтверждения'.encode())
        terminal.send('да'.encode() + b'\r')
        terminal.expect(DONE)
        self.assertEqual(terminal.snapshot(), (self.result, len(self.result)))
        self.assertFalse(self.sentinel.exists())

    def test_regular_cf_prints_and_recall_is_editable_without_execution(self):
        terminal = self.terminal()
        terminal.send('cf "покажи файлы"\n'.encode())
        terminal.expect(DONE)
        terminal.expect(PROMPT)
        terminal.send(b'\x1b[A')
        self.assertEqual(terminal.snapshot(), (self.result, len(self.result)))
        self.assertEqual(self.arguments(), ['--print', 'покажи файлы'])
        self.assertFalse(self.sentinel.exists())

    def test_regular_cf_respects_disabled_history(self):
        terminal = self.terminal()
        terminal.send(b'set +o history\n')
        terminal.expect(PROMPT)
        terminal.send('cf "покажи файлы"\n'.encode())
        terminal.expect(DONE)
        terminal.expect(PROMPT)
        terminal.send(b'\x1b[A')
        line, _ = terminal.snapshot()
        self.assertNotEqual(line, self.result)
        self.assertFalse(self.sentinel.exists())

    def test_source_preserves_prompt_command_and_enter(self):
        terminal = self.terminal()
        terminal.send(b'''PROMPT_COMMAND='printf "__HOOK__"'; source "$CF_BASH_SCRIPT"; source "$CF_BASH_SCRIPT"\n''')
        terminal.expect(b'__HOOK__')
        terminal.expect(PROMPT)
        terminal.send(b"printf '__NORMAL_ENTER__\\n'\n")
        terminal.expect(b'__NORMAL_ENTER__\r\n')
        terminal.expect(b'__HOOK__')
        terminal.expect(PROMPT)


if __name__ == '__main__':
    unittest.main()
