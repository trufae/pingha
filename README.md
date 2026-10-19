# 👃 pingha

A small POSIX-shell tool for pinning, updating and inspecting GitHub Actions.
Requires **jq** and **curl**, checked before any work starts.
Reviewing changes also requires **git**.

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
pingha -r                   # review commits and changes before updating
pingha -d                   # create Dependabot configuration if none exists
pingha -u -i                # update, then inspect using the same cache
```

Long options are `--pin`, `--update`, `--info`, `--review`, `--dependabot`, and `--help`.
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

`--review` compares each action's current reference with the latest tag selected
by `--update`. It prints the resolved commit range, a GitHub comparison link, all
incoming commits with authors and full messages, and a full repository diff
(including bundled code and binary patches). Pinned actions use their actual
SHA, not the tag in their comment. Unpinned tags and branches are resolved to
their current SHA; they cannot reveal where a movable tag pointed in the past.
Repeated actions and subdirectories sharing a commit range are reviewed once.

```sh
pingha --review > review.txt # read-only report; check the exit status for failures
pingha --review --update    # print the review, then apply the reviewed versions
```

Review always runs before other modes, regardless of option order. Failed
lookups, fetches or comparisons return a nonzero status and prevent subsequent
changes. Combining review and update applies updates without an interactive
prompt; run review alone first if you want time to inspect the report.

The report warns when the latest tag does not descend from the current commit
and lists the commits dropped from its history. The diff always compares the two
actual trees, including changes removed by a rollback or divergent release.
Review uses temporary bare Git repositories, fetching full history without
checking out or running action code. Large histories can take time and disk
space; all fetched data is removed on exit. `GITHUB_TOKEN` also authenticates Git
fetches for private actions. A review report helps manual inspection; it does
not automatically establish that an update is safe.

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

`make test` runs offline regression tests using Python 3, Git, a fake GitHub API
and local Git repositories for release comparisons.
Use `PINGHA_TEST_SHELL=/bin/dash make test` to exercise another POSIX shell.

## License

Public domain / CC0.
