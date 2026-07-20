# Security model

## Supported threat boundary

Nice Assistant assumes a trusted private LAN with untrusted browser input and
potentially untrusted model/provider output. Other LAN users must not access a
different account's chats, memories, media, audio, settings, jobs, or secrets.
Memory FTS queries join back through owner and lifecycle filters; unapproved or
cross-owner rows must never be returned even when their text matches. Candidate
provenance and forgotten/rejected history are sensitive backup data.

Direct public-internet exposure is unsupported. Remote access should terminate
HTTPS and identity controls at a reverse proxy or VPN.

## Required controls

- Server-side sessions with expiry, logout invalidation, login throttling, and
  secure cookie behavior when HTTPS is used.
- Same-origin enforcement and CSRF protection for state-changing requests.
- Request and upload limits before bodies are buffered.
- Owner-scoped database queries for every user-supplied identifier.
- Allowlisted provider URL schemes and private deployment policy.
- Encrypted provider secrets using a deployment-supplied master key.
- Structured secret redaction in logs, errors, backups, and job results.
- Defense-in-depth rejection of secret-like automatic memory candidates before
  persistence, independent of Task Model compliance.
- Chat transcript memory actions create owner-scoped pending proposals. Assistant
  prose cannot enter prompt context until a user reviews and approves the edited
  fact.
- Explicit permission and confirmation policy for tools with side effects.

These controls are implemented at the ASGI boundary and service entry points.
All state-changing `/api/v1` calls require `X-Nice-Assistant-CSRF: 1`; browser
origins must match the target or `NICE_ASSISTANT_ALLOWED_ORIGINS`. The API does
not enable credentialed cross-origin requests. Session cookies are
`SameSite=Strict` and `HttpOnly`; set `NICE_ASSISTANT_SECURE_COOKIES=1` only when
the browser-facing origin is HTTPS. Authenticated activity renews both the
server expiry and browser cookie when inactive-session expiry is enabled. When
the user disables automatic inactivity logout, the server keeps the session
valid and the browser uses a session cookie rather than a contradictory fixed
30-minute cookie. Login failures are bounded per client and normalized username
and do not disclose whether a username exists.

User-configurable LAN provider base URLs accept only HTTP(S), contain no
credentials/query/fragment, and must target a private/loopback/Tailscale address,
a recognized LAN/container hostname, or an exact
`NICE_ASSISTANT_PROVIDER_HOST_ALLOWLIST` entry. Link-local metadata and public
literal IP targets are rejected. An allowlist entry is an operator trust grant,
not proof that the remote service is private; public-internet providers remain
fixed server adapters rather than browser-supplied URLs.

## Deployment automation authority

The optional production deployment key is not a general administrative key. Its
root `authorized_keys` entry is source-restricted, uses OpenSSH `restrict`, and
forces a small permanent launcher. Allowed commands are Nice Assistant inspect,
verified backup, immutable-digest deploy, health, bounded redacted logs,
compatible container rollback, exact-running-digest guard update, and
immediately-previous guard rollback. Root-owned configuration fixes one
container name, one GHCR repository, one private state directory, and optionally
one Unraid template.

The enrollment installer rejects symlinked `authorized_keys` ancestry except
for stock Unraid's literal root-owned
`/root/.ssh -> /boot/config/ssh/root` persistence link. That exception also
requires an exact `/boot` VFAT mount, root-only target ancestry, effective
`fmask=0177` and `dmask=0077`, a non-symlinked key file, and a successful
same-directory atomic-replacement probe. It resolves writes to the verified
target and still replaces only the marked Nice Assistant entry. No other
symlink target or permissive mount is accepted, and the flash share must not be
writable or exported to clients.

The installer hashes the live authorization immediately before replacement,
flushes the staged file, verifies the result, and automatically restores a
same-directory root-only recovery copy if post-switch verification fails. That
copy remains until the replacement key succeeds and the retired key is denied
from a separate client. This protects ordinary concurrent administration and
recoverable filesystem failures; host root, a compromised Docker daemon, and a
non-cooperating root writer in the final comparison window remain outside the
threat boundary.

Replaceable guards are immutable bundles behind atomic relative links. The
launcher accepts update code only from the exact verified application
RepoDigest currently running, with the configured repository and expected OCI
source/revision labels. The running image is the operator/deployment acceptance
boundary; the launcher does not maintain a separate signed acceptance ledger.
It rejects declared image volumes and copies four fixed
paths from a stopped, networkless, read-only, nonprivileged extraction
container; neither the image nor candidate guard is executed. Strict manifest,
file-type, link-count, mode, size, hash, syntax, monotonic-version, independent
payload, and dual-normalization checks precede activation. OCI labels establish
the expected publication identity but are not cryptographic signatures.

The mode-`0600`, root-owned launcher configuration persists
`NICE_DEPLOY_PRESERVE_EXPLICIT_MAC`, which accepts only literal `true` or
`false`; a new enrollment or genuinely absent legacy value defaults to `false`,
while empty or malformed values fail closed. A runtime endpoint MAC does not prove operator
intent: Docker may assign `NetworkSettings.Networks.*.MacAddress` and project it
through deprecated `Config.MacAddress` during creation. The default policy
therefore omits endpoint MACs from recreated containers, excludes both runtime
representations from equality checks, and removes the deprecated projection.
Explicit preservation fails closed unless there is exactly one network endpoint
with a nonempty MAC and no contradictory legacy projection; only then is the
endpoint MAC preserved and compared. Guarded rollback state records and checks
the capture policy, and the launcher denies application deploy/rollback under
pre-correction bundle versions. These constraints prevent a restricted
deployment from silently converting a generated address into static
configuration.

Launcher and guard share one lock, and delegation receives an empty environment
plus only the verified config and inherited lock. Root-only journaling authorizes
cleanup of exact stopped helpers and one exact staging path, never broad Docker
cleanup. The launcher cannot accept a mutable tag, restore a database, downgrade
a schema, alter credentials, expose a port, or target a different container.
Successful deployment removes its temporary rollback duplicate and keeps the
prior digest and root-only definition. The laptop client uses a dedicated key,
strict host-key checking, `BatchMode`, and `IdentitiesOnly`; exact addresses and
fingerprints remain outside Git. The legacy direct-guard layout requires one
supervised migration, and launcher replacement remains supervised.

Private operator files are also excluded from the Docker build context. The
installed image build fails if `.local`, a deployment private-key name, or the
ignored remote configuration survives `COPY`. This protects local images as
well as Git history; a key ever included in an older local image must be rotated
before that image/cache is treated as harmless.

## Capability permissions

Persona-model output cannot directly start media generation and persona chat is
not offered platform tools. A separately configured, typed capability-planning
role may propose semantic prompt data. A separate conservative platform gate
admits only clear ordinary image actions to audited `auto` execution under the
selected persona's saved image-send permission; stories, discussion,
hypotheticals, quoted instructions, video, and consequential actions do not gain
automatic authority. The persona permission does not authorize unsolicited
generation. Explicit UI actions are recorded separately, repeated actions can
be idempotent, and all state changes produce durable audit events.
Capability, event, attachment, job, and artifact lookups are owner-scoped. Tool
results returned to future model context contain only safe status, error, and
protected artifact identifiers.
Every persona delta crosses a delimiter-aware output boundary before it can
reach SSE, persistence, speech, memory proposals, or future model context.
Protected system-prompt envelopes, including nested envelopes, are removed
across arbitrary provider chunk boundaries, and legacy stored assistant text
and conversation summaries are filtered on read and before reuse. If removal
leaves no user-facing persona
text, the platform substitutes a short safe failure message; summary filtering
does not invent replacement facts or retain an unusable summary checkpoint.

For strict explicit-image actions, raw persona prose is held and replaced with
one neutral platform-owned acknowledgement. Durable attachment state alone may
claim that an image is queued, running, completed, or failed. A bounded
deterministic request is used when the Task Model omits or cannot plan the
explicit action, but it receives no authority to select providers, workflows,
models, or privileged controls. This is a truthfulness and
prompt-confidentiality boundary, not a content-safety or identity-verification
substitute.

The capability-planning schema cannot select providers, URLs, models, LoRAs,
workflows, or resource controls. It can emit only server-advertised semantic
requirements. The deterministic catalog service owns resource selection and
persists an owner-scoped plan before execution. Pre-submission validation rejects
deleted, disabled, or revised selections rather than silently substituting a new
resource. Video keeps an additional explicit approval boundary.
Catalog content tags describe technical fitness; they do not bypass permission,
provider restrictions, or later identity/consent controls.

## External resource control

GPU coordination is administrator-only and disabled by default. Observe mode
uses provider telemetry but has no release authority. Managed release requires
two explicit assertions for the normalized endpoint fingerprint: the provider
service is exclusively controlled by this Nice Assistant deployment, and coarse
release is allowed. Changing the URL produces a different fingerprint and does
not inherit the grant.

The authorization is an operator attestation, not automatic proof of exclusive
network access. If other clients can reach the same Ollama, ComfyUI, or
Automatic1111 service, managed mode can disrupt their work and must remain off.
Release success is followed by a fresh capacity measurement; failed or
unavailable control never becomes a readiness claim. Resource audit rows omit
provider URLs, credentials, prompts, outputs, and model-generated content.

## Persona visual identity

Visual identity references are owner-scoped sensitive artifacts, not ordinary
public avatars. They require explicit consent and right-to-use attestation,
protected delivery, bounded image decoding, metadata-stripping re-encoding, and
review before use. Nice Assistant retains provenance and safe audit data; the
separate CompreFace adapter performs stateless two-image comparison and does not
enroll a provider-side subject.

No raw face embedding is stored. Only a real above-threshold comparison may
produce a `verified` persona claim. Provider outage, cancellation, missing faces,
or missing configuration stays `unverified`; a below-threshold comparison is
`rejected`. Consent withdrawal deletes reference files and cancels active
validation work while retaining tombstones needed to explain the deletion.

Identity-aware generation is also consent gated. A media plan snapshots
the profile revision, approved reference ID/digest, and exact workflow binding;
execution fails if any of them changed. The normalized reference and
owner-selected edit source/mask are sent only to
the operator-configured ComfyUI LAN endpoint and is never issued to the browser.
Generated artifacts remain `unverified` until comparison passes. Rejected
intermediate candidates stay owner-protected and are not rendered as persona
output under `block_claim`.
When the saved policy permits generation without an available conditioning
workflow, Nice Assistant does not send the reference and labels both plan and
result `unconditioned`/`unverified`. This path may run without a profile,
consent grant, or approved reference precisely because it neither reads nor
sends identity evidence. A saved `require_conditioning` policy or a changed
profile revision still invalidates a saved plan. Consent, reference digest,
and reviewed-state checks remain mandatory whenever reference conditioning is
actually selected.
Workflow setup may inspect ComfyUI `/object_info` through the same private-LAN
URL policy and server-held authentication as other provider checks. Provider
addresses, credentials, and raw provider errors are never returned to the
browser; the bounded response contains only safe node/input/asset and structural
compatibility facts. Structural compatibility is not execution or identity-match
evidence.
ComfyUI owns retention of successfully uploaded input files, so its input and
history retention must be configured as part of the deployment's sensitive-data
policy.

Task profiles and run records are owner scoped. Run audits do not store prompt
or output content; they retain only role/model/attempt/timing/token metadata and
redacted safe errors. Developer evaluation omits generated output unless its
operator explicitly requests `--show-output`.

## Sensitive artifacts

Database files, settings, logs, recordings, media, and backup archives may
contain personal information. Backups containing encrypted provider secrets
remain sensitive because the deployment key may exist elsewhere in the same
environment.

`NICE_ASSISTANT_MASTER_KEY` is required whenever provider secrets exist. Existing
plaintext secrets are encrypted and cleared during startup; startup is refused
without the key so insecure legacy storage cannot remain active. Losing or
changing the key makes encrypted provider credentials unrecoverable and requires
entering them again.

SQLite backups use the online backup API and an integrity check so committed
WAL-resident data is not silently omitted.
The restore drill rejects unsafe ZIP paths, verifies the manifest and SQLite
integrity, and runs current migrations against a temporary copy. It never
extracts over live data.
Full backups include identity references under `identity_references` and require
the same sensitive handling and consent-aware retention as the live files.
Backups also contain resource-control authorizations and endpoint fingerprints;
restore them only into the deployment whose endpoint ownership was attested,
and review them after topology changes.

## Public repository privacy

The public source tree must not become an infrastructure inventory. Exact
deployment addresses, hostnames, personal home paths, server/share paths,
hardware and storage measurements, concrete backup identifiers, persona content,
and unrelated private services belong under the ignored `.local/` directory.
Credentials and the deployment master key do not belong there either; they stay
in the deployment's secret-management layer.

`python scripts/audit_public_repo.py` scans tracked text and image metadata for
known local private values and high-confidence privacy or credential patterns.
The optional `.local/public-repo-private-values.txt` watchlist strengthens local
verification without publishing the values to CI. Public examples use
placeholders or documented test-only addresses. This working-tree check does not
remove data already present in Git history; history rewriting is a separate,
explicitly authorized destructive operation.
