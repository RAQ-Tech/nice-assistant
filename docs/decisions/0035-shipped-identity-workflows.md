# ADR 0035: Identity workflows ship with the product, and every pass carries its own bindings

- Status: accepted
- Date: 2026-08-17
- Owners: Nice Assistant maintainers

## Context

[ADR 0031](0031-structural-identity-conditioning.md) settled that a persona's
resemblance comes from a declared structural mechanism, and that comparing a
finished face is measurement rather than a means. It did not settle how an
operator gets such a mechanism running.

What existed was a guided import: export a graph from ComfyUI in API format,
send it to be inspected, then choose which of its inputs receives the request
prompt and which receives the approved reference. That is a node-graph task in a
settings page. It also never worked over real HTTP - the response model omitted
the field the browser gates its Save button on - so the feature had shipped
without anyone completing it once.

Underneath, three assumptions were wrong in ways that only show up on a real
deployment. A graph carries the checkpoint it was saved with, and nothing
overwrote it, so a preset could name one model and render another and the
picture still came out. Some identity techniques only condition when a
particular word is in the prompt and quietly return an ordinary picture without
it. And a recipe that does the identity work in a second pass could neither be
planned nor executed.

## Decision

**Known-good graphs ship with the product.** Their node IDs are fixed and their
bindings are declared by construction, so nothing asks a person which input is
which. Inspection changes role for them: for an imported graph it is discovery,
for a template it is verification - are these node types installed, are these
named files present.

Templates live in `assets/workflow-templates/` with `app/workflow_template.py`
beside them. That module is a **sibling of `app/preset_bundle.py`, not an
extension of it**. A preset bundle deliberately cannot carry a graph, because it
is the operator-to-operator import path and accepting one would mean running a
stranger's graph on your machine. A template is the product's own graph, read
from disk, and never accepted over the wire.

**A template states what it needs in plain language, and never implies it
checked more than it did.** `/object_info` reports the files a provider has for
inputs that name files. An identity model a node selects by device rather than
by a named input - InsightFace's `antelopev2` behind a CPU/CUDA/ROCM combo - is
invisible to it, and will be invisible to every check we can write. Saying so
where the result is read is the only honest option; a green tick that means less
than it looks like is worse than no tick.

**Nothing here claims to have been run.** The first requested persona image is
still the live test, exactly as it is for a graph an operator imported
themselves.

**A model resource declares its architecture; nothing sniffs it.** The
application has no access to the models directory, and `/object_info` reports
filenames rather than what is inside them. The distinction matters because an
identity adapter is trained against one text encoder, and families that share a
base architecture do not necessarily share that encoder: Pony and Illustrious
are SDXL derivatives that retrain it, which is exactly where an SDXL adapter's
likeness becomes unmeasured. A mismatch is marked and still installable - the
operator may know something the declaration does not - and an undeclared family
offers everything and asks for the declaration.

**A newer template version is offered, never applied.** Installing one writes a
second workflow and leaves the first alone. The graph already in the catalog may
have been tuned, and rewriting somebody's tuned graph is precisely what ADR 0030
refuses to do. Provenance lives in two nullable columns; null is the normal
state and means the graph did not come from here.

**The checkpoint is bound, or the disagreement is refused.** `checkpoint_bindings`
writes the preset's base model into a declared `ckpt_name` input at run time.
Where there is no binding, a preset whose graph bakes a different checkpoint is
refused when it is saved, naming both files, because the alternative is a
picture that came out fine and was made by the wrong model.

**A technique's trigger word is guaranteed or the run stops.** A workflow may
declare `required_prompt_token` with the `prompt_prefix` that supplies it. The
executor prepends the prefix when the compiled prompt lacks the word, and
refuses to run when a workflow declares a word it has no prefix for. A refusal
is better than an unconditioned picture presented as the persona.

**Every pass carries its own bindings, assigned rather than merged.** A binding
a pass does not declare must not stay pointing at the previous graph's node IDs.
The approved reference goes to the pass whose graph actually has the nodes for
it, and a capability that only a later pass provides still counts as one the
recipe has.

**A pass that takes no prompt says so.** A face swap over a finished picture has
no text input; its only string widgets are face indexes. Binding the request
into one of those to satisfy the prompt-binding rule would be worse than having
no binding, so such a workflow declares `consumes_prompt: false` and is refused
if it also claims it can generate.

**A mechanism is declared by an operator or proved by a graph.** A preset's
stored `identity_mechanisms` is what the operator meant; the workflow the plan
selects is what it can demonstrably do, and either is enough. That stops a
preset being refused for a capability its attached graph plainly has, and stops
a stored guess going stale when a workflow is added later. The identity settings
control offers exactly the mechanisms the catalog can apply, because a choice
that can only block is worse than no choice.

## Alternatives considered

**Keep only the guided import.** It is still there, behind a disclosure, and it
should be: a graph somebody has already tuned is worth more than a shipped one.
As the only path it asked every operator to do node-graph work before their
first persona picture, which is the step that made the feature unused.

**Let a preset bundle carry a graph.** One format for everything is tempting and
wrong. A bundle is meant to be shareable, and a shareable graph is a shareable
instruction to run arbitrary nodes on the recipient's machine.

**Detect the model family instead of declaring it.** There is nothing to detect
it with. Guessing from a filename would be a heuristic dressed as a fact, and
the failure it produces - unmeasured likeness - is exactly the kind this product
refuses to have silently.

**Give the face-swap template a text-consuming node so it can honour the
prompt-binding rule.** That is building a graph to satisfy a rule rather than to
do the job, and it would add a checkpoint load and a sampler to a pass that
needs neither.

## Consequences

The setup an operator does is: declare the model family, pick a template, check
it, add it, and add one approved reference. No graph is read and no input is
chosen. What cannot be checked from here is named where the check is read.

Templates are product surface now. A shipped graph that stops working when a
custom node changes its inputs is a defect in the product, not in somebody's
export, and `tests/test_workflow_templates.py` validates every shipped
template's bindings against its own graph through the same code that validates
an operator's.
