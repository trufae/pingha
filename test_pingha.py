import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('pingha')
OLD = '1' * 40
NEW = '2' * 40
TAGS = 'acme/action/tags?per_page=1'
CURL = '''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
url = next(arg for arg in args if arg.startswith('https://'))
key = url.split('/repos/', 1)[1]
with open(os.environ['REQUEST_LOG'], 'a') as log:
    log.write(key + '\\n')
response = json.loads(pathlib.Path(os.environ['RESPONSES']).read_text()).get(key)
if response is None:
    response = [404, {'message': 'Unexpected request: ' + key}]
pathlib.Path(args[args.index('-o') + 1]).write_text(json.dumps(response[1]))
print(response[0], end='')
'''
GIT = '''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
if 'diff' in args and os.environ.get('FAIL_DIFF'):
    sys.exit(1)
for i, arg in enumerate(args):
    if arg.startswith('https://'):
        assert arg == 'https://github.com/acme/action.git', arg
        with open(os.environ['GIT_REQUEST_LOG'], 'a') as log:
            log.write(json.dumps(args) + '\\n')
        if os.environ.get('FAIL_FETCH'):
            sys.exit(1)
        if os.environ.get('GITHUB_TOKEN'):
            helper = os.environ['GIT_ASKPASS']
            assert os.environ['GITHUB_TOKEN'] not in pathlib.Path(helper).read_text()
            for prompt, expected in [('Username', 'x-access-token'),
                                     ('Password', os.environ['GITHUB_TOKEN'])]:
                assert subprocess.check_output([helper, prompt], text=True).strip() == expected
        args[i] = os.environ['REVIEW_REPOSITORY']
os.execv(os.environ['REAL_GIT'], ['git', *args])
'''


class PinghaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pingha-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workflows = self.root / '.github/workflows'
        self.workflows.mkdir(parents=True)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        (self.bin / 'curl').write_text(CURL)
        (self.bin / 'curl').chmod(0o755)
        self.responses = {TAGS: [200, [{'name': 'v2', 'commit': {'sha': NEW}}]]}
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        RESPONSES=str(self.root / 'responses.json'),
                        REQUEST_LOG=str(self.root / 'requests'), TMPDIR=str(self.root),
                        NO_COLOR='1')
        self.env.pop('GITHUB_TOKEN', None)

    def write(self, content, name='ci.yml'):
        path = self.workflows / name
        path.write_bytes(content.encode())
        return path

    def run_tool(self, *args, code=0):
        (self.root / 'responses.json').write_text(json.dumps(self.responses))
        command = [os.environ['PINGHA_TEST_SHELL']] if 'PINGHA_TEST_SHELL' in os.environ else []
        result = subprocess.run([*command, str(SCRIPT), *args], cwd=self.root, env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertFalse(list(self.root.glob('pingha.*')), 'temporary cache leaked')
        return result

    def requests(self):
        log = self.root / 'requests'
        return log.read_text().splitlines() if log.exists() else []

    def git(self, *args, input=None):
        return subprocess.run([self.env['REAL_GIT'], '-C', str(self.upstream), *args],
                              env=self.env, input=input, capture_output=True, text=True,
                              check=True).stdout.strip()

    def review_repository(self):
        self.upstream = self.root / 'upstream'
        self.upstream.mkdir()
        self.env.update(REAL_GIT=shutil.which('git'),
                        REVIEW_REPOSITORY=str(self.upstream),
                        GIT_REQUEST_LOG=str(self.root / 'git-requests'),
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
                        GIT_AUTHOR_NAME='Action author', GIT_AUTHOR_EMAIL='author@example.test',
                        GIT_COMMITTER_NAME='Action committer', GIT_COMMITTER_EMAIL='committer@example.test')
        self.git('init', '--quiet', '--template=')
        (self.upstream / 'action.yml').write_text('name: old action\n')
        self.base = self.review_commit('Initial version')
        self.responses['acme/action/commits/v1'] = [200, {'sha': self.base}]
        (self.bin / 'git').write_text(GIT)
        (self.bin / 'git').chmod(0o755)

    def review_commit(self, message):
        self.git('add', '--all')
        self.git('commit', '--quiet', '-m', message)
        sha = self.git('rev-parse', 'HEAD')
        self.responses[TAGS] = [200, [{'name': 'v2', 'commit': {'sha': sha}}]]
        return sha

    def git_requests(self):
        log = self.root / 'git-requests'
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_review_full_history_and_diff_use_the_pin_not_the_comment(self):
        self.review_repository()
        (self.upstream / 'action.yml').write_text('name: new action\n')
        middle = self.review_commit('Update action\n\nDetailed commit explanation.')
        (self.upstream / 'dist').mkdir()
        (self.upstream / 'dist/index.js').write_text('console.log("changed runtime");\n')
        (self.upstream / 'binary').write_bytes(b'\x00\x01\xff')
        head = self.review_commit('Update bundled runtime')
        # A stale comment or moved tag must not replace the actual installed SHA.
        self.responses['acme/action/commits/v1'] = [200, {'sha': head}]
        source = f'- uses: acme/action/init@{self.base.upper()} # v1\r\n'
        path = self.write(source)
        path.chmod(0o640)
        result = self.run_tool('--review')
        self.assertEqual(path.read_bytes(), source.encode())
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.requests(), [TAGS])
        self.assertEqual(len(self.git_requests()), 1)
        self.assertEqual(self.git_requests()[0][-1], head)
        self.assertIn(f'{self.base}..{head}', result.stdout)
        self.assertIn(f'commit {middle}', result.stdout)
        self.assertIn(f'commit {head}', result.stdout)
        self.assertIn('Detailed commit explanation.', result.stdout)
        self.assertIn('Action author <author@example.test>', result.stdout)
        self.assertIn('-name: old action', result.stdout)
        self.assertIn('+name: new action', result.stdout)
        self.assertIn('diff --git a/dist/index.js b/dist/index.js', result.stdout)
        self.assertIn('GIT binary patch', result.stdout)
        self.assertNotIn('WARNING', result.stdout)

    def test_review_deduplicates_ranges_and_authenticates_without_saving_token(self):
        self.review_repository()
        (self.upstream / 'action.yml').write_text('name: new action\n')
        self.review_commit('New release')
        self.env['GITHUB_TOKEN'] = 'test-secret-token'
        source = ('- uses: acme/action/init@v1\n- uses: acme/action/init@v1\n'
                  f'- uses: ACME/action/other@{self.base}\n')
        self.write(source)
        result = self.run_tool('-r', '-r')
        self.assertEqual(result.stdout.count('Review:'), 2)
        self.assertEqual(result.stdout.count('Full diff'), 1)
        self.assertIn('already reviewed above', result.stdout)
        self.assertEqual(self.requests(), [TAGS, 'acme/action/commits/v1'])
        self.assertEqual(len(self.git_requests()), 1)
        self.assertNotIn(self.env['GITHUB_TOKEN'], result.stdout + result.stderr + str(self.git_requests()))

    def test_review_runs_before_update_regardless_of_option_order(self):
        self.review_repository()
        (self.upstream / 'action.yml').write_text('name: new action\n')
        head = self.review_commit('New release')
        path = self.write('- uses: acme/action@v1\n')
        result = self.run_tool('-u', '-r', '-i')
        self.assertEqual(path.read_text(), f'- uses: acme/action@{head} # v2\n')
        self.assertLess(result.stdout.index('Full diff'), result.stdout.index('.github/workflows/ci.yml:'))
        self.assertIn('up to date', result.stdout)
        self.assertEqual(self.requests(), [TAGS, 'acme/action/commits/v1'])

    def test_review_fetch_failure_is_cached_and_prevents_all_mutations(self):
        self.review_repository()
        (self.upstream / 'action.yml').write_text('name: new action\n')
        self.review_commit('New release')
        self.env['FAIL_FETCH'] = '1'
        source = '- uses: acme/action/init@v1\n- uses: acme/action/other@v1\n'
        path = self.write(source)
        result = self.run_tool('-u', '-d', '-r', code=1)
        self.assertEqual(path.read_text(), source)
        self.assertFalse((self.root / '.github/dependabot.yml').exists())
        self.assertIn('incomplete review', result.stderr)
        self.assertEqual(len(self.git_requests()), 1)

    def test_review_failed_lookups_prevent_updates(self):
        for endpoint in (TAGS, 'acme/action/commits/v1'):
            with self.subTest(endpoint=endpoint):
                original = self.responses.get(endpoint)
                self.responses[endpoint] = [404, {'message': 'Not Found'}]
                path = self.write('- uses: acme/action@v1\n')
                self.run_tool('-u', '-r', code=1)
                self.assertEqual(path.read_text(), '- uses: acme/action@v1\n')
                if original is None:
                    del self.responses[endpoint]
                else:
                    self.responses[endpoint] = original

    def test_review_diff_failure_prevents_updates(self):
        self.review_repository()
        (self.upstream / 'action.yml').write_text('name: new action\n')
        self.review_commit('New release')
        self.env['FAIL_DIFF'] = '1'
        path = self.write('- uses: acme/action@v1\n')
        result = self.run_tool('-u', '-r', code=1)
        self.assertIn('incomplete review', result.stderr)
        self.assertEqual(path.read_text(), '- uses: acme/action@v1\n')

    def test_review_isolates_git_paths_and_does_not_run_external_diff(self):
        self.review_repository()
        (self.upstream / 'action.yml').write_text('name: new action\n')
        self.review_commit('New release')
        self.write('- uses: acme/action@v1\n')
        self.env.update(GIT_DIR=str(self.upstream / '.git'),
                        GIT_COMMON_DIR=str(self.upstream / '.git'),
                        GIT_WORK_TREE=str(self.upstream),
                        GIT_INDEX_FILE=str(self.upstream / '.git/index'),
                        GIT_OBJECT_DIRECTORY=str(self.upstream / '.git/objects'),
                        GIT_EXTERNAL_DIFF='false')
        before = {path: path.read_bytes() for path in self.upstream.rglob('*') if path.is_file()}
        result = self.run_tool('-r')
        after = {path: path.read_bytes() for path in self.upstream.rglob('*') if path.is_file()}
        self.assertEqual(before, after)
        self.assertIn('-name: old action', result.stdout)
        self.assertIn('+name: new action', result.stdout)

    def test_review_up_to_date_does_not_fetch_git(self):
        self.responses['acme/action/commits/stable'] = [200, {'sha': NEW}]
        self.write(f'- uses: acme/action@{NEW}\n- uses: acme/action@stable\n')
        result = self.run_tool('-r')
        self.assertEqual(result.stdout.count('Up to date; no changes.'), 2)
        self.assertEqual(self.git_requests(), [])

    def test_review_diverged_history_includes_removed_commits_and_exact_tree_diff(self):
        self.review_repository()
        (self.upstream / 'old-only.txt').write_text('old branch content\n')
        old = self.review_commit('Current branch only')
        self.git('checkout', '--quiet', '-b', 'different', self.base)
        (self.upstream / 'new-only.txt').write_text('new branch content\n')
        head = self.review_commit('New branch only')
        self.write(f'- uses: acme/action@{old} # v1\n')
        result = self.run_tool('-r')
        self.assertIn('WARNING: latest tag does not descend', result.stdout)
        self.assertIn('Current branch only', result.stdout)
        self.assertIn('New branch only', result.stdout)
        self.assertIn('-old branch content', result.stdout)
        self.assertIn('+new branch content', result.stdout)
        self.assertEqual([request[-1] for request in self.git_requests()], [head, old])

    def test_review_rewind_shows_removed_commits(self):
        self.review_repository()
        (self.upstream / 'action.yml').write_text('name: newer action\n')
        old = self.review_commit('Commit removed by update')
        self.responses[TAGS] = [200, [{'name': 'v2', 'commit': {'sha': self.base}}]]
        self.write(f'- uses: acme/action@{old}\n')
        result = self.run_tool('-r')
        self.assertIn('WARNING', result.stdout)
        self.assertIn('Commit removed by update', result.stdout)
        self.assertIn('-name: newer action', result.stdout)
        self.assertIn('+name: old action', result.stdout)

    def test_review_exceeds_api_commit_and_file_limits(self):
        self.review_repository()
        stream = []
        for index in range(1, 252):
            message = f'Release change {index}\n'
            stream.append(f'commit refs/heads/release\n'
                          f'committer Author <author@example.test> {1700000000 + index} +0000\n'
                          f'data {len(message)}\n{message}')
            if index == 1:
                stream.append(f'from {self.base}\n')
            if index == 251:
                for file_index in range(301):
                    stream.append(f'M 100644 inline file-{file_index}.txt\ndata 4\nnew\n')
            stream.append('\n')
        self.git('fast-import', '--quiet', input=''.join(stream))
        head = self.git('rev-parse', 'release')
        self.responses[TAGS] = [200, [{'name': 'v2', 'commit': {'sha': head}}]]
        self.write(f'- uses: acme/action@{self.base}\n')
        result = self.run_tool('-r')
        self.assertEqual(sum(line.startswith('commit ') for line in result.stdout.splitlines()), 251)
        self.assertEqual(result.stdout.count('diff --git '), 301)
        self.assertIn('Release change 1\n', result.stdout)
        self.assertIn('Release change 251\n', result.stdout)

    def test_review_requires_git_before_any_mutation(self):
        empty_bin = self.root / 'empty-bin'
        empty_bin.mkdir()
        for command in ('cat', 'jq', 'curl'):
            (empty_bin / command).symlink_to(shutil.which(command))
        self.env['PATH'] = str(empty_bin)
        result = self.run_tool('-d', '-r', code=1)
        self.assertIn('required command not found: git', result.stderr)
        self.assertFalse((self.root / '.github/dependabot.yml').exists())

    def test_subactions_quotes_comments_and_cache(self):
        source = (f'  - uses: "acme/action/init@v1" # v1 keep this\r\n'
                  f"  - uses: 'acme/action/analyze@{OLD}' # v1\r\n"
                  '  - uses: acme/action@v1\r\n'
                  '  # uses: acme/action@v1\r\n'
                  '  - run: |\r\n'
                  '      uses: acme/action@v1\r\n'
                  '  - uses: ./local\r\n'
                  '  - uses: docker://image@v1\r\n')
        path = self.write(source)
        self.write('jobs:\n  reuse:\n    uses: acme/action/.github/workflows/check.yml@v1\n', 'space name.yaml')
        self.run_tool('-u', '-i')
        self.assertEqual(self.requests(), [TAGS])
        expected = source.replace('"acme/action/init@v1" # v1', f'"acme/action/init@{NEW}" # v2')
        expected = expected.replace(f"'acme/action/analyze@{OLD}' # v1", f"'acme/action/analyze@{NEW}' # v2")
        expected = expected.replace('  - uses: acme/action@v1', f'  - uses: acme/action@{NEW} # v2')
        self.assertEqual(path.read_bytes(), expected.encode())
        self.assertIn(f'acme/action/.github/workflows/check.yml@{NEW}', (self.workflows / 'space name.yaml').read_text())
        before = path.read_bytes()
        self.run_tool('--update')
        self.assertEqual(path.read_bytes(), before)

    def test_info_is_read_only_colored_and_deduplicated(self):
        source = (f'- uses: acme/action@{OLD} # v1\n- uses: acme/action@v2\n'
                  f'- uses: acme/action@{NEW} # v2\n- uses: acme/action@stable\n'
                  '- uses: acme/action@v2\n')
        path = self.write(source)
        self.responses['acme/action/commits/stable'] = [200, {'sha': NEW}]
        self.env.pop('NO_COLOR')
        self.env['CLICOLOR_FORCE'] = '1'
        result = self.run_tool('--info')
        self.assertEqual(path.read_text(), source)
        self.assertEqual(len(result.stdout.splitlines()), 5)
        self.assertIn('\x1b[32mpinned\x1b[0m', result.stdout)
        self.assertIn('\x1b[31munpinned\x1b[0m', result.stdout)
        self.assertEqual(result.stdout.count('up to date'), 3)
        self.assertIn('update to v2', result.stdout)
        self.assertEqual(self.requests(), [TAGS, 'acme/action/commits/stable'])

    def test_pin_branch_encoding_and_uppercase_pins(self):
        self.responses['acme/action/commits/release%2Fa%26b'] = [200, {'sha': NEW}]
        source = f'- uses: acme/action@release/a&b # reason\n- uses: acme/action@{"A" * 40}\n'
        path = self.write(source)
        self.run_tool('-p', '-p')
        self.assertEqual(self.requests(), ['acme/action/commits/release%2Fa%26b'])
        self.assertEqual(path.read_text(), source.replace('release/a&b # reason', f'{NEW} # release/a&b reason'))

    def test_ref_named_latest_is_not_a_latest_tag_request(self):
        self.responses['acme/action/commits/latest'] = [200, {'sha': NEW}]
        self.write('- uses: acme/action@latest\n')
        self.run_tool('-p')
        self.assertEqual(self.requests(), ['acme/action/commits/latest'])

    def test_failures_are_cached_and_reported(self):
        self.responses[TAGS] = [404, {'message': 'Not Found'}]
        path = self.write('- uses: acme/action@v1\n- uses: acme/action/init@v1\n')
        original = path.read_bytes()
        result = self.run_tool('-u', '-i', code=1)
        self.assertEqual(self.requests(), [TAGS])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(result.stderr.count('HTTP 404'), 1)
        self.assertEqual(result.stdout.count('unknown'), 2)

    def test_rate_limit_stops_further_requests(self):
        self.responses[TAGS] = [429, {'message': 'Please wait'}]
        self.write('- uses: acme/action@v1\n- uses: another/action@v1\n')
        self.run_tool('-i', code=1)
        self.assertEqual(self.requests(), [TAGS])

    def test_invalid_responses_never_change_workflows(self):
        for payload in ([], [{'name': 'v2', 'commit': {'sha': 'bad'}}], {'message': 'oops'}):
            with self.subTest(payload=payload):
                self.responses[TAGS] = [200, payload]
                path = self.write('- uses: acme/action@v1\n')
                self.run_tool('-u', code=1)
                self.assertEqual(path.read_text(), '- uses: acme/action@v1\n')

    def test_missing_jq_fails_before_starting(self):
        empty_bin = self.root / 'empty-bin'
        empty_bin.mkdir()
        (empty_bin / 'cat').symlink_to(shutil.which('cat'))
        self.env['PATH'] = str(empty_bin)
        result = self.run_tool('-d', code=1)
        self.assertIn('required command not found: jq', result.stderr)
        self.assertFalse((self.root / '.github/dependabot.yml').exists())
        self.assertEqual(self.requests(), [])
        self.run_tool('--help')

    def test_existing_dependabot_is_preserved(self):
        existing = self.root / '.github/dependabot.yaml'
        existing.write_text('custom configuration\n')
        self.run_tool('-d')
        self.assertEqual(existing.read_text(), 'custom configuration\n')
        self.assertFalse(existing.with_suffix('.yml').exists())

    def test_options_validated_before_mutation(self):
        self.run_tool('-d', '--wrong', code=1)
        self.assertFalse((self.root / '.github/dependabot.yml').exists())

    def test_no_actions_is_success(self):
        self.write('jobs:\n  job:\n    steps:\n      - run: true\n')
        self.run_tool('-u')
        self.assertEqual(self.requests(), [])

    def test_comments_do_not_start_block_strings_and_permissions_survive(self):
        path = self.write('  # run: |\n    uses: acme/action@v1 # 2026 audit approved\n')
        path.chmod(0o640)
        self.run_tool('-u')
        self.assertIn(NEW, path.read_text())
        self.assertIn('# v2 2026 audit approved', path.read_text())
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    def test_checksum_collisions_do_not_share_resolutions(self):
        checksum = self.bin / 'cksum'
        checksum.write_text('#!/bin/sh\ncat >/dev/null\nprintf "1 1\\n"\n')
        checksum.chmod(0o755)
        self.responses['other/action/tags?per_page=1'] = [200, [{'name': 'v3', 'commit': {'sha': OLD}}]]
        path = self.write('- uses: acme/action@v1\n- uses: other/action@v1\n')
        self.run_tool('-u')
        self.assertEqual(path.read_text(), f'- uses: acme/action@{NEW} # v2\n- uses: other/action@{OLD} # v3\n')

    def test_failed_current_ref_is_unknown(self):
        self.write('- uses: acme/action@missing\n')
        result = self.run_tool('-i', code=1)
        self.assertIn('unknown', result.stdout)
        self.assertNotIn('update to', result.stdout)


if __name__ == '__main__':
    unittest.main()
