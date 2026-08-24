# Security

## Reporting

Report anything you believe is a security problem through
[GitHub's private vulnerability reporting](https://docs.github.com/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
on this repository, rather than in a public issue. There is no service behind
this and no user data, so the realistic reports are about the supply chain and
about what a malformed input can make the code do.

## What is in scope

| Class | Example |
|-------|---------|
| Supply chain | A pinned action or a release artefact that has been tampered with |
| Malformed input | A crafted ROM, patch or disk image that makes the tool write outside its output folder, consume unbounded memory, or execute anything |
| Identification | A file that verifies against a manifest entry it is not, which would let the wrong bytes reach a disk |
| Leakage | Any path where the tool would distribute, fetch, or point at copyrighted content |

That last row is a security property here rather than a legal footnote. The
project's whole design is that it identifies files it must never carry, and a
change that breaks that is a defect of the same severity as a crash.

## What is not

The tool reads files you already hold and writes files you asked for. It has no
network access outside `n64vi db-update`, which fetches one public catalogue
over HTTPS. A report that amounts to "this program can write a file" is
describing what it is for.

Emulator behaviour, hardware behaviour, and the correctness of third-party
patches are all outside what this project can control.

## Where this is distributed

This repository's releases page, and nowhere else. Anything under this name from
another source is not from this project, and saying so is worth more than most of
the table above.

## Supply chain

Every release carries a CycloneDX bill of materials and a Sigstore bundle over
it. The bill is generated from an environment holding nothing but this package,
and the release fails if anything other than the package itself appears in it,
because the package has no runtime dependencies and is meant to keep having
none.
