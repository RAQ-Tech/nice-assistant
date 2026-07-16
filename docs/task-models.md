# Platform Task Models

Platform Task Models perform narrow cross-persona work. They are not personas,
do not speak to the user, and do not receive permission to select provider URLs,
media resources, or privileged settings.

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
