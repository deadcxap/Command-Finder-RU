"""Exercise corrected, explicitly selected Perl recipes only in temporary fixtures."""

import csv
import io
import itertools
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
import unittest
from contextlib import contextmanager
from urllib.parse import quote


SOURCE = Path(__file__).parents[1] / 'src/cf/data/commands/perl_oneliners.json'
PERL = '/usr/bin/perl'
SAFE_RECIPES = {
    (0, 0), (0, 2), (0, 3), (0, 4), (0, 5), (0, 9),
    (1, 1), (2, 3), (2, 12), (3, 4), (3, 6),
    (4, 1), (4, 3), (4, 5), (4, 6), (4, 7), (4, 10), (4, 11),
    (5, 5), (5, 7), (5, 9), (6, 5), (6, 9),
    (8, 1), (8, 5), (8, 6), (9, 0), (9, 3), (9, 7),
    (10, 2), (10, 3), (11, 7), (13, 3),
    (14, 4), (14, 8), (14, 9), (14, 10), (15, 5),
    (16, 7), (16, 9), (17, 4), (17, 7), (17, 9),
    (18, 1), (18, 2), (18, 3), (18, 6), (19, 1), (19, 6), (19, 7),
}
COMPILE_RECIPES = {
    (0, 0), (0, 1), (0, 2), (0, 3), (0, 4), (0, 5), (0, 9), (1, 1),
    (2, 3), (2, 12), (3, 4), (3, 6), (3, 8), (4, 1), (4, 3), (4, 5),
    (4, 6), (4, 7), (4, 10), (4, 11), (5, 5), (5, 7), (5, 8), (5, 9), (6, 5), (6, 7), (6, 9),
    (8, 1), (8, 5), (8, 6), (9, 0), (9, 3), (9, 6), (9, 7),
    (10, 0), (10, 1), (10, 2), (10, 3), (10, 4), (10, 5), (10, 6),
    (11, 7), (13, 3), (14, 4), (14, 8), (14, 9), (14, 10), (15, 5),
    (16, 7), (16, 9), (17, 4), (17, 7), (17, 9), (18, 1), (18, 2),
    (18, 3), (18, 6), (18, 9), (19, 1), (19, 6), (19, 7),
}


@unittest.skipUnless(Path(PERL).is_file(), 'Linux Perl is not installed at /usr/bin/perl')
class PerlRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = json.loads(SOURCE.read_text(encoding='utf-8'))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cf-perl-recipes-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def file(self, name, content):
        path = self.directory / name
        path.write_bytes(content.encode('utf-8') if isinstance(content, str) else content)
        return path

    @contextmanager
    def project_fixture(self):
        previous = self.directory
        with tempfile.TemporaryDirectory(prefix='.cf-perl-tree-', dir=SOURCE.parents[4]) as directory:
            self.directory = Path(directory)
            try:
                yield
            finally:
                self.directory = previous

    def run_recipe(self, ci, pi, *, arguments=None, input_data=None, check=True):
        self.assertIn((ci, pi), SAFE_RECIPES)
        pattern = self.source['commands'][ci]['patterns'][2 * pi]
        argv = shlex.split(pattern['command'])
        if (ci, pi) == (6, 9):
            self.assertEqual(argv[:5], ['sort', '-n', 'data.txt', '|', 'perl'])
            sorted_input = subprocess.run(
                ['/usr/bin/sort', '-n', 'data.txt'], cwd=self.directory,
                capture_output=True, check=True, timeout=5,
                env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8', 'TZ': 'UTC'},
            ).stdout
            argv = argv[4:]
            input_data = sorted_input
        self.assertEqual(argv[0], 'perl')
        code_index = next(i for i, value in enumerate(argv[1:], 1) if value in ('-e', '-pe', '-ne', '-le', '-lne', '-lane', '-ane'))
        original_arguments = argv[code_index + 2:]
        argv = [PERL, *argv[1:code_index + 2]]
        if arguments is not None:
            if original_arguments[:1] == ['--']:
                argv.append('--')
            argv += arguments
        elif original_arguments[:1] == ['<']:
            self.assertEqual(original_arguments, ['<', 'data.json'])
            input_data = (self.directory / 'data.json').read_bytes()
        else:
            argv += original_arguments
        for argument in argv[code_index + 2:]:
            self.assertFalse(argument.startswith('/'), 'Fixture tests must not use absolute source paths')
            self.assertNotIn(argument, ('|', '>', '<', ';', '&&'))
        result = subprocess.run(
            argv, cwd=self.directory, capture_output=True,
            input=input_data.encode('utf-8') if isinstance(input_data, str) else input_data,
            timeout=5, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8', 'TZ': 'UTC'},
        )
        if check:
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        return result

    def test_first_line_replacement_resets_for_each_file(self):
        self.file('file1.txt', 'PATTERN first\nPATTERN second\n')
        self.file('file2.txt', 'PATTERN third\nPATTERN fourth')
        self.run_recipe(0, 9)
        self.assertEqual((self.directory / 'file1.txt').read_text(), 'REPLACE first\nPATTERN second\n')
        self.assertEqual((self.directory / 'file2.txt').read_text(), 'REPLACE third\nPATTERN fourth')

    def test_former_pie_recipes_actually_edit_the_fixture(self):
        examples = [
            (0, 0, 'sample.py', '\tprint(1)\n', '    print(1)\n'),
            (0, 2, '.env', 'DB_HOST=localhost\nOTHER=localhost\n', 'DB_HOST=production.host\nOTHER=localhost\n'),
            (0, 3, 'file.txt', 'before BEGIN\ninside\nEND after\n', 'before REPLACED after\n'),
            (0, 4, 'file.txt', b'a\r\nb\r\n', b'a\nb\n'),
            (0, 5, 'file.txt', 'width height wideheight\n', 'height width wideheight\n'),
        ]
        for ci, pi, name, content, expected in examples:
            with self.subTest(ci=ci, pi=pi):
                path = self.file(name, content)
                self.run_recipe(ci, pi, arguments=[name])
                self.assertEqual(path.read_bytes(), expected.encode() if isinstance(expected, str) else expected)

    def test_line_length_does_not_count_record_separator(self):
        self.file('file.txt', 'a' * 80 + '\n' + 'b' * 81 + '\n')
        for ci, pi in ((2, 3), (17, 4)):
            with self.subTest(ci=ci, pi=pi):
                self.assertEqual(self.run_recipe(ci, pi).stdout, ('b' * 81 + '\n').encode())

    def test_length_counts_utf8_characters_and_excludes_crlf(self):
        contents = ('я' * 80 + '\r\n' + 'ю' * 81 + '\r\n').encode()
        self.file('file.txt', contents)
        for ci, pi in ((2, 3), (17, 4)):
            with self.subTest(ci=ci, pi=pi):
                self.assertEqual(self.run_recipe(ci, pi).stdout, ('ю' * 81 + '\r\n').encode())

    def test_repeated_words_report_actual_starting_line(self):
        self.file('file.txt', 'one\nTHE the\nred\nred\n')
        self.assertEqual(self.run_recipe(2, 12).stdout, b'2: doubled THE\n3: doubled red\n')

    def test_padding_retains_unterminated_line_and_does_not_truncate(self):
        self.file('file.txt', 'short\n' + 'x' * 41 + '\nlast')
        output = self.run_recipe(3, 4).stdout.decode()
        self.assertEqual(output, 'short' + ' ' * 35 + '\n' + 'x' * 41 + '\nlast' + ' ' * 36 + '\n')

    def test_padding_uses_utf8_character_width(self):
        self.file('file.txt', 'я' * 20 + '\r\n' + 'ю' * 41)
        output = self.run_recipe(3, 4).stdout.decode()
        self.assertEqual(output, 'я' * 20 + ' ' * 20 + '\n' + 'ю' * 41 + '\n')

    def test_trimming_preserves_empty_lines_and_lf_or_crlf(self):
        contents = b'  first \t\n\n \t\r\n\tlast  '
        self.file('file.txt', contents)
        self.assertEqual(self.run_recipe(3, 6).stdout, b'first\n\n\r\nlast')
        for pi, expected in (
            (1, b'  first\n\n\r\n\tlast'),
            (2, b'first \t\n\n\r\nlast  '),
            (3, b'first\n\n\r\nlast'),
        ):
            with self.subTest(pi=pi):
                path = self.file('file.txt', contents)
                self.run_recipe(18, pi)
                self.assertEqual(path.read_bytes(), expected)

    def test_csv_reordering_does_not_move_newlines_inside_rows(self):
        self.file('data.csv', 'a,b,c\nx,y,\nlast,row,end')
        self.assertEqual(self.run_recipe(4, 1).stdout, b'c,a,b\n,x,y\nend,last,row\n')

    def test_simple_csv_to_tsv_preserves_trailing_empty_fields(self):
        self.file('data.csv', 'a,b,\nx,,z\n')
        self.assertEqual(self.run_recipe(4, 3).stdout, b'a\tb\t\nx\t\tz\n')

    def test_unique_csv_values_do_not_include_newlines(self):
        self.file('data.csv', 'one,A\ntwo,B\nthree,A\n')
        self.assertEqual(self.run_recipe(4, 5).stdout, b'A\nB\n')

    def test_csv_json_output_escapes_text_and_preserves_utf8(self):
        self.file('data.csv', 'name,comment\nАда,says "hi" \\end\nБоб,\n')
        value = json.loads(self.run_recipe(4, 6).stdout)
        self.assertEqual(value, [{'name': 'Ада', 'comment': 'says "hi" \\end'}, {'name': 'Боб', 'comment': ''}])

    def test_csv_json_crlf_does_not_leak_carriage_returns(self):
        self.file('data.csv', b'name,value\r\nAda,hello\r\nBob,\r\n')
        self.assertEqual(json.loads(self.run_recipe(4, 6).stdout),
                         [{'name': 'Ada', 'value': 'hello'}, {'name': 'Bob', 'value': ''}])

    def test_tsv_column_deletion_edits_named_file_and_retains_short_rows(self):
        path = self.file('data.tsv', b'a\tb\tc\td\te\tf\r\na\tb\tc\r\nx\ty\t\r\n')
        self.run_recipe(4, 10)
        self.assertEqual(path.read_bytes(), b'a\tb\tc\td\te\na\tb\tc\nx\ty\t\n')

    def test_tsv_column_insertion_preserves_record_and_empty_field_boundaries(self):
        path = self.file('data.tsv', b'a\tb\tc\r\na\tb\tc\td\te\r\nx\ty\tz\t\r\none\ttwo\r\n')
        self.run_recipe(4, 11)
        self.assertEqual(path.read_bytes(),
                         b'a\tb\tc\tnew_value\na\tb\tc\tnew_value\td\te\nx\ty\tz\tnew_value\t\none\ttwo\t\tnew_value\n')

    def test_recursive_js_edit_skips_directories_with_matching_suffix(self):
        with self.project_fixture():
            self.file('app.js', 'oldFunction()\n')
            (self.directory / 'directory.js').mkdir()
            self.file('directory.js/nested.js', 'oldFunction()\n')
            argv = shlex.split(self.source['commands'][0]['patterns'][2]['command'])
            self.assertEqual(argv[0], 'find')
            argv[0] = '/usr/bin/find'
            argv[argv.index('perl')] = PERL
            result = subprocess.run(argv, cwd=self.directory, capture_output=True, timeout=5,
                                    env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'})
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stderr, b'')
            self.assertTrue((self.directory / 'directory.js').is_dir())
            self.assertEqual((self.directory / 'app.js').read_text(), 'newFunction()\n')
            self.assertEqual((self.directory / 'directory.js/nested.js').read_text(), 'newFunction()\n')

    def test_file_find_edits_nested_html_files_and_keeps_backups(self):
        with self.project_fixture():
            (self.directory / 'directory.html').mkdir()
            (self.directory / 'nested').mkdir()
            self.file('directory.html/child.html', 'old one\n')
            self.file('nested/a file.html', 'old two\n')
            self.file('nested/plain.txt', 'old unchanged\n')
            result = self.run_recipe(5, 5)
            self.assertEqual(result.stderr, b'')
            self.assertTrue((self.directory / 'directory.html').is_dir())
            for name, old in (('directory.html/child.html', 'old one\n'), ('nested/a file.html', 'old two\n')):
                self.assertEqual((self.directory / name).read_text(), old.replace('old', 'new'))
                self.assertEqual((self.directory / (name + '.bak')).read_text(), old)
            self.assertEqual((self.directory / 'nested/plain.txt').read_text(), 'old unchanged\n')

    def test_csv_json_empty_data_and_headers_only_produce_empty_array(self):
        for text in ('', 'name,value\n'):
            with self.subTest(text=text):
                self.file('data.csv', text)
                self.assertEqual(json.loads(self.run_recipe(4, 6).stdout), [])

    def test_csv_json_rejects_duplicate_headers_and_wrong_width(self):
        for text in ('name,name\na,b\n', 'name,value\nonly\n'):
            with self.subTest(text=text):
                self.file('data.csv', text)
                self.assertNotEqual(self.run_recipe(4, 6, check=False).returncode, 0)

    def test_first_ten_line_search_resets_after_short_files(self):
        self.file('a.txt', 'no\n' * 8)
        self.file('b.txt', 'no\n' * 9 + 'PATTERN here\n')
        self.file('c.txt', 'no\n' * 10 + 'PATTERN too late\n')
        self.file('empty.txt', '')
        output = self.run_recipe(5, 7, arguments=['a.txt', 'empty.txt', 'b.txt', 'c.txt']).stdout
        self.assertEqual(output, b'b.txt\n')

    def test_small_log_files_with_spaces_are_reported_whole(self):
        self.file('short log.log', b'x' * 20)
        self.file('large.log', b'x' * 1000)
        (self.directory / 'folder.log').mkdir()
        self.assertEqual(self.run_recipe(5, 9).stdout, b'short log.log\n')

    def test_both_median_recipes_cover_odd_even_negative_and_single_inputs(self):
        examples = [([3, 1, 2], 2), ([1, 4, 2, 3], 2.5), ([-9, -1, -5], -5), ([7], 7), ([-2, 8], 3)]
        for values, expected in examples:
            contents = '\n'.join(map(str, values))
            self.file('numbers.txt', contents)
            self.file('data.txt', contents)
            for pi in (5, 9):
                with self.subTest(values=values, pi=pi):
                    self.assertEqual(float(self.run_recipe(6, pi).stdout), expected)

    def test_median_recipes_reject_empty_input(self):
        self.file('numbers.txt', '')
        self.file('data.txt', '')
        for pi in (5, 9):
            with self.subTest(pi=pi):
                result = self.run_recipe(6, pi, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b'no numbers', result.stderr)

    def test_top_ip_report_handles_fewer_than_ten_and_empty_logs(self):
        self.file('access.log', '10.0.0.2 a\n10.0.0.1 b\n10.0.0.2 c\n')
        rows = [line.split() for line in self.run_recipe(8, 1).stdout.decode().splitlines()]
        self.assertEqual(rows, [['10.0.0.2', '2'], ['10.0.0.1', '1']])
        self.file('access.log', '')
        self.assertEqual(self.run_recipe(8, 1).stdout, b'')

    def test_log_average_accumulates_current_matches_only(self):
        self.file('app.log', 'first 10ms\nnot a timing\nnext 20.5ms and 30ms\n')
        self.assertEqual(self.run_recipe(8, 5).stdout, b'avg: 20.2ms\n')
        self.file('app.log', 'no samples\n')
        result = self.run_recipe(8, 5, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'no timing samples', result.stderr)

    def test_last_hour_excludes_old_and_future_records(self):
        now = datetime.now(timezone.utc)
        text = ''.join((now + delta).strftime('%Y-%m-%d %H:%M:%S') + f' {label}\n'
                       for delta, label in ((timedelta(minutes=-10), 'recent'), (timedelta(hours=-2), 'old'), (timedelta(hours=2), 'future')))
        self.file('app.log', text)
        output = self.run_recipe(8, 6).stdout.decode()
        self.assertIn('recent', output)
        self.assertNotIn('old', output)
        self.assertNotIn('future', output)

    def test_ipv4_extraction_and_sort_reject_invalid_octets_or_partial_matches(self):
        contents = '999.1.2.3 256.0.0.1 1.2.3.4 10.0.0.1 1.2.3.4\n'
        self.file('access.log', contents)
        self.file('file.txt', contents)
        self.assertEqual(self.run_recipe(1, 1).stdout, b'1.2.3.4\n10.0.0.1\n1.2.3.4\n')
        self.assertEqual(self.run_recipe(9, 0).stdout, b'1.2.3.4\n10.0.0.1\n')

    def test_mysql_setting_changes_only_selected_section_in_fixture(self):
        path = self.file('config.ini', '[client]\nmax_connections = 1\n[mysqld]\n max_connections = 2\n[other]\nmax_connections = 3\n')
        self.run_recipe(9, 3, arguments=['config.ini'])
        self.assertEqual(path.read_text(), '[client]\nmax_connections = 1\n[mysqld]\n max_connections = 200\n[other]\nmax_connections = 3\n')

    def test_ssh_commenting_only_modifies_temporary_fixture(self):
        path = self.file('sshd.conf', '  PermitRootLogin yes\nPermitRootLoginExtra value\n#PermitRootLogin no\n')
        self.run_recipe(9, 7, arguments=['sshd.conf'])
        self.assertEqual(path.read_text(), '#  PermitRootLogin yes\nPermitRootLoginExtra value\n#PermitRootLogin no\n')

    def test_json_merge_reads_both_files_with_second_taking_precedence(self):
        self.file('a.json', json.dumps({'left': 1, 'shared': 'old', 'nested': {'a': 1}}))
        self.file('b.json', json.dumps({'right': 2, 'shared': 'new', 'nested': {'b': 2}}))
        self.assertEqual(json.loads(self.run_recipe(10, 3).stdout),
                         {'left': 1, 'right': 2, 'shared': 'new', 'nested': {'b': 2}})

    def test_json_csv_quotes_commas_quotes_newlines_and_unicode(self):
        values = [{'a': 'one,two', 'b': 'says "hello"\nПривет'}, {'a': '', 'b': None}]
        self.file('data.json', json.dumps(values, ensure_ascii=False))
        rows = list(csv.reader(io.StringIO(self.run_recipe(10, 2).stdout.decode())))
        self.assertEqual(rows, [['one,two', 'says "hello"\nПривет'], ['', '']])
        self.file('data.json', '[]')
        self.assertEqual(self.run_recipe(10, 2).stdout, b'')

    def test_line_count_matches_lf_count_for_unterminated_final_line(self):
        for contents, expected in (('', 0), ('a', 0), ('a\nb', 1), ('a\nb\n', 2)):
            with self.subTest(contents=contents):
                self.file('file.txt', contents)
                self.assertEqual(int(self.run_recipe(13, 3).stdout), expected)

    def test_random_symbol_string_contains_sixteen_characters(self):
        output = self.run_recipe(14, 4).stdout.decode().rstrip('\n')
        self.assertEqual(len(output), 16)
        self.assertTrue(all(33 <= ord(character) <= 126 for character in output))

    def test_lcm_handles_signs_zero_and_distinct_arguments(self):
        for values, expected in (((36, 24), 72), ((-36, 24), 72), ((0, 24), 0), ((0, 0), 0), ((35, 64), 2240)):
            with self.subTest(values=values):
                self.assertEqual(int(self.run_recipe(14, 8, arguments=list(map(str, values))).stdout), expected)

    def test_permutations_retain_every_element_once(self):
        lines = self.run_recipe(14, 9).stdout.decode().splitlines()
        self.assertEqual(len(lines), 24)
        self.assertEqual({tuple(line.split()) for line in lines}, set(itertools.permutations(('1', '2', '3', '4'))))
        self.assertEqual(self.run_recipe(14, 9, arguments=[]).stdout, b'\n')

    def test_power_set_uses_values_and_includes_empty_subset(self):
        lines = self.run_recipe(14, 10).stdout.decode().splitlines()
        expected = {tuple(group) for size in range(5) for group in itertools.combinations(('a', 'b', 'c', 'd'), size)}
        self.assertEqual({tuple(line.split(',')) if line else () for line in lines}, expected)
        self.assertEqual(len(lines), 16)
        self.assertEqual(self.run_recipe(14, 10, arguments=[]).stdout, b'\n')

    def test_quoted_argument_list_is_valid_csv(self):
        values = ['plain', 'one,two', 'say "hi"', 'line\nbreak']
        output = self.run_recipe(15, 5, arguments=values).stdout.decode()
        self.assertEqual(list(csv.reader(io.StringIO(output))), [values])

    def test_percent_encoding_uses_utf8_bytes(self):
        for value in ('hello world!', 'Привет мир', 'a/b?x=1&y=2'):
            with self.subTest(value=value):
                self.assertEqual(self.run_recipe(16, 7, arguments=[value]).stdout.decode().strip(), quote(value, safe='-_.~'))

    def test_tab_expansion_tracks_columns_across_multiple_tabs(self):
        value = 'a\tb\tc\n\tX\tY\nаб\tZ\n'
        self.file('file.txt', value)
        self.assertEqual(self.run_recipe(16, 9).stdout.decode(), value.expandtabs(8))

    def test_symmetric_difference_ignores_duplicates_and_terminal_lf(self):
        self.file('file1.txt', 'a\nb\nb\nc')
        self.file('file2.txt', 'b\nd\nd\nc\n')
        self.assertEqual(self.run_recipe(17, 7).stdout, b'a\nd\n')
        self.file('file1.txt', '')
        self.assertEqual(self.run_recipe(17, 7).stdout, b'b\nd\nc\n')

    def test_context_contains_three_lines_after_each_match(self):
        self.file('file.txt', 'skip\nPATTERN\none\ntwo\nthree\nstop\n')
        self.assertEqual(self.run_recipe(17, 9).stdout, b'PATTERN\none\ntwo\nthree\n')

    def test_unicode_letters_survive_in_place_filter(self):
        path = self.file('file.txt', 'Ада, hello 123!\n\nБоб?\r\n')
        self.run_recipe(18, 6)
        self.assertEqual(path.read_bytes().decode(), 'Адаhello123\n\nБоб\r\n')

    def test_hex_color_extraction_retains_hash_and_all_six_digits(self):
        self.file('style.css', '#abc #AABBCC #12345 #1234567 #def\n')
        self.assertEqual(self.run_recipe(19, 1).stdout, b'#abc\n#AABBCC\n#def\n')

    def test_bracket_check_detects_wrong_order_and_nesting(self):
        self.file('file.txt', 'valid([])\nwrong)(\ncross([)]\nmissing[\nplain\n')
        self.assertEqual(self.run_recipe(19, 6).stdout, b'wrong)(\ncross([)]\nmissing[\n')

    def test_camelcase_matches_are_highlighted(self):
        self.file('file.txt', 'plain\nmyValue and otherThing\n')
        self.assertEqual(self.run_recipe(19, 7).stdout, b'\x1b[31mmyValue\x1b[0m and \x1b[31motherThing\x1b[0m\n')

    def test_no_broken_pie_flag_cluster_remains_and_mutations_are_marked(self):
        for command in self.source['commands']:
            for pattern in command['patterns']:
                self.assertNotIn('-pie ', pattern['command'])
                if ' -i ' in pattern['command'] or 'rename ' in pattern['command']:
                    self.assertTrue(pattern.get('destructive'), pattern['command'])

    def test_all_changed_programs_compile_without_running_their_commands(self):
        for ci, pi in sorted(COMPILE_RECIPES):
            with self.subTest(ci=ci, pi=pi):
                parts = shlex.split(self.source['commands'][ci]['patterns'][2 * pi]['command'])
                parts = parts[parts.index('perl'):]
                modules = []
                program = None
                for index, item in enumerate(parts[1:], 1):
                    if item.startswith('-M'):
                        modules.append(item)
                    if item in ('-e', '-pe', '-ne', '-le', '-lne', '-lane', '-ane', '-nlae'):
                        program = parts[index + 1]
                        break
                    if item == '-00pe0':
                        program = '0'
                        break
                self.assertIsNotNone(program)
                self.assertNotRegex(program, r'\bBEGIN\s*\{', 'Compile-only checks must not execute BEGIN blocks')
                result = subprocess.run(
                    [PERL, *modules, '-c', '-e', program], cwd=self.directory,
                    capture_output=True, timeout=5,
                    env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8', 'TZ': 'UTC'},
                )
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))


if __name__ == '__main__':
    unittest.main()
