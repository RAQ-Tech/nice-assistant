# ADR 0014: LAN HTTP security and operational evidence

- Status: accepted
- Date: 2026-07-14
- Owners: Nice Assistant maintainers

## Context

Private-LAN scope does not make browser input, other LAN clients, provider URLs,
or disk failures trustworthy. Cookie-authenticated writes previously relied on
`SameSite=Lax`, login attempts had no bounded lockout, and authenticated users
could supply outbound provider URLs without a private-target policy. Plain-text
logs and liveness-only health also made failures difficult to correlate without
risking secret exposure.

## Decision

Every state-changing `/api/v1` request requires the non-simple
`X-Nice-Assistant-CSRF: 1` header. If a browser sends `Origin`, it must match the
request target or an explicitly configured reverse-proxy origin. Session cookies
are `HttpOnly` and `SameSite=Strict`; operators enable `Secure` cookies for HTTPS
with `NICE_ASSISTANT_SECURE_COOKIES=1`.

Login failures are throttled in process by client address plus normalized
username, with a safe `429` and `Retry-After`. LAN provider URLs accept HTTP(S)
only, reject credentials/query/fragment in saved base URLs, reject public and
link-local literal addresses, and allow only recognized LAN/container names,
private/Tailscale addresses, or exact operator allowlist entries.

The ASGI boundary assigns a bounded request ID, emits security headers, records
request latency/status, and writes redacted JSON logs. Provider and job outcomes,
queue depth, storage use/retention, and readiness are exposed only through the
admin observability contract; `/ready` exposes a content-free deployment probe.
Generated artifacts use atomic writes and safe empty/disk-failure errors.

Backup verification safely extracts only the database into a temporary
directory, runs SQLite integrity and current migrations there, and never mutates
the live database. Configured cache/recording/log retention runs at startup.

## Alternatives considered

- Rely on `SameSite` alone: rejected because it is defense in depth, not a
  complete write authorization boundary.
- Store per-session CSRF secrets: not selected because the same-origin API does
  not enable credentialed CORS; requiring a custom header gives the needed
  preflight boundary without another durable secret.
- Permit arbitrary HTTP(S) provider hosts: rejected because authenticated SSRF
  is still a deployment and credential risk.
- Publish Prometheus-compatible labels containing paths/models: rejected for
  now because a small bounded JSON contract is easier to keep content-free and
  private. A future exporter can consume this contract.

## Consequences

All API clients and smoke scripts must send the CSRF marker on writes. HTTPS
deployments must configure their public origin and secure-cookie flag. Provider
services outside recognized private naming/address ranges require an exact
allowlist entry. Login throttling is process-local and resets after restart,
which is sufficient for the supported single-process private-LAN topology but
not a public multi-replica service.

Age retention defaults to 30 days for archived generated audio, opted-in STT
recordings, and archived logs. Setting a retention day value to zero disables
that category's age pruning. Backup snapshots and daily database backups remain
count bounded.

## Verification

Focused tests cover CSRF/origin rejection, security headers, secure cookies,
throttling, private/Tailscale/allowlisted provider URLs, metadata/public target
rejection, owner/admin isolation, readiness, queue/storage metrics, retention,
atomic full-disk behavior, empty artifacts, backup integrity/migration drills,
and corrupt snapshot errors. Complete repeated verification, process smoke,
container smoke, and clean shutdown remain release gates.
