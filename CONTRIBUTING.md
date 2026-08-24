# Contributing

## Getting it running

```bash
git clone https://github.com/gufranco/n64-video-interface-python.git
cd n64-video-interface-python
pip install -e ".[dev]"
pytest
```

The suite passes on a clean checkout with no ROMs, no patches and no cached
catalogue. That is deliberate and it is enforced in CI: anybody has to be able to
work on this without owning a collection. Tests that genuinely need real files
carry the `artifacts` marker and skip when the files are absent.

## The gates

| Gate | Command |
|------|---------|
| Format | `ruff format --check .` |
| Lint | `ruff check .` |
| Types | `mypy` |
| Tests | `pytest` |

Coverage is gated at 95% and every file is expected to clear it, not just the
project as a whole. `mypy` runs strict with every optional error class the
version offers.

## Things this project will not accept

**No ROM, patch, firmware or save data, in any form.** Not as a test fixture, not
base64 encoded, not "just a small one". The manifest carries identity and nothing
else, and a test asserts that no manifest entry has a payload field. This is the
one rule with no exceptions.

**No link or hint pointing at where to obtain those files.** The documentation
describes how to confirm a file you already have is the right one. It does not
help anybody find it.

**No claim that has not been checked.** If a change asserts something about the
hardware, the disk format or a patch, say how it was established. Several
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
