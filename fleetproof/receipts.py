"""Append-only, hash-chained execution receipts.

Each receipt commits to its predecessor's digest, so the chain detects both
tampering with a past entry and removal of an entry from the middle. This is
what makes a mission replayable rather than merely logged: the ordering and
content are cryptographically bound.

The chain is intentionally simple (SHA-256 over canonical JSON). It proves
integrity relative to a retained head digest; it is not a signature scheme and
does not prove authorship. That distinction is stated in the README rather
than being quietly implied away.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

GENESIS_DIGEST = "0" * 64


def canonical_json(payload: Mapping[str, Any]) -> str:
    """Serialize deterministically so digests are reproducible."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def digest_of(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Receipt:
    """One immutable record of something that happened."""

    receipt_id: str
    mission_id: str
    sequence: int
    event: str
    actor: str
    timestamp: str
    detail: Mapping[str, Any]
    parent_digest: str
    digest: str = field(default="")

    def body(self) -> Mapping[str, Any]:
        """The fields the digest commits to (everything except the digest)."""
        return {
            "receipt_id": self.receipt_id,
            "mission_id": self.mission_id,
            "sequence": self.sequence,
            "event": self.event,
            "actor": self.actor,
            "timestamp": self.timestamp,
            "detail": self.detail,
            "parent_digest": self.parent_digest,
        }

    def computed_digest(self) -> str:
        return digest_of(self.body())

    def sealed(self) -> "Receipt":
        return Receipt(
            receipt_id=self.receipt_id,
            mission_id=self.mission_id,
            sequence=self.sequence,
            event=self.event,
            actor=self.actor,
            timestamp=self.timestamp,
            detail=self.detail,
            parent_digest=self.parent_digest,
            digest=self.computed_digest(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {**self.body(), "digest": self.digest}

    @staticmethod
    def from_dict(raw: Mapping[str, Any]) -> "Receipt":
        return Receipt(
            receipt_id=str(raw["receipt_id"]),
            mission_id=str(raw["mission_id"]),
            sequence=int(raw["sequence"]),
            event=str(raw["event"]),
            actor=str(raw["actor"]),
            timestamp=str(raw["timestamp"]),
            detail=dict(raw.get("detail") or {}),
            parent_digest=str(raw["parent_digest"]),
            digest=str(raw.get("digest") or ""),
        )


class ChainError(Exception):
    """Raised when a receipt chain fails verification."""


def verify_chain(receipts: Sequence[Receipt]) -> None:
    """Verify sequence continuity, parent linkage, and digest integrity.

    Raises ChainError describing the first inconsistency found.
    """
    expected_parent = GENESIS_DIGEST
    for index, receipt in enumerate(receipts):
        if receipt.sequence != index:
            raise ChainError(
                f"sequence gap at position {index}: receipt claims "
                f"sequence {receipt.sequence}"
            )
        if receipt.parent_digest != expected_parent:
            raise ChainError(
                f"broken link at sequence {receipt.sequence}: parent_digest "
                f"{receipt.parent_digest[:12]}... does not match previous "
                f"digest {expected_parent[:12]}..."
            )
        recomputed = receipt.computed_digest()
        if receipt.digest != recomputed:
            raise ChainError(
                f"tampered content at sequence {receipt.sequence}: stored "
                f"digest {receipt.digest[:12]}... != recomputed "
                f"{recomputed[:12]}..."
            )
        expected_parent = receipt.digest
