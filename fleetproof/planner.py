"""Agent selection via Gemini, with a deterministic fallback.

Authority boundary, stated once and enforced structurally: the model chooses
*which registered agent* should attempt a mission step and explains why. It
does not decide whether an action is permitted. Policy evaluation happens in
`policy.evaluate`, which never imports this module.

Consequences of that split:
  * A prompt-injected or hallucinating model cannot escalate privilege. The
    worst it can do is nominate an agent that lacks the capability, which the
    policy engine turns into DENY.
  * The model's answer is constrained to an enum of registered agent IDs; any
    other response is rejected and the deterministic fallback is used.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from .registry import FLEET, agents_with_capability

DEFAULT_MODEL = os.environ.get("FLEETPROOF_MODEL", "gemini-3.5-flash")
DEFAULT_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
"""Gemini 3.5+ publisher models resolve on the `global` endpoint. Regional
endpoints return 404 for these models, which was verified before the build."""


@dataclass(frozen=True)
class Selection:
    """Which agent to use, why, and where the decision came from."""

    agent_id: str
    rationale: str
    source: str
    model: str | None = None


def _fallback(capability: str, reason: str) -> Selection:
    """Deterministic least-privilege selection.

    Chooses the *narrowest* registered agent that holds the capability, so the
    fallback never widens the blast radius relative to what the model might
    have picked.
    """
    candidates = agents_with_capability(capability)
    if not candidates:
        return Selection(
            agent_id="",
            rationale=f"No registered agent holds capability '{capability}'.",
            source="deterministic-fallback",
        )
    narrowest = min(candidates, key=lambda a: len(a.capabilities))
    return Selection(
        agent_id=narrowest.agent_id,
        rationale=(
            f"Least-privilege deterministic selection: {narrowest.display_name} "
            f"is the narrowest registered holder of '{capability}'. {reason}"
        ),
        source="deterministic-fallback",
    )


def _fleet_description() -> str:
    lines = []
    for agent in FLEET:
        caps = ", ".join(
            f"{c.name} (max {c.max_consequence.name})" for c in agent.capabilities
        )
        lines.append(f"- {agent.agent_id}: {agent.role} Capabilities: {caps}")
    return "\n".join(lines)


PROMPT = """You are the routing component of an enterprise agent control tower.

Registered fleet:
{fleet}

Mission objective:
{objective}

The next step requires the capability: {capability}

Select the single registered agent that should attempt this step. Prefer the
agent with the least privilege that still holds the required capability.

You are NOT deciding whether the action is authorized. A separate
deterministic policy engine decides that. Do not comment on authorization.

Respond with JSON only, no code fences:
{{"agent_id": "<one of the registered agent ids>", "rationale": "<one sentence>"}}
"""


def select_agent(
    *, objective: str, capability: str, project: str | None = None
) -> Selection:
    """Ask Gemini to route the step; fall back deterministically on any problem."""
    project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        return _fallback(capability, "No GOOGLE_CLOUD_PROJECT configured.")

    valid_ids = {a.agent_id for a in agents_with_capability(capability)}
    if not valid_ids:
        return _fallback(capability, "No candidate agents.")

    try:
        # A stale/invalid API key in the environment would otherwise send the
        # SDK down the Developer API path; force the Vertex transport.
        for stale in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
            os.environ.pop(stale, None)
        from google import genai

        client = genai.Client(
            vertexai=True, project=project, location=DEFAULT_LOCATION
        )
        response = client.models.generate_content(
            model=DEFAULT_MODEL,
            contents=PROMPT.format(
                fleet=_fleet_description(),
                objective=objective,
                capability=capability,
            ),
        )
        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[-1] if "\n" in text else text
            text = text.replace("json", "", 1).strip()
        parsed = json.loads(text)
        agent_id = str(parsed.get("agent_id", "")).strip()
        rationale = str(parsed.get("rationale", "")).strip()

        # Constrain the model to the registered enum. Anything else is a
        # routing failure, not an instruction to be followed.
        if agent_id not in valid_ids:
            return _fallback(
                capability,
                f"Model proposed unregistered or ineligible agent '{agent_id}'; "
                "rejected and replaced by least-privilege selection.",
            )
        return Selection(
            agent_id=agent_id,
            rationale=rationale or "Model provided no rationale.",
            source="gemini",
            model=DEFAULT_MODEL,
        )
    except Exception as exc:  # noqa: BLE001 - routing must degrade, not crash
        return _fallback(
            capability,
            f"Model routing unavailable ({type(exc).__name__}); "
            "used deterministic selection.",
        )
