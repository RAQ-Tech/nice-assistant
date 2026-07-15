# ADR 0008: Separately configured platform Task Models

- Status: Accepted
- Date: 2026-07-14
- Owners: Nice Assistant maintainers

## Context

Chat titles, long-conversation summaries, memory extraction, and capability
planning are platform responsibilities. Making each persona model perform those
jobs couples correctness to persona prompts, consumes persona context, and lets
an entertainment-oriented model make privileged background decisions. The
12 GB shared-VRAM deployment also cannot safely assume that background model
calls may overlap without affecting chat and media latency.

Step 14 established a permissioned capability execution boundary, but its native
persona-model tool calls still made the persona the capability planner. Media
checkpoint, LoRA, workflow, and identity decisions need an even narrower later
contract and must not leak into capability planning.

## Decision

- Nice Assistant defines four owner-scoped platform roles: chat title generation,
  conversation summarization, memory extraction, and capability planning.
- Every role has a strict typed input, JSON Schema output, parser, input/output
  budget, timeout, temperature, primary model, optional fallback model, and
  documented terminal fallback behavior.
- Ollama receives the output schema through its structured `format` field.
  Persona chat requests receive no tool schema. Persona-emitted tool calls are
  rejected rather than treated as permissioned platform requests.
- Capability planning may select only an available semantic capability, prompt,
  and server-advertised operation/domain/content/feature requirements. Provider,
  checkpoint, workflow, LoRA, identity reference, URL, and resource selection
  are excluded from its schema. ADR 0009 assigns those resource decisions to a
  deterministic catalog coordinator.
- Title and capability tasks run after the persona reply within the durable chat
  job. Summarization runs during context compaction. Memory extraction remains a
  separate post-turn job. They use the interactive queue lane; the default one
  worker prevents Nice Assistant from overlapping these calls with chat.
- `task_model_profiles` stores per-user role configuration. `task_model_runs`
  stores role, selected/executed provider and model, attempts, token estimates,
  latency, and safe errors. It deliberately stores neither task prompts nor
  generated task output.
- The Settings screen is an operator control and diagnostic surface, not a
  public test lab. A developer-only CLI provides curated local Ollama screening
  without showing output unless explicitly requested.

## Alternatives considered

- Continue using persona models for background work. Rejected because persona
  style and platform correctness have different requirements and trust levels.
- Build one unrestricted coordinator that also chooses media resources. Rejected
  because model/catalog compatibility and visual identity need explicit,
  explainable constraints in Steps 16–18.
- Run all task calls concurrently. Rejected as a default because shared local
  GPU memory is the current deployment constraint; operators may deliberately
  change worker counts with the resulting concurrency tradeoff documented.
- Expose a user-facing task-model lab. Rejected because model qualification is a
  developer/operator concern, not part of the companion experience.

## Consequences

Persona responses no longer depend on native tool-call support for capability
planning. A separate local model can be small and deterministic while a persona
uses a larger or more expressive model. Model switching may add load latency on
limited VRAM, so using one suitable local task model for all roles is the
recommended starting point.

Task failure cannot silently invent a platform result: titles have deterministic
fallback, summaries and capability planning may skip, and memory extraction
fails only its post-turn job. Provider/model quality still requires live
screening on the deployment hardware. Media resource planning is implemented by
the catalog coordinator; persona visual identity remains explicitly
unimplemented.

## Verification

- Strict contract tests reject extra or unavailable fields and prove the current
  user text is untrusted payload data.
- API tests prove owner-scoped profiles/runs, distinct persona/task models,
  readiness, fallback, redaction, and content-free audit records.
- Migration and restart tests preserve prior rows, seed four roles, enforce
  constraints, and fail interrupted runs safely.
- Browser unit and Playwright tests cover profile editing, readiness, and recent
  run diagnostics through canonical `/api/v1` routes.
- `scripts/evaluate_task_models.py` screens all four roles against curated
  contract and semantic cases on an explicitly selected Ollama model.
