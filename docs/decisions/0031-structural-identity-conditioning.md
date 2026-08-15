# ADR 0031: Identity resemblance is structural; comparison is advisory

- Status: accepted as direction; amends ADR 0013 and extends ADR 0012 and
  ADR 0018. Implementation tracked in `BACKLOG.md`
- Date: 2026-08-14
- Owners: Nice Assistant maintainers

## Context

ADR 0012 established reviewed-reference conditioning. ADR 0013 added a measured
correction loop: after a conditioned candidate is generated, a stateless
comparison service scores it against the approved reference, and a real
below-threshold score may trigger another attempt up to a snapshotted limit.

Two problems with treating that loop as the way persona resemblance is achieved:

- **It converts a quality problem into a compute problem.** If the conditioning
  itself does not reliably produce the persona, the loop generates candidate
  after candidate hoping one passes, and it can reject every one. The bound
  keeps it finite, not correct. A person waiting on a picture in a conversation
  experiences this as latency and then as failure.
- **It makes an optional service load-bearing.** The comparison service consumes
  CPU continuously once running, including while idle, so requiring it imposes a
  standing cost on the deployment even when no image is being generated. A
  product behavior that only works while an optional container is running is not
  an optional dependency.

Comparison also cannot do the job being asked of it. It runs after generation
and can only accept or reject a finished image. Nothing about scoring a result
makes the next result better.

## Decision

**Resemblance is produced at generation time by a declared mechanism.** A
persona carries a durable Identity Spec: the approved reference set, canonical
appearance text, the conditioning method a preset must apply (for example
reference-adapter conditioning or an identity pass in a multi-pass preset), and
the conditioning parameters that were tested for that persona. Presets declare
which identity mechanisms they implement. A persona image is planned only
against a preset whose declared mechanism the Identity Spec supports, and the
parameters used come from the spec rather than from per-request improvisation.

**Comparison is advisory quality control, not a generation mechanism.** A
verifier, when configured and reachable, scores a finished candidate and labels
it. It does not gate whether resemblance is attempted, does not choose the
conditioning, and is not what the product relies on for identity.

**The comparison-driven retry loop from ADR 0013 is demoted.** It is disabled by
default. When an operator enables it, it stays bounded exactly as ADR 0013
specifies. It is never described, in the product or the documentation, as the
means by which a persona keeps a consistent face.

**The product is fully functional with no verifier running.** Nothing may
require the comparison service to be started, and nothing may poll it in the
background merely to keep readiness fresh. Readiness checks are on demand.
Absent or stopped is a normal, honestly labeled state, not a degraded one - with
one unchanged exception: a profile whose saved policy is `require_conditioning`
still blocks, because that policy is about conditioning, not about comparison.

**Verified still means what it meant.** A `verified` claim continues to require
a real passed comparison, per ADR 0010 and ADR 0012. Turning comparison off does
not turn unverified results into verified ones; it means results are labeled
unverified, which is the truthful description of an unmeasured image.

## Alternatives considered

- **Keep generate-and-compare as the primary mechanism.** Rejected for the two
  reasons above. It is a check pretending to be a control.
- **Remove comparison entirely.** Rejected because measuring resemblance is
  genuinely useful when tuning a preset or reviewing a reference set, and
  because ADR 0010 and ADR 0012 already depend on a real measurement for the
  `verified` claim. The correct change is demotion, not deletion.
- **Keep the retry loop enabled by default but lower the limit.** Rejected
  because the default would still spend a conversation's latency budget on
  resampling, and would still imply that resampling is how resemblance works.
- **Have the platform pick conditioning parameters per request.** Rejected for
  the same reason ADR 0030 rejects runtime plan assembly: the parameters that
  make a particular persona resemble a particular reference are tested
  knowledge, and they belong in a durable record.

## Consequences

The identity work moves earlier - into the Identity Spec, the preset's declared
mechanism, and the reference set - where it can be made reliable and inspected,
rather than later into resampling. A persona image that comes out wrong is now a
preset or spec problem with a journal entry explaining what was applied, not an
unbounded retry.

Operators who never run a verifier get identity conditioning, honest
`unverified` labeling, and no standing background cost. Operators who do run one
get the same measurement they have today, on demand, plus an optional bounded
retry they must switch on deliberately.

`docs/persona-visual-identity.md` and `docs/media-catalog.md` change with the
implementation: comparison is described as optional post-hoc measurement
throughout, and the correction loop is described as an off-by-default operator
tool. The existing durable records - profiles, consent, references, validations,
attempts - are unchanged, and no migration rewrites them.

Whether the comparison adapter remains CompreFace is deliberately left open.
Demoting it to advisory removes the reason to decide that now, and no
replacement may be advertised until it implements the same stateless comparison
contract.

## Verification

- Service tests prove a persona image is planned and generated with no verifier
  configured, produces an `unverified` label, and does not attempt a comparison.
- Tests prove the retry loop is off by default, that enabling it preserves the
  ADR 0013 bound, and that provider unavailability still never triggers a retry.
- Tests prove no code path polls the verifier on a timer, and that readiness is
  evaluated only on demand.
- Tests prove a persona image is refused rather than silently downgraded when a
  preset does not implement a mechanism the Identity Spec requires, subject to
  the existing ADR 0018 fallback policy.
- Browser tests prove settings describe comparison as optional measurement and
  never as the source of resemblance.
- Real resemblance quality on installed presets and references remains
  deployment acceptance evidence, not a CI claim.
