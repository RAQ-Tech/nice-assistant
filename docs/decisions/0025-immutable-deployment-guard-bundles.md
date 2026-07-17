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
- MAC-address preservation is an explicit root-owned deployment policy, not an
  inference from Docker runtime state. `guard.conf` persists
  `NICE_DEPLOY_PRESERVE_EXPLICIT_MAC` as literal `true` or `false`, with
  `false` as the default for a new enrollment or a legacy configuration where
  the value is absent. An explicitly empty or malformed value fails closed. Supervised
  re-enrollment inherits an existing literal value unless the operator
  explicitly opts into preservation, and a policy change is rejected while
  guarded application rollback state exists.
- With the default `false` policy, endpoint MAC addresses are omitted from
  recreation payloads and ignored during comparison because
  `NetworkSettings.Networks.*.MacAddress` can be generated at runtime. The
  deprecated container-wide `Config.MacAddress` projection is also ignored and
  always removed from create payloads and canonical comparisons. A nonempty
  runtime value in either location is never evidence that the operator
  configured a static address.
- On first enrollment, an available Unraid template is the configuration
  provenance for explicit `--mac-address` intent. Without a template or prior
  persisted policy, a nonempty deprecated projection is ambiguous and requires
  an explicit supervised choice instead of being silently discarded.
- The uncommon `true` policy is an explicit operator assertion. It requires
  exactly one configured network endpoint with a nonempty endpoint MAC and
  fails closed for zero or multiple endpoints, an empty endpoint address, an
  invalid policy value, or a contradictory nonempty legacy
  `Config.MacAddress`. Only that unambiguous endpoint MAC is preserved and
  comparison-gated.
- Beginning with bundle version 3, `inspect` and `health` report the active
  `guard_bundle_version` as an integer and `preserve_explicit_mac` as a boolean.
  The guard reads the version from its own root-owned, validated bundle
  manifest and reports the persisted root-only policy; neither value is
  inferred from Docker runtime state. These bounded fields expose no path,
  address, container definition, or secret. Version 2 remains a valid
  application guard but predates this response contract, so its responses do
  not contain either field.
- Launcher and delegated guard actions share one lock. Delegation uses an empty
  environment with a fixed path and an inherited verified lock descriptor.
  Root-only journaling permits only exact interrupted-update helpers and staging
  paths to be cleaned on the next invocation.
- `rollback-guard` swaps only the current and immediately previous validated
  bundles. It is separate from application container rollback and never restores
  a database. The permanent launcher blocks application `deploy` and `rollback`
  whenever the selected guard bundle predates the version 2 MAC-provenance
  correction; read-only and recovery actions remain available.
- An existing direct-guard installation requires one final supervised,
  transactional migration. New installations require one supervised bootstrap.
  Routine future bundle updates do not require an administrative shell.
  Replacing the permanent launcher itself remains deliberately supervised.
- Canonical, non-symlinked root-owned `authorized_keys` ancestry remains the
  default. The only symlink exception is stock Unraid's literal root-owned
  `/root/.ssh -> /boot/config/ssh/root` layout: `/boot` must be the exact VFAT
  mount with `fmask=0177` and `dmask=0077`, the resolved ancestry must remain
  root-private, and a same-directory atomic-write probe must pass. Enrollment
  does not require exporting the flash share or making it writable to clients.
- Key replacement snapshots the original file, preserves every unmarked entry,
  prepares exactly one managed entry beside the target, and compares the live
  file with the snapshot immediately before atomic rename. Concurrent changes
  fail closed instead of being overwritten. A root-only sibling recovery is
  restored automatically when post-rename verification fails and remains
  available until separate-client replacement-key acceptance succeeds.

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
switching the stable launcher path, then compare-and-swaps the prepared exact
managed authorized-key entry last. Unrelated authorized keys are preserved.
Failure before the launcher switch leaves the legacy guard usable; the
root-only installation journal recovers interruptions after either switch.

On stock Unraid, only the restricted SSH authorization is stored through the
flash-backed root SSH path. The launcher, immutable bundles, definition, lock,
and journal remain on a separate root-only persistent filesystem with real Unix
ownership, modes, symlinks, and atomic rename. Live acceptance must prove both
sides survive the host's normal persistence boundary.

The MAC-provenance correction is guard bundle version 2, and the permanent
launcher refuses an older initial bootstrap. The correction rollout enrolls
version 2; its first live rollback drill follows a genuine version 3 update,
selects version 2, and re-updates to version 3 before further application work.
Later fresh enrollments use the current accepted version 2-or-newer bundle and
do not need to recreate this historical transition. During the first drill, the
version 3 `inspect` and `health` responses must report bundle version `3` and
the enrolled MAC policy before rollback, version 2 must continue to answer
without those newer fields, and re-update must restore the version 3 fields
with the same policy. If a historical version 1 bundle is ever selected, the
launcher refuses application deployment and rollback because that guard can
promote a Docker-generated endpoint MAC into static configuration. Read-only
and guard-recovery actions remain available until version 2 or newer is active.

Guarded application rollback state records the literal MAC policy that captured
its previous container definition. Rollback fails closed if that policy is
missing or differs from the current root-owned policy; neither re-enrollment nor
a guard action silently reinterprets an existing rollback definition.

## Verification

`tests/test_deployment_guard.py` combines static contract checks with an
executable root/Linux fake-Docker harness. The harness exercises sanitized
delegation, bootstrap/update, mixed-case provenance, exact running-digest
rejection, stopped helpers, wrong-mode cleanup, interrupted pointer recovery,
two consecutive default-policy generated-MAC projections, and explicit
single-endpoint MAC preservation. It also rejects malformed policy, ambiguous
multi-network preservation, and legacy/endpoint disagreement, and proves the
launcher blocks application actions under version 1 before re-enabling them
under version 2. Version 3 contract tests prove that both `inspect` and `health`
return the manifest-backed integer `guard_bundle_version` and boolean
`preserve_explicit_mac` without weakening version 2 compatibility. Static
checks cover the remaining manifest schema/path/type/size, installer ordering,
client, and installed-image contracts. The correction rollout's live
acceptance additionally exercises installation at version 2, immutable
application deployment of the genuine version 3 image, guard update from that
running digest with both observability fields, guard rollback to version 2, and
re-update to version 3 with the fields restored before further application
work. It also proves exact helper cleanup and the single-container invariant.
Stock-Unraid enrollment additionally proves the exact symlink/mount/mask branch,
no client-writable or exported flash share, new-key success, old-key denial, one
managed marker, unchanged hashes for unrelated entries, recovery-file
retirement, and persistence of both authorization and launcher state. A
non-cooperating host-root writer remains outside the compare-before-rename
concurrency boundary.
