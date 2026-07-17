# ADR 0024: Image-authoritative container runtime

- Status: accepted
- Date: 2026-07-17
- Owners: Nice Assistant maintainers

## Context

The container previously copied application source into `/data/project` and
then executed that persistent copy. A copy operation does not remove files that
disappeared from a newer image. Redeploying or temporarily downgrading an image
could therefore run a mixed application tree whose revision label did not
describe the code actually executing. A mutable `latest` tag reused from a
local cache made that failure especially confusing.

Persistent storage is required for the database, settings, generated media, and
archives. Application source is already installed in the immutable image and
does not belong in the production state volume.

## Decision

- Installed containers execute `/opt/nice-assistant`, the source shipped in the
  selected OCI image.
- Legacy `PROJECT_ROOT` and `SYNC_PROJECT_ON_START` values do not affect
  production startup.
- Persistent `/data/project` content is left untouched for safe operator review;
  it is not executed and is never deleted automatically.
- Repository-source sync remains available only through the explicit
  `NICE_ASSISTANT_DEVELOPMENT_PROJECT_SYNC=1` development escape hatch. It is
  off by default and is not a production deployment option.
- Production promotion verifies an immutable digest and OCI revision. A mutable
  tag is a convenience for update discovery, not deployment evidence.

## Alternatives considered

- Continue copying into `/data/project` and delete it first. Rejected because an
  automatic recursive delete risks user changes and still makes source part of
  deployment state.
- Atomically replace a persistent project directory. Rejected for production
  because it adds complexity without a persistence requirement.
- Keep source sync but improve only the Unraid update instructions. Rejected
  because the runtime must remain truthful even when a platform reuses a tag or
  preserves legacy environment variables.

## Consequences

Existing data, media, secrets, mappings, ports, and archives are unchanged.
Operators may remove an obsolete `/data/project` directory after verifying the
new deployment, but Nice Assistant never does so automatically. Development
users who explicitly enable source sync accept that the persistent tree is
outside the immutable production guarantee.

## Verification

- Static entrypoint tests prove the packaged source is the default and legacy
  environment variables cannot opt production back into persistent source.
- The installed-container smoke runs without the development escape hatch,
  checks migration head and health, and verifies the reported revision.
- Deployment acceptance pulls and runs an immutable GHCR digest, then checks
  `/health`, `/ready`, logs, revision, and browser behavior.
