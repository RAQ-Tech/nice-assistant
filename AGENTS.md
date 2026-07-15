# Nice Assistant engineering instructions

## Product direction

Nice Assistant is a private-LAN, voice-first companion. Natural turn-taking,
credible speech, persona continuity, dependable memory, and graceful provider
fallback are core product behavior. Image and video generation are supporting
capabilities and must not compromise the conversational foundation.

Local speech engines run as separate LAN services. The Nice Assistant container
must remain hardware-agnostic and must not claim to control provider residency
unless it uses a real, verified provider control API.

## Foundation-first rule

Before extending a subsystem, identify design or implementation choices that
would make the requested result unreliable, misleading, unsafe, or difficult to
evolve. Call those issues out and fix or explicitly isolate them. Do not add a
feature on top of a known faulty foundation merely to minimize the diff.

Large replacements are acceptable when they materially improve the end result.
Preserve data and documented external behavior through migrations or deliberate,
documented compatibility breaks.

## Truthful product behavior

- Do not advertise placeholders, stubs, modeled state, or unverified provider
  support as working features.
- Disabled or unavailable capabilities must be labeled honestly in the API,
  settings UI, README, and provider readiness checks.
- A saved setting is not complete until a test proves that it changes runtime
  behavior.
- Provider failures shown to users must be useful but must never expose secrets.

## Architecture and documentation

- Keep HTTP/WebSocket routing, application services, provider adapters,
  persistence, and browser state/audio code in separate modules.
- New HTTP APIs use `/api/v1`. Keep legacy `/api` compatibility only while a
  documented consumer still depends on it.
- Record durable architectural choices in `docs/decisions/` using the ADR
  template.
- Update the applicable product, architecture, security, testing, operations,
  roadmap, and debt documents in the same change as behavior they describe.

## Repository privacy and local records

- Never commit exact deployment addresses, hostnames, user-specific server
  paths, hardware inventories, storage capacity, backup identifiers, account
  email addresses, persona content, or unrelated private service names.
- Keep useful installation-specific evidence under `.local/`, which is ignored
  by Git. Public documentation uses placeholders and describes repeatable
  procedures rather than one operator's topology.
- Never store credentials or the deployment master key in `.local/` or public
  documentation. Secrets belong in the deployment's secret-management layer.
- Run `python scripts/audit_public_repo.py` before every public commit. Maintain
  `.local/public-repo-private-values.txt` so local verification catches known
  private values if they are accidentally reintroduced.

## Verification and delivery

Run focused tests first, then the complete suite, then the process or container
smoke relevant to the change. Provider contract tests must use deterministic
fakes in CI; live-provider checks are explicit deployment acceptance tests.

For an approved implementation, finish all in-scope work, update documentation,
commit a focused change, and push the current branch. Never discard unrelated
user changes to make a commit clean.
