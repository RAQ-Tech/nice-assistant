"""The per-persona scene backlog.

A record of pictures that have been proposed for a persona but not made. It
exists separately from the retained library because "we could make this" and "we
have this" are different facts, and conflating them would make a plan look like
an achievement.

Approved entries are produced in the background during quiet hours, as bulk work
so a picture somebody asked for is always chosen first. Nothing reaches
generation without a person having approved it, and an entry that fails or is
interrupted returns to `approved` rather than sitting in `generating` forever.
"""

from __future__ import annotations

from datetime import datetime
import json

from app.media_scene import normalize_scene, scene_is_empty, scene_summary
from app.persona_card import CARD_STORED_FIELDS
from app.pregeneration import PregenerationPolicy, may_produce, policy_for_owner
from app.provider_contracts import CancellationToken, ProviderError
from app.repositories import UnitOfWork, now_ts
from app.service_errors import NotFoundError, RequestError, ServiceError
from app.task_contracts import SCENE_PROPOSAL, SceneProposalTaskInput, TaskContractError


BACKLOG_STATES = ("proposed", "approved", "generating", "done", "retired")
BACKLOG_SOURCES = ("operator", "persona_card", "lorebook", "conversation")
# Transitions an operator can ask for. Generation states are set by the work
# itself, not by someone clicking, so they are deliberately absent.
OPERATOR_TRANSITIONS = {
    "proposed": {"approved", "retired"},
    "approved": {"proposed", "retired"},
    "generating": set(),
    "done": {"retired"},
    "retired": {"proposed"},
}
MAX_BACKLOG_ENTRIES = 500


class SceneBacklogService:
    def __init__(
        self,
        session_factory,
        secret_store,
        logger,
        task_models=None,
        capabilities=None,
        jobs=None,
        policy: PregenerationPolicy | None = None,
    ):
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.logger = logger
        self.task_models = task_models
        self.capabilities = capabilities
        self.jobs = jobs
        self.policy = policy or PregenerationPolicy()

    def _uow(self):
        return UnitOfWork(self.session_factory, self.secret_store)

    def entries(self, user_id: str, *, persona_id: str | None = None, state: str | None = None) -> list[dict]:
        if state and state not in BACKLOG_STATES:
            raise RequestError("unknown backlog state", 400)
        with self._uow() as uow:
            rows = uow.repo.scene_backlog_entries(user_id, persona_id=persona_id, state=state)
            return [self._public(row) for row in rows]

    def propose(
        self,
        user_id: str,
        *,
        persona_id: str,
        scene,
        source: str = "operator",
        source_detail: str = "",
    ) -> dict:
        if source not in BACKLOG_SOURCES:
            raise RequestError("unknown backlog source", 400)
        scene = normalize_scene(scene)
        if scene_is_empty(scene):
            raise RequestError("describe the picture being proposed", 400)
        with self._uow() as uow:
            if not uow.repo.persona_for_user(user_id, persona_id):
                raise NotFoundError("persona not found")
            if uow.repo.scene_backlog_count(user_id) >= MAX_BACKLOG_ENTRIES:
                raise RequestError(
                    f"the scene backlog is full at {MAX_BACKLOG_ENTRIES} entries; retire some before adding more",
                    409,
                )
            row = uow.repo.add_scene_backlog_entry(
                user_id=user_id,
                persona_id=persona_id,
                scene_json=json.dumps(scene, separators=(",", ":"), ensure_ascii=False),
                source=source,
                source_detail=str(source_detail or "")[:500],
            )
            return self._public(row)

    def propose_from_persona(self, user_id: str, persona_id: str, *, limit: int = 5) -> dict:
        """Ask the task model for pictures this persona could plausibly send.

        Everything it draws on is already the persona's: its card, its lorebook
        titles, and what the conversation has been about. Proposals arrive as
        `proposed` and are never approved automatically, so nothing reaches
        generation without a person agreeing to it.
        """

        if not self.task_models:
            raise RequestError("scene proposals are unavailable", 503)
        limit = max(1, min(int(limit or 5), 10))
        with self._uow() as uow:
            persona = uow.repo.persona_for_user(user_id, persona_id)
            if not persona:
                raise NotFoundError("persona not found")
            card = " ".join(str(getattr(persona, field, "") or "").strip() for field in CARD_STORED_FIELDS).strip()
            lore = [row.title for row in uow.repo.persona_lore_entries(user_id, persona_id, enabled_only=True)]
            existing = [
                scene_summary(json.loads(row.scene_json or "{}"))
                for row in uow.repo.scene_backlog_entries(user_id, persona_id=persona_id)
            ]
            themes = uow.repo.recent_persona_themes(user_id, persona_id)
            name = persona.name
        try:
            outcome = self.task_models.run(
                user_id,
                SCENE_PROPOSAL,
                SceneProposalTaskInput(
                    persona_name=name,
                    card=card[:4000],
                    lore_titles=tuple(lore[:20]),
                    recent_themes=tuple(themes),
                    existing_summaries=tuple(item for item in existing if item)[:40],
                    limit=limit,
                ),
                CancellationToken(),
            )
        except (ProviderError, TaskContractError, ServiceError) as exc:
            raise RequestError(
                getattr(exc, "user_message", None) or "The task model could not propose scenes.", 502
            ) from exc
        proposed = []
        for proposal in outcome.output.proposals:
            try:
                proposed.append(
                    self.propose(
                        user_id,
                        persona_id=persona_id,
                        scene=proposal.scene,
                        source=proposal.source,
                        source_detail=proposal.source_detail,
                    )
                )
            except RequestError:
                # One unusable proposal should not discard the rest.
                continue
        # A fallback and "no ideas" both come back empty, and they need
        # different fixes, so the difference is reported rather than lost.
        return {
            "proposed": proposed,
            "requested": limit,
            "model_answered": not outcome.fallback_used,
        }

    def owner_policy(self, user_id: str) -> PregenerationPolicy:
        """The policy in force for this owner, read fresh.

        Read on every pass rather than held from startup, so switching it off
        stops the next pass rather than the next restart. `self.policy` is the
        deployment's, which supplies the initial values and keeps one veto:
        production it has switched off cannot be switched back on here.
        """

        with self._uow() as uow:
            settings = uow.repo.settings(user_id) or {}
        return policy_for_owner(settings.get("preferences") or {}, self.policy)

    def deployment_forbids(self) -> bool:
        """Whether this deployment refuses background production outright."""

        return not self.policy.enabled

    def production_readiness(self, user_id: str, *, hour: int | None = None) -> dict:
        """Report whether a background picture could start now, and why not."""

        # The queue only exists once the service has started, and a stopped
        # queue means nothing is running, which is a valid answer.
        queue = getattr(self.jobs, "queue", None) if self.jobs else None
        snapshot = queue.snapshot() if queue else {"pending": {}, "active": {}}
        pending = snapshot.get("pending") or {}
        active = snapshot.get("active") or {}
        policy = self.owner_policy(user_id)
        with self._uow() as uow:
            approved = len(uow.repo.scene_backlog_entries(user_id, state="approved"))
        decision = may_produce(
            policy,
            hour=int(datetime.now().hour if hour is None else hour),
            interactive_pending=int(pending.get("interactive", 0)),
            media_pending=int(pending.get("media", 0)),
            media_active=int(active.get("media", 0)),
            approved_waiting=approved,
        )
        return {
            "allowed": decision.allowed,
            "reason": decision.reason,
            "approved_waiting": approved,
            "window": f"{policy.start_hour:02d}:00-{policy.end_hour:02d}:00",
            "enabled": policy.enabled,
            "start_hour": policy.start_hour,
            "end_hour": policy.end_hour,
            "max_per_run": policy.max_per_run,
            # Stated rather than implied: a control that cannot be switched on
            # has to say why, not simply refuse.
            "deployment_forbids": self.deployment_forbids(),
            "inside_window": policy.window_contains(int(datetime.now().hour if hour is None else hour)),
        }

    def owners_with_work(self) -> list[str]:
        """Owners who have an approved scene waiting."""

        with self._uow() as uow:
            return uow.repo.owners_with_approved_scenes()

    def produce_due(self, user_id: str, *, hour: int | None = None) -> dict:
        """Start background pictures for approved scenes, if the policy allows.

        Reports what happened either way. A caller that gets nothing needs to
        know whether the machine was busy, the hour was wrong, or there was
        simply nothing approved, and only the reason distinguishes them.
        """

        readiness = self.production_readiness(user_id, hour=hour)
        if not readiness["allowed"]:
            return {"started": [], "reason": readiness["reason"]}
        if not self.capabilities:
            return {"started": [], "reason": "image capabilities are unavailable"}
        started = []
        for _ in range(max(1, int(readiness["max_per_run"]))):
            claimed = self._claim_next_entry(user_id)
            if not claimed:
                break
            entry_id, request_id, job_id = claimed
            try:
                self.capabilities.submit_background(
                    user_id,
                    request_id,
                    job_id,
                    on_settled=self._settlement(request_id),
                )
            except Exception:
                # The request exists and its job does not, so the entry would
                # otherwise wait for a completion that can never arrive.
                self._release_entry(user_id, entry_id)
                if self.logger:
                    self.logger.warning("background picture could not be submitted")
                break
            started.append({"entry_id": entry_id, "request_id": request_id, "job_id": job_id})
        if not started:
            return {"started": [], "reason": "no approved scene could be planned"}
        return {"started": started, "reason": readiness["reason"]}

    def _claim_next_entry(self, user_id: str):
        """Take the oldest approved scene and plan it, inside one transaction."""

        with self._uow() as uow:
            rows = uow.repo.approved_scene_backlog_entries(user_id, limit=1)
            if not rows:
                return None
            row = rows[0]
            try:
                scene = json.loads(row.scene_json or "{}")
            except (TypeError, ValueError):
                scene = {}
            prompt = scene_summary(scene)
            if not prompt:
                # Unproducible, and it would be selected again on every pass.
                row.state = "retired"
                row.updated_at = now_ts()
                return None
            prepared = self.capabilities.prepare_background_request(
                uow.repo,
                user_id=user_id,
                persona_id=row.persona_id,
                scene=scene,
                prompt=prompt,
                entry_id=row.id,
            )
            if not prepared:
                # No usable plan tonight. Leaving it approved is correct: the
                # scene is fine, the machine is not ready for it.
                return None
            request_id, job_id = prepared
            row.state = "generating"
            row.capability_request_id = request_id
            row.updated_at = now_ts()
            return row.id, request_id, job_id

    def _settlement(self, request_id: str):
        """Move the entry to match how its request actually ended."""

        def settle(repo, status: str, media_id: str) -> None:
            row = repo.scene_backlog_entry_for_capability(request_id)
            if not row or row.state != "generating":
                return
            if status == "completed" and media_id:
                row.state = "done"
                row.media_id = media_id
            else:
                row.state = "approved"
                row.capability_request_id = None
            row.updated_at = now_ts()

        return settle

    def _release_entry(self, user_id: str, entry_id: str) -> None:
        with self._uow() as uow:
            row = uow.repo.scene_backlog_entry(user_id, entry_id)
            if row and row.state == "generating":
                row.state = "approved"
                row.capability_request_id = None
                row.updated_at = now_ts()

    def set_state(self, user_id: str, entry_id: str, state: str) -> dict:
        if state not in BACKLOG_STATES:
            raise RequestError("unknown backlog state", 400)
        with self._uow() as uow:
            row = uow.repo.scene_backlog_entry(user_id, entry_id)
            if not row:
                raise NotFoundError("backlog entry not found")
            if state not in OPERATOR_TRANSITIONS.get(row.state, set()):
                raise RequestError(
                    f"a {row.state} scene cannot be moved to {state}",
                    409,
                )
            row.state = state
            row.updated_at = now_ts()
            return self._public(row)

    def remove(self, user_id: str, entry_id: str) -> bool:
        with self._uow() as uow:
            row = uow.repo.scene_backlog_entry(user_id, entry_id)
            if not row:
                return False
            uow.session.delete(row)
            return True

    @staticmethod
    def _public(row) -> dict:
        try:
            scene = json.loads(row.scene_json or "{}")
        except (TypeError, ValueError):
            scene = {}
        return {
            "id": row.id,
            "persona_id": row.persona_id,
            "scene": scene,
            "summary": scene_summary(scene),
            "state": row.state,
            "source": row.source,
            "source_detail": row.source_detail or "",
            "media_id": row.media_id,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
