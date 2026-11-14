"""Exercise corrected command recipes only in disposable local fixtures."""

import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).absolute().parent.parent


def recipe(category, command_index, pattern_index):
    data = json.loads((ROOT / f'src/cf/data/commands/{category}.json').read_text())
    return data['commands'][command_index]['patterns'][pattern_index]


class GitRecipeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cf-git-recipes-')
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        self.env = {
            'PATH': '/usr/bin:/bin',
            'LC_ALL': 'C.UTF-8',
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': '/dev/null',
            'GIT_AUTHOR_NAME': 'Fixture Author',
            'GIT_AUTHOR_EMAIL': 'author@example.invalid',
            'GIT_COMMITTER_NAME': 'Fixture Committer',
            'GIT_COMMITTER_EMAIL': 'committer@example.invalid',
        }
        self.git('init', '-q', '-b', 'main')
        (self.repo / 'base.txt').write_text('base\n')
        self.commit('base')

    def git(self, *args, author=None):
        env = dict(self.env)
        if author:
            env['GIT_AUTHOR_NAME'] = author
        return subprocess.run(['/usr/bin/git', *args], cwd=self.repo, env=env,
                              check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, message, author=None):
        self.git('add', '-A')
        self.git('commit', '-q', '-m', message, author=author)
        return self.git('rev-parse', 'HEAD')

    def run_recipe(self, ci, pi, replacements=None):
        command = recipe('git', ci, pi)['command']
        for before, after in (replacements or {}).items():
            command = command.replace(before, after)
        return subprocess.run(['/bin/bash', '-c', command], cwd=self.repo, env=self.env,
                              check=True, capture_output=True, text=True).stdout

    def add_file(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text)

    def test_amend_message_keeps_staged_changes_out_of_commit(self):
        before_tree = self.git('rev-parse', 'HEAD^{tree}')
        self.add_file('base.txt', 'base\nstaged but not committed\n')
        self.git('add', 'base.txt')
        self.run_recipe(3, 2)
        self.assertEqual(self.git('rev-parse', 'HEAD^{tree}'), before_tree)
        self.assertEqual(self.git('log', '-1', '--format=%B'), 'Updated message')
        self.assertIn('staged but not committed', self.git('diff', '--cached'))
        self.assertTrue(recipe('git', 3, 2)['destructive'])

    def test_descendant_details_do_not_fall_back_to_head_for_empty_range(self):
        head = self.git('rev-parse', 'HEAD')
        self.assertEqual(self.run_recipe(12, 8, {'<hash>': head}), '')

    def test_first_descendant_in_linear_history_and_its_details(self):
        root = self.git('rev-parse', 'HEAD')
        self.add_file('next.txt', 'next\n')
        next_hash = self.commit('first descendant')
        self.add_file('later.txt', 'later\n')
        self.commit('second descendant')
        self.assertEqual(self.run_recipe(12, 7, {'<hash>': root}).strip(), next_hash)
        shown = self.run_recipe(12, 8, {'<hash>': root})
        self.assertIn('first descendant', shown)
        self.assertNotIn('second descendant', shown)

    def test_commit_message_recipe_excludes_headers_and_stats(self):
        head = self.git('rev-parse', 'HEAD')
        self.assertEqual(self.run_recipe(24, 1, {'abc1234': head}).strip(), 'base')

    def changed_paths(self):
        self.git('switch', '-q', '-c', 'feature')
        files = {
            'root file.txt': 'root\n',
            'asset.bin': b'\x00binary',
            'src/lib one/file name.py': 'one\ntwo\n',
            'src/pkg/tab\tname.txt': 'tabbed name\n',
            'src/pkg/new\nline.txt': 'newline name\n',
            'src/pkg/deeper/four.py': 'four\n',
            'src/pkg/deeper/even/more.py': 'more\n',
        }
        for name, text in files.items():
            self.add_file(name, text)
        self.commit('files with whitespace')

    @staticmethod
    def parse_stats(output):
        result = {}
        for line in output.splitlines():
            match = re.fullmatch(r'(.*?)\s+(\d+) files \+(\d+) -(\d+)', line)
            if not match:
                raise AssertionError(f'Unexpected statistics row: {line!r}')
            result[match[1]] = tuple(map(int, match.groups()[1:]))
        return result

    def test_top_level_counts_keep_whitespace_paths_and_group_root_files(self):
        self.changed_paths()
        counts = {}
        for line in self.run_recipe(14, 11).splitlines():
            match = re.fullmatch(r'\s*(\d+) (.+)', line)
            self.assertIsNotNone(match)
            counts[match[2]] = int(match[1])
        self.assertEqual(counts, {'.': 2, 'src': 5})

    def test_top_level_numstat_counts_binary_files_without_fictitious_lines(self):
        self.changed_paths()
        self.assertEqual(self.parse_stats(self.run_recipe(14, 12)), {
            '.': (2, 1, 0), 'src': (5, 6, 0),
        })

    def test_depth_groups_directories_and_never_uses_basename_as_directory(self):
        self.changed_paths()
        self.assertEqual(self.parse_stats(self.run_recipe(14, 13)), {
            '.': (2, 1, 0), 'src/lib one': (1, 2, 0),
            'src/pkg': (2, 2, 0), 'src/pkg/deeper': (2, 2, 0),
        })

    def test_author_stats_keep_full_author_names_and_whitespace_filenames(self):
        self.git('switch', '-q', '-c', 'feature')
        self.add_file('src/one file.txt', 'one\ntwo\n')
        self.commit('first change', author='Ann Author With Spaces')
        self.add_file('src/one file.txt', 'one\ntwo\nthree\n')
        self.add_file('next file.txt', 'next\n')
        self.commit('second change', author='Bob Other Person')
        totals = {}
        for line in self.run_recipe(14, 14).splitlines():
            match = re.fullmatch(r'(.*?)\s+\+(\d+)\s+-(\d+)', line)
            self.assertIsNotNone(match, repr(line))
            totals[match[1]] = (int(match[2]), int(match[3]))
        self.assertEqual(totals, {'Ann Author With Spaces': (2, 0), 'Bob Other Person': (2, 0)})


class ShellRecipeTests(unittest.TestCase):
    def test_alias_guard_does_not_treat_a_function_as_an_alias(self):
        command = recipe('misc', 21, 3)['command']
        result = subprocess.run(['/bin/bash', '-c', 'll() { :; }; ' + command],
                                env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, '')

    def test_alias_guard_removes_an_existing_alias(self):
        command = recipe('misc', 21, 3)['command']
        result = subprocess.run(['/bin/bash', '-c', "alias ll='printf hi'; " + command + '; alias ll'],
                                env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not found', result.stderr)

    def test_installed_package_filter_excludes_removed_and_half_configured(self):
        command = recipe('package_managers', 2, 1)['command']
        filter_command = command.split('|', 1)[1]
        result = subprocess.run(['/bin/bash', '-c', filter_command],
                                env={'PATH': '/usr/bin:/bin'},
                                input='ii  good 1 amd64 installed package\nhi  held 1 amd64 held installed package\nri  remove-later 1 amd64 still installed\niiR  damaged 1 amd64 needs reinstallation\nrc  removed 1 amd64 leftover config\niF  broken 1 amd64 half configured\n',
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, 'ii  good 1 amd64 installed package\nhi  held 1 amd64 held installed package\nri  remove-later 1 amd64 still installed\n')


if __name__ == '__main__':
    unittest.main()
