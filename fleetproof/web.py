"""HTTP surface for the FleetProof control tower.

Store selection is explicit and reported by /healthz: if Firestore is
configured it is used, otherwise the in-memory store is used and the UI says
so. The app never silently degrades to ephemeral state while implying
durability.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import EngineError, FleetProofEngine
from .planner import DEFAULT_LOCATION, DEFAULT_MODEL
from .registry import FLEET, MISSION_PLAN, ACTION_CATALOG
from .store import FirestoreStore, InMemoryStore, Store

STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE") or None


def build_store() -> tuple[Store, str]:
    """Return the store and a human-readable note about why it was chosen."""
    if not PROJECT:
        return InMemoryStore(), (
            "GOOGLE_CLOUD_PROJECT is unset; using ephemeral in-memory state."
        )
    try:
        store = FirestoreStore(PROJECT, FIRESTORE_DATABASE)
        # Touch the backend so a misconfiguration surfaces at startup rather
        # than in the middle of a demo.
        store.list_missions()
        return store, "Firestore connected."
    except Exception as exc:  # noqa: BLE001 - reported in /healthz
        return InMemoryStore(), (
            f"Firestore unavailable ({type(exc).__name__}: {exc}); "
            "using ephemeral in-memory state."
        )


STORE, STORE_NOTE = build_store()
ENGINE = FleetProofEngine(STORE, project=PROJECT or None)

app = FastAPI(
    title="Aevion FleetProof",
    description="Airworthiness control for enterprise AI agents.",
    version="0.1.0",
)


class MissionRequest(BaseModel):
    objective: str = Field(
        default=(
            "Assess the open fleet incident backlog and publish a remediation "
            "notice to the customer status page."
        ),
        min_length=8,
        max_length=500,
    )


class ApprovalRequest(BaseModel):
    approver: str = Field(default="owner", min_length=1, max_length=120)


class DenialRequest(BaseModel):
    approver: str = Field(default="owner", min_length=1, max_length=120)
    reason: str = Field(default="Not authorized.", min_length=1, max_length=500)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "store": STORE.describe(),
        "store_note": STORE_NOTE,
        "project": PROJECT or None,
        "model": DEFAULT_MODEL,
        "model_location": DEFAULT_LOCATION,
    }


@app.get("/api/fleet")
def fleet() -> dict[str, Any]:
    return {
        "agents": [
            {
                "agent_id": a.agent_id,
                "display_name": a.display_name,
                "role": a.role,
                "capabilities": [
                    {
                        "name": c.name,
                        "description": c.description,
                        "max_consequence": c.max_consequence.name,
                    }
                    for c in a.capabilities
                ],
            }
            for a in FLEET
        ],
        "plan": [
            {
                "capability": name,
                "action_id": ACTION_CATALOG[name].action_id,
                "consequence": ACTION_CATALOG[name].consequence.name,
                "target": ACTION_CATALOG[name].target,
                "description": ACTION_CATALOG[name].description,
            }
            for name in MISSION_PLAN
        ],
    }


@app.get("/api/missions")
def list_missions() -> dict[str, Any]:
    return {"missions": [dict(m.to_dict()) for m in STORE.list_missions()]}


@app.post("/api/missions")
def create_mission(request: MissionRequest) -> dict[str, Any]:
    mission = ENGINE.create_mission(request.objective)
    return {"mission": dict(mission.to_dict())}


@app.post("/api/missions/{mission_id}/advance")
def advance(mission_id: str) -> dict[str, Any]:
    try:
        return dict(ENGINE.advance(mission_id).to_dict())
    except EngineError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/missions/{mission_id}/approve")
def approve(mission_id: str, request: ApprovalRequest) -> dict[str, Any]:
    try:
        return dict(ENGINE.approve(mission_id, approver=request.approver).to_dict())
    except EngineError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/missions/{mission_id}/deny")
def deny(mission_id: str, request: DenialRequest) -> dict[str, Any]:
    try:
        return dict(
            ENGINE.deny(
                mission_id, approver=request.approver, reason=request.reason
            ).to_dict()
        )
    except EngineError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/missions/{mission_id}/replay")
def replay(mission_id: str) -> dict[str, Any]:
    return dict(ENGINE.replay(mission_id))


@app.get("/")
def index() -> Any:
    candidate = STATIC_DIR / "index.html"
    if candidate.exists():
        return FileResponse(candidate)
    return JSONResponse({"error": "UI not found"}, status_code=404)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
