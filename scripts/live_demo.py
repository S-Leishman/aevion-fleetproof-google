"""Live end-to-end proof against real Gemini routing and real Firestore.

Unlike the unit tests, this exercises the actual cloud dependencies, so it is
the evidence that the deployed configuration works rather than just the logic.

Writes evidence/LIVE_RUN.json. Exits non-zero if any required property of the
run fails, so it can serve as a gate rather than a demo script.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fleetproof.domain import MissionState, Verdict  # noqa: E402
from fleetproof.engine import EngineError, FleetProofEngine  # noqa: E402
from fleetproof.store import FirestoreStore  # noqa: E402

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "aevion-shield")
OBJECTIVE = (
    "Assess the open fleet incident backlog and publish a remediation notice "
    "to the customer status page."
)


def main() -> int:
    failures: list[str] = []
    record: dict[str, object] = {"project": PROJECT, "objective": OBJECTIVE}

    store = FirestoreStore(PROJECT)
    engine = FleetProofEngine(store, project=PROJECT)

    mission = engine.create_mission(OBJECTIVE)
    record["mission_id"] = mission.mission_id
    print(f"mission: {mission.mission_id}")

    steps: list[dict[str, object]] = []

    # Step 1 + 2: observation and reversible work should execute unattended.
    for expected in ("read_telemetry", "draft_notice"):
        step = engine.advance(mission.mission_id)
        action_name = step.action.name if step.action else None
        source = step.mission.selection_source
        print(f"  advance -> {action_name} executed={step.executed} routing={source}")
        steps.append(step.to_dict())
        if action_name != expected:
            failures.append(f"expected {expected}, got {action_name}")
        if not step.executed:
            failures.append(f"{expected} did not execute")

    # Step 3: the consequential action must HOLD.
    held = engine.advance(mission.mission_id)
    steps.append(held.to_dict())
    print(
        f"  advance -> {held.action.name if held.action else None} "
        f"verdict={held.decision.verdict.value if held.decision else None}"
    )
    if held.executed:
        failures.append("CRITICAL: consequential action executed without authority")
    if not held.decision or held.decision.verdict is not Verdict.HOLD:
        failures.append("consequential action did not HOLD")
    if held.mission.state is not MissionState.HELD:
        failures.append(f"mission state is {held.mission.state}, expected HELD")

    # Approval executes it once.
    approved = engine.approve(mission.mission_id, approver="owner")
    steps.append(approved.to_dict())
    print(f"  approve -> executed={approved.executed}")
    if not approved.executed:
        failures.append("approved action failed to execute")

    # A second approval must be refused.
    double_approve_refused = False
    try:
        engine.approve(mission.mission_id, approver="owner")
    except EngineError as exc:
        double_approve_refused = True
        print(f"  second approve refused: {exc}")
    if not double_approve_refused:
        failures.append("CRITICAL: duplicate approval was not refused")

    # Replay must verify, read back from Firestore.
    replay = engine.replay(mission.mission_id)
    print(
        f"  replay -> {replay['chain_integrity']} "
        f"receipts={replay['receipt_count']}"
    )
    if replay["chain_integrity"] != "VERIFIED":
        failures.append(f"chain integrity {replay['chain_integrity']}")

    publishes = [
        r
        for r in replay["receipts"]
        if r["event"] == "EFFECT_EXECUTED"
        and r["detail"].get("action") == "publish_notice"
    ]
    if len(publishes) != 1:
        failures.append(f"expected exactly 1 publish effect, found {len(publishes)}")

    routing_sources = {
        r["detail"].get("selection_source")
        for r in replay["receipts"]
        if r["event"] == "AGENT_SELECTED"
    }
    record["routing_sources"] = sorted(s for s in routing_sources if s)
    record["steps"] = steps
    record["replay"] = replay
    record["double_approval_refused"] = double_approve_refused
    record["failures"] = failures
    record["result"] = "PASS" if not failures else "FAIL"

    out = pathlib.Path(__file__).resolve().parent.parent / "evidence" / "LIVE_RUN.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")

    print(f"\nrouting sources observed: {record['routing_sources']}")
    print(f"result: {record['result']}")
    if failures:
        for f in failures:
            print(f"  FAILURE: {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
