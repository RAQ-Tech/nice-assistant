# ADR 0034: Turn event replay stays bounded and process-local

- Status: accepted
- Date: 2026-08-16
- Owners: Nice Assistant maintainers

## Context

A turn streams its reply as events. A browser that reconnects mid-reply is sent
a snapshot of what it missed, from a bounded in-memory buffer held by the
process that produced it. The buffer drops old events under a size and count
limit, and expires shortly after a turn reaches a terminal state.

The question left open was whether those events should instead be written to a
durable log, so replay survives a restart and could be served by a process other
than the one that generated it.

## Decision

They stay in memory, bounded, and process-local.

Correctness does not depend on the buffer. Reconnect is already correct without
it: the snapshot carries the sequence its text covers, so a subscriber neither
replays a delta twice nor silently misses one that was evicted. What the buffer
provides is that a reconnect within the same process does not have to re-read
the transcript.

A restart already ends an unfinished turn rather than resuming it. That is a
deliberate, documented behaviour with its own startup sweep, not an accident the
buffer is covering for. A durable event log would let a browser replay the
deltas of a turn that no longer exists, which is a worse answer than the honest
one it gets now.

The cost of the alternative is real: every delta of every turn written durably,
on a private-LAN deployment where the database is one SQLite file on a machine
that is also running a GPU. That is a write amplification nobody asked for, to
make reconnect marginally smoother in a case that already works.

## What this depends on

One application process. Replay is not the only thing that assumes it: login
throttling and request metrics are in memory too. A second process would hold
its own copy of all three, so a reconnect could be answered by a process that
never saw the turn, and a lockout would be worth as many attempts as there are
processes.

That assumption is now enforced rather than documented. `app/single_process.py`
refuses to start when the environment asks for more than one worker, and says
why. It is a guard and not a proof: someone can still launch several processes
directly. It catches the configuration change made by somebody who does not know
this constraint exists, which is the case worth catching.

## What would overturn this

A multi-replica or public deployment. That already requires shared rate-limit
and telemetry infrastructure and a new threat model, both recorded as
deliberately not being done. A durable event log belongs in that work, not
before it: building it now would be paying the write cost for years to make a
future migration slightly shorter.

## Alternatives considered

- Write every turn event to the database. Rejected: cost above, and it would
  offer replay of turns a restart has already ended.
- Write only terminal events durably. Rejected as neither one thing nor the
  other - it does not help a mid-reply reconnect, which is the only case the
  buffer exists for, and the terminal state is already durable on the turn.
- Leave the assumption documented only. Rejected: a limitation that is true
  until somebody changes a worker count, and then silently false, is worth a
  guard that costs four lines.
