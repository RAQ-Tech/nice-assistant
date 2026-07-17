# ADR 0025: Immutable deployment guard bundles

- Status: accepted
- Date: 2026-07-17
- Owners: Nice Assistant maintainers
- Extends: ADR 0022

## Context

ADR 0022 restricted routine production work to one source-bound forced command,
but its first implementation made the replaceable guard itself that command.
Application promotion was autonomous while a guard repair still required a root
session. Replacing the live guard file in place would also risk leaving the only
repair path partially written or unusable.

## Decision

- `authorized_keys` permanently targets a small root-owned launcher. The
  launcher and root-only configuration remain outside remotely replaceable
  bundles.
- The guard program and its two jq filters live in immutable, digest-named
  releases behind validated relative `current` and `previous` links. Activation
  changes `current` atomically only after complete validation.
- The launcher handles `update-guard <digest>` itself. A remote update accepts
  only the exact immutable RepoDigest verified as the running Nice Assistant
  container, from the configured repository, with the expected OCI source and a
  40-character revision. Running that image is the operator/deployment
  acceptance boundary; no separate signed acceptance ledger exists. Bundle
  versions cannot decrease; an equal version must describe identical files.
- The launcher rejects image-declared volumes, creates one stopped, networkless,
  read-only, nonprivileged extraction container, and never starts or executes
  it. It copies only the fixed manifest, guard, create filter, and normalization
  filter paths. Symlinks, hardlinks, special files, wrong modes, oversized
  files, unexpected manifest fields, and checksum failures are rejected.
- Candidate shell syntax and jq behavior are checked without executing the
  candidate guard. Before activation, a launcher-owned payload builder proves
  that the candidate filter cannot add or remove container privileges, mounts,
  ports, environment, commands, health checks, labels, or networks. A stopped
  probe is then compared by both a launcher-owned canonical comparator and the
  candidate normalizer. The probe is never started.
- Launcher and delegated guard actions share one lock. Delegation uses an empty
  environment with a fixed path and an inherited verified lock descriptor.
  Root-only journaling permits only exact interrupted-update helpers and staging
  paths to be cleaned on the next invocation.
- `rollback-guard` swaps only the current and immediately previous validated
  bundles. It is separate from application container rollback and never restores
  a database.
- An existing direct-guard installation requires one final supervised,
  transactional migration. New installations require one supervised bootstrap.
  Routine future bundle updates do not require an administrative shell.
  Replacing the permanent launcher itself remains deliberately supervised.

## Alternatives considered

- Let the guard overwrite itself. Rejected because an interruption or defective
  candidate could remove the only remote repair path.
- Accept any historical digest from the approved repository. Rejected because a
  stolen restricted key could authorize arbitrary root code or downgrade the
  guard. The remotely accepted digest must already be the running application.
- Execute the candidate image or guard to validate it. Rejected because
  validation must not grant code execution before activation.
- Update through application mounts or Docker socket side effects. Rejected
  because the application container has no authority over root deployment state.

## Consequences

Repository publication remains a trust boundary: OCI labels identify provenance
but are not a cryptographic maintainer attestation. Code already accepted as the
running immutable application may become the next guard only after independent
validation. A compromised Docker daemon or host root remains outside this
boundary.

The legacy migration stages and validates the first bundle before atomically
switching the stable launcher path, then installs the prepared exact managed
authorized-key entry last. Unrelated authorized keys are preserved. Failure
before the launcher switch leaves the legacy guard usable; the root-only
installation journal recovers interruptions after either switch.

## Verification

`tests/test_deployment_guard.py` combines static contract checks with an
executable root/Linux fake-Docker harness. The harness exercises sanitized
delegation, bootstrap/update, mixed-case provenance, exact running-digest
rejection, stopped helpers, wrong-mode cleanup, and interrupted pointer
recovery. Static checks cover the remaining manifest schema/path/type/size,
installer ordering, client, and installed-image contracts. Live acceptance
additionally exercises installation, update, guard rollback, re-update,
application deployment, exact helper cleanup, persistence, and the
single-container invariant.
