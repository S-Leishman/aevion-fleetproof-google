# Aevion FleetProof

**Airworthiness control for enterprise AI agents.**

An enterprise agent control tower that discovers registered agents, routes work
to the least-privileged agent that can do it, gates consequential actions on
explicit human authority, and preserves a hash-chained, replayable record of
what happened.

Submitted to the **All Things Agentic** hackathon, category **Fortified
Enterprise Fleet**.

## Status

**PROTOTYPE — student submission.** A working vertical slice, deployed and
verified on Google Cloud, not a production system. Downstream effects are
simulated; the control plane is real. See
[Honest limitations](#honest-limitations) for the precise boundary.

This is an experimental project in a student namespace. For the canonical
Aevion LLC engineering estate, see [Aevion-ai](https://github.com/Aevion-ai).

---

## Prior-work disclosure

Aevion FleetProof was created during the All Things Agentic submission period.
The project is informed by pre-existing Aevion concepts around evidence,
policy gating, replayability, and human authority. Any pre-existing code
incorporated into this repository is identified explicitly. The FleetProof
application, Gemini integration, Google Cloud deployment, workflow, tests, and
submission artifacts were created for this hackathon.

**No pre-existing Aevion source files have been copied into this repository.**
Everything under `fleetproof/`, `web/`, `tests/`, and `scripts/` was written
for this submission.

The conceptual lineage is public and worth naming rather than implying. Aevion
maintains [ProofOS](https://github.com/Aevion-ai/ProofOS), which implements a
constitutional halt gate and a receipt chain, and publishes verification SDKs
using Ed25519 signatures. FleetProof is a new application of that family of
ideas to the enterprise agent-fleet problem on Google Cloud. Where the
pre-existing work is stronger, this README says so plainly rather than
claiming parity — see [Honest limitations](#honest-limitations); in particular
FleetProof's receipts are hash-chained only, whereas Aevion's prior evidence
work uses Ed25519 signatures.

What is genuinely new here, and not inherited from prior Aevion work:

- the Gemini/GenAI SDK routing layer and the model-routes/policy-authorizes split
- the consequence-class action catalog, owned by the catalog rather than the
  proposing agent
- least-privilege agent selection with a deterministic fallback
- per-mission, per-action scoped human grants
- exactly-once execution semantics bound to approval
- the Cloud Run + Firestore deployment and the control-tower UI

---

## The problem this addresses

Enterprise agent platforms answer "how do I run agents safely?" FleetProof
answers a different question:

> Which agent was authorized, what evidence supported the decision, what effect
> actually occurred, and who held the authority?

An agent fleet that can act on production systems needs a control plane where
the consequential boundary is enforced by construction, not by prompt
instructions the model may ignore or be manipulated past.

## The core design decision

**The language model routes. It does not authorize.**

Gemini selects which registered agent should attempt a step and explains why.
A separate, deterministic policy engine decides whether the resulting action
may execute. `fleetproof/policy.py` performs no I/O and imports no model code,
so authority decisions are pure functions of `(action, agent, grants)`.

Consequences:

- A prompt-injected or hallucinating model cannot escalate privilege. The worst
  it can do is nominate an unsuitable agent, which the policy engine converts
  into `DENY`.
- The model's answer is constrained to the enum of registered agent IDs. Any
  other response is rejected and replaced by a least-privilege deterministic
  selection.
- If Gemini is unreachable, routing degrades to the deterministic fallback. The
  authority gate is unaffected, because it never depended on the model.

Actions are classified by a catalog, not by the agent proposing them:

| Class | Meaning | Human authority |
|---|---|---|
| `OBSERVATION` | Reads state, no effect | Not required |
| `REVERSIBLE` | Writes state the fleet can undo | Not required |
| `CONSEQUENTIAL` | Externally visible or hard to undo | **Required** |

An agent cannot relabel an external publish as internal drafting, because it
proposes an action *by name* and the catalog assigns the consequence class.

## The demonstrated workflow

```
MISSION RECEIVED
  -> agents discovered (3 registered)
  -> least-privileged capable agent selected  [Gemini]
  -> read_telemetry    OBSERVATION   -> executes unattended
  -> draft_notice      REVERSIBLE    -> executes unattended
  -> publish_notice    CONSEQUENTIAL -> POLICY CHECK
       CAPABILITY      PRESENT
       POLICY          PASS
       OWNER AUTHORITY ABSENT
       VERDICT         HOLD          -> no external effect occurs
  -> human approves
       OWNER AUTHORITY VERIFIED
       EFFECT          EXECUTED (exactly once)
  -> replay verifies the hash-chained receipt record
```

The held action is the point of the demo: capability is present and policy
passes, and it still does not execute.

## Architecture

```
                    Browser (control tower HUD)
                              |
                      Cloud Run (FastAPI)
                              |
        +---------------------+----------------------+
        |                     |                      |
   planner.py            engine.py              policy.py
  Gemini 3.5 Flash    state machine +        deterministic
  via GenAI SDK       receipt writer         authority gate
  (routing only)                            (no I/O, no model)
        |                     |
        |                     v
        |                  store.py
        |          +--------------------+
        |          |                    |
        |     FirestoreStore     InMemoryStore
        |     (deployed state)   (tests, no network)
        v
   least-privilege
   deterministic
   fallback
```

Firestore layout:

```
missions/{mission_id}
missions/{mission_id}/receipts/{000000..}   append-only, hash-chained
missions/{mission_id}/grants/{action_id}    explicit human authorizations
```

### Evidence chain

Each receipt commits to its predecessor's SHA-256 digest over canonical JSON,
so the chain detects modification of a past entry and removal of an entry from
the middle. Receipt documents use a zero-padded sequence as the document ID and
are written with a create-only operation, so a duplicate sequence collides
rather than silently forking the chain.

**Scope of that claim:** this proves integrity relative to a retained head
digest. It is not a signature scheme and does not prove authorship. Signed
receipts would be the next increment.

## Required stack

| Requirement | Implementation |
|---|---|
| Gemini 3.5 or newer | `gemini-3.5-flash` via Vertex AI |
| Google agent framework | Google GenAI SDK (`google-genai`) |
| Google Cloud service | Cloud Run (hosting) and Firestore (state) |

**Configuration note worth recording:** Gemini 3.5+ publisher models resolve on
the `global` Vertex endpoint. Regional endpoints (`us-central1`, `us-east5`)
return `404 NOT_FOUND` for these models even though they appear in
`models.list()`. `GOOGLE_CLOUD_LOCATION` therefore defaults to `global`.

## Run it locally

```bash
pip install -r requirements.txt
export GOOGLE_CLOUD_PROJECT=your-project     # PowerShell: $env:GOOGLE_CLOUD_PROJECT="..."
gcloud auth application-default login
uvicorn fleetproof.web:app --reload --port 8080
```

Open <http://127.0.0.1:8080>. Without `GOOGLE_CLOUD_PROJECT` the app runs on
ephemeral in-memory state and `/api/health` says so explicitly.

## Tests

```bash
python -m pytest tests -q
```

The suite deliberately makes no network or model calls: whether a consequential
action can bypass human authority must not depend on Gemini being reachable.

Verified properties include:

- A consequential action holds without authority; no effect is recorded.
- A grant scoped to another mission or another action does not authorize.
- A missing capability yields `DENY`, not `HOLD` (escalating to a human for an
  impossible action would be a false ask).
- The registry ceiling holds even when a human grant is present.
- An approved action executes exactly once; duplicate approval is refused.
- The chain detects both tampering and removal.
- **Non-vacuity:** mutating only the grant flips `HOLD` to `ALLOW`, proving the
  hold is caused by the missing grant rather than by always holding.

## Live cloud proof

`scripts/live_demo.py` exercises real Gemini routing and real Firestore, and
exits non-zero if any required property fails, so it functions as a gate rather
than a demo script. Output is written to `evidence/LIVE_RUN.json`.

```bash
python scripts/live_demo.py
```

## Honest limitations

- **Downstream effects are simulated.** No real customer status page is written.
  The control plane — routing, consequence classification, the authority gate,
  approval, and the receipt chain — is real and runs on every action. The UI
  states this.
- **Receipts are hash-chained, not signed.** Integrity, not authorship.
- **Three agents, one mission plan.** This is a deliberate vertical slice. Agent
  registry management, identity federation, and multi-mission scheduling are
  extension points, not implemented claims.
- **Single-writer receipt appends.** Serialized in-process; the create-only
  document write is what prevents cross-process chain forks.

## Author

Scott Leishman — Arizona State University student, Aevion LLC.

## License

MIT. See [LICENSE](LICENSE).
