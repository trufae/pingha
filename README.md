# pingha

A tiny, pure-POSIX-shell tool that keeps the GitHub Actions in your workflows pinned to immutable commit SHAs.

## Why pinning matters

GitHub Actions are usually referenced by a moving tag:

```yaml
- uses: actions/checkout@v4
```

That is convenient, but a tag is only a pointer. The owner of the action — or anyone who compromises their account — can move `v4` to a different commit at any time. The next time your workflow runs it may execute code you never reviewed, with full access to your repository secrets and CI environment.

Pinning replaces the tag with the full commit SHA and keeps the version as a comment:

```yaml
- uses: actions/checkout@11bd71901bbe5b1630ceea73d2752379e6975d27 # v4
```

Because a SHA cannot be repointed, this guarantees that the exact same code runs every time. The comment makes it easy for humans to see which semantic version the SHA corresponds to.

## How it works

1. `pingha --pin` reads every `.yml`/`.yaml` file in `.github/workflows`.
2. For each `uses: owner/repo@<tag>` line that is not already a SHA, it asks the GitHub API what commit that tag currently points to.
3. It rewrites the line to `uses: owner/repo@<sha> # <tag>`.
4. If a newer tag is available, it prints a suggestion but does not change the tag unless you use `--update`.
5. `pingha --update` moves every action to the latest published tag and pins it.
6. `pingha --dependabot` creates `.github/dependabot.yml` so Dependabot can open PRs when actions have new releases.

## Installation

Copy the `pingha` script somewhere on your `$PATH` and make it executable:

```sh
chmod +x pingha
cp pingha ~/bin/
```

The only runtime dependencies are POSIX shell utilities plus `curl` and `sed`.

## Usage

```sh
# Pin all unpinned actions in place
pingha --pin

# Update every action to its latest tag and pin it
pingha --update

# Add Dependabot for GitHub Actions
pingha --dependabot
```

For private repositories or to avoid API rate limits, set a token:

```sh
GITHUB_TOKEN=ghp_... pingha --update
```

## Example

Before:

```yaml
- uses: actions/checkout@v4
- uses: actions/setup-python@v5
```

After `pingha --pin`:

```yaml
- uses: actions/checkout@11bd71901bbe5b1630ceea73d2752379e6975d27 # v4
- uses: actions/setup-python@0d5eda585c54a7394d25d1cd71e2e9df3ddb5f9c # v5
```

After `pingha --update`:

```yaml
- uses: actions/checkout@<latest-sha> # v5
- uses: actions/setup-python@<latest-sha> # v6
```

## Dependabot integration

`pingha --dependabot` writes the following file and reminds you to commit it:

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    commit-message:
      prefix: "actions"
    labels:
      - "dependencies"
      - "github-actions"
```

Dependabot will then open pull requests when your pinned actions have newer releases.

## License

Public domain / CC0 — do whatever you want.
