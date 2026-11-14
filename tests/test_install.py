"""Проверки установки из релиза без сети, PyPI и настоящего окружения модели."""

from functools import partial
import gzip
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import threading
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).parents[1]
FAKE_PYTHON = r'''#!{python}
import json, os, pathlib, re, shutil, sys
args = sys.argv[1:]
log = pathlib.Path(os.environ['FAKE_LOG'])
probe = args[args.index('-c') + 1] if '-c' in args else ''
if 'ensurepip' in probe:
    missing = os.environ.get('FAKE_BOOTSTRAP_MISSING')
    if missing:
        print("ModuleNotFoundError: No module named '" + missing + "'", file=sys.stderr)
        sys.exit(19)
    sys.exit(0)
if args[:2] == ['-m', 'venv']:
    if os.environ.get('FAKE_VENV_FAILURE') == '1':
        print('The virtual environment was not created successfully because ensurepip is not available.', file=sys.stderr)
        sys.exit(20)
    target = pathlib.Path(args[-1]) / 'bin'
    target.mkdir(parents=True)
    shutil.copyfile(__file__, target / 'python')
    (target / 'python').chmod(0o755)
    (target / 'cf').write_text("#!/bin/bash\nprintf '%s\\n' \"$*|${CF_DB_DIR:-}|${CF_MODEL_DIR:-}\" >> \"$FAKE_LOG\"\ncase \"${1:-}\" in\n --version) printf 'cf %s\\n' \"$(cat \"$(dirname \"$0\")/version\")\" ;;\n --install-shell) printf '# fake shell integration\\n' ;;\n --stats) printf 'База: 1 команда, 1 формулировка\\n' ;;\n *) printf 'ls -la\\n' ;;\nesac\n")
    (target / 'cf').chmod(0o755)
    sys.exit(0)
if args[:2] == ['-m', 'pip']:
    with log.open('a') as handle:
        handle.write(json.dumps(args) + '\n')
    if os.environ.get('FAKE_PIP_FAILURE') == '1':
        sys.exit(17)
    wheel = next((pathlib.Path(arg) for arg in args if arg.endswith('.whl')), None)
    if wheel:
        pathlib.Path(__file__).with_name('version').write_text(wheel.name.split('-')[1])
    sys.exit(0)
if args and args[0] in ('-', '-c'):
    code = sys.stdin.read() if args[0] == '-' else args[1]
    if args[0] == '-c' and ('from cf' in code or 'import cf' in code):
        if os.environ.get('FAKE_RUNTIME_FAILURE') == '1':
            sys.exit(18)
        if '__version__' in code and 'print(' in code:
            print(pathlib.Path(__file__).with_name('version').read_text())
        sys.exit(0)
    if os.environ.get('FAKE_MACHINE'):
        code = 'import platform; platform.machine = lambda: ' + repr(os.environ['FAKE_MACHINE']) + '\n' + code
    if os.environ.get('FAKE_PYTHON_MINOR'):
        minor = int(os.environ['FAKE_PYTHON_MINOR'])
        code = ('import sys\nclass FakeVersion(tuple):\n    major = 3\n    minor = ' + str(minor)
                + '\nsys.version_info = FakeVersion((3, ' + str(minor) + ', 0, "final", 0))\n' + code)
    if args[0] == '-':
        args = ['-c', code, *args[1:]]
    else:
        args = ['-c', code, *args[2:]]
os.execv(os.environ['FAKE_REAL_PYTHON'], [os.environ['FAKE_REAL_PYTHON'], *args])
'''


class QuietHandler(SimpleHTTPRequestHandler):
    requests = []

    def do_GET(self):
        self.requests.append(self.path)
        super().do_GET()

    def log_message(self, *args):
        pass


def archive(path, files):
    with path.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w') as result:
            for name, content in files.items():
                item = tarfile.TarInfo(name)
                item.size = len(content)
                result.addfile(item, io.BytesIO(content))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReleaseInstallation:
    def __init__(self, directory):
        self.directory = directory
        self.web = directory / 'web'
        self.home = directory / 'home'
        self.home.mkdir()
        self.prefix = self.home / '.local/share/cf-ru'
        self.log = directory / 'commands.log'
        self.log.touch()
        fake = directory / 'fake-python'
        fake.write_text(FAKE_PYTHON.replace('{python}', sys.executable))
        fake.chmod(0o755)
        self.environment = {
            'PATH': '/usr/bin:/bin',
            'HOME': str(self.home),
            'LANG': 'C.UTF-8',
            'CF_PYTHON': str(fake),
            'FAKE_LOG': str(self.log),
            'FAKE_REAL_PYTHON': sys.executable,
        }
        QuietHandler.requests = []
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(self.web)))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.environment['CF_RELEASE_BASE_URL'] = f'http://127.0.0.1:{self.server.server_port}/releases'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def release(self, tag='v0.2.0', latest=True, malicious=None):
        version = tag.removeprefix('v').replace('-rc.', 'rc')
        destination = self.web / 'releases/download' / tag
        destination.mkdir(parents=True, exist_ok=True)
        wheel = destination / f'command_finder_ru-{version}-py3-none-any.whl'
        wheel.write_bytes(b'wheel fixture')
        model = destination / 'cf-model-fixture.tar.gz'
        archive(model, malicious or {
            'model.onnx': b'model', 'tokenizer.json': b'{}',
            'metadata.json': b'{}', 'checksums.json': b'{}',
        })
        database = destination / f'cf-data-{tag}.tar.gz'
        archive(database, {'cf.db': b'fixture database'})
        requirements = destination / 'requirements-runtime.txt'
        requirements.write_text('numpy==1.26.4\n')
        (destination / 'uninstall.sh').write_bytes((ROOT / 'uninstall.sh').read_bytes())
        (destination / 'install.sh').write_bytes((ROOT / 'install.sh').read_bytes())
        manifest = {
            'schema_version': 1, 'tag': tag, 'version': version,
            'platform': 'linux-x86_64', 'python_versions': ['3.12', '3.13'],
            'wheel': wheel.name, 'wheel_sha256': digest(wheel),
            'model': {'file': model.name, 'sha256': digest(model), 'identity': digest(model)},
            'database': {'file': database.name, 'sha256': digest(database)},
            'requirements': {'file': requirements.name, 'sha256': digest(requirements)},
        }
        (destination / 'release.json').write_text(json.dumps(manifest))
        self.checksums(destination)
        if latest:
            target = self.web / 'releases/latest/download'
            target.mkdir(parents=True, exist_ok=True)
            for path in destination.iterdir():
                shutil.copyfile(path, target / path.name)
        return destination

    def checksums(self, destination):
        (destination / 'SHA256SUMS').write_text(''.join(
            f'{digest(path)}  {path.name}\n'
            for path in sorted(destination.iterdir()) if path.name != 'SHA256SUMS'
        ))

    def run(self, *arguments, **environment):
        return subprocess.run(
            ['/bin/bash', str(ROOT / 'install.sh'), *arguments],
            env={**self.environment, **environment}, cwd=self.directory,
            capture_output=True, text=True, timeout=20,
        )


@pytest.fixture
def installation(tmp_path):
    result = ReleaseInstallation(tmp_path)
    try:
        yield result
    finally:
        result.close()


def success(result):
    assert result.returncode == 0, result.stdout + '\n' + result.stderr


def test_latest_installs_published_assets_without_build_or_seed(installation):
    installation.release()
    success(installation.run())
    current = installation.prefix / 'current'
    assert current.is_symlink()
    assert (current / 'data/cf.db').read_bytes() == b'fixture database'
    assert (current / 'model/model.onnx').read_bytes() == b'model'
    executable = installation.home / '.local/bin/cf'
    assert executable.is_symlink()
    result = subprocess.run([str(executable), '--version'], env=installation.environment,
                            capture_output=True, text=True, timeout=5)
    success(result)
    assert '0.2.0' in result.stdout
    log = installation.log.read_text()
    assert '--only-binary' in log
    assert '--no-cache-dir' in log
    assert '--seed' not in log
    assert '--prepare-model' not in log
    assert 'torch' not in log
    assert '/releases/latest/download/release.json' in QuietHandler.requests
    assert any('/releases/download/v0.2.0/' in path for path in QuietHandler.requests)


def test_installation_reports_numbered_steps_and_download_progress(installation):
    installation.release()
    result = installation.run()
    success(result)
    assert re.findall(r'\[([1-8])/8\]', result.stderr) == list('12345678')
    assert 'Проверка системы' in result.stderr
    assert 'Создание Python-окружения' in result.stderr
    assert 'Проверка модели и поиска' in result.stderr
    model_output = result.stderr.split('cf-model-fixture.tar.gz', 1)[1].split('[5/8]', 1)[0]
    assert re.search(r'100(?:[.,]0)?\s*%', model_output)
    assert re.search(r'\d+(?:[.,]\d+)?\s*(?:Б|КиБ|МиБ|ГиБ|B|KiB|MiB|GiB)', model_output)
    assert '\r' not in result.stderr


def test_download_without_content_length_reports_received_size(installation, monkeypatch):
    send_header = QuietHandler.send_header

    def without_length(self, keyword, value):
        if keyword.lower() != 'content-length':
            send_header(self, keyword, value)

    monkeypatch.setattr(QuietHandler, 'send_header', without_length)
    installation.release()
    result = installation.run()
    success(result)
    model_output = result.stderr.split('cf-model-fixture.tar.gz', 1)[1].split('[5/8]', 1)[0]
    assert re.search(r'\d+(?:[.,]\d+)?\s*(?:Б|КиБ|МиБ|ГиБ|B|KiB|MiB|GiB)', model_output)
    assert not re.search(r'\d+\s*%', model_output)


def test_failed_installation_identifies_step_without_reporting_success(installation):
    installation.release()
    result = installation.run(FAKE_PIP_FAILURE='1')
    assert result.returncode != 0
    assert '[6/8]' in result.stderr
    assert 'Установка приложения и зависимостей' in result.stderr
    assert re.search(r'Ошибка[^\n]*\[6/8\]', result.stderr)
    assert '[7/8]' not in result.stderr
    assert '[8/8]' not in result.stderr
    assert 'Установлен v0.2.0' not in result.stderr + result.stdout


def test_terminal_download_updates_received_bytes_before_completion(tmp_path):
    source = (ROOT / 'install.sh').read_text().split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    definitions = source.rsplit('\ntry:\n    main()', 1)[0]
    installer = {}
    exec(compile(definitions, str(ROOT / 'install.sh'), 'exec'), installer)

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    class Response(io.BytesIO):
        headers = {'Content-Length': '6'}

        def read(self, size=-1):
            return super().read(min(size, 2))

        read1 = read

    output = Terminal()
    ticks = iter((0, 0.25, 0.5, 0.75))
    installer['sys'] = SimpleNamespace(stderr=output)
    installer['time'] = SimpleNamespace(monotonic=lambda: next(ticks))
    installer['urlopen'] = lambda *args, **kwargs: Response(b'abcdef')
    destination = tmp_path / 'model.tar.gz'
    installer['download']('https://example.invalid/model.tar.gz', destination)
    assert destination.read_bytes() == b'abcdef'
    rendered = output.getvalue()
    assert '\r\x1b[2K' in rendered
    assert rendered.index('33%') < rendered.index('66%') < rendered.index('100%')
    assert rendered.endswith('\n')


def test_rc_requires_explicit_version_and_shell_is_not_duplicated(installation):
    installation.release('v0.2.0')
    installation.release('v0.3.0-rc.1', latest=False)
    success(installation.run('--version', 'v0.3.0-rc.1', '--shell'))
    success(installation.run('--version', 'v0.3.0-rc.1', '--shell'))
    bashrc = (installation.home / '.bashrc').read_text()
    assert sum(line.startswith('source ') for line in bashrc.splitlines()) == 1
    assert str(installation.prefix / 'cf.bash') in bashrc
    result = subprocess.run([str(installation.home / '.local/bin/cf'), '--version'],
                            env=installation.environment, capture_output=True, text=True, timeout=5)
    assert '0.3.0rc1' in result.stdout
    assert not any('/latest/' in path for path in QuietHandler.requests)


def test_shell_loader_makes_command_available_without_manual_path_edit(installation):
    installation.release()
    success(installation.run('--shell'))
    result = subprocess.run(
        ['/bin/bash', '-c', 'source "$1"; source "$1"; command -v cf; printf "%s\\n" "$PATH"',
         'bash', str(installation.prefix / 'cf.bash')],
        env=installation.environment, capture_output=True, text=True, timeout=5,
    )
    success(result)
    executable, path = result.stdout.splitlines()
    assert executable == str(installation.home / '.local/bin/cf')
    assert path.split(':').count(str(installation.home / '.local/bin')) == 1


def test_source_hint_activates_command_in_parent_bash(installation):
    installation.release()
    bashrc = installation.directory / 'shell config with spaces.bashrc'
    result = subprocess.run(
        ['/bin/bash', '-c', 'bash "$1" --shell && source "$2" && command -v cf',
         'bash', str(ROOT / 'install.sh'), str(bashrc)],
        env={**installation.environment, 'CF_BASHRC': str(bashrc)},
        cwd=installation.directory, capture_output=True, text=True, timeout=20,
    )
    success(result)
    assert f'source {shlex.quote(str(bashrc))}' in result.stderr
    assert result.stdout.strip() == str(installation.home / '.local/bin/cf')


@pytest.mark.parametrize('asset', ['cf-data-v0.3.0.tar.gz', 'command_finder_ru-0.3.0-py3-none-any.whl',
                                   'requirements-runtime.txt', 'release.json'])
def test_corrupt_download_preserves_existing_installation(installation, asset):
    installation.release()
    success(installation.run())
    current = (installation.prefix / 'current').resolve()
    release = installation.release('v0.3.0', latest=False)
    if asset == 'release.json':
        manifest = json.loads((release / asset).read_text())
        manifest['version'] = '0.9.0'
        (release / asset).write_text(json.dumps(manifest))
    else:
        (release / asset).write_bytes(b'corrupted after checksums')
    result = installation.run('--version', 'v0.3.0')
    assert result.returncode != 0
    assert (installation.prefix / 'current').resolve() == current
    assert (current / 'data/cf.db').read_bytes() == b'fixture database'


def test_pip_failure_preserves_existing_installation(installation):
    installation.release()
    success(installation.run())
    current = (installation.prefix / 'current').resolve()
    installation.release('v0.3.0', latest=False)
    result = installation.run('--version', 'v0.3.0', FAKE_PIP_FAILURE='1')
    assert result.returncode != 0
    assert (installation.prefix / 'current').resolve() == current


def test_archive_cannot_escape_installation(installation):
    installation.release(malicious={'../../escaped': b'not allowed'})
    result = installation.run()
    assert result.returncode != 0
    assert not (installation.prefix / 'current').exists()
    assert not list(installation.directory.rglob('escaped'))


def test_existing_foreign_command_is_preserved(installation):
    installation.release()
    executable = installation.home / '.local/bin/cf'
    executable.parent.mkdir(parents=True)
    executable.write_text('a different application')
    result = installation.run()
    assert result.returncode != 0
    assert executable.read_text() == 'a different application'


def test_invalid_version_is_rejected_before_download(installation):
    result = installation.run('--version', 'main')
    assert result.returncode != 0
    assert not QuietHandler.requests


@pytest.mark.parametrize('missing', ['venv', 'ensurepip'])
def test_missing_python_bootstrap_fails_before_download_or_installation_changes(installation, missing):
    result = installation.run(FAKE_BOOTSTRAP_MISSING=missing)
    assert result.returncode != 0
    assert re.search(r'Ошибка[^\n]*\[1/8\]', result.stderr)
    assert f'sudo apt-get install python3.{sys.version_info.minor}-venv' in result.stderr
    assert 'CF_PYTHON' in result.stderr
    assert 'returned non-zero exit status' not in result.stderr
    assert 'Traceback' not in result.stderr
    assert not QuietHandler.requests
    assert not installation.prefix.exists()
    assert not (installation.home / '.local/bin').exists()


def test_missing_bootstrap_advice_matches_selected_python_minor(installation):
    result = installation.run(FAKE_BOOTSTRAP_MISSING='ensurepip', FAKE_PYTHON_MINOR='13')
    assert result.returncode != 0
    assert 'sudo apt-get install python3.13-venv' in result.stderr
    assert 'python3.12-venv' not in result.stderr
    assert not QuietHandler.requests
    assert not installation.prefix.exists()


def test_late_missing_ensurepip_reports_actionable_error_and_preserves_current(installation):
    installation.release()
    success(installation.run())
    current = (installation.prefix / 'current').resolve()
    installation.release('v0.3.0', latest=False)
    result = installation.run('--version', 'v0.3.0', FAKE_VENV_FAILURE='1')
    assert result.returncode != 0
    assert re.search(r'Ошибка[^\n]*\[5/8\]', result.stderr)
    assert f'sudo apt-get install python3.{sys.version_info.minor}-venv' in result.stderr
    assert 'returned non-zero exit status' not in result.stderr
    assert '[6/8]' not in result.stderr
    assert 'Установлен v0.3.0' not in result.stderr
    assert (installation.prefix / 'current').resolve() == current


def test_failed_runtime_validation_preserves_existing_installation(installation):
    installation.release()
    success(installation.run())
    current = (installation.prefix / 'current').resolve()
    installation.release('v0.3.0', latest=False)
    result = installation.run('--version', 'v0.3.0', FAKE_RUNTIME_FAILURE='1')
    assert result.returncode != 0
    assert (installation.prefix / 'current').resolve() == current


def test_unchanged_model_is_reused_across_releases(installation):
    installation.release()
    success(installation.run())
    original_model = (installation.prefix / 'current/model').resolve()
    installation.release('v0.3.0', latest=False)
    QuietHandler.requests.clear()
    result = installation.run('--version', 'v0.3.0')
    success(result)
    assert '[4/8]' in result.stderr
    assert 'Готовая модель уже установлена' in result.stderr
    assert (installation.prefix / 'current/model').resolve() == original_model
    assert not any(path.endswith('/cf-model-fixture.tar.gz') for path in QuietHandler.requests)


def test_uninstall_preserves_data_unless_purged(installation):
    installation.release()
    success(installation.run('--shell'))
    current = (installation.prefix / 'current').resolve()
    uninstaller = installation.prefix / 'uninstall.sh'
    result = subprocess.run(['/bin/bash', str(uninstaller)], env=installation.environment,
                            capture_output=True, text=True, timeout=10)
    success(result)
    assert not (installation.home / '.local/bin/cf').exists()
    assert 'source ' not in (installation.home / '.bashrc').read_text()
    assert (current / 'data/cf.db').exists()
    assert not (current / 'venv').exists()
    success(installation.run())
    result = subprocess.run(['/bin/bash', str(uninstaller), '--purge'], env=installation.environment,
                            capture_output=True, text=True, timeout=10)
    success(result)
    assert not installation.prefix.exists()


def test_latest_never_installs_a_release_candidate(installation):
    installation.release('v0.3.0-rc.1')
    result = installation.run()
    assert result.returncode != 0
    assert not (installation.prefix / 'current').exists()


def test_installer_can_run_from_standard_input_without_checkout(installation):
    installation.release()
    result = subprocess.run(['/bin/bash', '-s', '--'], input=(ROOT / 'install.sh').read_text(),
                            env=installation.environment, cwd=installation.directory,
                            capture_output=True, text=True, timeout=20)
    success(result)
    assert (installation.home / '.local/bin/cf').is_symlink()


def test_unsupported_architecture_is_rejected_before_download(installation):
    result = installation.run(FAKE_MACHINE='aarch64')
    assert result.returncode != 0
    assert 'x86_64' in result.stdout + result.stderr
    assert not QuietHandler.requests


def test_failed_first_install_can_be_retried(installation):
    installation.release()
    result = installation.run(FAKE_PIP_FAILURE='1')
    assert result.returncode != 0
    assert not (installation.prefix / 'current').exists()
    success(installation.run())
    assert (installation.prefix / 'current').is_symlink()


def test_bad_shell_config_preserves_current_installation(installation):
    installation.release()
    success(installation.run())
    current = (installation.prefix / 'current').resolve()
    installation.release('v0.3.0', latest=False)
    invalid_bashrc = installation.directory / 'bashrc-directory'
    invalid_bashrc.mkdir()
    result = installation.run('--version', 'v0.3.0', '--shell', CF_BASHRC=str(invalid_bashrc))
    assert result.returncode != 0
    assert (installation.prefix / 'current').resolve() == current


def test_installation_paths_with_spaces_work(installation):
    installation.release()
    prefix = installation.directory / 'custom install'
    success(installation.run('--shell', CF_INSTALL_DIR=str(prefix)))
    current = prefix / 'current'
    assert (current / 'data/cf.db').exists()
    executable = installation.home / '.local/bin/cf'
    result = subprocess.run([str(executable), '--version'], env=installation.environment,
                            capture_output=True, text=True, timeout=5)
    success(result)
    assert '0.2.0' in result.stdout
    result = subprocess.run(['/bin/bash', str(prefix / 'uninstall.sh'), '--purge'],
                            env=installation.environment, capture_output=True, text=True, timeout=10)
    success(result)
    assert not prefix.exists()


def test_unmarked_nonempty_install_directory_is_not_claimed(installation):
    installation.release()
    prefix = installation.directory / 'existing-directory'
    prefix.mkdir()
    unrelated = prefix / 'personal.txt'
    unrelated.write_text('unrelated data')
    result = installation.run(CF_INSTALL_DIR=str(prefix))
    assert result.returncode != 0
    assert unrelated.read_text() == 'unrelated data'
    assert not (prefix / 'install.json').exists()
    assert not (installation.home / '.local/bin/cf').exists()
