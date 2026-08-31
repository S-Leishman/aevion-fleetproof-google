"use strict";

const state = { missionId: null, held: false };

const el = (id) => document.getElementById(id);

async function api(path, options) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || `${response.status} ${response.statusText}`);
  }
  return body;
}

// --- HUD -------------------------------------------------------------------

async function loadHealth() {
  try {
    const health = await api("/api/health");
    el("chip-store").innerHTML = `STORE <b>${
      health.store.startsWith("Firestore") ? "FIRESTORE" : "IN-MEMORY"
    }</b>`;
    el("chip-model").innerHTML = `MODEL <b>${health.model}</b>`;
  } catch (err) {
    el("chip-store").innerHTML = `STORE <b>ERROR</b>`;
  }
}

function setAuthority(verdict) {
  const chip = el("chip-authority");
  chip.innerHTML = `AUTHORITY <b class="${verdict}">${verdict}</b>`;
}

// --- Fleet -----------------------------------------------------------------

async function loadFleet() {
  const data = await api("/api/fleet");
  el("fleet").innerHTML = data.agents
    .map(
      (agent) => `
      <div class="agent" data-agent="${agent.agent_id}">
        <h3>${agent.display_name}</h3>
        <div class="role">${agent.role}</div>
        <div class="caps">${agent.capabilities
          .map(
            (cap) =>
              `<span class="cap ${cap.max_consequence}" title="${cap.description}">${cap.name} · ${cap.max_consequence}</span>`
          )
          .join("")}</div>
      </div>`
    )
    .join("");
}

function highlightAgent(agentId) {
  document.querySelectorAll(".agent").forEach((node) => {
    node.classList.toggle("active", node.dataset.agent === agentId);
  });
}

// --- Gate ------------------------------------------------------------------

function renderGate(action, decision) {
  const panel = el("gate-panel");
  if (!decision) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const yn = (value) =>
    value ? '<b class="yes">PRESENT</b>' : '<b class="no">ABSENT</b>';
  const verdictClass =
    decision.verdict === "ALLOW" ? "yes" : decision.verdict === "HOLD" ? "hold" : "no";
  el("gate-grid").innerHTML = `
    <div class="gate-cell"><span>ACTION</span><b>${action ? action.name : "—"}</b></div>
    <div class="gate-cell"><span>CONSEQUENCE</span><b>${decision.consequence}</b></div>
    <div class="gate-cell"><span>CAPABILITY</span>${yn(decision.capability_present)}</div>
    <div class="gate-cell"><span>POLICY</span>${
      decision.policy_pass ? '<b class="yes">PASS</b>' : '<b class="no">FAIL</b>'
    }</div>
    <div class="gate-cell"><span>OWNER AUTHORITY</span>${yn(
      decision.human_authority_present
    )}</div>
    <div class="gate-cell"><span>VERDICT</span><b class="${verdictClass}">${
    decision.verdict
  }</b></div>`;
  el("gate-rationale").textContent = decision.rationale;
  el("gate-actions").hidden = decision.verdict !== "HOLD";
}

// --- Timeline --------------------------------------------------------------

function summarize(receipt) {
  const d = receipt.detail || {};
  if (receipt.event === "POLICY_DECISION") {
    return `${d.verdict} · ${d.consequence} · authority ${
      d.human_authority_present ? "PRESENT" : "ABSENT"
    }`;
  }
  if (receipt.event === "EFFECT_EXECUTED") return d.effect || d.action || "";
  if (receipt.event === "AGENT_SELECTED")
    return `${d.agent_id} via ${d.selection_source} — ${d.rationale || ""}`;
  if (receipt.event === "ACTION_PROPOSED")
    return `${d.action} → ${d.target} (${d.consequence})`;
  if (receipt.event === "MISSION_RECEIVED") return d.objective || "";
  if (receipt.event === "HUMAN_AUTHORITY_GRANTED") return `action ${d.action_id}`;
  if (receipt.event === "HUMAN_AUTHORITY_REFUSED") return d.reason || "";
  return JSON.stringify(d);
}

async function loadReplay() {
  if (!state.missionId) return;
  const replay = await api(`/api/missions/${state.missionId}/replay`);
  const badge = el("integrity");
  badge.textContent = `CHAIN ${replay.chain_integrity}`;
  badge.className = `integrity ${replay.chain_integrity}`;
  el("timeline").innerHTML = replay.receipts.length
    ? replay.receipts
        .map(
          (r) => `<li class="${r.event}">
            <span class="ev">${r.sequence}. ${r.event}</span>
            <span class="actor"> · ${r.actor}</span>
            <span class="dg"> · ${r.digest.slice(0, 12)}…</span>
            <span class="det">${summarize(r)}</span>
          </li>`
        )
        .join("")
    : '<li class="empty">No receipts yet.</li>';
}

// --- Mission flow ----------------------------------------------------------

function applyStep(step) {
  const mission = step.mission;
  state.missionId = mission.mission_id;
  state.held = mission.state === "HELD";
  el("mission-meta").textContent =
    `${mission.mission_id} · state ${mission.state} · agent ` +
    `${mission.assigned_agent_id || "—"} · routing ${mission.selection_source}`;
  highlightAgent(mission.assigned_agent_id);
  renderGate(step.action, step.decision);
  if (step.decision) setAuthority(step.decision.verdict);
  el("btn-advance").disabled = state.held || mission.state === "COMPLETE";
}

async function guard(fn) {
  try {
    await fn();
  } catch (err) {
    el("gate-rationale").textContent = `ERROR: ${err.message}`;
    el("gate-panel").hidden = false;
  }
}

el("btn-create").addEventListener("click", () =>
  guard(async () => {
    const body = JSON.stringify({ objective: el("objective").value });
    const data = await api("/api/missions", { method: "POST", body });
    state.missionId = data.mission.mission_id;
    state.held = false;
    el("mission-meta").textContent = `${data.mission.mission_id} · state ${data.mission.state}`;
    el("btn-advance").disabled = false;
    el("btn-replay").disabled = false;
    el("gate-panel").hidden = true;
    setAuthority("IDLE");
    await loadReplay();
  })
);

el("btn-advance").addEventListener("click", () =>
  guard(async () => {
    const step = await api(`/api/missions/${state.missionId}/advance`, {
      method: "POST",
    });
    applyStep(step);
    await loadReplay();
  })
);

el("btn-approve").addEventListener("click", () =>
  guard(async () => {
    const step = await api(`/api/missions/${state.missionId}/approve`, {
      method: "POST",
      body: JSON.stringify({ approver: "owner" }),
    });
    applyStep(step);
    await loadReplay();
  })
);

el("btn-deny").addEventListener("click", () =>
  guard(async () => {
    const step = await api(`/api/missions/${state.missionId}/deny`, {
      method: "POST",
      body: JSON.stringify({ approver: "owner", reason: "Legal review pending." }),
    });
    applyStep(step);
    await loadReplay();
  })
);

el("btn-replay").addEventListener("click", () => guard(loadReplay));

loadHealth();
loadFleet();
