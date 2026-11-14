"""Execute corrected read/text recipes only against isolated Linux fixtures."""

import gzip
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

import pytest


DATA = Path(__file__).parent.parent / "src/cf/data/commands"


def pattern(category, command_index, pattern_index):
    source = json.loads((DATA / f"{category}.json").read_text(encoding="utf-8"))
    return source["commands"][command_index]["patterns"][pattern_index]


def run_recipe(category, command_index, pattern_index, directory, *, env=None):
    command = pattern(category, command_index, pattern_index)["command"]
    allowed = {
        ("filesystem", 5, 2),
        *(('filesystem', 8, index) for index in range(1, 5)),
        *(('search', 0, index) for index in (0, 2, 5)),
        ("search", 9, 3), ("search", 9, 4), ("search", 11, 3), ("search", 11, 4),
        ("text_processing", 1, 0), ("text_processing", 1, 1),
        ("text_processing", 2, 2), ("text_processing", 5, 3),
        ("text_processing", 23, 2),
    }
    assert (category, command_index, pattern_index) in allowed
    for forbidden in ("sudo", "/etc/", "/dev/", "/mnt/", "find /"):
        assert forbidden not in command
    environment = {"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8", **(env or {})}
    return subprocess.run(["/bin/bash", "-c", command], cwd=directory, env=environment,
                          text=True, capture_output=True, timeout=10)


@pytest.mark.parametrize("index,value", [(1, "size"), (2, "inode"), (3, "mtime"), (4, "mode")])
def test_gnu_stat_fields_have_the_requested_meaning(tmp_path, index, value):
    target = tmp_path / "file.txt"
    target.write_bytes(b"one\ntwo")
    target.chmod(0o640)
    attributes = target.stat()
    expected = {"size": "7", "inode": str(attributes.st_ino), "mtime": str(int(attributes.st_mtime)), "mode": "640"}
    result = run_recipe("filesystem", 8, index, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected[value]


def test_rmdir_ignores_nonempty_directory_but_not_a_missing_one(tmp_path):
    directory = tmp_path / "dir"
    directory.mkdir()
    (directory / "data").write_text("retain")
    assert run_recipe("filesystem", 5, 2, tmp_path).returncode == 0
    assert (directory / "data").read_text() == "retain"
    (directory / "data").unlink()
    assert run_recipe("filesystem", 5, 2, tmp_path).returncode == 0
    assert not directory.exists()
    assert run_recipe("filesystem", 5, 2, tmp_path).returncode != 0


def test_find_extension_excludes_similarly_named_directories(tmp_path):
    (tmp_path / "real.py").write_text("print(1)")
    (tmp_path / "not_a_file.py").mkdir()
    result = run_recipe("search", 0, 0, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["./real.py"]


def test_find_delete_does_not_remove_matching_empty_directories(tmp_path):
    (tmp_path / "cache.tmp").write_text("old")
    (tmp_path / "keep.tmp").mkdir()
    result = run_recipe("search", 0, 2, tmp_path)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "cache.tmp").exists()
    assert (tmp_path / "keep.tmp").is_dir()


def test_find_exec_has_a_working_terminator_and_compresses_files(tmp_path):
    (tmp_path / "app.log").write_text("event\n")
    (tmp_path / "folder.log").mkdir()
    result = run_recipe("search", 0, 5, tmp_path)
    assert result.returncode == 0, result.stderr
    assert gzip.decompress((tmp_path / "app.log.gz").read_bytes()) == b"event\n"
    assert (tmp_path / "folder.log").is_dir()


@pytest.mark.skipif(not Path("/usr/bin/rg").is_file(), reason="Linux ripgrep is not installed")
def test_bulk_replace_preserves_unusual_filenames_and_all_occurrences(tmp_path):
    names = ["two words.txt", "quote'file.txt", "line\nbreak.txt", "-option.txt"]
    for name in names:
        (tmp_path / name).write_text("oldFunc oldFunc\n")
    (tmp_path / "unchanged.txt").write_text("leave alone")
    result = run_recipe("search", 9, 3, tmp_path)
    assert result.returncode == 0, result.stderr
    assert all((tmp_path / name).read_text() == "newFunc newFunc\n" for name in names)
    assert (tmp_path / "unchanged.txt").read_text() == "leave alone"
    assert run_recipe("search", 9, 3, tmp_path).returncode == 0


@pytest.mark.skipif(not Path("/usr/bin/rg").is_file(), reason="Linux ripgrep is not installed")
def test_rg_counts_literal_occurrences_not_lines_or_regex_dots(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "app.js").write_text("console.log console.log\nconsoleXlog\nconsole.log\n")
    result = run_recipe("search", 9, 4, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "src/app.js:3"


def test_fd_pattern_is_an_exact_dotenv_filename_and_includes_ignored_files():
    command = shlex.split(pattern("search", 8, 2)["command"])
    expression = re.compile(command[-1])
    assert expression.fullmatch(".env")
    assert not expression.fullmatch("xenv")
    assert not expression.fullmatch(".env.local")
    assert "-H" in command and "-I" in command
    assert command[command.index("-t") + 1] == "f"


def fake_fzf(tmp_path, script):
    tools = tmp_path / "tools"
    tools.mkdir()
    binary = tools / "fzf"
    binary.write_text("#!/bin/sh\n" + script)
    binary.chmod(0o755)
    return {"PATH": f"{tools}:/usr/bin:/bin"}


def git(directory, *arguments):
    environment = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    return subprocess.run(["/usr/bin/git", *arguments], cwd=directory, env=environment,
                          text=True, capture_output=True, check=True)


@pytest.mark.skipif(not Path("/usr/bin/git").is_file(), reason="Linux Git is not installed")
def test_branch_picker_uses_clean_branch_names_and_quotes_selection(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-q", "--allow-empty", "-m", "fixture")
    branch = "feature/quote'branch"
    git(tmp_path, "branch", branch)
    environment = fake_fzf(tmp_path, '/usr/bin/cat >/dev/null\n/usr/bin/printf "%s\\n" "$CF_TEST_BRANCH"\n')
    environment.update({"CF_TEST_BRANCH": branch, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"})
    result = run_recipe("search", 11, 3, tmp_path, env=environment)
    assert result.returncode == 0, result.stderr
    assert git(tmp_path, "branch", "--show-current").stdout.strip() == branch


@pytest.mark.skipif(not Path("/usr/bin/git").is_file(), reason="Linux Git is not installed")
def test_branch_picker_cancellation_does_not_change_branch(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    environment = fake_fzf(tmp_path, "exit 130\n")
    environment.update({"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"})
    result = run_recipe("search", 11, 3, tmp_path, env=environment)
    assert result.returncode != 0
    assert git(tmp_path, "branch", "--show-current").stdout.strip() == "main"


def test_selected_log_deletion_handles_spaces_and_newlines(tmp_path):
    names = ["two words.log", "line\nbreak.log"]
    for name in names:
        (tmp_path / name).write_text("remove")
    (tmp_path / "keep.txt").write_text("retain")
    environment = fake_fzf(tmp_path, "/usr/bin/cat\n")
    result = run_recipe("search", 11, 4, tmp_path, env=environment)
    assert result.returncode == 0, result.stderr
    assert all(not (tmp_path / name).exists() for name in names)
    assert (tmp_path / "keep.txt").read_text() == "retain"


def test_sed_stdout_and_inplace_patterns_have_distinct_effects(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("old old foo foo\n")
    result = run_recipe("text_processing", 1, 0, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "new new foo foo\n"
    assert target.read_text() == "old old foo foo\n"
    result = run_recipe("text_processing", 1, 1, tmp_path)
    assert result.returncode == 0, result.stderr
    assert target.read_text() == "old old bar bar\n"


def test_awk_filter_uses_csv_input_fields(tmp_path):
    (tmp_path / "data.csv").write_text("a,b,150\nx,y,50\n")
    result = run_recipe("text_processing", 2, 2, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "a,b,150\n"


def test_sort_uses_only_second_column_and_preserves_equal_keys(tmp_path):
    (tmp_path / "data.csv").write_text("z,a,z\na,a,a\nb,b,x\n")
    result = run_recipe("text_processing", 5, 3, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "z,a,z\na,a,a\nb,b,x\n"


@pytest.mark.parametrize("contents,expected", [
    ("first\n\nsecond\n", ["second", "first"]),
    ("first\nline\n\nsecond", ["second", "first\nline"]),
    ("", []),
])
def test_paragraph_reversal_preserves_lines_inside_blocks(tmp_path, contents, expected):
    (tmp_path / "document.txt").write_text(contents)
    result = run_recipe("text_processing", 23, 2, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().split("\n\n") == expected if expected else result.stdout == ""


@pytest.mark.parametrize("exists,expected_status", [(True, 1), (False, 0)])
def test_group_removal_postcondition_succeeds_only_for_an_absent_group(tmp_path, exists, expected_status):
    command = pattern("permissions", 16, 3)["command"]
    assert command.startswith("sudo groupdel testgroup && ")
    tools = tmp_path / "tools"
    tools.mkdir()
    getent = tools / "getent"
    getent.write_text(f"#!/bin/sh\nexit {0 if exists else 2}\n")
    getent.chmod(0o755)
    safe_command = command.replace("sudo groupdel testgroup", "/usr/bin/true", 1)
    result = subprocess.run(["/bin/bash", "-c", safe_command], cwd=tmp_path,
                            env={"PATH": f"{tools}:/usr/bin:/bin"}, timeout=5)
    assert result.returncode == expected_status


def test_directory_acl_grants_traversal_and_destructive_recipes_are_marked():
    for pi in (1, 2):
        entry = pattern("permissions", 7, pi)
        assert "g:devteam:rwx" in shlex.split(entry["command"])
    for category, ci, indices in (
        ("filesystem", 5, range(5)), ("filesystem", 22, range(5)),
        ("filesystem", 24, (2,)), ("disk", 2, (1, 2, 3)),
        ("permissions", 13, range(4)), ("permissions", 16, range(4)),
        ("search", 9, (3,)), ("search", 11, (4,)),
    ):
        for pi in indices:
            assert pattern(category, ci, pi).get("destructive") is True


def test_root_scan_templates_do_not_cross_filesystems_or_enter_host_mounts():
    find = shlex.split(pattern("search", 0, 3)["command"])
    assert find[:2] == ["find", "/"]
    assert "-xdev" in find
    assert find[find.index("-path") + 1] == "/mnt"
    assert find.index("-prune") < find.index("-type")
    for pi in (0, 2):
        ncdu = shlex.split(pattern("disk", 19, pi)["command"])
        assert "-x" in ncdu and ncdu[ncdu.index("--exclude") + 1] == "/mnt"
