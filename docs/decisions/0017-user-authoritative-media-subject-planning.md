# ADR 0017: User-authoritative media subject planning

- Status: Accepted; fallback behavior superseded by ADR 0018 and blocked-card
  behavior superseded by ADR 0023
- Date: 2026-07-15
- Owners: Nice Assistant maintainers

## Context

Capability planning receives both the user's request and the persona model's
reply. The planner could add `identity_control` whenever a persona was selected,
even when the user explicitly requested an unrelated image. A healthy ordinary
ComfyUI model was then rejected because no identity workflow was present. The
approval card reduced the rejection to a generic blocked message and a disabled
button, hiding the requirement that caused the decision.

The inverse is also unsafe: silently removing identity conditioning from a real
persona-image request would present an ordinary text-to-image result as though it
preserved the reviewed persona reference.

## Decision

- Capability Task Model output includes a required `persona_subject` boolean.
  It is true only when the user's requested image depicts the selected persona
  or must preserve that persona's established appearance.
- The user request is the sole conversational input to capability planning.
  Persona-model prose is excluded so it cannot invent or expand a media subject.
- The platform, not the Task Model, derives `identity_control`: it removes that
  feature for unrelated images and requires it for a valid persona subject.
- A narrow deterministic exclusion guard overrides an erroneous positive model
  classification when the user explicitly says not to include the persona or
  directly addresses the selected persona with equivalent wording. The guard
  can only remove identity conditioning; it never infers that a request depicts
  the persona, and generic scene exclusions such as "no background people" do
  not activate it.
- Persona images continue to prefer the reviewed-reference workflow contract
  from ADR 0012. ADR 0018 permits only an explicit, saved, visibly disclosed
  unconditioned fallback; it never represents that result as conditioned.
- A blocked plan produces a compact retryable attachment failure. Collapsed
  Details and authenticated diagnostics show hard requirements and per-resource
  rejection reasons and provide a route to Media Catalog configuration.

## Alternatives considered

- Remove `identity_control` whenever no workflow is configured. Rejected because
  a persona selfie would then become an ordinary image while appearing to retain
  identity.
- Infer persona subjects with application keyword matching. Rejected because
  conversational references are semantic and keyword routing would recreate the
  hidden heuristic boundary removed by ADR 0007. The explicit negative guard is
  deliberately narrower: it honors direct user exclusion and cannot add a
  persona subject.
- Continue trusting the Task Model's free choice of feature tags. Rejected
  because the production failure demonstrated that persona context can leak into
  an unrelated request.

## Consequences

Ordinary images in persona chats can use ordinary catalog models. Genuine
persona-image requests use a configured identity-capable ComfyUI workflow when
one is ready. Without one, the saved persona policy either permits a visibly
unconditioned result or blocks with targeted setup and recheck actions. The
operator can see exactly which requirement rejected each resource and where to
configure it.

## Verification

- Task-contract tests prove unrelated requests cannot retain
  `identity_control`, explicit no-persona requests override an incorrect model
  classification, and genuine persona subjects always require identity control.
- API tests exercise both decisions in one persona chat and inspect the durable
  media plans.
- Browser tests cover compact rejection details and the Media Catalog action.
- Deployment acceptance must request, generate, and reveal an ordinary image
  through a real persona chat without a second approval. Persona-image acceptance additionally
  requires a real installed identity workflow and model assets.
