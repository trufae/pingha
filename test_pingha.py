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
