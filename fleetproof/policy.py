"""Deterministic authority policy.

Design rule: this module contains no model calls and no I/O. A language model
may decide *which* agent should attempt a mission, but it never decides
whether a consequential action is permitted. That decision is a pure function
of (action, agent, grants), so it is fully testable and cannot be argued out
of by a persuasive model response.

The gate is fail-closed: any path that does not affirmatively establish human
authority for a consequential action yields HOLD.
"""

from __future__ import annotations

from .domain import (
    Action,
    Agent,
    AuthorityGrant,
    Consequence,
    Decision,
    Verdict,
)

POLICY_VERSION = "fleetproof-policy-1.0.0"

CONSEQUENTIAL_THRESHOLD = Consequence.CONSEQUENTIAL
"""Actions at or above this class require an explicit human grant."""


def evaluate(
    *,
    action: Action,
    agent: Agent,
    mission_id: str,
    grants: tuple[AuthorityGrant, ...] = (),
) -> Decision:
    """Decide whether `agent` may execute `action` on `mission_id`.

    Evaluation order is deliberate. Capability is checked before authority so
    that a missing capability produces DENY (the agent cannot do this at all)
    rather than HOLD (a human could unblock it). Escalating to a human for an
    action the agent could never perform would be a false ask.
    """
    capability = agent.capability(action.name)
    capability_present = capability is not None

    if not capability_present:
        return Decision(
            verdict=Verdict.DENY,
            action_id=action.action_id,
            consequence=action.consequence,
            capability_present=False,
            policy_pass=False,
            human_authority_present=False,
            rationale=(
                f"Agent {agent.agent_id} has no registered capability "
                f"'{action.name}'. Registered: {list(agent.capability_names())}."
            ),
            policy_version=POLICY_VERSION,
        )

    # The registry ceiling is an upper bound on what this agent may attempt.
    # An agent registered only for reversible work cannot reach for a
    # consequential action even with human authority present, because the
    # grant authorizes the action, not a capability the agent lacks.
    assert capability is not None
    within_registry_ceiling = capability.max_consequence >= action.consequence
    if not within_registry_ceiling:
        return Decision(
            verdict=Verdict.DENY,
            action_id=action.action_id,
            consequence=action.consequence,
            capability_present=True,
            policy_pass=False,
            human_authority_present=False,
            rationale=(
                f"Action consequence {action.consequence.name} exceeds the "
                f"registered ceiling {capability.max_consequence.name} for "
                f"capability '{action.name}' on agent {agent.agent_id}."
            ),
            policy_version=POLICY_VERSION,
        )

    if action.consequence < CONSEQUENTIAL_THRESHOLD:
        return Decision(
            verdict=Verdict.ALLOW,
            action_id=action.action_id,
            consequence=action.consequence,
            capability_present=True,
            policy_pass=True,
            human_authority_present=False,
            rationale=(
                f"{action.consequence.name} action within registered ceiling; "
                "no human authority required."
            ),
            policy_version=POLICY_VERSION,
        )

    # Consequential path: require an explicit, narrowly scoped grant.
    authority = any(g.covers(mission_id, action.action_id) for g in grants)
    if authority:
        return Decision(
            verdict=Verdict.ALLOW,
            action_id=action.action_id,
            consequence=action.consequence,
            capability_present=True,
            policy_pass=True,
            human_authority_present=True,
            rationale=(
                "Consequential action authorized by explicit human grant "
                f"scoped to mission {mission_id} and action {action.action_id}."
            ),
            policy_version=POLICY_VERSION,
        )

    return Decision(
        verdict=Verdict.HOLD,
        action_id=action.action_id,
        consequence=action.consequence,
        capability_present=True,
        policy_pass=True,
        human_authority_present=False,
        rationale=(
            "CAPABILITY PRESENT. POLICY PASS. OWNER AUTHORITY ABSENT. "
            f"Action '{action.name}' is {action.consequence.name} and targets "
            f"{action.target}. Held pending explicit human approval."
        ),
        policy_version=POLICY_VERSION,
    )
