/* AAE Assurance Console — read-only client.
 *
 * No framework, no build step, no external request. Every fetch below is a
 * GET against this origin; the console has no route that writes, so there is
 * nothing here that could be tricked into performing one.
 *
 * Two rules the rendering follows:
 *
 *   Plain language first, the engine's own token second. "Approval required"
 *   is what a person needs; VERIFY is what the audit record says. Both are
 *   shown, and the canonical value is monospaced so it reads as a machine
 *   value rather than a sentence.
 *
 *   Raw JSON is never the primary presentation. It lives in the drawer under
 *   "Technical evidence", one keystroke away and not in the way.
 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

/* Plain language for the engine's canonical decisions. The canonical value is
 * always shown alongside; this replaces nothing in the audit record. */
const DECISION = {
  accept:   { label: "Allowed",                          cls: "d-allowed" },
  verify:   { label: "Approval required",                cls: "d-approval" },
  abstain:  { label: "Blocked — insufficient evidence",  cls: "d-blocked" },
  escalate: { label: "Higher authority required",        cls: "d-authority" },
};

const EFFECT = {
  EFFECT_VERIFIED:        { label: "Effect confirmed in the business record", state: "ok" },
  EFFECT_MISMATCH:        { label: "Effect DID NOT match what was approved",  state: "bad" },
  EFFECT_UNOBSERVABLE:    { label: "Could not observe the effect",            state: "unknown" },
  EFFECT_VERIFIER_FAILED: { label: "The verifier itself failed",              state: "unknown" },
  EFFECT_UNSUPPORTED:     { label: "No verification is declared for this tool", state: "unknown" },
};

/* Every value that reaches innerHTML below passes through here. That is the
 * invariant this file rests on: escape at the interpolation, not at the
 * source, so a new field cannot be added without meeting it. Attributes are
 * the exception — see KINDS. */
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const when = (iso) => !iso ? "—" : String(iso).replace("T", " ").slice(0, 19);

const shortId = (v) => {
  const s = String(v ?? "");
  return /^[0-9a-f]{32,}$/i.test(s) ? s.slice(0, 8) + "…" : s;
};

/* The only four status kinds. Constrained rather than interpolated freely:
 * `kind` lands inside a class attribute, and an attribute is the one place
 * esc() does not protect on its own. Every caller passes a literal today —
 * this keeps that true when one of them stops. */
const KINDS = new Set(["ok", "warn", "bad", "unknown"]);

function state(kind, text, canonical) {
  const safeKind = KINDS.has(kind) ? kind : "unknown";
  return `<span class="state state-${safeKind}">${esc(text)}</span>` +
    (canonical ? `<span class="canon">${esc(canonical)}</span>` : "");
}

async function getJSON(path) {
  const response = await fetch(path, { headers: { "Accept": "application/json" } });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const message = body && body.error ? body.error : `Request failed (${response.status})`;
    const ref = body && body.correlation_id ? body.correlation_id : null;
    throw Object.assign(new Error(message), { correlationId: ref });
  }
  return body;
}

/* ── Navigation ───────────────────────────────────────────────────── */

function show(name) {
  $$(".panel").forEach((p) => { p.hidden = p.id !== `s-${name}`; });
  $$(".nav-item").forEach((b) => {
    const active = b.dataset.surface === name;
    b.classList.toggle("is-active", active);
    if (active) { b.setAttribute("aria-current", "page"); }
    else { b.removeAttribute("aria-current"); }
  });
  location.hash = name;
  if (name === "records") { loadRecords(); }
}

$$(".nav-item").forEach((b) =>
  b.addEventListener("click", () => show(b.dataset.surface)));

/* ── Overview ─────────────────────────────────────────────────────── */

function renderBanner(data) {
  const banner = $("#status-banner");
  const kind = !data ? "loading" : data.all_clear ? "ok"
    : data.assurance && !data.assurance.reachable ? "bad" : "warn";
  banner.className = `banner banner-${kind}`;
  banner.querySelector(".banner-icon").textContent =
    { ok: "✓", warn: "!", bad: "×", loading: "…" }[kind];
  banner.querySelector(".banner-title").textContent =
    data ? data.headline : "Checking enforcement controls";

  if (!data) { $("#status-sub").textContent = ""; return; }
  const a = data.assurance;
  const bits = [
    a.audit.verified === true ? "Audit integrity verified"
      : a.audit.verified === false ? "AUDIT INTEGRITY FAILED"
      : "Audit integrity unknown",
    a.tool_policy_pinned ? "Tool policies active and pinned"
      : a.tool_policy_enforced ? "Tool policies active, not pinned"
      : "Tool policies not enforced",
    "Read-only console",
  ];
  $("#status-sub").textContent = bits.join(" · ");
}

function renderAttention(items) {
  const list = $("#attention");
  list.classList.toggle("is-clear", items.length === 0);
  list.innerHTML = items.length
    ? items.map((t) => `<li>${esc(t)}</li>`).join("")
    : `<li>No deviations. Every control this console can check is in its expected state.</li>`;
}

function renderBusiness(counts) {
  $("#business-facts").innerHTML = `
    <div><dt>Open work orders</dt><dd>${counts.open ?? 0}</dd></div>
    <div><dt>Closed</dt><dd>${counts.closed ?? 0}</dd></div>
    <div><dt>Total records</dt><dd>${counts.total ?? 0}</dd></div>`;
}

async function loadOverview() {
  try {
    const data = await getJSON("/api/overview");
    renderBanner(data);
    renderAttention(data.attention);
    renderBusiness(data.records);
    renderAssurance(data.assurance);
    $("#updated").textContent = `Last checked ${when(data.assurance.checked_at)}`;
    return data;
  } catch (err) {
    renderBanner(null);
    $("#status-banner").className = "banner banner-bad";
    $("#status-banner").querySelector(".banner-icon").textContent = "×";
    $("#status-banner").querySelector(".banner-title").textContent =
      "This console cannot reach the control plane";
    $("#status-sub").textContent = err.message +
      (err.correlationId ? ` (reference ${err.correlationId})` : "");
    $("#updated").textContent = "Not updated";
  }
}

async function loadActivity() {
  const body = $("#activity tbody");
  try {
    const data = await getJSON("/api/records");
    const rows = data.events.slice(0, 8);
    body.innerHTML = rows.length ? rows.map((e) => `
      <tr><td class="when">${esc(when(e.occurred_at))}</td>
          <td class="id">${esc(e.wo_id)}</td>
          <td>${esc(actionLabel(e.tool_name))}<span class="canon">${esc(e.tool_name)}</span></td>
          <td>${esc(e.actor)}</td></tr>`).join("")
      : `<tr class="empty"><td colspan="4">No governed write has been recorded yet.</td></tr>`;
  } catch (err) {
    body.innerHTML = `<tr class="empty"><td colspan="4">${esc(err.message)}</td></tr>`;
  }
}

const ACTION = {
  close_work_order: "Closed a work order",
  create_work_order: "Opened a work order",
  set_work_order_priority: "Changed priority",
  read_work_order: "Read a work order",
  purge_work_order_history: "Purged history",
};
const actionLabel = (tool) => ACTION[tool] || tool;

/* ── Business records ─────────────────────────────────────────────── */

function describeChange(detail) {
  if (!detail || typeof detail !== "object") { return "—"; }
  if ("from" in detail && "to" in detail) { return `${detail.from} → ${detail.to}`; }
  if ("reason" in detail) { return String(detail.reason); }
  if ("title" in detail) { return String(detail.title); }
  const keys = Object.keys(detail);
  return keys.length ? keys.map((k) => `${k}: ${detail[k]}`).join(", ") : "—";
}

async function loadRecords() {
  const orders = $("#records tbody");
  const history = $("#history tbody");
  try {
    const data = await getJSON("/api/records");
    orders.innerHTML = data.work_orders.length ? data.work_orders.map((w) => `
      <tr><td class="id">${esc(w.wo_id)}</td>
          <td>${esc(w.title)}</td>
          <td class="id">${esc(w.asset_id)}</td>
          <td>${state(w.status === "closed" ? "ok" : "unknown", w.status)}</td>
          <td>${esc(w.priority)}</td>
          <td>${esc(w.updated_by)}</td>
          <td class="when">${esc(when(w.updated_at))}</td></tr>`).join("")
      : `<tr class="empty"><td colspan="7">No work orders.</td></tr>`;

    history.innerHTML = data.events.length ? data.events.map((e) => `
      <tr><td class="when">${esc(when(e.occurred_at))}</td>
          <td class="id">${esc(e.wo_id)}</td>
          <td>${esc(actionLabel(e.tool_name))}<span class="canon">${esc(e.tool_name)}</span></td>
          <td>${esc(describeChange(e.detail))}</td></tr>`).join("")
      : `<tr class="empty"><td colspan="4">Nothing has been changed through a governed path yet.</td></tr>`;
  } catch (err) {
    const cell = `<tr class="empty"><td colspan="7">${esc(err.message)}</td></tr>`;
    orders.innerHTML = cell;
    history.innerHTML = cell;
  }
}

/* ── System assurance ─────────────────────────────────────────────── */

function renderAssurance(a) {
  if (!a) { return; }
  const core = a.core || {};
  const audit = a.audit || {};
  $("#assurance-facts").innerHTML = `
    <div><dt>Control plane</dt><dd>${a.reachable
      ? state("ok", a.health === "ok" ? "Operational" : String(a.health || "Reachable"))
      : state("bad", "Not reachable")}</dd></div>

    <div><dt>Runtime mode</dt><dd>${a.runtime_mode === "production"
      ? state("ok", "Production")
      : state("warn", a.runtime_mode || "Unknown")}</dd></div>

    <div><dt>Enabled capability</dt><dd>${state(
      (a.capabilities || []).includes("assess") ? "warn" : "ok",
      (a.capabilities || []).join(", ") || "Unknown",
      "REMORA_ENABLED_SURFACES")}</dd></div>

    <div><dt>Verified engine</dt><dd>${core.version
      ? state("ok", `REMORA ${core.version}`, `commit ${core.commit}`)
      : state("unknown", "Not reported")}</dd></div>

    <div><dt>Engine release</dt><dd>${core.release
      ? state(core.status === "stable" ? "ok" : "warn",
              core.release, core.status || "")
      : state("unknown", "Not reported")}</dd></div>

    <div><dt>Tool policy protection</dt><dd>${
      a.tool_policy_pinned ? state("ok", "Signed and pinned")
      : a.tool_policy_enforced ? state("warn", "Signed, not pinned")
      : state("bad", "Not enforced")}</dd></div>

    <div><dt>Audit integrity</dt><dd>${
      audit.verified === true ? state("ok", "Verified")
      : audit.verified === false ? state("bad", "Verification FAILED")
      : state("unknown", "Not checked")}</dd></div>

    <div><dt>Verified audit records</dt><dd>${
      audit.records != null ? esc(audit.records) : state("unknown", "Unknown")}</dd></div>

    <div><dt>Console access</dt><dd>${state("ok", "Read-only", "viewer")}</dd></div>

    <div><dt>Database credential</dt><dd>${state("ok", "Read-only", "aae_reader")}</dd></div>`;
}

/* ── Decisions ────────────────────────────────────────────────────── */

function problem(message, ref) {
  return `<div class="problem"><p>${esc(message)}</p>` +
    (ref ? `<p class="muted small">Reference <code>${esc(ref)}</code></p>` : "") +
    `</div>`;
}

function renderDecision(id, proposal, lifecycle) {
  const canonical = String(proposal.decision || "").toLowerCase();
  const meaning = DECISION[canonical] || { label: canonical || "Unknown", cls: "" };
  const effect = proposal.effect && proposal.effect.status
    ? EFFECT[proposal.effect.status] : null;

  const reasons = (proposal.reasons || []).length
    ? `<p class="decision-sub">Because: ${esc((proposal.reasons || []).join(", "))}</p>` : "";

  const head = `
    <div class="decision-head ${meaning.cls}">
      <p class="decision-title">${esc(meaning.label)}</p>
      <p class="decision-sub">
        <strong>${esc(proposal.tool_name || "unknown tool")}</strong>
        &middot; proposal <code>${esc(shortId(id))}</code>
        &middot; state <code>${esc(proposal.current_state || "?")}</code>
      </p>
      ${reasons}
      <p class="decision-sub"><span class="canon">${esc(canonical.toUpperCase())}</span></p>
    </div>`;

  const effectBlock = effect ? `
    <dl class="facts"><div>
      <dt>Effect verification</dt>
      <dd>${state(effect.state, effect.label, proposal.effect.status)}</dd>
    </div></dl>` : "";

  const events = (lifecycle && lifecycle.events) || [];
  const timeline = events.length ? `<ul class="timeline">${events.map((e) => {
    const payload = Object.assign({}, e.payload || {}, e);
    const decision = payload.decision ? ` → ${payload.decision}` : "";
    const why = (payload.reasons || []).join(", ");
    return `<li><span class="seq">#${esc(payload.sequence_no ?? "?")}</span>
      <div><div class="what">${esc(payload.event || "event")}${esc(decision)}</div>
      ${why ? `<div class="why">${esc(why)}</div>` : ""}</div></li>`;
  }).join("")}</ul>` : `<p class="muted">No lifecycle events recorded.</p>`;

  return `${head}${effectBlock}
    <h2>What happened, in order</h2>${timeline}
    <p><button id="show-evidence" class="ghost">Technical evidence</button></p>`;
}

async function lookup(id) {
  const target = $("#decision-result");
  target.innerHTML = `<p class="muted">Looking up ${esc(shortId(id))}…</p>`;
  try {
    const proposal = await getJSON(`/api/proposals/${encodeURIComponent(id)}`);
    let lifecycle = null;
    try { lifecycle = await getJSON(`/api/proposals/${encodeURIComponent(id)}/lifecycle`); }
    catch { /* a proposal without a readable trail still renders */ }
    target.innerHTML = renderDecision(id, proposal, lifecycle);
    const button = $("#show-evidence");
    if (button) { button.addEventListener("click", () => openEvidence(id)); }
  } catch (err) {
    target.innerHTML = problem(err.message, err.correlationId);
  }
}

$("#lookup").addEventListener("submit", (event) => {
  event.preventDefault();
  const id = $("#pid").value.trim();
  if (id) { lookup(id); }
});

/* ── Drawer ───────────────────────────────────────────────────────── */

let lastFocus = null;

async function openEvidence(id) {
  lastFocus = document.activeElement;
  const drawer = $("#drawer");
  $("#drawer-body").innerHTML = `<p class="muted">Loading…</p>`;
  drawer.hidden = false;
  $("#drawer-close").focus();
  try {
    const bundle = await getJSON(`/api/proposals/${encodeURIComponent(id)}/evidence`);
    $("#drawer-body").innerHTML =
      `<pre>${esc(JSON.stringify(bundle, null, 2))}</pre>`;
  } catch (err) {
    $("#drawer-body").innerHTML = problem(err.message, err.correlationId);
  }
}

function closeDrawer() {
  $("#drawer").hidden = true;
  if (lastFocus) { lastFocus.focus(); }
}

$("#drawer-close").addEventListener("click", closeDrawer);

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("#drawer").hidden) { closeDrawer(); return; }
  if (event.key === "/" && document.activeElement.tagName !== "INPUT") {
    event.preventDefault();
    show("decisions");
    $("#pid").focus();
  }
});

/* ── Refresh ──────────────────────────────────────────────────────── */

async function refresh() {
  await loadOverview();
  await loadActivity();
  if (!$("#s-records").hidden) { loadRecords(); }
}

/* Paused while the tab is hidden: a console polling a governance API from a
 * forgotten background tab is noise in someone's audit log. */
let timer = null;
function startPolling() {
  stopPolling();
  timer = setInterval(refresh, 15000);
}
function stopPolling() {
  if (timer) { clearInterval(timer); timer = null; }
}
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { stopPolling(); } else { refresh(); startPolling(); }
});

const initial = (location.hash || "#overview").slice(1);
show(["overview", "decisions", "records", "assurance"].includes(initial)
  ? initial : "overview");
refresh();
startPolling();
