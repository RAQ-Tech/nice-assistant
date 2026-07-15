from __future__ import annotations


IDENTITY_CONTROL_FEATURE = "identity_control"
IDENTITY_CONDITIONING_MODE = "approved_reference_workflow"


def public_identity_conditioning(
    snapshot: dict | None,
    *,
    applied: bool = False,
    verification_status: str | None = None,
    claim_status: str | None = None,
) -> dict | None:
    if not isinstance(snapshot, dict) or not snapshot.get("required"):
        return None
    status = "conditioned" if applied and snapshot.get("status") == "ready" else snapshot.get("status", "blocked")
    result = {
        "required": True,
        "status": status,
        "mode": snapshot.get("mode"),
        "persona_id": snapshot.get("persona_id"),
        "profile_id": snapshot.get("profile_id"),
        "profile_revision": snapshot.get("profile_revision"),
        "reference_id": snapshot.get("reference_id"),
        "reference_sha256": snapshot.get("reference_sha256"),
        "workflow_resource_id": snapshot.get("workflow_resource_id"),
        "correction_workflow_resource_id": snapshot.get("correction_workflow_resource_id"),
        "acceptance_threshold": snapshot.get("acceptance_threshold"),
        "max_generation_attempts": snapshot.get("max_generation_attempts"),
        "failure_policy": snapshot.get("failure_policy"),
        "appearance_description_included": bool(snapshot.get("appearance_description")),
        "verification_status": verification_status or "not_evaluated",
    }
    if applied and status == "conditioned":
        result["claim_status"] = claim_status or ("verified" if verification_status == "passed" else "unverified")
    return result


def prompt_with_identity_description(prompt: str, snapshot: dict | None) -> str:
    description = " ".join(str((snapshot or {}).get("appearance_description") or "").split()).strip()
    if not description:
        return prompt
    return f"{prompt}\n\nPersona appearance requirements: {description}"
