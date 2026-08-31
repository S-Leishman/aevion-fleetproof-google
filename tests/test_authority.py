"""Authority invariants.

These tests are the product claim. Each one corresponds to a statement the
README makes, and each is written so that weakening the gate breaks it.

Deliberately excluded: any network or model call. Whether a consequential
action can bypass human authority must not depend on Gemini being reachable.
"""

from __future__ import annotations

import pytest

from fleetproof.domain import (
    Action,
    AuthorityGrant,
    Consequence,
    MissionState,
    Verdict,
)
from fleetproof.engine import EngineError, FleetProofEngine
from fleetproof.policy import evaluate
from fleetproof.receipts import ChainError, Receipt, verify_chain
from fleetproof.registry import (
    ACTION_CATALOG,
    REMEDIATION_WRITER,
    STATUS_PUBLISHER,
    TELEMETRY_ANALYST,
)
from fleetproof.store import InMemoryStore

PUBLISH = ACTION_CATALOG["publish_notice"]
DRAFT = ACTION_CATALOG["draft_notice"]
READ = ACTION_CATALOG["read_telemetry"]


@pytest.fixture()
def engine(monkeypatch: pytest.MonkeyPatch) -> FleetProofEngine:
    """Engine with deterministic routing, so tests never call the model."""

    def fake_select(*, objective: str, capability: str, project=None):
        from fleetproof.planner import Selection
        from fleetproof.registry import agents_with_capability

        candidates = agents_with_capability(capability)
        chosen = min(candidates, key=lambda a: len(a.capabilities))
        return Selection(
            agent_id=chosen.agent_id,
            rationale="deterministic test routing",
            source="test-stub",
        )

    monkeypatch.setattr("fleetproof.engine.planner.select_agent", fake_select)
    return FleetProofEngine(InMemoryStore())


# --- Policy unit level ------------------------------------------------------


def test_consequential_action_holds_without_authority() -> None:
    decision = evaluate(
        action=PUBLISH, agent=STATUS_PUBLISHER, mission_id="msn-1", grants=()
    )
    assert decision.verdict is Verdict.HOLD
    assert decision.capability_present is True
    assert decision.policy_pass is True
    assert decision.human_authority_present is False


def test_consequential_action_allows_with_scoped_grant() -> None:
    grant = AuthorityGrant(
        granted_by="owner",
        mission_id="msn-1",
        action_id=PUBLISH.action_id,
        granted_at="2026-08-31T00:00:00Z",
    )
    decision = evaluate(
        action=PUBLISH, agent=STATUS_PUBLISHER, mission_id="msn-1", grants=(grant,)
    )
    assert decision.verdict is Verdict.ALLOW
    assert decision.human_authority_present is True


def test_grant_for_another_mission_does_not_authorize() -> None:
    """A grant is scoped to one mission; it must not transfer."""
    grant = AuthorityGrant(
        granted_by="owner",
        mission_id="msn-OTHER",
        action_id=PUBLISH.action_id,
        granted_at="2026-08-31T00:00:00Z",
    )
    decision = evaluate(
        action=PUBLISH, agent=STATUS_PUBLISHER, mission_id="msn-1", grants=(grant,)
    )
    assert decision.verdict is Verdict.HOLD


def test_grant_for_another_action_does_not_authorize() -> None:
    """A grant is scoped to one action; approving a draft cannot publish."""
    grant = AuthorityGrant(
        granted_by="owner",
        mission_id="msn-1",
        action_id=DRAFT.action_id,
        granted_at="2026-08-31T00:00:00Z",
    )
    decision = evaluate(
        action=PUBLISH, agent=STATUS_PUBLISHER, mission_id="msn-1", grants=(grant,)
    )
    assert decision.verdict is Verdict.HOLD


def test_missing_capability_denies_rather_than_holds() -> None:
    """Escalating to a human for an impossible action would be a false ask."""
    decision = evaluate(
        action=PUBLISH, agent=TELEMETRY_ANALYST, mission_id="msn-1", grants=()
    )
    assert decision.verdict is Verdict.DENY
    assert decision.capability_present is False


def test_registry_ceiling_denies_even_with_grant() -> None:
    """Human authority cannot grant a capability the agent is not registered for."""
    over_ceiling = Action(
        action_id="act-escalated-draft",
        name="draft_notice",
        description="A consequential action smuggled in under a reversible name.",
        consequence=Consequence.CONSEQUENTIAL,
        target="https://status.example-enterprise.com/incidents",
    )
    grant = AuthorityGrant(
        granted_by="owner",
        mission_id="msn-1",
        action_id=over_ceiling.action_id,
        granted_at="2026-08-31T00:00:00Z",
    )
    decision = evaluate(
        action=over_ceiling,
        agent=REMEDIATION_WRITER,
        mission_id="msn-1",
        grants=(grant,),
    )
    assert decision.verdict is Verdict.DENY


def test_observation_and_reversible_need_no_human() -> None:
    for action, agent in ((READ, TELEMETRY_ANALYST), (DRAFT, REMEDIATION_WRITER)):
        decision = evaluate(
            action=action, agent=agent, mission_id="msn-1", grants=()
        )
        assert decision.verdict is Verdict.ALLOW, action.name
        assert decision.human_authority_present is False


# --- Non-vacuity ------------------------------------------------------------


def test_gate_is_non_vacuous() -> None:
    """Prove the HOLD is caused by the missing grant, not by always holding.

    Mutating only the grant flips the verdict. If this test passed while the
    previous ones did too but the gate ignored grants entirely, that would be
    a vacuous gate.
    """
    without = evaluate(
        action=PUBLISH, agent=STATUS_PUBLISHER, mission_id="msn-1", grants=()
    )
    with_grant = evaluate(
        action=PUBLISH,
        agent=STATUS_PUBLISHER,
        mission_id="msn-1",
        grants=(
            AuthorityGrant(
                granted_by="owner",
                mission_id="msn-1",
                action_id=PUBLISH.action_id,
                granted_at="2026-08-31T00:00:00Z",
            ),
        ),
    )
    assert without.verdict is Verdict.HOLD
    assert with_grant.verdict is Verdict.ALLOW
    assert without.verdict is not with_grant.verdict


# --- Engine / end-to-end ----------------------------------------------------


def test_mission_holds_at_consequential_boundary(engine: FleetProofEngine) -> None:
    mission = engine.create_mission("Assess incidents and publish remediation.")

    first = engine.advance(mission.mission_id)
    assert first.executed is True
    assert first.action is not None and first.action.name == "read_telemetry"

    second = engine.advance(mission.mission_id)
    assert second.executed is True
    assert second.action is not None and second.action.name == "draft_notice"

    third = engine.advance(mission.mission_id)
    assert third.executed is False
    assert third.decision is not None
    assert third.decision.verdict is Verdict.HOLD
    assert third.mission.state is MissionState.HELD

    # The decisive assertion: no external effect was recorded.
    replay = engine.replay(mission.mission_id)
    executed_targets = [
        r["detail"].get("target")
        for r in replay["receipts"]
        if r["event"] == "EFFECT_EXECUTED"
    ]
    assert PUBLISH.target not in executed_targets


def test_approved_action_executes_exactly_once(engine: FleetProofEngine) -> None:
    mission = engine.create_mission("Assess incidents and publish remediation.")
    engine.advance(mission.mission_id)
    engine.advance(mission.mission_id)
    held = engine.advance(mission.mission_id)
    assert held.decision is not None and held.decision.verdict is Verdict.HOLD

    approved = engine.approve(mission.mission_id, approver="owner")
    assert approved.executed is True
    assert approved.decision is not None
    assert approved.decision.human_authority_present is True

    # A second approval must not produce a second external effect.
    with pytest.raises(EngineError):
        engine.approve(mission.mission_id, approver="owner")

    replay = engine.replay(mission.mission_id)
    publishes = [
        r
        for r in replay["receipts"]
        if r["event"] == "EFFECT_EXECUTED"
        and r["detail"].get("action") == "publish_notice"
    ]
    assert len(publishes) == 1


def test_denial_prevents_execution(engine: FleetProofEngine) -> None:
    mission = engine.create_mission("Assess incidents and publish remediation.")
    engine.advance(mission.mission_id)
    engine.advance(mission.mission_id)
    engine.advance(mission.mission_id)

    refused = engine.deny(
        mission.mission_id, approver="owner", reason="Legal review pending."
    )
    assert refused.executed is False
    assert refused.mission.state is MissionState.DENIED

    replay = engine.replay(mission.mission_id)
    assert not [
        r
        for r in replay["receipts"]
        if r["event"] == "EFFECT_EXECUTED"
        and r["detail"].get("action") == "publish_notice"
    ]


# --- Receipt chain ----------------------------------------------------------


def test_replay_verifies_chain(engine: FleetProofEngine) -> None:
    mission = engine.create_mission("Assess incidents and publish remediation.")
    engine.advance(mission.mission_id)
    engine.advance(mission.mission_id)
    engine.advance(mission.mission_id)
    engine.approve(mission.mission_id, approver="owner")

    replay = engine.replay(mission.mission_id)
    assert replay["chain_integrity"] == "VERIFIED"
    assert replay["chain_error"] is None
    assert replay["receipt_count"] > 0

    events = [r["event"] for r in replay["receipts"]]
    assert events[0] == "MISSION_RECEIVED"
    assert "HUMAN_AUTHORITY_GRANTED" in events
    # The grant must precede the publish effect in the recorded order.
    publish_index = max(
        i
        for i, r in enumerate(replay["receipts"])
        if r["event"] == "EFFECT_EXECUTED"
        and r["detail"].get("action") == "publish_notice"
    )
    grant_index = events.index("HUMAN_AUTHORITY_GRANTED")
    assert grant_index < publish_index


def test_chain_detects_tampering() -> None:
    """A modified receipt must fail verification, or the chain proves nothing."""
    store = InMemoryStore()
    store.append_receipt(
        mission_id="msn-1", event="A", actor="x", detail={"n": 1}
    )
    store.append_receipt(
        mission_id="msn-1", event="B", actor="x", detail={"n": 2}
    )
    receipts = store.list_receipts("msn-1")
    verify_chain(receipts)  # baseline: clean chain verifies

    tampered = [
        receipts[0],
        Receipt(
            receipt_id=receipts[1].receipt_id,
            mission_id=receipts[1].mission_id,
            sequence=receipts[1].sequence,
            event="B-ALTERED",
            actor=receipts[1].actor,
            timestamp=receipts[1].timestamp,
            detail=receipts[1].detail,
            parent_digest=receipts[1].parent_digest,
            digest=receipts[1].digest,
        ),
    ]
    with pytest.raises(ChainError):
        verify_chain(tampered)


def test_chain_detects_removal() -> None:
    """Dropping a middle receipt must break the chain."""
    store = InMemoryStore()
    for n in range(3):
        store.append_receipt(
            mission_id="msn-1", event=f"E{n}", actor="x", detail={"n": n}
        )
    receipts = store.list_receipts("msn-1")
    with pytest.raises(ChainError):
        verify_chain([receipts[0], receipts[2]])
