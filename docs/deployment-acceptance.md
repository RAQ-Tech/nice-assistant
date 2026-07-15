# Deployment acceptance record template

Real deployment evidence is intentionally local because it commonly contains
private addresses, hostnames, paths, hardware inventories, storage capacity,
backup identifiers, provider inventories, and timings that identify one
operator's environment.

Store the working record at `.local/deployment-acceptance.md`. The directory is
ignored by Git. Do not put credentials or the deployment master key there; those
belong only in the deployment's secret-management layer.

## Public acceptance checklist

Record the following locally for each real deployment:

- application revision and image digest;
- supported exposure boundary and HTTPS termination;
- hardware class and available CPU, RAM, GPU memory, and storage;
- provider topology and explicit shared/exclusive ownership decisions;
- readiness results and redacted latency/capacity measurements;
- chat, memory, completed-file speech, media, cancellation, and protected-file
  behavior for capabilities the build truthfully supports;
- restart recovery and clean-shutdown evidence;
- backup archive, SQLite-integrity, migration, and rollback-drill results;
- provider outage and capacity-pressure behavior;
- unavailable or deliberately deferred capabilities.

Public documentation may state whether a capability has been accepted, but must
not include the operator's exact endpoints, server/share paths, hostnames,
capacity snapshots, backup names, personal model inventory, persona content, or
unrelated private services.

## Current product boundary

The supported private-LAN deployment has completed chat, memory, completed-file
speech, managed local-media cleanup, running cancellation, restart recovery, and
non-destructive backup verification. This is a product-status statement, not a
portable performance claim.

Provider-neutral streaming TTS, local STT, natural turn-taking, barge-in, and
real visual-identity deployment acceptance remain separate future work. A
destructive live restore also remains an explicitly authorized operator drill.
