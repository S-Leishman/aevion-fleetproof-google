"""The control tower: mission orchestration across the registered fleet.

Every state transition writes a receipt before returning, so the chain is the
authoritative record of what happened rather than a side-effect log written
afterwards.

Two properties this module is responsible for:

1. A consequential action cannot execute without a matching human grant.
   Enforced by routing every execution through `policy.evaluate` and refusing
   to execute on any verdict other than ALLOW.

2. An approved action executes exactly once. Enforced by recording the action
   in `mission.completed_actions` and rejecting re-execution, so a replayed
   approval or a double-clicked button cannot produce two external effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .domain import (
    Action,
    AuthorityGrant,
    Decision,
    Mission,
    MissionState,
    Verdict,
)
from .policy import evaluate
from .receipts import Receipt, verify_chain
from .registry import (
    MISSION_PLAN,
    action_by_name,
    agent_by_id,
)
from . import planner
from .store import Store, new_id, utc_now_iso


class EngineError(Exception):
    """Raised when a caller requests an operation the engine refuses."""


@dataclass(frozen=True)
class StepResult:
    """The outcome of attempting one mission step."""

    mission: Mission
    action: Action | None
    decision: Decision | None
    receipt: Receipt | None
    executed: bool
    message: str

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "mission": dict(self.mission.to_dict()),
            "action": (
                {
                    "action_id": self.action.action_id,
                    "name": self.action.name,
                    "description": self.action.description,
                    "consequence": self.action.consequence.name,
                    "target": self.action.target,
                }
                if self.action
                else None
            ),
            "decision": dict(self.decision.to_dict()) if self.decision else None,
            "receipt": self.receipt.to_dict() if self.receipt else None,
            "executed": self.executed,
            "message": self.message,
        }


class FleetProofEngine:
    """Orchestrates missions, policy checks, approvals, and receipts."""

    def __init__(self, store: Store, *, project: str | None = None) -> None:
        self._store = store
        self._project = project

    # --- mission lifecycle -------------------------------------------------

    def create_mission(self, objective: str) -> Mission:
        mission = Mission(mission_id=new_id("msn"), objective=objective)
        self._store.save_mission(mission)
        self._store.append_receipt(
            mission_id=mission.mission_id,
            event="MISSION_RECEIVED",
            actor="control-tower",
            detail={"objective": objective},
        )
        return mission

    def next_capability(self, mission: Mission) -> str | None:
        """The next unfinished step in the plan, or None if the plan is done."""
        for capability in MISSION_PLAN:
            action = action_by_name(capability)
            if action and action.action_id not in mission.completed_actions:
                return capability
        return None

    def advance(self, mission_id: str) -> StepResult:
        """Attempt the next mission step.

        Returns a StepResult describing what happened. A HOLD is a normal,
        successful outcome of this method, not an error.
        """
        mission = self._require_mission(mission_id)
        capability = self.next_capability(mission)
        if capability is None:
            mission.state = MissionState.COMPLETE
            mission.pending_action_id = None
            self._store.save_mission(mission)
            return StepResult(
                mission=mission,
                action=None,
                decision=None,
                receipt=None,
                executed=False,
                message="Mission plan complete.",
            )

        action = action_by_name(capability)
        if action is None:
            raise EngineError(f"No catalog entry for capability '{capability}'.")

        # --- discovery / routing -------------------------------------------
        mission.state = MissionState.DISCOVERING
        self._store.save_mission(mission)
        selection = planner.select_agent(
            objective=mission.objective,
            capability=capability,
            project=self._project,
        )
        if not selection.agent_id:
            self._store.append_receipt(
                mission_id=mission.mission_id,
                event="ROUTING_FAILED",
                actor="control-tower",
                detail={"capability": capability, "rationale": selection.rationale},
            )
            raise EngineError(selection.rationale)

        agent = agent_by_id(selection.agent_id)
        if agent is None:
            raise EngineError(f"Selected agent '{selection.agent_id}' not registered.")

        mission.assigned_agent_id = agent.agent_id
        mission.selection_rationale = selection.rationale
        mission.selection_source = selection.source
        mission.state = MissionState.ASSIGNED
        mission.pending_action_id = action.action_id
        self._store.save_mission(mission)
        self._store.append_receipt(
            mission_id=mission.mission_id,
            event="AGENT_SELECTED",
            actor="control-tower",
            detail={
                "capability": capability,
                "agent_id": agent.agent_id,
                "selection_source": selection.source,
                "model": selection.model,
                "rationale": selection.rationale,
            },
        )

        # --- proposal + policy ---------------------------------------------
        mission.state = MissionState.ACTION_PROPOSED
        self._store.save_mission(mission)
        self._store.append_receipt(
            mission_id=mission.mission_id,
            event="ACTION_PROPOSED",
            actor=agent.agent_id,
            detail={
                "action_id": action.action_id,
                "action": action.name,
                "consequence": action.consequence.name,
                "target": action.target,
            },
        )

        grants = tuple(self._store.list_grants(mission.mission_id))
        decision = evaluate(
            action=action,
            agent=agent,
            mission_id=mission.mission_id,
            grants=grants,
        )
        decision_receipt = self._store.append_receipt(
            mission_id=mission.mission_id,
            event="POLICY_DECISION",
            actor="policy-engine",
            detail=dict(decision.to_dict()),
        )

        if decision.verdict is Verdict.ALLOW:
            return self._execute(mission, action, agent.agent_id, decision)

        if decision.verdict is Verdict.HOLD:
            mission.state = MissionState.HELD
            self._store.save_mission(mission)
            return StepResult(
                mission=mission,
                action=action,
                decision=decision,
                receipt=decision_receipt,
                executed=False,
                message=(
                    f"HELD: {action.name} requires human authority. "
                    "No external effect has occurred."
                ),
            )

        mission.state = MissionState.DENIED
        self._store.save_mission(mission)
        return StepResult(
            mission=mission,
            action=action,
            decision=decision,
            receipt=decision_receipt,
            executed=False,
            message=f"DENIED: {decision.rationale}",
        )

    # --- approval ----------------------------------------------------------

    def approve(self, mission_id: str, *, approver: str) -> StepResult:
        """Record a human grant for the held action, then execute it once."""
        mission = self._require_mission(mission_id)
        if mission.state is not MissionState.HELD or not mission.pending_action_id:
            raise EngineError(
                "Approval is only valid for a mission holding a pending action; "
                f"mission is {mission.state.value}."
            )

        action = self._action_by_id(mission.pending_action_id)
        if action.action_id in mission.completed_actions:
            raise EngineError(
                f"Action {action.action_id} already executed for this mission; "
                "refusing to execute a second time."
            )

        grant = AuthorityGrant(
            granted_by=approver,
            mission_id=mission.mission_id,
            action_id=action.action_id,
            granted_at=utc_now_iso(),
        )
        self._store.add_grant(grant)
        self._store.append_receipt(
            mission_id=mission.mission_id,
            event="HUMAN_AUTHORITY_GRANTED",
            actor=approver,
            detail={"action_id": action.action_id, "granted_at": grant.granted_at},
        )

        agent_id = mission.assigned_agent_id or ""
        agent = agent_by_id(agent_id)
        if agent is None:
            raise EngineError(f"Mission has no valid assigned agent ('{agent_id}').")

        # Re-evaluate rather than trusting that the grant is sufficient. The
        # gate is the same code path as the unapproved attempt; only the
        # grant input changed.
        decision = evaluate(
            action=action,
            agent=agent,
            mission_id=mission.mission_id,
            grants=tuple(self._store.list_grants(mission.mission_id)),
        )
        self._store.append_receipt(
            mission_id=mission.mission_id,
            event="POLICY_DECISION",
            actor="policy-engine",
            detail=dict(decision.to_dict()),
        )
        if decision.verdict is not Verdict.ALLOW:
            mission.state = MissionState.HELD
            self._store.save_mission(mission)
            return StepResult(
                mission=mission,
                action=action,
                decision=decision,
                receipt=None,
                executed=False,
                message=f"Still not permitted after approval: {decision.rationale}",
            )
        return self._execute(mission, action, agent.agent_id, decision)

    def deny(self, mission_id: str, *, approver: str, reason: str) -> StepResult:
        """Explicitly refuse the held action."""
        mission = self._require_mission(mission_id)
        if mission.state is not MissionState.HELD or not mission.pending_action_id:
            raise EngineError("Denial is only valid for a held mission.")
        action = self._action_by_id(mission.pending_action_id)
        mission.state = MissionState.DENIED
        mission.pending_action_id = None
        self._store.save_mission(mission)
        receipt = self._store.append_receipt(
            mission_id=mission.mission_id,
            event="HUMAN_AUTHORITY_REFUSED",
            actor=approver,
            detail={"action_id": action.action_id, "reason": reason},
        )
        return StepResult(
            mission=mission,
            action=action,
            decision=None,
            receipt=receipt,
            executed=False,
            message=f"Refused by {approver}: {reason}",
        )

    # --- execution ---------------------------------------------------------

    def _execute(
        self, mission: Mission, action: Action, actor: str, decision: Decision
    ) -> StepResult:
        """Perform the action's effect exactly once and record it.

        The idempotency check lives here, on the single path that produces
        effects, rather than at each caller.
        """
        if action.action_id in mission.completed_actions:
            raise EngineError(
                f"Refusing duplicate execution of {action.action_id}."
            )

        effect = self._effect_for(action)
        mission.completed_actions.append(action.action_id)
        mission.pending_action_id = None
        mission.state = (
            MissionState.COMPLETE
            if self.next_capability(mission) is None
            else MissionState.EXECUTED
        )
        self._store.save_mission(mission)
        receipt = self._store.append_receipt(
            mission_id=mission.mission_id,
            event="EFFECT_EXECUTED",
            actor=actor,
            detail={
                "action_id": action.action_id,
                "action": action.name,
                "consequence": action.consequence.name,
                "target": action.target,
                "human_authority_present": decision.human_authority_present,
                "effect": effect,
            },
        )
        return StepResult(
            mission=mission,
            action=action,
            decision=decision,
            receipt=receipt,
            executed=True,
            message=f"Executed {action.name}.",
        )

    def _effect_for(self, action: Action) -> str:
        """The simulated work product of an action.

        These are simulated effects. The control-plane behaviour being
        demonstrated -- routing, gating, approval, receipts -- is real; the
        downstream systems are not, and the UI and README say so.
        """
        effects = {
            "read_telemetry": (
                "Read 3 open incidents: elevated p99 latency in eu-west, "
                "two degraded ingest workers."
            ),
            "draft_notice": (
                "Drafted internal remediation notice covering the eu-west "
                "latency regression and ingest worker restarts."
            ),
            "publish_notice": (
                "Published remediation notice to the customer status page "
                "(simulated external endpoint)."
            ),
        }
        return effects.get(action.name, f"Performed {action.name}.")

    # --- inspection / replay ----------------------------------------------

    def replay(self, mission_id: str) -> Mapping[str, Any]:
        """Reconstruct a mission's history and verify chain integrity."""
        receipts = self._store.list_receipts(mission_id)
        try:
            verify_chain(receipts)
            integrity = "VERIFIED"
            error = None
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            integrity = "FAILED"
            error = str(exc)
        return {
            "mission_id": mission_id,
            "chain_integrity": integrity,
            "chain_error": error,
            "receipt_count": len(receipts),
            "head_digest": receipts[-1].digest if receipts else None,
            "receipts": [r.to_dict() for r in receipts],
        }

    # --- helpers -----------------------------------------------------------

    def _require_mission(self, mission_id: str) -> Mission:
        mission = self._store.load_mission(mission_id)
        if mission is None:
            raise EngineError(f"Unknown mission '{mission_id}'.")
        return mission

    def _action_by_id(self, action_id: str) -> Action:
        from .registry import ACTION_CATALOG

        for action in ACTION_CATALOG.values():
            if action.action_id == action_id:
                return action
        raise EngineError(f"Unknown action id '{action_id}'.")
