"""Print the current state and receipt chain summary for one mission."""

import json
import sys
import urllib.request

BASE = "https://fleetproof-672mjmzgtq-uc.a.run.app"
mission_id = sys.argv[1] if len(sys.argv) > 1 else ""
if not mission_id:
    raise SystemExit("usage: check_mission.py <mission_id>")

with urllib.request.urlopen(
    f"{BASE}/api/missions/{mission_id}/replay", timeout=60
) as response:
    replay = json.load(response)

print(f"chain_integrity: {replay['chain_integrity']}")
print(f"receipts: {replay['receipt_count']}")
print(f"head_digest: {(replay['head_digest'] or '')[:16]}")
for receipt in replay["receipts"]:
    detail = receipt["detail"]
    note = (
        detail.get("effect")
        or detail.get("verdict")
        or detail.get("action_id")
        or ""
    )
    print(f"  {receipt['sequence']:2}. {receipt['event']:26} {str(note)[:60]}")
