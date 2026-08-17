# Platform Task Models

Platform Task Models perform narrow cross-persona work. They are not personas,
do not speak to the user, and do not receive permission to select provider URLs,
media resources, or privileged settings.

## Where task models run

Every task role reads conversation text, and memory extraction reads all of it.
A cloud provider is a legitimate choice for any of them and stays available:
somebody with modest hardware may get a better result that way, and that is
their call to make rather than this product's.

What it must never be is the choice that happens by itself. Every role defaults
to the local provider, nothing falls back from a local provider to a cloud one
unless somebody paired them deliberately, and the settings control names each
option as running on this machine or leaving it - so picking one is informed
rather than incidental.

The homepage says where each part of a conversation currently goes, computed by
`app/data_locality.py` so the browser cannot drift from the server's answer.
That list is the thing to keep honest when a provider is added: an unrecognised
provider is described as local, which is right for a LAN adapter and wrong for a
cloud one.

## What a readiness check knows

Three separate facts, reported separately, because conflating them let a profile
with no API key describe itself as ready and then fail with
`openai_api_key_missing` when it ran:

- `adapter_installed` - the provider adapter exists in this build.
- `credentials_configured` - the account has whatever credential that adapter
  requires. An adapter declares its own requirement; readiness does not
  special-case provider names, and no message ever contains the credential.
- `live_verified` - a real request has proved the provider answers. Always
  `false`. Readiness spends no requests, so it can never honestly claim this;
  a task run reports its own outcome and the run audit records it.

`ready` means the attempt could be made, not that it has been. A keyless primary
with a working fallback reports `fallback_ready`; a keyless primary with a
keyless fallback reports `unavailable`.

The credential is checked before the model name. Both can be wrong at once, and
the key is the one to fix first, because until it is configured the provider's
model list cannot be trusted either.

An installed adapter is not an offered provider. The OpenAI adapter exists to
keep the structured-output contract provider-neutral. It is deliberately absent
from the Task Model settings selector, and stays absent until the privacy
question about sending conversation-derived text to OpenAI is answered.

## Roles and failure behavior

| Role | Runs when | Typed result | Terminal fallback |
| --- | --- | --- | --- |
| Chat titles | First turn in an untitled chat | One title | Deterministic title from the user text |
| Conversation summaries | Context compaction needs an older-prefix checkpoint | One factual summary | Skip the new checkpoint and use bounded transcript truncation |
| Memory extraction | After a successful turn in `saved` mode | Reviewable fact candidates | Fail only the extraction job; never change the completed turn |
| Capability planning | After a persona reply when capabilities are available | Capability, prompt, and controlled semantic requirements | Create no capability request |

All model outputs must match the role's strict JSON Schema and parser. Extra
fields are rejected. Capability output may add only an operation, domains,
content tags, and required features from server-advertised vocabularies.
When the current chat holds an editable image, the platform also advertises that
chat's completed attachments as opaque references, and the output may select one
as a source or mask. Those references are the only way a plan can name an
existing image: media identifiers are never sent to the model, an unadvertised
reference is rejected, and the platform re-resolves the reference from its own
owner-scoped query before the edit is prepared. A planned edit always requires
owner confirmation. See ADR 0029.
Image requests also include a typed `persona_subject` decision based on the user
request. The platform removes `identity_control` from unrelated images and adds
it for persona subjects; assistant reply prose cannot expand the subject. See
ADR 0017. A narrow server guard also honors explicit exclusions such as
"without you" even if the Task Model incorrectly marks the selected persona as
the subject; generic scene exclusions cannot remove persona identity control.

Chat-title generation runs while a chat still has a recognized placeholder.
The browser creates the canonical `New chat` title, and the server also recognizes
legacy `New conversation` and `Untitled chat` values so existing chats can recover
on their next successful turn. A title-model output that is itself one of those
placeholders is rejected and uses the deterministic user-text title instead, so
a nominally successful model call cannot restore the untitled state.
Provider, checkpoint, model, workflow, LoRA, generation settings, and identity
references remain excluded. The deterministic media coordinator resolves the
semantic request against operator metadata; see `docs/media-catalog.md`.

Capability prompt text is bounded to 1,000 characters in both schema and
parser. This keeps nested structured output compatible with the deployed
Ollama/llama.cpp grammar compiler; much larger string bounds were rejected
before inference on the accepted Unraid deployment. Empty controlled
vocabularies remain arrays of strings without impossible empty enums. Ordinary
discussion, explanation, and planning must produce no capability request unless
the user explicitly asks to create or modify media. A deterministic permission
guard skips capability planning entirely when a message begins with an explicit
literal text-response contract such as `reply with exactly` or `answer only`.
The guard is deliberately prefix-scoped: a later formatting clause cannot veto
a preceding explicit media request. This is a safety boundary around model
output, not a replacement for semantic planning of real media intent.

## Configuration

Each user has one profile per role under Settings -> Task Models:

- enabled state;
- primary Ollama provider/model;
- optional fallback model;
- maximum input and output tokens;
- provider timeout and temperature;
- one documented failure behavior.

A blank model resolves to the first model listed by Ollama. This is convenient
for first run but explicit model names are more repeatable. The readiness action
checks the installed-model list and reports whether primary or fallback is
usable; configuration alone is not treated as provider health.

Using one small, reliable local model for all four roles is the recommended
starting point on a 12 GB shared GPU. The default single interactive worker
serializes persona chat and task work. Increasing
`JOB_QUEUE_INTERACTIVE_WORKERS` can overlap model calls and cause VRAM contention;
switching between different loaded models can add latency even when calls remain
serialized.

## Audit and privacy

`task_model_runs` records role, requested and executed model, content-free
attempts, estimated token counts, latency, fallback state, and redacted safe
errors. It never stores the task prompt or generated result. Restart recovery
marks a running task failed with `interrupted by server restart`.

The recent-run list in Settings is an operator diagnostic, not a model lab. It
does not display conversation content.

## Developer qualification

Run the curated screening cases against an explicitly selected local model:

```bash
python scripts/evaluate_task_models.py \
  --base-url http://OLLAMA_HOST:11434 \
  --model MODEL_NAME
```

The cases check title specificity, summary correction retention, stable-memory
extraction, credential exclusion, ordinary and literal-response capability
precision, and image capability recall. By default the report contains only
pass/fail, latency, and safe failure details. `--show-output` is an explicit
opt-in for local debugging.

Passing this small suite proves contract compatibility, not general model
quality. Final selection should also be timed on the Unraid deployment and
observed through normal long-chat, memory-review, and media-request behavior.
The active deployment record and selected model belong in
`docs/deployment-acceptance.md`, not in product defaults.

## Scene proposals

A fifth role proposes pictures a persona could plausibly send, from the
persona's own card, its lorebook titles, and what recent conversations were
about. It returns typed scenes with provenance: which of those sources suggested
each idea, and the specific detail it drew on.

It never names a provider, model, LoRA, workflow, or generation setting, and it
does not write prompt text - the same boundary every other role has. Its output
lands in the persona's scene backlog as `proposed`; approval is a person's
decision.

Migration `0029_scene_proposal_role` widens the role vocabulary. The profile and
run tables constrain it with a CHECK, and SQLite cannot alter one in place, so
both are rebuilt with existing rows copied verbatim.
