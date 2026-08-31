# Devpost submission copy — Aevion FleetProof

Paste-ready. Every claim here is backed by something in the repo or by a
verified run; nothing is aspirational. If a field is owner-only it says so.

---

## Project name

Aevion FleetProof

## Tagline

Airworthiness control for enterprise AI agents — the model routes, deterministic
policy authorizes, humans hold the consequential boundary.

## Category

Fortified Enterprise Fleet

---

## What it does

FleetProof is an agent control tower. It discovers registered agents, routes
each mission step to the least-privileged agent that can perform it, and gates
consequential actions on explicit human authority — preserving a hash-chained,
replayable receipt of what was proposed, what policy decided, who approved, and
what actually ran.

The demo turns on one moment. An agent proposes publishing to an external
status page. Capability is **present**. Policy **passes**. The action still does
not execute, because owner authority is **absent**:

```
ACTION       publish_notice
CONSEQUENCE  CONSEQUENTIAL
CAPABILITY   PRESENT
POLICY       PASS
OWNER AUTH   ABSENT
VERDICT      HOLD
```

A human approves, it executes exactly once, and replay verifies the chain.

## The problem

Enterprise agent platforms mostly answer "how do I run agents safely?"
FleetProof answers a different question: *which agent was authorized, what
evidence supported that decision, what effect actually occurred, and who held
the authority?*

A fleet that can act on production systems needs the consequential boundary
enforced by construction — not by prompt instructions a model may ignore, or be
manipulated past.

## How it's built

**The language model routes. It does not authorize.**

Gemini 3.5 Flash selects which registered agent should attempt a step and
explains why. A separate deterministic policy engine decides whether the
resulting action may execute. `fleetproof/policy.py` performs no I/O and
imports no model code, so an authority decision is a pure function of
`(action, agent, grants)`.

That separation buys three concrete properties:

- A prompt-injected or hallucinating model cannot escalate privilege. The worst
  it can do is nominate an unsuitable agent, which policy converts to `DENY`.
- The model's answer is constrained to the enum of registered agent IDs.
  Anything else is rejected and replaced by deterministic least-privilege
  selection.
- If Gemini is unreachable, routing degrades to the deterministic fallback and
  the authority gate is unaffected — it never depended on the model.

Actions are classified by a catalog rather than by the agent proposing them, so
an agent cannot relabel an external publish as internal drafting.

| Class | Meaning | Human authority |
|---|---|---|
| `OBSERVATION` | Reads state, no effect | Not required |
| `REVERSIBLE` | Writes state the fleet can undo | Not required |
| `CONSEQUENTIAL` | Externally visible or hard to undo | **Required** |

## Required stack

| Requirement | Implementation |
|---|---|
| Gemini 3.5 or newer | `gemini-3.5-flash` via Vertex AI |
| Google agent framework | Google GenAI SDK (`google-genai`) |
| Google Cloud service | Cloud Run (hosting) + Firestore (state) |

## What we learned

Gemini 3.5+ publisher models resolve only on the **`global`** Vertex endpoint.
Regional endpoints (`us-central1`, `us-east5`) return `404 NOT_FOUND` for these
models *even though they appear in `models.list()`*. That cost real debugging
time and is not obvious from the model listing, so `GOOGLE_CLOUD_LOCATION`
defaults to `global`.

Second lesson, on testing: the whole test suite makes no network or model calls
on purpose. Whether a consequential action can bypass human authority must not
depend on Gemini being reachable.

The test that matters most is the **non-vacuity** one. It is easy to write a
gate that looks safe because it always holds. So one test mutates *only* the
grant and asserts `HOLD` flips to `ALLOW` — proving the hold is caused by the
missing authority rather than by refusing everything.

## Honest limitations

- **Downstream effects are simulated.** No real customer status page is written.
  The control plane — routing, consequence classification, the authority gate,
  approval, the receipt chain — is real and runs on every action. The UI says so.
- **Receipts are hash-chained, not signed.** That proves integrity relative to a
  retained head digest. It is not a signature scheme and does not prove
  authorship. Signed receipts are the next increment.
- **Three agents, one mission plan.** A deliberate vertical slice. Registry
  management, identity federation, and multi-mission scheduling are extension
  points, not implemented claims.
- **Prototype, student submission.** Not a production system.

## Prior-work disclosure

FleetProof was created during the submission period. It is informed by
pre-existing Aevion concepts around evidence, policy gating, replayability, and
human authority. **No pre-existing Aevion source files were copied into this
repository** — everything under `fleetproof/`, `web/`, `tests/`, and `scripts/`
was written for this submission.

The conceptual lineage is public and worth naming rather than implying: Aevion
maintains [ProofOS](https://github.com/Aevion-ai/ProofOS), which implements a
constitutional halt gate and a receipt chain. Where that prior work is stronger,
this submission says so — its evidence work uses Ed25519 signatures, whereas
FleetProof's receipts are hash-chained only.

Genuinely new here: the GenAI SDK routing layer and the model-routes /
policy-authorizes split; the consequence-class action catalog owned by the
catalog rather than the proposing agent; least-privilege selection with
deterministic fallback; per-mission, per-action scoped human grants;
exactly-once execution bound to approval; and the Cloud Run + Firestore
deployment with the control-tower UI.

---

## Links

| Field | Value |
|---|---|
| Repository | https://github.com/S-Leishman/aevion-fleetproof-google |
| Live demo | https://fleetproof-837076960937.us-central1.run.app |
| Architecture | `docs/ARCHITECTURE.md` |
| Demo video | **OWNER: upload `AEVION_FLEETPROOF_DEMO.mp4`, paste URL** |

## Built with

`python` · `fastapi` · `google-genai` · `gemini-3.5-flash` · `google-cloud-run`
· `google-cloud-firestore` · `vertex-ai` · `docker`

---

## Verified state at time of drafting

Cloud Run revision `fleetproof-00003-7zj`, verified by
`scripts/verify_deployed.py`:

```
health:  Firestore connected, gemini-3.5-flash, location=global
mission: msn-2d3d04a2b896
  read_telemetry   executed=True
  draft_notice     executed=True
  publish          verdict=HOLD  executed=False
  approve       -> executed=True
  duplicate approve refused (HTTP 409)
  replay        -> VERIFIED, receipts=15
  routing sources: ['gemini']
result: PASS
```

Local suite: 14 tests passed, exit 0, no network or model calls.

## Owner actions remaining

1. Upload `AEVION_FLEETPROOF_DEMO.mp4` (1:43, on Desktop) to YouTube or Vimeo,
   set it public or unlisted, and paste the URL into the Devpost video field.
2. Confirm the repository is public.
3. Submit before the deadline.
