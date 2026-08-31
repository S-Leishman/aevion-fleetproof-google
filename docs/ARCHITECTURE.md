# Architecture

## The load-bearing decision

FleetProof separates two things that agent platforms usually conflate:

| Concern | Who decides | Where |
|---|---|---|
| Which agent should attempt this work? | Gemini 3.5 Flash | `planner.py` |
| May the resulting action actually execute? | Deterministic policy | `policy.py` |

`policy.py` performs no I/O and imports no model code. It cannot be reached by
a prompt, and it produces the same verdict for the same inputs every time.
That is what makes the authority gate testable and non-bypassable.

```mermaid
flowchart TB
    subgraph client["Client"]
        UI["Control tower HUD<br/>mission · authority chip · evidence timeline"]
    end

    subgraph run["Cloud Run — FastAPI"]
        API["web.py<br/>HTTP surface"]
        ENGINE["engine.py<br/>mission state machine<br/>writes a receipt per transition"]
        PLANNER["planner.py<br/>agent routing"]
        POLICY["policy.py<br/>deterministic authority gate<br/>no I/O · no model"]
        REGISTRY["registry.py<br/>3 agents + action catalog<br/>owns consequence class"]
    end

    subgraph google["Google Cloud"]
        GEMINI["Vertex AI<br/>gemini-3.5-flash<br/>location: global"]
        FS[("Firestore<br/>missions · receipts · grants")]
    end

    HUMAN(["Human owner"])

    UI -->|"REST"| API
    API --> ENGINE
    ENGINE -->|"which agent?"| PLANNER
    PLANNER -->|"GenAI SDK"| GEMINI
    PLANNER -.->|"model unreachable or<br/>ineligible answer"| FALLBACK["least-privilege<br/>deterministic fallback"]
    FALLBACK --> ENGINE
    ENGINE -->|"may this execute?"| POLICY
    POLICY --> REGISTRY
    ENGINE <-->|"state + receipt chain"| FS
    POLICY -->|"HOLD"| HUMAN
    HUMAN -->|"explicit scoped grant"| ENGINE

    classDef det fill:#14562f,stroke:#3ddc84,color:#fff
    classDef mdl fill:#17456f,stroke:#4da3ff,color:#fff
    classDef hum fill:#6b4a12,stroke:#ffb020,color:#fff
    class POLICY,REGISTRY,FALLBACK det
    class GEMINI,PLANNER mdl
    class HUMAN hum
```

Green is deterministic. Blue is model-driven. Amber is human. Note that no blue
path reaches an effect without passing through green.

## Mission lifecycle

```mermaid
stateDiagram-v2
    [*] --> RECEIVED
    RECEIVED --> DISCOVERING: advance
    DISCOVERING --> ASSIGNED: agent selected
    ASSIGNED --> ACTION_PROPOSED: agent proposes action
    ACTION_PROPOSED --> EXECUTED: ALLOW<br/>(observation / reversible)
    ACTION_PROPOSED --> HELD: HOLD<br/>(consequential, no grant)
    ACTION_PROPOSED --> DENIED: DENY<br/>(no capability / over ceiling)
    HELD --> EXECUTED: human grant + re-evaluation
    HELD --> DENIED: human refusal
    EXECUTED --> DISCOVERING: more plan remains
    EXECUTED --> COMPLETE: plan finished
    DENIED --> [*]
    COMPLETE --> [*]
```

Two details that matter:

- `HELD --> EXECUTED` **re-runs the full policy evaluation** rather than
  trusting that a grant exists. The approval path and the refused path go
  through identical code; only the grant input differs.
- Execution records the action in `completed_actions` before returning, so a
  replayed approval or double-clicked button cannot produce a second external
  effect.

## Authority evaluation order

```mermaid
flowchart TD
    A["Action proposed"] --> B{"Agent registered<br/>for this capability?"}
    B -->|no| DENY1["DENY<br/>agent cannot do this at all"]
    B -->|yes| C{"Consequence within<br/>registered ceiling?"}
    C -->|no| DENY2["DENY<br/>exceeds registry grant"]
    C -->|yes| D{"Consequence ≥<br/>CONSEQUENTIAL?"}
    D -->|no| ALLOW1["ALLOW<br/>no human needed"]
    D -->|yes| E{"Grant scoped to<br/>THIS mission AND<br/>THIS action?"}
    E -->|no| HOLD["HOLD<br/>owner authority absent"]
    E -->|yes| ALLOW2["ALLOW<br/>authority verified"]
```

Capability is checked **before** authority deliberately. A missing capability
yields `DENY`, not `HOLD`, because escalating to a human for an action the agent
could never perform would be a false ask that trains people to rubber-stamp.

The gate is fail-closed: any path that does not affirmatively establish a
matching grant for a consequential action ends at `HOLD`.

## Evidence chain

```
receipt[n].parent_digest == receipt[n-1].digest
receipt[n].digest == SHA256(canonical_json(receipt[n].body))
```

Verification checks sequence continuity, parent linkage, and digest integrity,
so it detects modification of a past entry *and* removal of an entry from the
middle. Receipts are stored under a zero-padded sequence document ID and
written with a create-only operation, so a duplicate sequence collides rather
than silently forking the chain.

**Claim boundary:** this proves integrity relative to a retained head digest.
It is not a signature scheme and does not prove authorship. Ed25519-signed
receipts are the natural next increment.

## Extension points (not implemented)

Named here so they are read as future work rather than shipped capability:

- agent registry management and dynamic registration
- federated agent identity
- multi-mission scheduling and fleet-wide observability
- signed receipts and external anchoring
- real downstream connectors (effects are currently simulated)
