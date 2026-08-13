# CLAUDE.md

## Engineering instructions

[`AGENTS.md`](AGENTS.md) is authoritative for this repository. Read it before
making changes and follow it exactly: product direction, the foundation-first
rule, truthful product behavior, architecture and documentation boundaries,
repository privacy, and verification and delivery.

Those rules are deliberately not repeated here. Two copies of the same
instruction drift apart, and the copy that drifts is the one that gets
followed. This file adds only the two entry points that `AGENTS.md` does not
name directly.

## Verify

The repository verifier is the definition of done. It must pass with no errors
before a change is considered complete:

```
npm run verify
```

It runs, in order: browser typecheck, browser unit tests, browser production
build, the public-repository privacy audit, Python compile, static analysis and
formatter checks, the unit/API suite with coverage, the process smoke check, the
Playwright browser journeys, and the human-experience scenarios.

Variants, for when they are the right tool:

- `npm run verify:foundation` - repeats the unit/API suite three times. Use it
  when a change could introduce order-dependent or intermittent behavior.
- `python scripts/verify.py --skip-browser-e2e` - skips the Playwright journeys
  for a faster inner loop. Never use it as the final check.
- `python scripts/audit_public_repo.py` - the privacy audit alone. Run before
  every public commit, as required by `AGENTS.md`.

A passing verifier is not acceptance evidence for work marked
**Blocked - deployment** in the backlog. Those items additionally require the
installed browser journey on the real private-LAN topology.

## Backlog

[`BACKLOG.md`](BACKLOG.md) is the single list of remaining work. It is the index
and the honest status; the detail stays in `docs/`.

Items are grouped by what can actually be started, not by topic: **Ready**,
**Needs decision**, **Blocked - operator**, **Blocked - deployment**, and
**Not advertised**. Read the group before starting an item - roughly half the
list cannot be completed from this repository alone, and the blocking reason is
recorded with each entry.

Keep it current in the same change as the behavior it describes, exactly as
`AGENTS.md` requires for the documents under `docs/`. When an item is finished,
update `BACKLOG.md` and the source document it points at together. The
**Not advertised** section is a guard, not a wishlist: nothing in it may ship as
a stub or be described as working.
