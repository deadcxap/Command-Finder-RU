"""Regression checks for corrected recipes using local fixtures only."""

import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys

import pytest


DATA = Path(__file__).resolve().parents[1] / 'src/cf/data/commands'
pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux-specific command recipes')


def pattern(category, command_index, pattern_index):
    data = json.loads((DATA / f'{category}.json').read_text())
    return data['commands'][command_index]['patterns'][pattern_index]


def executable(path, body):
    path.write_text('#!/bin/sh\n' + body + '\n')
    path.chmod(0o755)


def shell(command, directory, environment):
    return subprocess.run(['/bin/bash', '-c', command], cwd=directory, env=environment,
                          check=True, capture_output=True, text=True, timeout=10).stdout


def test_cron_append_preserves_other_jobs_and_comments(tmp_path):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    cron = tmp_path / 'crontab.txt'
    existing = '# existing schedule\n15 8 * * * /home/user/other-task.sh\n'
    cron.write_text(existing)
    executable(bin_dir / 'crontab', 'case "$1" in\n-l) /usr/bin/cat "$CRON_FIXTURE";;\n-) /usr/bin/cat > "$CRON_FIXTURE.new"; /usr/bin/mv "$CRON_FIXTURE.new" "$CRON_FIXTURE";;\n*) exit 64;;\nesac')
    environment = {**os.environ, 'PATH': f'{bin_dir}:/usr/bin:/bin', 'CRON_FIXTURE': str(cron)}
    shell(pattern('process', 25, 3)['command'], tmp_path, environment)
    assert cron.read_text() == existing + '0 0 * * * /home/user/backup.sh\n'


def test_cron_append_also_works_when_no_previous_crontab_exists(tmp_path):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    cron = tmp_path / 'crontab.txt'
    executable(bin_dir / 'crontab', 'case "$1" in\n-l) if [ -f "$CRON_FIXTURE" ]; then /usr/bin/cat "$CRON_FIXTURE"; else exit 1; fi;;\n-) /usr/bin/cat > "$CRON_FIXTURE.new"; /usr/bin/mv "$CRON_FIXTURE.new" "$CRON_FIXTURE";;\n*) exit 64;;\nesac')
    environment = {**os.environ, 'PATH': f'{bin_dir}:/usr/bin:/bin', 'CRON_FIXTURE': str(cron)}
    shell(pattern('process', 25, 3)['command'], tmp_path, environment)
    assert cron.read_text() == '0 0 * * * /home/user/backup.sh\n'


def test_top_process_recipe_outputs_twenty_processes_plus_header(tmp_path):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    listing = tmp_path / 'processes.txt'
    listing.write_text('USER PID %MEM\n' + ''.join(f'user {i} {100 - i}\n' for i in range(1, 31)))
    executable(bin_dir / 'ps', '/usr/bin/cat "$PS_FIXTURE"')
    environment = {**os.environ, 'PATH': f'{bin_dir}:/usr/bin:/bin', 'PS_FIXTURE': str(listing)}
    lines = shell(pattern('process', 0, 3)['command'], tmp_path, environment).splitlines()
    assert lines[0] == 'USER PID %MEM'
    assert len(lines[1:]) == 20
    assert lines[-1].split()[1] == '20'


def test_usb_log_recipe_filters_before_taking_recent_lines(tmp_path):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    log = tmp_path / 'kernel.log'
    log.write_text(''.join(f'usb device {i}\nCPU unrelated {i}\n' for i in range(25)))
    executable(bin_dir / 'dmesg', '/usr/bin/cat "$KERNEL_FIXTURE"')
    environment = {**os.environ, 'PATH': f'{bin_dir}:/usr/bin:/bin', 'KERNEL_FIXTURE': str(log)}
    lines = shell(pattern('system', 9, 3)['command'], tmp_path, environment).splitlines()
    assert lines == [f'usb device {i}' for i in range(5, 25)]


@pytest.fixture
def listener():
    server = socket.socket()
    try:
        server.bind(('127.0.0.1', 0))
        server.listen(4)
    except OSError:
        server.close()
        pytest.skip('Loopback sockets are unavailable')
    try:
        yield server
    finally:
        server.close()


@pytest.mark.skipif(not Path('/usr/bin/ss').is_file(), reason='Linux ss is not installed')
def test_listening_socket_filter_selects_the_exact_local_port(listener):
    port = listener.getsockname()[1]
    command = pattern('networking', 14, 2)['command'].replace('8080', str(port))
    argv = shlex.split(command)
    argv[0] = '/usr/bin/ss'
    output = subprocess.run(argv, check=True, capture_output=True, text=True, timeout=10).stdout
    rows = output.splitlines()[1:]
    assert len(rows) == 1
    assert rows[0].split()[3].rsplit(':', 1)[1] == str(port)


@pytest.mark.skipif(not Path('/usr/bin/ss').is_file(), reason='Linux ss is not installed')
def test_connection_counter_ignores_header_and_counts_server_sockets_only(listener, tmp_path):
    port = listener.getsockname()[1]
    clients, accepted = [], []
    try:
        for _ in range(2):
            client = socket.create_connection(('127.0.0.1', port), timeout=3)
            clients.append(client)
            connection, _ = listener.accept()
            accepted.append(connection)
        command = pattern('process', 17, 4)['command'].replace('8080', str(port))
        watch_arguments = shlex.split(command)
        inner = watch_arguments[-1]
        output = shell(inner, tmp_path, {**os.environ, 'PATH': '/usr/bin:/bin'})
        assert int(output.strip()) == 2
    finally:
        for connection in clients + accepted:
            connection.close()


def test_process_termination_and_crontab_removal_require_confirmation():
    data = json.loads((DATA / 'process.json').read_text())
    for command in data['commands']:
        for entry in command['patterns']:
            argv = shlex.split(entry['command'])
            kills_processes = argv[0] in ('killall', 'pkill') and '-HUP' not in argv
            removes_schedule = argv[:2] == ['crontab', '-r']
            if kills_processes or removes_schedule:
                assert entry.get('destructive') is True, entry['command']
