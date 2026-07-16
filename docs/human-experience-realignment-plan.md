# Human experience realignment plan

- Status: Approved; implementation in progress
- Date: 2026-07-16
- Scope: conversation, speech, image generation, persona truthfulness, settings,
  verification, and delivery priorities

## Goal

Make Nice Assistant feel like messaging and speaking with a coherent person.
Provider coordination, identity evidence, audit history, and diagnostics remain
available, but they must support that experience instead of becoming the normal
conversation UI.

This plan distinguishes the currently shipped checkout from unpublished local
repairs. A local repair is not treated as delivered until it is tested through
the installed browser workflow and published.

## Non-negotiable experience contract

1. Conversation remains usable while speech or media work is running.
2. An ordinary, explicit request for an image is already permission to generate
   it. It does not require a second approval by default.
3. A working basic image provider is enough to generate an image. Visual
   identity improves or verifies a result; missing identity setup does not block
   the default path.
4. Generated images arrive as durable, ordinary chat attachments associated
   with the selected persona, not as operator plan cards.
5. Technical selection, resource, identity, and provider details are available
   through optional details and operator settings, not expanded in normal chat.
6. Image failures use one compact inline message with a retry action and safe,
   collapsed details.
7. `Blur images` is an easily accessible chat control, is persisted per user,
   and defaults to off. When enabled, the first activation reveals an image and
   the second opens the preview.
8. The persona does not claim that an image was taken, sent, or verified until
   the platform has evidence for that state.
9. Strict identity conditioning, post-generation blocking, and approval before
   generation remain available as explicit advanced policies.
10. Product completion claims require the same installed-browser journey used
    by an operator, not only service tests or mocked browser routes.

## Current alignment gaps

1. **Voice-first priority is inverted.** The media catalog, coordination, and
   identity roadmap expanded while streaming speech, automatic turn detection,
   local transcription fallback, and real barge-in remain deferred. This
   conflicts with ADR 0001 and the product vision.
2. **Speech blocks conversation in the shipped browser.** Typing, Send, and
   push-to-talk are disabled while speech plays. A local repair exists but has
   not yet completed installed-browser acceptance, and capability controls are
   still coupled to the global chat phase.
3. **Conversational images require redundant approval.** The user asks for an
   image, the Task Model creates a pending request, and the UI asks the user to
   approve the same action again. This behavior is required by current ADRs, so
   changing it requires an explicit product and architecture decision.
4. **The image UI is an operator console.** Normal chat exposes prompt, plan,
   resource, model, workflow, estimated memory use, identity state, warnings,
   and rejection reasons in a large persistent card.
5. **Basic generation can still be blocked by optional identity work.** The
   shipped fallback contradicts the intended default by requiring profile,
   consent, and reference state before using an unconditioned image path. A
   local repair exists but is unpublished.
6. **Imperfect identity results are hidden by default.** A failed optional
   comparison defaults to withholding the generated result. The human-oriented
   default should show the result with a subtle unverified label; strict
   blocking should be opt-in.
7. **Blur behavior is split and uncontrollable.** The older ordinary-message
   renderer still blurs images, while capability results bypass that code and
   open the preview directly. There is no chat control, and the surviving path
   effectively defaults to on rather than off.
8. **Direct and conversational generation are separate products.** The direct
   image action uses everyday provider settings, bypasses catalog planning, and
   renders a synthetic message. A natural-language request uses the Task Model,
   Media Catalog, approval, and a capability card.
9. **Some generated images are not durable transcript entries.** Direct results
   are inserted into browser state and can disappear from the conversation
   after reload even though the artifact remains in the media library.
10. **Media work can monopolize the chat UI.** The backend has separate work
    lanes, but the browser uses one global busy phase that replaces Send with
    Cancel and prevents independent conversation and media interaction.
11. **Persona claims can get ahead of platform truth.** The persona response is
    generated before capability planning and completion, allowing conversational
    text to imply that a picture was taken or sent while the request is blocked,
    pending, or failed.
12. **Capability intent precision is not at the auto-run quality bar.** Live
    acceptance produced an unsolicited image request from an ordinary story
    prompt. Automatic generation must be limited to explicit action intent and
    must return no capability for discussion, narration, or hypothetical text.
13. **Image failures are duplicated and oversized.** A failed capability leaves
    the full technical card in the transcript and may also set a global error.
    Rejected-resource details remain permanently expanded.
14. **Active media presentation is not reload-resilient.** Polling is owned by
    the approval-button call. Reloading or reopening a chat does not reliably
    resume active capability observation and reconcile exactly one result.
15. **Basic readiness mirrors internal architecture.** Image Generation, Task
    Models, Media Catalog, Visual Identity, and GPU Coordination each expose a
    portion of readiness. A normal operator lacks one answer to “Can chat make
    an image now?”
16. **Reply latency includes unrelated platform tasks.** Title generation and
    capability planning run sequentially after persona generation but before
    the browser treats the turn as complete and begins speech.
17. **Chat-title behavior is not accepted end to end.** A deterministic title
    may already be stored while the browser continues to show a placeholder;
    cancellation and failure paths can miss reconciliation. The local render
    timing change is not proof of the complete installed workflow.
18. **Speech output is not yet companion quality.** The shipped path uses
    completed-file playback, can send raw assistant formatting to TTS, has no
    automatic provider fallback, and does not provide natural interruption.
19. **The visible Cancel state is not always truthful.** A stale pending browser
    request can remain after server-side turn completion, while TTS preparation
    does not expose equivalent cancellation.
20. **Manual memory saving can promote raw prose to fact.** One-click saving can
    store an entire user or assistant message as active memory instead of
    creating an editable, reviewable fact candidate.
21. **The default chat chrome emphasizes system state.** Model, workspace,
    memory mode, multiple status indicators, and per-message tools are more
    prominent than necessary for a person-like default surface.
22. **Graceful provider fallback is a stated goal, not a delivered behavior.**
    Speech and image services select one configured provider and surface its
    failure rather than trying an approved alternative.
23. **Verification overstates lived readiness.** Existing tests strongly cover
    services and mocked card behavior, but the installed-container smoke does
    not complete the exact request, generation, durable display, reload, blur,
    interruption, and failure journeys that define the feature.

## Delivery plan

### Phase 0 - reset the product contract

- [x] Add a superseding ADR for low-risk companion capabilities. Treat an
  explicit user image request as permission to run by default while retaining
  durable audit, cancellation, and an advanced `Ask before generating images`
  policy.
- [x] Preserve confirmation for destructive, externally consequential, or
  explicitly strict actions; do not generalize image auto-run into blanket tool
  autonomy.
- [x] Update product vision language that currently requires every media plan to
  be shown before approval. Deterministic planning remains required, but its
  details move out of the default transcript.
- [x] Make the default identity policies `allow_unconditioned` and
  `show_unverified`. Keep `require_conditioning` and post-comparison blocking as
  deliberate advanced choices.
- [x] Reorder the roadmap so regression restoration is followed by voice-core
  acceptance before additional capability-catalog expansion.

**Exit gate:** the product documents, ADRs, permission contract, and UX contract
describe one coherent behavior.

### Phase 1 - stabilize the current regressions

- [x] Finish and review the unpublished identity fallback so zero profile,
  draft profile, missing consent, missing reference, and missing identity
  workflow all fall back to an ordinary ready plan under the default policy.
- [x] Ensure an unconditioned job never transmits or uses identity reference
  data. Documentation must claim that boundary precisely without overstating
  which metadata was inspected while planning.
- [x] Route identity-card behavior from the effective conditioning status, not
  merely the original `required_features` list, so unrelated plan failures do
  not send users to identity setup.
- [x] Finish speaking-time composer behavior with a playback token/state model:
  typing remains available, Send stops current playback and submits once, and
  push-to-talk interrupts playback cleanly.
- [x] Remove the pending/speaking race by entering `speaking` only after playback
  actually starts and by clearing the completed turn before TTS preparation.
- [x] Reconcile titles immediately after turn acceptance and on success,
  cancellation, and failure; verify both header and chat drawer.
- [x] Use the existing speech-text cleaner before TTS and cover Markdown, links,
  code, and hidden-reasoning removal.
- [x] Add real DOM tests for these state transitions; installed-browser acceptance
  remains part of each production promotion rather than this source slice.

**Exit gate:** basic image generation is not identity-blocked by default; typing,
sending, and push-to-talk work during speech; a first-turn title is visible
without waiting for speech.

### Phase 2 - create one durable image path

- [x] Represent every direct or Task-Model image request as the same durable,
  owner-scoped capability plus chat-attachment record.
- [x] Migrate the direct image action off its synthetic browser-only message and
  manual-plan presentation without breaking its simple one-click behavior.
- [x] Render queued, running, completed, failed, and canceled media from durable
  server state so refresh and navigation resume cleanly and never duplicate an
  attachment.
- [x] Admit media work independently from conversation and speech phases. Keep a
  compact cancel action scoped to the media job.
- [x] Continue deterministic catalog/resource selection and immutable audit
  records without exposing them in the normal transcript.

**Exit gate:** both image entry points produce the same durable chat attachment,
survive reload, and do not block a new conversation turn.

### Phase 3 - make image orchestration conversational

- [x] Raise capability intent precision before enabling default auto-run. The
  Task Model must produce no request for stories, discussion, explanations,
  hypotheticals, or quoted instructions.
- [x] Add explicit-action fixtures and adversarial negatives to the Task Model
  evaluation set. Gate auto-run on the evaluated typed result, not keyword
  heuristics or persona claims.
- [x] Run capability planning independently from reply delivery so title,
  memory, and media tasks cannot delay a completed persona response.
- [x] Give the persona truthful capability state: it may say it is making an
  image after admission, but may only present it as sent after a durable result
  exists.
- [x] Auto-queue normal explicit image requests under the default policy. Add an
  advanced, persisted confirmation policy for operators who want it.
- [x] Keep identity status as attachment metadata. Show only a subtle
  `Identity not verified` indicator when material; put evidence and provenance
  behind Details.

**Exit gate:** an explicit image request begins without a second approval, an
ordinary story never starts generation, and persona wording never outruns the
actual job state.

### Phase 4 - restore the picture-message experience

- [x] Replace the expanded capability card with a shared compact attachment
  component: small progress, image, scoped cancel/retry, and optional Details.
- [x] On success, show the image as an ordinary persona chat attachment. Do not
  auto-open a modal or technical panel.
- [x] On failure, show one small inline notice such as `I couldn't make that
  image. Retry - Details`. Keep raw resource IDs, workflow IDs, memory estimates,
  and rejection lists out of the default transcript.
- [x] Extract one shared image interaction binder used by all chat attachments.
- [x] Add `Blur images` to chat controls as a persisted per-user toggle,
  defaulting to off, with `aria-pressed` and keyboard support.
- [x] When blur is on, initially blur every generated image; first activation
  reveals it in place and second activation opens the preview. Turning blur off
  reveals current images immediately; turning it on resets current-chat reveal
  state.
- [x] Keep fullscreen preview opt-in and image-focused, with technical details
  elsewhere.

**Exit gate:** success looks like receiving a picture message, failure occupies
one compact row, and blur behavior is consistent across every image source.

### Phase 5 - simplify everyday image setup

- [x] Add one everyday `Images` readiness summary that separately answers:
  provider reachable, basic generation ready now, and optional identity
  enhancement ready.
- [ ] Make provider setup seed or repair the minimum normal generation path
  without requiring the operator to understand Task Models or catalog records.
- [ ] Keep catalog metadata, resource compatibility, workflows, identity
  bindings, and memory estimates under advanced/operator settings.
- [x] Ensure every displayed policy is editable where it is shown, or link
  directly to the one editable source of truth.
- [x] Use human labels in chat and everyday settings; reserve internal names such
  as `identity_control` for optional diagnostics.

**Exit gate:** a new operator can configure a provider and answer “ready to make
chat images” from one screen; identity setup is visibly optional.

### Phase 6 - restore the voice-first roadmap (deferred by operator decision)

- [ ] Stream TTS audio and begin playback before a complete response file exists.
- [ ] Implement automatic end-of-turn detection with push-to-talk retained as a
  dependable fallback.
- [ ] Implement true barge-in that stops playback and superseded provider work.
- [ ] Add approved quality-first and local fallback chains for TTS and STT, with
  compact user-facing degradation notices.
- [ ] Evaluate providers with repeatable latency, reliability, and blind
  listening criteria rather than configuration readiness alone.
- [x] Reduce default chat chrome to persona and conversation essentials; move
  model, workspace, memory, and diagnostic state behind progressive disclosure.

**Exit gate:** a hands-free conversation supports natural turns, interruption,
fallback, and credible speech without removing push-to-talk reliability.

### Phase 7 - strengthen continuity and truthfulness

- [x] Change manual memory save into an editable pending fact proposal. Never
  promote raw assistant prose directly to accepted factual memory.
- [x] Add scenario evaluations for corrections, long conversations, persona
  switching, memory boundaries, emotional tone, spoken rendering, and provider
  outages.
- [x] Ensure each visible Cancel action maps to work the platform can actually
  cancel and disappears when that work has completed.
- [x] Add approved image-provider fallback while preserving deterministic audit
  and avoiding hidden identity claims.

**Exit gate:** continuity and degradation behavior are measured through lived
scenarios, not inferred from infrastructure tests.

### Phase 8 - acceptance and publication

- [ ] Run focused backend and frontend tests, then the complete repository suite.
- [ ] Build the production frontend and container.
- [ ] Run installed-browser acceptance at desktop and mobile widths with mouse,
  touch, and keyboard input.
- [ ] Run the public-repository privacy audit before each public commit.
- [ ] Publish in small, reversible commits and verify the deployed revision and
  exact browser journey after each user-visible phase.
- [ ] Do not mark a roadmap item delivered until its installed-browser journey
  and documented failure behavior pass.

## Required installed-browser journeys

1. With a working ordinary image provider and no identity records, an explicit
   request auto-queues and produces one durable inline image without setup,
   approval, plan card, or modal.
2. The same request works while speech is playing; the composer remains
   editable, sending interrupts speech, and media continues independently.
3. Reload during queued or running generation resumes the compact status and
   eventually displays exactly one result.
4. Missing identity workflow, reference, or verifier does not block default
   generation. Explicit strict conditioning blocks with one compact actionable
   notice.
5. A failed identity comparison shows the best result with an unverified label
   by default. An explicit strict policy withholds it.
6. `Blur images` defaults off, persists, applies to every generated-image path,
   and performs reveal-then-preview when on.
7. Provider failure produces one compact Retry/Details notice and leaves chat
   usable; no secret or raw internal identifier appears.
8. A story, explanation, hypothetical, or quoted image instruction produces no
   image job.
9. The persona never says an image was sent when the request is blocked, pending,
   canceled, or failed.
10. A first-turn title appears in the header and drawer even when the title Task
    Model is slow, unavailable, or returns a placeholder.
11. TTS never reads Markdown, links, code, or hidden reasoning, and typed or
    spoken interruption works during playback.

## Proposed delivery slices

1. Product contract ADR and regression safety net.
2. Shipped identity fallback, composer interruption, title reconciliation, and
   speech-text cleanup.
3. Unified durable media record and reload recovery.
4. Default auto-run with high-precision intent contract.
5. Human-scale attachment UI, compact errors, and blur toggle.
6. One-screen everyday image readiness and advanced-policy cleanup.
7. Streaming voice, turn detection, barge-in, and provider fallback.
8. Continuity evaluations, full installed acceptance, documentation, and
   roadmap reconciliation.

Each slice should be independently testable and publishable. No later slice may
reintroduce a blocked composer, a non-durable attachment, or mandatory identity
setup into the basic path.
