# ADR 0022: Restricted digest deployment guard

- Status: accepted
- Date: 2026-07-16
- Owners: Nice Assistant maintainers

## Context

Long-running delivery should not require an operator to supervise routine image
promotion, backup, health, log, and rollback work. General SSH or Docker access
would grant far more authority than that job needs. Recreating a container from
a hand-maintained second definition could also silently lose Unraid mounts,
ports, environment, network settings, restart policy, or secrets.

## Decision

- A dedicated laptop Ed25519 key is installed only during a supervised server
  session. Its root `authorized_keys` entry uses `restrict`, a source-address
  constraint, and one forced command. The forced command accepts only `inspect`,
  `backup`, `deploy <digest>`, `health`, `logs`, and `rollback`.
- The guard configuration and captured deployment evidence are root-owned and
  mode `0600`; its state directory is mode `0700`. They never enter Git or chat.
- Installation first captures the running Nice Assistant container, recreates a
  stopped probe from that effective Docker configuration, and compares the
  normalized mounts, ports, environment, restart policy, labels, and networks.
  The key is not authorized if this definition check fails.
- Deployment accepts only an immutable `sha256` digest from the configured
  `ghcr.io/<owner>/nice-assistant` repository with a valid source-revision label.
  It creates and verifies an application backup, runs the candidate migration
  drill against a copy, and changes only the configured Nice Assistant
  container. Candidate acceptance checks effective configuration, Docker
  health, `/health`, `/ready`, startup logs, digest, and source revision.
- A failed candidate automatically returns to the stopped prior container only
  when the migration drill proves the live database revision is unchanged. The
  guard never restores a database, runs a downgrade, changes credentials, or
  touches another container. Schema-changing recovery requires operator
  approval.
- When an Unraid template is configured, installation preserves its original
  root-only copy and deployment changes only its single `Repository` value.
- Installed-browser acceptance remains a separate authenticated laptop check.
  A healthy container is necessary but is not proof that the human experience
  passed.

## Alternatives considered

- Grant unrestricted SSH or Docker access. Rejected because a stolen automation
  key could change unrelated services or the host.
- Maintain a second Compose or `docker run` definition. Rejected because it can
  drift from the operator's working Unraid configuration and secrets.
- Automatically restore a pre-deploy database. Rejected because destructive
  data recovery requires explicit operator judgment.

## Consequences

The first installation needs one supervised root session and a confirmed SSH
host key. A container without an approved repository digest or revision label
cannot be enrolled. A schema-changing candidate may deploy successfully, but a
later failure cannot be automatically rolled back. The prior digest, verified
backup, captured definitions, migration report, and bounded logs remain private
operator evidence.

## Verification

- `tests/test_deployment_guard.py` checks shell syntax, the forced-command
  allowlist, exact digest policy, configuration preservation, backup/migration
  gates, rollback boundary, key restrictions, and strict laptop SSH options.
- The installer must pass its stopped-probe definition comparison before it
  writes the authorized key.
- Each production promotion additionally requires the public deployment
  checklist and the private installed-browser record.
