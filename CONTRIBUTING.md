# Contributing to Argus

Argus is built like a real software development lifecycle: foundations first, quality over
velocity, small and meaningful tests, and a build -> review -> revise loop on every unit of work.
This document is the working agreement. Read it before opening a branch.

See also: [docs/versioning.md](docs/versioning.md) (releases and tags) and
[docs/testing.md](docs/testing.md) (what we test and why).

## Prose conventions

These apply to all prose: docs, README, UI copy, commit messages, PR descriptions.

- **No em dashes.** Use a spaced hyphen ` - `, a comma, or parentheses. (Code and generated config
  are exempt.) This is enforced in CI by `scripts/lint-conventions.sh`.
- Plain, direct language. Explain the *why*, not just the *what*.

## Branching strategy

We use **trunk-based development with short-lived branches**.

- `main` is always releasable. Every commit on `main` has passed CI.
- All work happens on a **short-lived branch off `main`**, opened per change, merged via PR, then
  deleted. Aim for hours-to-days, not weeks. If a branch outlives a few days, it is too big - split it.
- **No long-running branches** (no `develop`, no release branches). Releases are cut as tags on `main`
  (see [docs/versioning.md](docs/versioning.md)).
- Direct commits to `main` are reserved for the initial baseline only. Everything after goes through
  a PR.

### Branch naming

`<type>/<short-kebab-summary>`, where `<type>` matches the commit types below:

| Prefix | For |
|---|---|
| `feat/` | a new capability (e.g. `feat/ocb-distro-manifest`) |
| `fix/` | a bug fix |
| `docs/` | docs only |
| `chore/` | tooling, CI, deps, scaffolding (e.g. `chore/phase-0-foundations`) |
| `test/` | adding or reworking tests only |
| `refactor/` | behavior-preserving code change |

### Why trunk-based for this repo

Argus is a small repo worked by one maintainer plus AI agents. Trunk-based keeps integration
continuous and the history linear and legible, which matters because the SQLite ledger and the design
docs treat git as part of the audit trail. A heavier model like Git Flow adds release/develop branches
whose merge overhead buys us nothing at this size and would fragment the history we rely on for review.

## Commit conventions

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<optional scope>): <summary in the imperative, no trailing period>

<optional body: the why>

<optional footer: BREAKING CHANGE, Co-Authored-By, refs>
```

- **Types:** `feat`, `fix`, `docs`, `chore`, `test`, `refactor`, `perf`, `ci`, `build`.
- Summary is imperative mood ("add", not "added"), lowercase, under ~72 chars, no em dashes.
- A `feat:` or `fix:` commit maps to a CHANGELOG entry. `BREAKING CHANGE:` in the footer (or a `!`
  after the type) bumps the major version.
- Agent-authored commits end with the `Co-Authored-By` trailer.
- Sign off every commit with `git commit -s`; see [Developer Certificate of Origin](#developer-certificate-of-origin).

## The build -> review -> revise loop

Every unit of work follows the same loop. Do not pile up half-built subsystems.

1. **Branch** off `main` with a `<type>/...` name.
2. **Build** one small, coherent change. Add or update tests in the same PR (see
   [docs/testing.md](docs/testing.md)). Keep PRs small and reviewable.
3. **Review.** Run `/code-review` on the diff and address findings. Self-review is mandatory; a
   second reviewer when one is available.
4. **Revise** until the change is correct and CI is green.
5. **Merge** to `main` (squash to keep history linear) and delete the branch.

Finish, test, and review one thing before starting the next.

## Definition of done (per PR)

- [ ] CI green (conventions lint + applicable test layers).
- [ ] Tests added or updated for the change, and they are small, fast, and meaningful.
- [ ] CHANGELOG updated if the change is user-facing (`feat`/`fix`).
- [ ] Versions pinned (no floating `latest`); docs updated if behavior or interfaces changed.
- [ ] No em dashes in prose.

## Developer Certificate of Origin

Argus uses the [Developer Certificate of Origin](https://developercertificate.org/)
(DCO) instead of a contributor license agreement. Every commit in a pull request
must carry a `Signed-off-by:` line with your name and email, and they must match
the commit's author. `git commit -s` adds it from your `user.name` and
`user.email`:

```
Signed-off-by: Jane Doe <jane@example.com>
```

By adding that line you certify the following, quoted from
<https://developercertificate.org/>:

```
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.


Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```

The `dco` CI check runs on every pull request to `main`. It checks each
non-merge commit the pull request adds, and fails naming each commit whose
sign-off is missing or doesn't match its author (name and email, compared
without regard to case). To fix it:

```bash
# the last commit only
git commit --amend --signoff --no-edit

# every commit on your branch (this signs every rebased commit as you, which
# satisfies the check only for commits whose author is you)
git rebase --signoff origin/main

# sign-off present but under a different name or email than the author:
# set user.name / user.email to the identity you want, then
git commit --amend --reset-author --signoff --no-edit

git push --force-with-lease
```

Commits already on `main` from before this rule are not re-checked. A pull request
opened by someone with write access to this repository (admin, maintain or
write; tenish's members all have it) skips the check, including one opened
from a fork; it is enforced on pull requests from everyone else. On a same-repo pull request, commits authored exactly as
Dependabot's noreply address are skipped too, because Dependabot cannot sign off
under that address.
