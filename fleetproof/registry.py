"""The fleet: three registered agents and the catalog of actions they may take.

The action catalog is the single authority on consequence classification. An
agent proposes an action *by name*; the catalog decides how severe it is. This
separation is the point — a compromised or over-eager agent cannot relabel an
external publish as internal drafting.
"""

from __future__ import annotations

from .domain import Action, Agent, Capability, Consequence

# --- Capabilities -----------------------------------------------------------

READ_TELEMETRY = Capability(
    name="read_telemetry",
    description="Query fleet incident telemetry and summarize findings.",
    max_consequence=Consequence.OBSERVATION,
)

DRAFT_NOTICE = Capability(
    name="draft_notice",
    description="Compose an internal draft remediation notice.",
    max_consequence=Consequence.REVERSIBLE,
)

PUBLISH_NOTICE = Capability(
    name="publish_notice",
    description="Publish a notice to the external customer status page.",
    max_consequence=Consequence.CONSEQUENTIAL,
)

# --- The fleet --------------------------------------------------------------

TELEMETRY_ANALYST = Agent(
    agent_id="telemetry-analyst",
    display_name="Telemetry Analyst",
    role="Reads incident telemetry. Cannot write anything.",
    capabilities=(READ_TELEMETRY,),
)

REMEDIATION_WRITER = Agent(
    agent_id="remediation-writer",
    display_name="Remediation Writer",
    role="Drafts internal remediation notices. Cannot publish externally.",
    capabilities=(READ_TELEMETRY, DRAFT_NOTICE),
)

STATUS_PUBLISHER = Agent(
    agent_id="status-publisher",
    display_name="Status Publisher",
    role=(
        "Registered to publish to the external status page. Every publish is "
        "consequential and therefore gated on human authority."
    ),
    capabilities=(DRAFT_NOTICE, PUBLISH_NOTICE),
)

FLEET: tuple[Agent, ...] = (
    TELEMETRY_ANALYST,
    REMEDIATION_WRITER,
    STATUS_PUBLISHER,
)


def agent_by_id(agent_id: str) -> Agent | None:
    for agent in FLEET:
        if agent.agent_id == agent_id:
            return agent
    return None


# --- Action catalog ---------------------------------------------------------

ACTION_CATALOG: dict[str, Action] = {
    "read_telemetry": Action(
        action_id="act-read-telemetry",
        name="read_telemetry",
        description="Read the open incident backlog for the fleet.",
        consequence=Consequence.OBSERVATION,
        target="internal://telemetry/incidents",
    ),
    "draft_notice": Action(
        action_id="act-draft-notice",
        name="draft_notice",
        description="Draft the customer-facing remediation notice internally.",
        consequence=Consequence.REVERSIBLE,
        target="internal://drafts/remediation-notice",
    ),
    "publish_notice": Action(
        action_id="act-publish-notice",
        name="publish_notice",
        description="Publish the remediation notice to the public status page.",
        consequence=Consequence.CONSEQUENTIAL,
        target="https://status.example-enterprise.com/incidents",
    ),
}

MISSION_PLAN: tuple[str, ...] = ("read_telemetry", "draft_notice", "publish_notice")
"""The ordered action sequence for the demonstration mission."""


def action_by_name(name: str) -> Action | None:
    return ACTION_CATALOG.get(name)


def agents_with_capability(name: str) -> tuple[Agent, ...]:
    return tuple(agent for agent in FLEET if agent.capability(name) is not None)
