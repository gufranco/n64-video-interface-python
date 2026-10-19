# Contributing

## Getting it running

```bash
git clone https://github.com/gufranco/n64-video-interface-python.git
cd n64-video-interface-python
uv sync
uv run pytest
```

[uv](https://docs.astral.sh/uv/) installs the Python named in `.python-version`
and the exact tool versions in `uv.lock`, so a local run and CI use the same ones.

The suite passes on a clean checkout with no ROMs and no network. That is
enforced in CI: anybody can work on this without owning a collection.

## The gates

| Gate | Command |
|------|---------|
| Format | `uv run ruff format --check .` |
| Lint | `uv run ruff check .` |
| Types | `uv run mypy` |
| Tests | `uv run pytest --cov` |

Coverage is gated at 100% of statements and branches. `mypy` runs strict with every optional error class the
version offers.

## Things this project will not accept

**No ROM, patch, firmware or save data, in any form.** Not as a test fixture, not
base64 encoded, not "just a small one". This is the one rule with no exceptions.

**No link or hint pointing at where to obtain those files.** The documentation
describes how to confirm a file you already have is the right one. It does not
help anybody find it.

**No claim that has not been checked.** If a change asserts something about the
hardware, the ROM layout or a patch, say how it was established. Several
findings in this project's history reversed on measurement, and the ones that
survived did so because somebody ran them rather than reasoned about them.

## Distribution

Releases go to this repository's releases page and nowhere else.

Each one carries a CycloneDX bill of materials generated from an environment holding
nothing but this package, and a Sigstore bundle over it. That is what makes the empty
dependency tree checkable instead of merely claimed.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/). The release is cut
by semantic-release from the commit history, so the type prefix decides the
version. `feat` gives a minor, `fix` gives a patch, and a `BREAKING CHANGE:`
footer gives a major.

## Tests

Tests are named for the behaviour they check rather than the function they call,
and each one has a single act. If a test needs a comment to be understood, the
test is doing too much.

A bug fix ships with a test that fails without the fix. There are several places
in this codebase where the test came first and found the defect a careful reading
had missed, which is the argument for the habit.
