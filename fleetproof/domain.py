"""Core domain types for FleetProof.

This module is deliberately free of cloud and network dependencies so that
the authority invariants can be tested deterministically.

The central idea: an action's *consequence class* is a property of the action
itself, not of the agent requesting it. Authority is checked against the
consequence class, so no amount of agent capability or credential can move a
consequential action past the gate without an explicit human grant.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Mapping


class Consequence(enum.Enum):
    """How far an action's effects reach, and whether they can be undone.

    Ordered least to most severe. The ordering matters: policy compares
    against a threshold, so adding a more severe class later cannot
    accidentally fall below an existing threshold.
    """

    OBSERVATION = 1
    """Reads state. No effect on the world."""

    REVERSIBLE = 2
    """Writes state the fleet itself can undo (drafts, internal notes)."""

    CONSEQUENTIAL = 3
    """Externally visible or hard to undo. Requires human authority."""

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Consequence):
            return NotImplemented
        return self.value < other.value

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Consequence):
            return NotImplemented
        return self.value >= other.value


class Verdict(enum.Enum):
    """The policy engine's decision about a proposed action."""

    ALLOW = "ALLOW"
    HOLD = "HOLD"
    DENY = "DENY"


class MissionState(enum.Enum):
    """Lifecycle of a mission through the control tower."""

    RECEIVED = "RECEIVED"
    DISCOVERING = "DISCOVERING"
    ASSIGNED = "ASSIGNED"
    ACTION_PROPOSED = "ACTION_PROPOSED"
    HELD = "HELD"
    EXECUTED = "EXECUTED"
    DENIED = "DENIED"
    COMPLETE = "COMPLETE"


@dataclass(frozen=True)
class Capability:
    """A single thing an agent is registered to be able to do.

    `max_consequence` is the ceiling the *registry* grants this capability.
    It never by itself authorizes a consequential action; it only describes
    what the agent is built to attempt.
    """

    name: str
    description: str
    max_consequence: Consequence


@dataclass(frozen=True)
class Agent:
    """A registered fleet member."""

    agent_id: str
    display_name: str
    role: str
    capabilities: tuple[Capability, ...]

    def capability(self, name: str) -> Capability | None:
        for cap in self.capabilities:
            if cap.name == name:
                return cap
        return None

    def capability_names(self) -> tuple[str, ...]:
        return tuple(cap.name for cap in self.capabilities)


@dataclass(frozen=True)
class Action:
    """A concrete action an agent proposes to take.

    `consequence` is assigned by the action catalog, not by the proposing
    agent, which is what prevents an agent from self-declaring an external
    publish as merely reversible.
    """

    action_id: str
    name: str
    description: str
    consequence: Consequence
    target: str

    @property
    def requires_human_authority(self) -> bool:
        return self.consequence >= Consequence.CONSEQUENTIAL


@dataclass(frozen=True)
class AuthorityGrant:
    """An explicit human authorization for one action on one mission.

    Scoped deliberately narrowly: a grant names both the mission and the
    action, so it cannot be replayed against a different action.
    """

    granted_by: str
    mission_id: str
    action_id: str
    granted_at: str

    def covers(self, mission_id: str, action_id: str) -> bool:
        return self.mission_id == mission_id and self.action_id == action_id


@dataclass(frozen=True)
class Decision:
    """The outcome of a policy evaluation, with its reasoning preserved."""

    verdict: Verdict
    action_id: str
    consequence: Consequence
    capability_present: bool
    policy_pass: bool
    human_authority_present: bool
    rationale: str
    policy_version: str

    def to_dict(self) -> Mapping[str, object]:
        return {
            "verdict": self.verdict.value,
            "action_id": self.action_id,
            "consequence": self.consequence.name,
            "capability_present": self.capability_present,
            "policy_pass": self.policy_pass,
            "human_authority_present": self.human_authority_present,
            "rationale": self.rationale,
            "policy_version": self.policy_version,
        }


@dataclass
class Mission:
    """A unit of work routed across the fleet."""

    mission_id: str
    objective: str
    state: MissionState = MissionState.RECEIVED
    assigned_agent_id: str | None = None
    selection_rationale: str = ""
    selection_source: str = "unknown"
    completed_actions: list[str] = field(default_factory=list)
    pending_action_id: str | None = None

    def to_dict(self) -> Mapping[str, object]:
        return {
            "mission_id": self.mission_id,
            "objective": self.objective,
            "state": self.state.value,
            "assigned_agent_id": self.assigned_agent_id,
            "selection_rationale": self.selection_rationale,
            "selection_source": self.selection_source,
            "completed_actions": list(self.completed_actions),
            "pending_action_id": self.pending_action_id,
        }
