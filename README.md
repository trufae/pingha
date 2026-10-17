# 👃 pingha

A small POSIX-shell tool for pinning, updating and inspecting GitHub Actions.
Requires **jq** and **curl**, checked before any work starts.

## Installation

```sh
make user-install            # installs to ~/.local/bin
# or: make install PREFIX=/usr/local
```

## Usage

Run from the repository root:

```sh
pingha -p                   # pin each action to its current ref's commit
pingha -u                   # update and pin to the latest tag returned by GitHub
pingha -i                   # inspect current versions and update status
pingha -d                   # create Dependabot configuration if none exists
pingha -u -i                # update, then inspect using the same cache
```

Long options are `--pin`, `--update`, `--info`, `--dependabot`, and `--help`.
All options are validated before changes are made.

`--info` prints one row per distinct action/reference, with these columns:

```text
ACTION                 CURRENT             PIN        NEXT
actions/checkout       <sha> # v4           pinned     update to v5
actions/setup-python   v6                  unpinned   up to date
```

Pinned references are green and unpinned references are red in terminals.
`NO_COLOR` disables colors; `CLICOLOR_FORCE=1` enables them when redirecting output.
Pin status and update status are independent: an unpinned tag can be up to date.
Update status compares commit SHAs, so equivalent tags do not cause false updates.
Failed lookups show `unknown` and return a nonzero exit status.

Every GitHub API response, including failures, is cached **for one invocation**.
Repeated actions and subdirectories in the same repository share the cache,
including across combined options. Temporary cache files are removed on exit;
the next invocation fetches fresh data. Rate-limit responses stop further requests.
Use `GITHUB_TOKEN` for private repositories or higher API limits:

```sh
GITHUB_TOKEN=ghp_... pingha -i
```

The tool reads `.yml` and `.yaml` files directly in `.github/workflows`.
It supports ordinary scalar `uses:` entries, including quoted references,
subdirectory actions and reusable workflows. Local and Docker actions, comments,
and YAML block strings are skipped. Flow-style mappings and YAML aliases are not
processed. Quotes, explanatory comments, CRLF endings and file permissions are
preserved. Existing Dependabot configurations are left intact.

Pinning replaces a movable tag with an immutable commit and a version comment:

```yaml
- uses: actions/checkout@11bd71901bbe5b1630ceea73d2752379e6975d27 # v4
```

`--update` follows the first tag in GitHub's repository tags endpoint, as before;
it does not filter prereleases or restrict major-version upgrades. Review changes
before committing them.

## Tests

`make test` runs offline regression tests using Python 3 and a fake GitHub API.
Use `PINGHA_TEST_SHELL=/bin/dash make test` to exercise another POSIX shell.

## License

Public domain / CC0.
