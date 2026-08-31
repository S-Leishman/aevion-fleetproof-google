"""Verify the deployed Cloud Run service end to end over HTTP.

Proves the deployed container -- not just the local process -- can reach
Firestore and Vertex, hold a consequential action, execute it once on
approval, and return a verified receipt chain.

Writes evidence/DEPLOYED_RUN.json. Exits non-zero on any failure.
"""

from __future__ import annotations

import json
import pathlib
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "").rstrip("/")
if not BASE:
    print("usage: verify_deployed.py <service-url>")
    raise SystemExit(2)


def call(path: str, payload: dict | None = None) -> dict:
    url = f"{BASE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method="POST" if payload is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(f"{path} -> HTTP {exc.code}: {body[:300]}") from exc


def main() -> int:
    failures: list[str] = []
    record: dict[str, object] = {"base_url": BASE}

    health = call("/api/health")
    record["health"] = health
    print(f"health: {health}")
    if not str(health.get("store", "")).startswith("Firestore"):
        failures.append(
            f"deployed service is not using Firestore: {health.get('store_note')}"
        )

    index_status = None
    try:
        with urllib.request.urlopen(f"{BASE}/", timeout=60) as response:
            index_status = response.status
            html = response.read().decode(errors="replace")
        if "FLEETPROOF" not in html.upper():
            failures.append("index page did not render the UI shell")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"index page failed: {exc}")
    record["index_status"] = index_status

    created = call("/api/missions", {"objective": (
        "Assess the open fleet incident backlog and publish a remediation "
        "notice to the customer status page."
    )})
    mission_id = created["mission"]["mission_id"]
    record["mission_id"] = mission_id
    print(f"mission: {mission_id}")

    for expected in ("read_telemetry", "draft_notice"):
        step = call(f"/api/missions/{mission_id}/advance", {})
        name = (step.get("action") or {}).get("name")
        print(f"  advance -> {name} executed={step['executed']}")
        if name != expected or not step["executed"]:
            failures.append(f"step {expected} did not execute (got {name})")

    held = call(f"/api/missions/{mission_id}/advance", {})
    verdict = (held.get("decision") or {}).get("verdict")
    print(f"  advance -> publish verdict={verdict} executed={held['executed']}")
    record["hold_decision"] = held.get("decision")
    if held["executed"]:
        failures.append("CRITICAL: consequential action executed without authority")
    if verdict != "HOLD":
        failures.append(f"expected HOLD, got {verdict}")

    approved = call(f"/api/missions/{mission_id}/approve", {"approver": "owner"})
    print(f"  approve -> executed={approved['executed']}")
    if not approved["executed"]:
        failures.append("approved action did not execute")

    duplicate_refused = False
    try:
        call(f"/api/missions/{mission_id}/approve", {"approver": "owner"})
    except RuntimeError as exc:
        duplicate_refused = "409" in str(exc)
        print("  duplicate approve refused (HTTP 409)")
    if not duplicate_refused:
        failures.append("CRITICAL: duplicate approval was not refused")
    record["duplicate_refused"] = duplicate_refused

    replay = call(f"/api/missions/{mission_id}/replay")
    print(
        f"  replay -> {replay['chain_integrity']} receipts={replay['receipt_count']}"
    )
    record["chain_integrity"] = replay["chain_integrity"]
    record["receipt_count"] = replay["receipt_count"]
    record["head_digest"] = replay["head_digest"]
    if replay["chain_integrity"] != "VERIFIED":
        failures.append(f"chain integrity {replay['chain_integrity']}")

    publishes = [
        r for r in replay["receipts"]
        if r["event"] == "EFFECT_EXECUTED"
        and r["detail"].get("action") == "publish_notice"
    ]
    if len(publishes) != 1:
        failures.append(f"expected 1 publish effect, found {len(publishes)}")

    routing = sorted({
        r["detail"].get("selection_source")
        for r in replay["receipts"] if r["event"] == "AGENT_SELECTED"
    } - {None})
    record["routing_sources"] = routing
    print(f"  routing sources: {routing}")

    record["failures"] = failures
    record["result"] = "PASS" if not failures else "FAIL"
    out = (
        pathlib.Path(__file__).resolve().parent.parent
        / "evidence"
        / "DEPLOYED_RUN.json"
    )
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")

    print(f"\nresult: {record['result']}")
    for failure in failures:
        print(f"  FAILURE: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
