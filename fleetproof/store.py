"""Persistence for missions, receipts, and authority grants.

Two implementations behind one protocol:

* `InMemoryStore` — used by tests, so authority invariants are verified
  without network flakiness deciding whether a security property holds.
* `FirestoreStore` — the deployed store, satisfying the hackathon's Google
  Cloud service requirement and providing durable cross-session state.

`append_receipt` is the integrity-critical operation. It reads the current
chain head and links the new receipt to it, so callers cannot accidentally
fork the chain by supplying a stale parent.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from .domain import AuthorityGrant, Mission, MissionState
from .receipts import GENESIS_DIGEST, Receipt


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Store(Protocol):
    """Durable state operations the engine depends on."""

    def save_mission(self, mission: Mission) -> None: ...

    def load_mission(self, mission_id: str) -> Mission | None: ...

    def list_missions(self) -> list[Mission]: ...

    def append_receipt(
        self, *, mission_id: str, event: str, actor: str, detail: Mapping[str, Any]
    ) -> Receipt: ...

    def list_receipts(self, mission_id: str) -> list[Receipt]: ...

    def add_grant(self, grant: AuthorityGrant) -> None: ...

    def list_grants(self, mission_id: str) -> list[AuthorityGrant]: ...

    def describe(self) -> str: ...


def _mission_from_dict(raw: Mapping[str, Any]) -> Mission:
    return Mission(
        mission_id=str(raw["mission_id"]),
        objective=str(raw["objective"]),
        state=MissionState(str(raw["state"])),
        assigned_agent_id=raw.get("assigned_agent_id"),
        selection_rationale=str(raw.get("selection_rationale") or ""),
        selection_source=str(raw.get("selection_source") or "unknown"),
        completed_actions=list(raw.get("completed_actions") or []),
        pending_action_id=raw.get("pending_action_id"),
    )


class InMemoryStore:
    """Process-local store. Deterministic and dependency-free."""

    def __init__(self) -> None:
        self._missions: dict[str, Mapping[str, Any]] = {}
        self._receipts: dict[str, list[Receipt]] = {}
        self._grants: dict[str, list[AuthorityGrant]] = {}
        self._lock = threading.Lock()

    def save_mission(self, mission: Mission) -> None:
        with self._lock:
            self._missions[mission.mission_id] = dict(mission.to_dict())

    def load_mission(self, mission_id: str) -> Mission | None:
        with self._lock:
            raw = self._missions.get(mission_id)
        return _mission_from_dict(raw) if raw else None

    def list_missions(self) -> list[Mission]:
        with self._lock:
            return [_mission_from_dict(r) for r in self._missions.values()]

    def append_receipt(
        self, *, mission_id: str, event: str, actor: str, detail: Mapping[str, Any]
    ) -> Receipt:
        with self._lock:
            chain = self._receipts.setdefault(mission_id, [])
            parent = chain[-1].digest if chain else GENESIS_DIGEST
            receipt = Receipt(
                receipt_id=new_id("rcpt"),
                mission_id=mission_id,
                sequence=len(chain),
                event=event,
                actor=actor,
                timestamp=utc_now_iso(),
                detail=dict(detail),
                parent_digest=parent,
            ).sealed()
            chain.append(receipt)
            return receipt

    def list_receipts(self, mission_id: str) -> list[Receipt]:
        with self._lock:
            return list(self._receipts.get(mission_id, []))

    def add_grant(self, grant: AuthorityGrant) -> None:
        with self._lock:
            self._grants.setdefault(grant.mission_id, []).append(grant)

    def list_grants(self, mission_id: str) -> list[AuthorityGrant]:
        with self._lock:
            return list(self._grants.get(mission_id, []))

    def describe(self) -> str:
        return "InMemoryStore (ephemeral)"


class FirestoreStore:
    """Firestore-backed store.

    Layout:
        missions/{mission_id}
        missions/{mission_id}/receipts/{sequence padded}
        missions/{mission_id}/grants/{grant_id}

    Receipts use a zero-padded sequence as the document ID so natural document
    ordering matches chain order, and so a duplicate sequence write collides
    rather than silently forking the chain.
    """

    def __init__(self, project: str, database: str | None = None) -> None:
        from google.cloud import firestore

        kwargs: dict[str, Any] = {"project": project}
        if database:
            kwargs["database"] = database
        self._db = firestore.Client(**kwargs)
        self._project = project
        self._database = database or "(default)"
        self._lock = threading.Lock()

    def _mission_ref(self, mission_id: str):
        return self._db.collection("missions").document(mission_id)

    def save_mission(self, mission: Mission) -> None:
        self._mission_ref(mission.mission_id).set(dict(mission.to_dict()))

    def load_mission(self, mission_id: str) -> Mission | None:
        snap = self._mission_ref(mission_id).get()
        if not snap.exists:
            return None
        return _mission_from_dict(snap.to_dict() or {})

    def list_missions(self) -> list[Mission]:
        return [
            _mission_from_dict(doc.to_dict() or {})
            for doc in self._db.collection("missions").stream()
        ]

    def append_receipt(
        self, *, mission_id: str, event: str, actor: str, detail: Mapping[str, Any]
    ) -> Receipt:
        # Serialize appends within this process; the padded-sequence document
        # ID plus a create-only write is what protects against cross-process
        # races producing two receipts at the same sequence.
        with self._lock:
            existing = self.list_receipts(mission_id)
            parent = existing[-1].digest if existing else GENESIS_DIGEST
            sequence = len(existing)
            receipt = Receipt(
                receipt_id=new_id("rcpt"),
                mission_id=mission_id,
                sequence=sequence,
                event=event,
                actor=actor,
                timestamp=utc_now_iso(),
                detail=dict(detail),
                parent_digest=parent,
            ).sealed()
            doc = (
                self._mission_ref(mission_id)
                .collection("receipts")
                .document(f"{sequence:06d}")
            )
            doc.create(receipt.to_dict())
            return receipt

    def list_receipts(self, mission_id: str) -> list[Receipt]:
        docs = (
            self._mission_ref(mission_id)
            .collection("receipts")
            .order_by("sequence")
            .stream()
        )
        return [Receipt.from_dict(d.to_dict() or {}) for d in docs]

    def add_grant(self, grant: AuthorityGrant) -> None:
        self._mission_ref(grant.mission_id).collection("grants").document(
            f"{grant.action_id}"
        ).set(
            {
                "granted_by": grant.granted_by,
                "mission_id": grant.mission_id,
                "action_id": grant.action_id,
                "granted_at": grant.granted_at,
            }
        )

    def list_grants(self, mission_id: str) -> list[AuthorityGrant]:
        docs = self._mission_ref(mission_id).collection("grants").stream()
        out: list[AuthorityGrant] = []
        for doc in docs:
            raw = doc.to_dict() or {}
            out.append(
                AuthorityGrant(
                    granted_by=str(raw.get("granted_by") or "unknown"),
                    mission_id=str(raw.get("mission_id") or mission_id),
                    action_id=str(raw.get("action_id") or doc.id),
                    granted_at=str(raw.get("granted_at") or ""),
                )
            )
        return out

    def describe(self) -> str:
        return f"FirestoreStore(project={self._project}, database={self._database})"
