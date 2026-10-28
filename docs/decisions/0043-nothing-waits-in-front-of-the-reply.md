# ADR 0043: Nothing waits in front of the reply

- Status: accepted
- Date: 2026-10-27
- Owners: Nice Assistant maintainers

## Context

Real-time voice conversation is the product's direction, and a per-turn cost
needs a reason stronger than the feature it buys (BACKLOG, Assumptions). A
review of the reply path on 2026-10-27 found six places where something stood
between somebody finishing a sentence and the persona starting to answer. None
was a feature anyone had chosen to pay for:

- **Transcription stopped the server.** The upload handler was `async` and called
  the synchronous conversion and Whisper request directly, so for the six
  seconds or so a transcription takes (ADR 0041) the event loop served nothing:
  not the reply stream, not the speech playing it. The photo upload and the
  browser event route had the same shape.
- **A turn queued behind the previous turn's follow-ups.** Title, capability
  planning and memory extraction share the chat's ordering key (ADR 0021), and
  the queue let only the oldest job under a key start. Somebody who answered
  quickly waited for every one of those model calls first. ADR 0021's "the reply
  never waits" held for the reply that created them, not for the next one.
- **Compaction ran before the first word.** ADR 0004 put summary catch-up inside
  prompt planning, at up to two model calls per turn. It accepted the latency as
  occasional; a voice conversation that hits it mid-sentence does not.
- **Every turn read the whole transcript.** Context loading fetched every prior
  turn, two messages and one capability query at a time, and then discarded what
  the summary already covered. It grew linearly with the chat.
- **Background calls could reload the model.** A task role on the same Ollama
  model as the reply asked for a smaller `num_ctx`. Ollama keeps one loaded copy
  per context size, so a smaller one unloads the model and loads it again, and
  the next reply pays that load. No timing existed that could show it.
- **Nothing measured the loop.** Provider latency was one sum and one maximum
  across every operation, and a failure in the log named a class and nothing
  else.

## Decision

**Blocking request work runs on a worker thread.** Transcription, the identity
reference upload and the browser event route hand their synchronous work to a
thread, the way speech synthesis already did. Audio conversion gives up after
thirty seconds rather than holding a request forever.

**A turn goes ahead of queued background work for its chat.** Under one ordering
key the queue starts the earliest job of the most urgent class waiting. A turn
is `interactive` and follow-ups are `standard`, so the turn takes the next slot.
Three things do not change: a turn never overtakes an earlier turn, nothing under
a key starts while another job under it is running, and a running follow-up is
never interrupted. Model affinity - staying on the model that ran last - only
chooses between jobs that are equally urgent.

**Compaction runs after the reply.** Planning a prompt uses the summary as it
stands and fits history to the budget, as it always did when catch-up fell
short. When the summary is behind, the reply records that it is due, and a
`summary_followup` job brings it up to date after the reply, ordered after
capability planning and before memory extraction. The next turn starts from the
summary it leaves. A turn reports `summary_catchup_limited` only when history the
summary does not yet cover was actually left out of its prompt. A failing
summary model fails the follow-up job, visibly, and leaves the reply alone;
`summary_provider_failed` is no longer written by new turns, though records that
carry it still display.

**Context loads from the summary boundary.** When the summary ends inside a
turn, loading starts at that turn; what came before is counted, not read, so
`omitted_message_count` means what it meant before. Messages, capability
requests and counts are each one query whatever the number of turns. If the
boundary is not where it is expected, the whole transcript is read, which is
slower and was always correct.

**A background task on the reply's model asks for the reply's context.** The
reply's model, server and `num_ctx` travel with its follow-ups. A task on that
same model and server asks for the same context when it is large enough for the
task, so Ollama does not reload. A task on a different model asks for what it
needs, because a larger allocation there would only cost memory. Separately, the
account API key is now added only for providers that need one; Ollama is never
sent it.

**The loop is timed.** Each turn records how long it waited for a worker, how
long its prompt took, how long until the first token, how long Ollama spent
loading the model, and how long until it finished. Speech records conversion,
transcription, and the time to the first sound of each spoken piece. They are
kept per stage, with recent medians and 95th percentiles, under `stages` in the
admin observability report, and each turn writes one `turn timing` line to the
event log. Warnings and errors also go to the console, so `docker logs` shows
them, and a logged exception names the file and line it was raised at.

## Alternatives considered

- **Separate ordering keys for follow-ups.** Lets a turn start sooner, but lets a
  follow-up run concurrently with a turn in the same chat when more than one
  worker is configured, which is the SQLite contention ADR 0021 avoided.
  Priority within the key gives the turn the slot without that.
- **Interrupt a running follow-up when a turn arrives.** Would save the rest of
  one model call at the cost of cancellation paths in every task role and lost
  work. A running job is at most one model call; the queued ones were the cost.
- **Keep compaction on the reply path, but cheaper.** Any model call before the
  first word is the cost being removed. A history that does not fit was already
  trimmed to fit, and the summary now catches up while the reply is being
  listened to.
- **Set `keep_alive` from the application.** Keeping the model loaded between
  conversations is real, but it is a property of the Ollama service and of the
  memory the card shares with image generation. `OLLAMA_KEEP_ALIVE` on the Ollama
  container sets it for every client; managed resource coordination already
  unloads the model with `keep_alive: 0` when picture work needs the memory.
  See `docs/operations.md`.
- **Give every task the chat's context window.** Avoids the reload for the same
  model, but grows the memory of a different, smaller task model for nothing.

## Consequences

The first word of a reply no longer waits on transcription elsewhere, on the
last reply's background work, on summarization, or on reading the whole
transcript. A long chat's first reply after the summary falls behind may carry
less history than before for exactly one turn, and says so on the turn.
Summaries are written by follow-up jobs, so a chat's summary can lag a reply by
the length of one model call. Job consumers see a `summary_job_id` in a turn's
result and in `followup_job_ids` when one was scheduled.

The reload fix is the cheap half of the question. Whether reloads were
happening at all is now measurable from `turn.model_load`, on the deployment;
nothing here could observe the real Ollama.

## Verification

- `tests/test_reply_path.py`: a health check answers while a transcription is
  held open, and fails without the change; a stuck conversion times out; a reply
  completes while the summary model is held shut; reading from the boundary
  gives the prompt a full read would; a follow-up on the reply's model asks for
  the reply's context and is not sent the account key; each stage is recorded
  once and logged once; a logged error names its file and line.
- `tests/test_job_queue.py`: a turn goes ahead of queued follow-ups for its chat,
  never ahead of an earlier turn, and not behind another chat's follow-up that
  happens to use the loaded model.
- `tests/test_context_service.py`: a long chat's summary is written after the
  first reply and used by the next; a failing summary model fails its follow-up
  and leaves the reply completed.
- Deployment: `turn.model_load` in the observability report on the real Ollama.
  Near zero across a conversation whose follow-ups run between replies is the
  evidence that no reload stands in front of a reply; seconds mean one still
  does, from something this change did not cover.
