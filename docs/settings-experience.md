# Settings experience

Nice Assistant settings are product controls, not a mirror of database fields
or provider payloads. A person who operates their own server should be able to
understand what a setting changes, whether the related feature is ready, and
what to do next without reading the source code.

## Interaction rules

- Lead each tab with its purpose in plain language.
- Keep the primary screen concise. Put short explanations behind a consistent
  information icon that appears on hover and keyboard focus; keep warnings and
  consequences that require a decision visible in the flow.
- Put the common path first. Hide provider diagnostics, thresholds, raw
  workflow controls, and destructive actions under clearly labeled advanced
  sections.
- Prefer pickers, previews, and recognizable names over opaque resource IDs.
- Show readiness as separate truthful facts. Do not collapse configured,
  reachable, generation-capable, and verified into one ambiguous status.
- Explain optional dependencies where they are used, including what the
  dependency cannot do.
- Use labels based on the operator's intent. Internal consent, capability, and
  provider terminology belongs in supporting text only when it materially
  affects privacy or behavior.
- Keep dangerous actions separate, explicit, and reversible where possible.
- Do not erase expert control; progressive disclosure should make it available
  without making it the first thing every user must understand.

## Delivery chunks

### 21A — Visual Identity — delivered

- Guide the operator through selecting a persona, enabling private reference
  storage, choosing an image, and explicitly approving it.
- Replace protected-media ID entry with an owner-scoped generated-image
  thumbnail picker.
- Separate reference storage, reference-aware generation, optional comparison,
  and automatic blocking into honest readiness rows.
- Explain that CompreFace is an optional comparison service. It can evaluate a
  generated face but cannot make generation resemble the reference.
- Use fictional-persona language for the rights confirmation while preserving
  the durable backend consent and audit model.
- Keep verifier settings, thresholds, manual validation, history, and deletion
  in an optional advanced section.

### 21B — Everyday settings — delivered

General, TTS, STT, Image Generation, Video Generation, Memory, User, Personas,
and Workspaces now use the same approachable structure:

- Common choices appear first in goal-oriented cards; diagnostics, credentials,
  retention, tuning payloads, and new-persona defaults begin closed.
- A shared accessible information icon reveals concise explanations on hover or
  keyboard focus without filling the page with instructional copy.
- Speech and transcription copy describes the completed-audio and push-to-talk
  behavior that exists today; it does not imply streaming speech or local STT.
- Memory distinguishes pending, forget, and permanent delete, including atomic
  bulk actions. Persona editors remain collapsed until selected, and workspaces
  explain their organizational scope.
- Local image connection choices remain readily available while sampling,
  authentication, and raw JSON controls live under advanced disclosure.

This presentation change does not alter provider semantics or saved settings.

### 21C — Operator settings — delivered

Models, Task Models, Media Catalog, GPU Coordination, and Data retain their
operator controls behind a consistent guided structure:

- Each tab leads with its actual purpose and separate readiness facts rather
  than presenting configuration as proof of health.
- Models shows the effective default, installed Ollama count, context window,
  and saved per-model customization count. Sampling controls and the real
  per-model override editor begin closed.
- Task roles and media resources are collapsed named editors. Budgets, failure
  policy, raw provider payloads, and content-free audit records remain
  available under nested advanced disclosure.
- Media compatibility is selected by named base model instead of requiring
  operators to copy internal IDs. Catalog drafts, planning limits, and
  deterministic plan previews remain explicit.
- GPU Coordination separates measured capacity, adapter capability, and
  operator authorization. Managed-mode consequences remain visible because
  they affect external provider state.
- Data separates backup creation from restore verification and destructive
  archive deletion. Permanent deletion uses an explicit consequence warning.
- Tabs with independent persistence no longer display a global save button
  that cannot save their changes; each operation has a local action instead.

The operator logic is split into focused typed modules so the settings shell no
longer owns Task Model, media-catalog, coordination, or backup workflows.

These chunks were intentionally separate. Visual Identity needed a new
protected media-list contract and an interaction redesign; the everyday and
operator tabs have different audiences and therefore use separate modules and
interaction depth.
