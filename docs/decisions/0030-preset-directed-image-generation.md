# ADR 0030: Preset-directed image generation

- Status: accepted as direction; implementation tracked as the image generation
  program in `BACKLOG.md`
- Date: 2026-08-14
- Owners: Nice Assistant maintainers

## Context

The media catalog (ADR 0009) models resource *fitness*: which model, LoRA, or
workflow is technically able to serve a request. The coordinator then assembles
a plan by scoring domain overlap and priority. That answers "what can run" but
not "what is known to produce a good picture", and it can compose a combination
no one has ever tested.

Three defects in the existing path make the requested quality unreachable. They
are recorded in `docs/debt-register.md`:

1. A ComfyUI workflow resource cannot receive the request prompt. The executor
   builds a fixed nine-node graph, writes the positive prompt into node `"6"`
   and the negative into node `"7"`, then merges the operator's inline graph
   over it. There are declared bindings for identity, source, and mask images
   but none for prompt, seed, or dimensions. An imported graph therefore either
   loses the prompt or renders the text that was baked in at export time. It
   still produces an image, so the failure is silent.
2. Prompt construction is hardcoded and global. Every local prompt is prefixed
   with fixed quality words and paired with one negative string that varies only
   on the NSFW toggle. Checkpoint families disagree sharply here: some require
   score or booru tags, some are damaged by quality boilerplate, and some
   support no negative prompt at all.
3. Capability planning receives only the current user message, so a request that
   refers to something established earlier in the conversation cannot be routed
   or described correctly.

Generation quality in this domain is mostly recipe knowledge: the sampler,
scheduler, step count, CFG, dimensions, LoRA weights, and prompt style that a
particular checkpoint wants. That knowledge is what the product must capture,
and it is not derivable from a filename.

## Decision

**A Generation Preset is the planned unit.** A preset is an operator-authored,
tested bundle: a workflow graph or provider generate call, the exact checkpoint
and LoRAs at their tested weights, sampler settings, permitted dimensions, a
prompt dialect, a pass structure, the inputs it accepts, and a routing card.
Planning selects a preset; it does not assemble one. Catalog resources remain
the inventory a preset references, and existing model/workflow/LoRA plans
migrate to implicit single-pass presets. Automatic LoRA selection survives only
where a preset declares an open slot, and the existing explicit compatibility
edges still apply.

**A Scene is the typed intermediate representation.** The Task Model emits a
structured record - subject, action, setting, wardrobe, framing, lighting,
camera, mood - not finished prompt text. It never emits a provider, model, LoRA,
workflow, filename, URL, or generation setting; that boundary from ADR 0007 and
ADR 0009 is unchanged.

**A deterministic prompt compiler renders the Scene into the preset's dialect.**
Dialect declares style (natural language, booru tags, or hybrid), prefix and
suffix templates, the preset's own negative prompt, whether a negative prompt is
supported at all, LoRA trigger-word placement, and a target length. Compilation
is pure: the same Scene and preset always produce the same text. The platform
safety negative, applied when NSFW is disabled, remains separate and
platform-owned. Hardcoded style text is removed from the planned path.

**Every input a workflow needs is declared, or the workflow cannot be enabled.**
Prompt, negative prompt, seed, and dimension bindings join the existing
identity, source, and mask bindings, validated the same way against the inline
graph. A workflow that cannot receive the request prompt is refused rather than
enabled, because silently ignoring the request is worse than blocking it. A
workflow resource is the whole graph; it is not a patch merged over a default
graph the operator never saw.

**Routing is a platform shortlist and an operator-written card.** The platform
hard-filters presets that are legal for the request - kind, operation, persona
subject, content policy, provider reachability, capacity. It then offers the
Task Model a small shortlist of opaque id, title, and routing card, with no
resource identity, and the model picks one and writes the Scene. When the model
fails, times out, or returns an unusable answer, the existing deterministic
score selects from the same shortlist. The operator writes the routing card in
plain language and can test it against a real message.

**Every generation writes one durable journal.** Stages, timings, the context
used, the shortlist and why entries survived, the chosen preset and the reason,
the Scene, the compiled positive and negative text, the resolved graph, each
pass, the provider exchange, quality checks, and the outcome. It is reachable in
one click from the image, exports as a single file that can be handed to another
person alongside that image, and contains no credentials, no absolute server
paths, and no reference-image bytes.

**Library serving and library production are separate.** Serving a ready image
instead of generating one, and letting a proactive persona message be written
after its picture is chosen rather than before, is a small change that is
valuable against a hand-filled library. Producing that library - idea
generation, idle scheduling, photo sets, preference weighting - is a much larger
change that depends on the Scene record and is sequenced after it.

**The preset bundle format is built now; sharing ships later.** The format is
required for the built-in starter presets. Export and import become user-facing
only with a scrub-and-preview step on export, an asset remapping step that
resolves referenced checkpoints and LoRAs against the local installation, and an
explicit statement that importing a preset executes another person's graph on
the operator's machine. Discovery, ratings, and a registry are out of scope.

## Alternatives considered

- **Keep composing plans from scored resource tags.** Rejected because it can
  emit an untested combination during a conversation, which is the specific
  failure the owner asked to prevent, and because tags are a lossy intermediate
  between what was asked for and what should run.
- **Let the Task Model write the final prompt text.** Rejected because prompt
  syntax is a property of the checkpoint, not of the request. A small local
  model asked to emit booru tags correctly and consistently is a reliability
  risk, and the same Scene must be renderable into several dialects.
- **Let the Task Model choose freely from the whole catalog.** Rejected because
  it reintroduces resource identity into model output. The shortlist keeps the
  boundary while giving the model real routing power, because the shortlist is
  operator-built.
- **Infer routing from workflow filenames or node contents.** Rejected for the
  reason ADR 0009 already gives: the catalog never infers capability from a
  name.
- **Treat a missing prompt binding as an operator choice and generate anyway.**
  Rejected under the truthful-behavior rule. A picture that ignores the request
  while appearing to honor it is the worst available outcome.
- **Build library production before the Scene record.** Rejected because it
  would mass-produce images through the unfixed prompt path and would train
  preference weighting on results that are then discarded.

## Consequences

Operators gain a unit of configuration that matches how image generation
knowledge actually travels, and a first-run experience that starts from tested
presets instead of an empty expert screen. Reviewing a bad result becomes
reading one file rather than reconstructing a decision from several tables.

Costs: the catalog gains a new primary object and a migration; the Task Model
capability contract changes shape; the ComfyUI executor stops being a fixed
graph with a patch applied. Existing enabled workflows must be reviewed for
prompt bindings rather than auto-guessed, because guessing which node was meant
to receive the prompt could change what an operator's tested graph produces.

The routing tester in settings is deliberate temporary tooling. It exists to
make preset authoring tractable and is expected to be removed once routing is
demonstrably stable. It is documented as temporary rather than presented as a
permanent feature.

Identity conditioning is addressed separately in ADR 0031.

## Verification

- Contract tests prove the Task Model cannot emit resource identity, that an
  unknown preset id is rejected, and that the deterministic fallback selects
  from the same shortlist the model was offered.
- Provider tests prove a graph using arbitrary node IDs receives the compiled
  prompt through declared bindings, and that a graph without a prompt binding
  cannot be enabled.
- Compiler tests prove determinism, that two dialects render the same Scene
  differently, and that a dialect declaring no negative support sends none.
- Journal tests prove one record per generation across conversational, direct,
  edit, and library-served paths, and that an export carries no credential,
  path, or reference-image content. `python scripts/audit_public_repo.py` runs
  against a synthetic export fixture.
- Browser tests cover reaching a journal from an image, the routing tester, and
  the consolidated settings surfaces.
- Live ComfyUI graph compatibility, real preset quality, and idle-time capacity
  behavior remain deployment acceptance rather than CI claims.
