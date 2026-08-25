# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# django-react-foundation

Two independently-versioned packages in one repo, shared by several projects rather than
owned by any one of them:

| package | lives at | installed as |
|---|---|---|
| `drf_foundation` (Python) | `python/` | `uv add "django-drf-foundation @ git+…#subdirectory=python"` |
| `react-vite-foundation` (JS) | repo root | `pnpm add "github:trevor-e/django-react-foundation#js-v<x>"` |

Nothing is published to a registry. **Consumers install from a git tag, so the tag is the
release.** The JS package is at the root because npm's git-dependency syntax has no
`#subdirectory=` equivalent; `uv` does, so Python gets the nested slot.

Current consumers: [`adulting`](https://github.com/trevor-e/adulting) and
[`pystonks`](https://github.com/trevor-e/pystonks), both at `~/dev/`.

## Docs table — task → doc

| If you're... | Read first |
|---|---|
| New to this repo | This file, then `docs/blueprint.md` |
| **Adding anything to either package** | `docs/blueprint.md` §17 — the gates. Then use the `extract-to-foundation` skill, which walks them. |
| Using the Python package (API, wiring, what it deliberately excludes) | `python/README.md` |
| Using the JS package (apiClient, query keys, SEO, prerender, auth-ui) | `README.md` |
| Following the stack's conventions (layout, testing, CI, deploy, wire-schema pipeline) | `docs/blueprint.md` — the shared doctrine, vendored into consumers |
| Deciding what a consumer should pick up next | `scripts/adoption-report.py` |
| Stamping out a new project on this stack | `scripts/new-project.sh <name> <dir>`, blueprint §15 |

## The rule that matters most here

**Nothing lands in either package with one consumer.** A module only one project can use is
strictly worse than a copy: same maintenance, plus a version pin and a release. Two failed
attempts at extracting auth cost more than writing the code twice would have, both because
the interface was designed against one consumer and the second was consulted last.

Blueprint §17 is the authority; the `extract-to-foundation` skill (tracked in `skills/`,
symlinked into `~/.claude/skills/`) walks its gates and refuses to write a proposal before
the two-call-site evidence exists. Use it rather than reasoning from memory — the specific
failure it prevents is asserting what the *other* consumer does without opening its code.

Two corollaries worth stating on their own:

- **Extract the intersection, not the union.** If sharing something needs an injection point
  per difference, it is two things wearing a trench coat. `drf_foundation.accounts` is the
  reference: the token mechanics moved up (identical in both, and a bug there hands away an
  account), the view bodies stayed down (one product decision per line).
- **`python/README.md`'s "deliberately does NOT cover" list is load-bearing.** It is the only
  thing standing between a future proposal and a re-attempt at something already ruled out.
  Phrase exclusions as gate failures naming what would have to change — "nothing shared to
  extract until X" — never as effort, which reads as "not yet" and invites the re-attempt
  (§17c). If X happens, re-examine rather than leaving the exclusion on a lapsed premise.

## Commands

No Makefile here — the two packages are driven directly.

```bash
# Python package — what CI runs
cd python && uv sync --locked --group dev   # --locked fails if uv.lock drifted from pyproject
uv run ruff check . && uv run ruff format --check .
uv run pytest -q

# JS package — what CI runs
pnpm typecheck && pnpm test

# Are the consumers behind, or running local copies of shared modules?
scripts/adoption-report.py            # add --exit-code to fail when anything is behind
```

## Coverage: the package's tests are the only tests

A consumer's suite exercises the consumer's call in the consumer's execution mode and
silently skips every other mode this package supports. So the question before tagging is
**which execution modes can a consumer call this from**, and each one needs coverage *here*.

`py-v0.14.0` killed SSE in production 73 seconds after deploy with this suite and both
consumers' suites green, because the Django test client is sync and the async branch had no
coverage anywhere. Anything touching DB connections, event loops or streaming: assume the
untested branch is the broken one.

Assert behavior, not names. The auth throttles shipped a hole for months under tests that
only checked `scope == "auth-login"`.

## Releasing

The version bump goes **in the tagging commit** — CI fails a tag whose version disagrees
with what the package declares.

```bash
# 1. bump version in python/pyproject.toml (or package.json) + relock, alone in one commit
# 2. tag and push
git tag py-v0.21.0 && git push origin py-v0.21.0
# 3. wait for the GitHub Release to appear — a tag without one did not pass
```

Then adopt in **both** consumers before starting the next extraction (§17 Gate 3). In each
consumer the pin bump is its own commit, with regenerated types and the migration onto the
new module as separate ones — the tag is coarse, so a bump carrying four other things cannot
be reverted without reverting them too.

Release tags are prefixed by side: `py-v*` and `js-v*`. Unprefixed `v0.x` tags predate the
convention and still resolve, but new releases only get prefixed tags.

## No OpenSpec here

Both consumers track non-trivial changes with OpenSpec, and blueprint §16 prescribes it for
projects on this stack. **This repo does not use it** — there is no `openspec/`, and a change
here is a commit plus, when it lands in a package, a release. Don't go looking for a
proposal to apply, and don't add the directory without deciding it is worth the ceremony for
a two-package library.

What plays the role a proposal would: blueprint §17's gates for whether something belongs
here at all, and `python/README.md`'s exclusions list for what has already been ruled out.

## Docs discipline

`docs/blueprint.md` is **vendored into consumers, not forked** — they re-sync it rather than
editing in place. A change here reaches them only when someone re-syncs, so land blueprint
edits and the consumer re-syncs together. A copy that gets edited locally becomes a fork and
silently stops receiving what is added here; that already happened once (§12).
