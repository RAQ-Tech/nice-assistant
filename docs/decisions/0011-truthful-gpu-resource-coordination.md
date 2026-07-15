# ADR 0011: Truthful external GPU resource coordination

- Status: Accepted
- Date: 2026-07-14
- Owners: Nice Assistant maintainers

## Context

Nice Assistant commonly shares one GPU among Ollama and separately deployed
Automatic1111 or ComfyUI services. Starting an image job from a configured VRAM
estimate alone can force model eviction, spill into system RAM, or make chat
unresponsive. The deleted residency adapters were not a valid solution: they
modeled loaded state without observing or controlling the actual provider.

The providers expose different facts and controls. ComfyUI reports device and
queue state and has a coarse model-free control; Automatic1111 reports memory
and can unload its checkpoint; Ollama reports loaded models and per-model VRAM
and accepts an explicit unload request. None of those APIs proves that Nice
Assistant is the only client of a service.

## Decision

- Add a provider-neutral capacity and release boundary backed by the providers'
  real HTTP APIs. Unknown or unavailable telemetry remains unknown; configured
  catalog VRAM is still an operator estimate.
- Keep coordination disabled by default. `observe` admits catalog-planned local
  image work only after measured free VRAM meets the plan estimate plus reserve.
  `managed` may additionally request coarse release from the target media
  service and Ollama before admission. After a local image job that actually
  held the shared-resource lease reaches a terminal state, `managed` also
  requests release from that job's media provider so its model does not remain
  resident and force the next interactive model into CPU-heavy execution.
- Permit release only after an administrator explicitly attests that the exact
  endpoint is exclusively controlled and separately enables release. The grant
  is bound to a normalized endpoint fingerprint, so changing the endpoint
  invalidates it. Failed controls never imply success; capacity is measured
  again before admission.
- Hold waiting work in the queue without occupying a worker. While coordination
  is enabled, Nice Assistant serializes its own chat/task and local-image GPU
  work with an in-process lease and gives queued interactive work priority.
- Time out with a safe failure instead of waiting forever or bypassing unknown
  capacity. Cancellation removes waiting admission records. Durable audit
  events contain provider, fingerprint, action, outcome, and non-secret facts.
- Apply measured-capacity admission only to catalog-planned local image work
  with a non-zero estimate. Unknown demand is admitted without a fabricated
  capacity decision, but local image work still participates in the process
  lease and authorized post-job cleanup. Cancellation before execution never
  causes a release; running cancellation retains the lifecycle record so work
  that may already have loaded a model is reclaimed. Video remains later work.

## Alternatives considered

- Keep modeled residency state. Rejected because it was neither measured nor
  authoritative and could advertise control that did not exist.
- Let every configured provider be unloaded automatically. Rejected because
  other LAN clients may own active work and provider controls are coarse.
- Use `nvidia-smi` from the Nice Assistant container. Rejected because the lean
  container is hardware-agnostic, process memory does not express provider
  intent, and it would not provide a safe service-level release contract.
- Block a media worker while polling. Rejected because capacity waits would
  consume execution slots and reproduce the starvation this change prevents.
- Infer required VRAM from observed free memory. Rejected because free memory
  does not describe the pending model/workflow; the explicit catalog estimate
  remains the admission requirement.

## Consequences

Operators gain observable and optional managed behavior without giving the
product false GPU ownership. Managed mode can unload Ollama or media models and
therefore add reload latency to the next request. Post-job cleanup finishes
inside the lease boundary, so queued chat cannot start between media completion
and reclamation. The in-process lease covers only work submitted by this Nice
Assistant process; exclusive authorization is required precisely because
uncoordinated external clients cannot be serialized.
Provider-version differences surface as unavailable telemetry or failed release
events rather than optimistic readiness.

This is the capacity/admission foundation, not full workflow orchestration.
Identity conditioning, inpainting, multi-stage cancellation, post-generation
identity correction, and real deployment tuning remain follow-up work.

## Verification

- Migration tests preserve existing jobs and enforce policy/authorization
  constraints in migration `0012_resource_coordination`.
- Provider tests cover capacity parsing, queue facts, Ollama model VRAM, coarse
  releases, unavailable endpoints, and safe error redaction.
- Service/API tests cover administrator isolation, disabled/observe/managed
  modes, authorization invalidation, remeasurement, timeout, cancellation,
  durable audit, non-blocking media admission, unknown-demand lifecycle,
  post-job reclamation, cleanup lease exclusion, and chat priority.
- Vitest covers measured/unknown status, explicit authorization, release opt-in,
  and saved runtime mode.
- Three repository verification runs, process smoke, image build, and installed
  container smoke are required before publication.
