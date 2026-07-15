# Design principles

1. **Believability over feature count.** Optimize the core conversation before
   expanding the capability catalog.
2. **Truth over optimistic UI.** Show unavailable, degraded, fallback, and
   partial states explicitly.
3. **Measured providers.** Select speech providers using blind listening,
   latency, reliability, cost, and hardware evidence.
4. **Cancelable work.** A canceled or superseded turn must stop consuming user
   attention and should stop provider work when the provider allows it.
5. **Explicit memory.** Persist useful facts with provenance and user control;
   do not confuse transcript history with durable memory.
6. **Provider independence.** Product concepts must not inherit one vendor's
   wire format or lifecycle assumptions.
7. **Private-LAN honesty.** Harden the supported LAN boundary without claiming
   direct-internet SaaS security.
8. **Recoverability.** Schema changes, provider failures, restarts, and storage
   pressure need observable recovery paths.
9. **Causal context.** A turn must see completed predecessors, exclude later
   queued input, remain inside a declared budget, and disclose degradation.
10. **Reviewable memory.** Extracted facts are proposals, not truth, until the
    owner approves them; edits and forgetting preserve an auditable history.
11. **Explainable coordination.** Privileged resource selection uses explicit
    operator metadata and compatibility, never model/LoRA filenames or persona
    guesses, and is visible before execution.
12. **Identity requires evidence.** Persona appearance guidance and generation
    inputs are not proof of identity. Only consented, reviewed references and a
    real accepted comparison may support a persona identity claim.
13. **Approachable control.** Settings lead with the operator's goal, show
    truthful readiness and next actions, prefer recognizable choices over raw
    IDs, and preserve expert controls through progressive disclosure.
