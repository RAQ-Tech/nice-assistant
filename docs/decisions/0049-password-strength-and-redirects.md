# ADR 0049: Password strength and redirects across the LAN boundary

- Status: accepted
- Date: 2026-10-29
- Owners: Nice Assistant maintainers

## Context

The two security findings ADR 0048 left open. Passwords were hashed with
PBKDF2-HMAC-SHA256 at 120,000 iterations, a fifth of current guidance, and the
count was not stored with the hash, so it could not be raised without every
existing password failing. Provider addresses are checked against the
private-LAN policy when they are configured, but urllib then followed any
redirect to any host: a LAN service could send a request - a prompt, a picture -
to the internet, and an internet address the owner asked for, such as an avatar
picture, could send the server into the LAN.

## Decision

**A hash says how it was made.** New hashes are
`pbkdf2_sha256$600000$salt$digest`. A hash in the old `salt$digest` form is
still verified at 120,000 iterations. When a sign-in succeeds against a hash made
more weakly than one made now, the hash is replaced, since that is the only moment
the password itself is in hand. A stored count above ten million, or a form this
code does not recognise, is refused rather than trusted. Measured cost: about
0.13 s per sign-in on a desktop CPU.

**A redirect stays on its side of the boundary.** Every urllib request the
process makes goes through one shared opener, and the application installs a
redirect handler into it at startup. A redirect may stay on the same host, or
move to another host on the same side of the provider policy: LAN to LAN,
internet to internet. Crossing in either direction, or to a scheme other than
HTTP(S), fails the request as an HTTP error, which every caller already reports.
An allowlisted host counts as inside.

## Alternatives considered

- **Re-hash every password at once.** Impossible without the passwords, which is
  the point of hashing them.
- **A stronger algorithm such as scrypt or Argon2.** scrypt is in the standard
  library, but its memory cost is a new tuning decision for a small home server;
  Argon2 needs a new dependency. The stored scheme name leaves room for either.
- **Refuse every redirect.** Simplest, but an avatar picture, a model lookup and
  plain HTTP-to-HTTPS upgrades legitimately redirect. Only crossing the boundary
  is the risk.
- **Check redirects at each call site.** There are more than twenty, and the next
  one added would not know to.

## Consequences

An account nobody signs into keeps its old hash until somebody does. The redirect
handler is process-wide state: it is the same for every application instance
except for the operator's allowlist, which comes from configuration. A provider
that genuinely redirects to the other side of the boundary - none known - fails
with an HTTP error instead of being followed.

## Verification

- `tests/test_sessions.py`: a new hash names its scheme and 600,000 iterations; a
  damaged or foreign hash is refused; an old hash still signs in, is replaced at
  that sign-in, and the new one signs in again; a wrong password changes nothing.
- `tests/test_lan_hardening.py`: each direction across the boundary is refused,
  the same side is followed, an allowlisted host counts as inside, and a real
  local server's redirects are followed or refused through the opener the app
  installs.
