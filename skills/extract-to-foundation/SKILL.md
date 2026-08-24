---
name: extract-to-foundation
description: Move shared functionality into the django-react-foundation packages (drf_foundation / react-vite-foundation) through the blueprint §17 gates. Use when the user wants to extract, share, hoist, or "move X into the foundation", when a second project needs code a first project already has, or when adopting/bumping a foundation version across consumers. Also use to evaluate whether a proposed extraction should happen at all.
---

# Extracting into the foundation

Two failed attempts at extracting auth (blueprint §17b) cost more than writing the code
twice would have. Both failed because the interface was designed against one consumer and
the second consumer was consulted last. **This skill exists to make consumer #2 the design
review, before the tag.**

Blueprint §17 in `django-react-foundation/docs/blueprint.md` is the authority. Read it if
anything below is ambiguous.

## The repos

| | path | package |
|---|---|---|
| Foundation | `~/dev/django-react-foundation` | `drf_foundation` (`python/`), `react-vite-foundation` (repo root) |
| Consumer | `~/dev/adulting` | pins in `backend/pyproject.toml` `[tool.uv.sources]`, `frontend/package.json` |
| Consumer | `~/dev/pystonks` | same two places |

## Hard rules

1. **Do not write a proposal before Gates 0–2 pass.** Evidence is cheap; a 22-task
   proposal against an unverified assumption is what burned twice. Gather first.
2. **Never claim a gate passed from memory or from a docstring.** Read both consumers'
   actual code. The `SessionAuthentication401` item died because "adulting has the same
   bug" was assumed rather than checked — and it was wrong.
3. **A gate that fails ends the extraction.** Report the failure and stop. Do not shrink
   the scope and re-run the same reasoning; that is the loop that produced two dead
   proposals.

## Gate 0 — two call sites, in hand

Find the code in **both** consumers. Not "both could use it" — both *have* it, today.

```bash
# adjust the pattern; search both consumers for the same responsibility
rg -n '<the thing>' ~/dev/adulting/backend ~/dev/pystonks/backend
```

State explicitly, with file:line for each: consumer A has it at X, consumer B has it at Y.

**Fails if only one consumer has it.** Then this is not an extraction. It is either
premature (nothing to share yet — say so and stop) or a **stack-convergence proposal**
(Gate 2), which is a different change with its own justification. Do not smuggle it in as
an extraction.

## Gate 1 — diff the two, ship the intersection only

Put the two implementations side by side and classify **every** part:

- **identical** in both → candidate to move up
- **differs per product** → stays down, in the consumer

Write the classification down. Then apply the trench-coat test:

> If sharing it requires an injection point per difference, it is not one thing. It is two
> things wearing a trench coat.

Count the hooks/callbacks/settings the shared version would need purely to paper over
differences. More than one or two and the answer is no — you have not removed duplication,
you have moved it into a signature and added indirection on top.

Prefer the part where **a mistake is expensive** over the part that is longest.
`drf_foundation.accounts` is the reference: token mechanics moved (identical, and a bug
hands away an account); view bodies stayed (Turnstile, invite tokens, audit verbs,
notification kinds, mail templates — one product decision per line).

Also check the shapes actually match. A Django `EMAIL_BACKEND` and a provider you call
yourself are not the same object with different names, and "these both send email" is not
Gate 1 evidence.

## Gate 2 — converge first, extract second

If the two differ because the projects are on **different stacks** (one on a framework the
other deliberately avoids), the extraction is blocked behind a migration.

Say so, scope the migration as its own change in the consumer that has to move, and stop.
Never sequence it as extract-then-hope-the-other-one-bends. The foundation must never grow a
dependency a consumer deliberately avoids.

## Gate 3 — one module, one tag, both consumers adopt now

Scope check before writing any code: **can both consumers adopt this in the same working
session?** If no, the batch is too big — split it and extract the first piece only.

Then, in order:

1. Land the module in the foundation, with tests (Gate 4).
2. Bump the version in `python/pyproject.toml` or `package.json` **in the tagging commit**
   — CI's release workflow fails a tag whose version doesn't match.
3. Tag `py-v<version>` / `js-v<version>` and push. Wait for the Release to appear; a tag
   with no GitHub Release did not pass.
4. Adopt in consumer A, then consumer B. Both, before starting the next extraction.

## Gate 4 — coverage travels with the code, in the shape the consumer runs it

The package's own tests are the only tests that will ever cover the package. A consumer's
suite exercises the consumer's call in the consumer's execution mode and silently skips
every other mode.

Before tagging, ask: **which execution modes can a consumer call this from?**

- Callable from an async view? It needs an async test in `python/tests/`.
- Has a sync branch and an async branch? Both get covered *here* — no consumer will cover
  the branch it doesn't take.
- Anything touching DB connections, event loops, or streaming: assume the untested branch
  is the broken one. `py-v0.14.0` killed SSE in production 73 seconds after deploy with
  the foundation's suite and both consumers' suites green, because the Django test client
  is sync and the async branch had no coverage anywhere.

Run the full gate locally before tagging:

```bash
cd ~/dev/django-react-foundation/python && uv sync --locked --group dev \
  && uv run ruff check . && uv run ruff format --check . && uv run pytest -q
cd ~/dev/django-react-foundation && pnpm typecheck && pnpm test
```

## Gate 5 — the bump is its own commit

In each consumer, the version bump commit contains **only** the pin change and its
lockfile. Regenerated API types, the blueprint re-sync, and the migration onto the new
module are separate commits.

The tag is coarse — there is no rolling back one module out of a release, only back to the
previous tag, giving up everything in between. A bump commit carrying four other things
cannot be reverted without reverting them too, and the revert always happens under time
pressure.

```bash
# adulting
cd ~/dev/adulting/backend   # edit rev = "py-v<new>" in [tool.uv.sources], then:
uv lock && uv sync
# pystonks: same. Frontend: edit the #js-v<new> ref in frontend/package.json, then pnpm install
```

After bumping, run the consumer's own full gate (`make test` / its CI equivalent) and
regenerate API types in a **separate** commit if the schema moved.

## Reporting

Whatever the outcome, end with which gates passed, which failed, and the evidence — with
file:line, not recollection. If the extraction was rejected, record the reason in the
foundation's `python/README.md` "deliberately does NOT cover" list, phrased as a gate
failure rather than as effort (blueprint §17c): **"there is nothing shared to extract until
X"**, naming the X. An exclusion phrased as "a much heavier lift" reads as *not yet* and
invites the same dead proposal a third time.
