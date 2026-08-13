# Autonomous decision log

Decisions taken without operator sign-off, recorded so they can be reviewed in
one sitting instead of one interruption at a time. Each entry states what was
decided, what the alternative was, and how to reverse it.

The operator asked for the project to keep moving without per-decision approval.
This file is the other half of that arrangement: nothing is hidden, and anything
here can be overturned.

Reverse-chronological. Newest decisions first.

## Deferred operator input

Items that genuinely need the operator, listed so they can be done in one pass.
Nothing below blocks further development.

| # | Needs | Why it needs a person | Consequence of waiting |
|---|---|---|---|
| D1 | Update the Unraid container to `ghcr.io/raq-tech/nice-assistant:latest` | Live system change on hardware the assistant cannot reach | None of the merged work is running yet |
| D2 | Settings → Models → context allocation → `8192` | Live setting; costs ~0.5GB VRAM | Character card plus lore fit poorly at 4096 |
| D3 | Settings → Task Models → point roles at a small clean instruct model | Requires knowing which models are installed | Memory extraction and capability planning stay less reliable on an abliterated model |
| D4 | Persona card content | Authored character material; nobody else knows the persona | The persona-depth features are mechanism with nothing in them |
| D5 | Whether lore should be shareable across personas in a workspace | Product scope question | Shared background must be authored per persona |

`docs/persona-authoring.md` exists so D4 is a fifteen-minute edit rather than a
blank page.

## Delivery decisions

### A10 — Voice core assessed as blocked rather than started

Roadmap steps 10 to 13 were the next planned capability. Step 10 is a *blind
listening evaluation and provider decision*: choosing a voice by ear. That is not
a decision that can be made and logged for later review, because the artifact
being judged is sound. The roadmap additionally gates the whole block on the
current work being accepted in production, which has not happened yet, and states
that step 11 cannot select providers until the listening decision is approved.

Building streaming or fallback scaffolding ahead of that would mean advertising a
capability the deployment cannot honor, which `AGENTS.md` forbids and the debt
register already commits against: no streaming TTS endpoint is advertised until
step 11 implements it.

*Alternative:* build the provider-neutral streaming transport now and choose a
voice later. Rejected because fallback and streaming both imply provider
selection, and a half-built voice path is worse than an honest deferral.
*Reverse:* nothing to reverse; no code was written.

### A9 — Dependabot configuration added

Alerts were enabled but no `.github/dependabot.yml` existed, so nothing opened
update pull requests and 31 advisories accumulated silently. Weekly `pip` and
`npm` updates, monthly actions, grouped so a routine bump is one review.

*Alternative:* leave it manual. Rejected because manual is what produced 31.
*Reverse:* delete the file.

### A8 — Dependency bumps taken without staging

Pillow 11.3.0 → 12.3.0 is a major version, and Pillow decodes untrusted uploads
in `app/identity_images.py`. Bumped anyway, because several advisories were
decode-path issues in exactly that surface, and the identity, audit, and full
suites pass unchanged.

*Alternative:* pin to 11.x and wait. Rejected; that leaves the vulnerable decode
path in production.
*Reverse:* revert `d29a942`.

### A7 — `npm audit fix` without `--force`

Cleared all browser advisories through the lockfile only. No declared version
changed.

*Alternative:* `--force`, which would have upgraded toolchain majors. Rejected as
disproportionate for devDependencies that never reach the runtime image.
*Reverse:* revert `5d72da0`.

### A6 — Rebase merges, and merging without review

Every pull request was merged by the assistant with rebase, preserving one
commit per logical change so any single change stays independently revertible.

*Alternative:* squash. Rejected because several pull requests explicitly relied
on their commits being separately revertible.
*Reverse:* `git revert` the specific commit.

### A5 — Pull request #63 taken out of draft and merged

It was left draft in an earlier session because its memory-scope fix overlapped
an undecided memory design discussion. That discussion resolved as "defer the
memory overhaul", which leaves the scope fix standing on its own as a defect fix.

*Alternative:* leave it draft indefinitely. Rejected; it was blocking on a
question that no longer existed.
*Reverse:* revert `69de67a`.

## Design decisions

### A4 — Phase 2 built despite the spec saying defer

`docs/persona-depth-spec.md` recommended deferring everything after the character
card until the deployment could afford 8k of context. That recommendation was
routing around a missing guarantee rather than a missing setting: nothing
protected conversation history at all, so saved memory and a summary could
already crowd out recent turns before any authored material existed.

Building the history floor the spec's own testing section asked for removes the
hazard, so phase 2 and phase 3 shipped at 4096.

*Alternative:* wait for the operator's VRAM decision. Rejected because the
decision gated nothing once the floor existed.
*Reverse:* revert `86f30d7` and `780f7c9`.

### A3 — Numbers the spec left open

| Setting | Value | Reasoning |
|---|---|---|
| `history_floor_ratio` | 0.25 | Roughly five or six messages at 4096; scales with the window rather than needing a second setting |
| Yield order | summary → memory → lore → example dialogue | Reverse authority order. Summary goes first because it is history at lower fidelity |
| `card_max_ratio` | 0.30 | Matches the spec's own ~1000-token card at 4096 |
| Lore scan window | 3 messages | Taken from the spec |

*Reverse:* all four are constants in `app/context_policy.py` and
`app/persona_lore.py`.

### A2 — Card cap measured against the narrowest configured window

A persona is not bound to one model, so capping against the most generous
configured context window would let a card break on the smallest one.

*Alternative:* cap against the account default. Rejected as it produces a card
that fails on a model the operator has configured.
*Reverse:* `smallest_configured_context_window` in `app/persona_card.py`.

### A1 — Choices that resolved toward truthfulness

- `card_example_dialogue` was left out of the phase 1 migration even though the
  spec proposed one migration, because a stored field nothing reads reads as
  support that does not exist.
- `{{user}}` renders as a generic speaker rather than the account username. A
  username is a credential, not a chosen display name.
- Lore keys are literal text, never patterns. Operator-authored regex is a
  footgun and a denial-of-service surface.
- Card fields are writable only through the card route, so the save-time cap has
  exactly one enforcement point.
- Media preference validation checks only values that changed, because the
  browser resubmits every stored value and an account holding a legacy value
  would otherwise be unable to save anything at all.
- The 8k context allocation was **not** changed on the operator's behalf. It is a
  live system setting with a VRAM cost, and measuring the result needs the
  hardware.
