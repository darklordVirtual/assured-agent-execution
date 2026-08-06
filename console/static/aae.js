/* AAE Assurance Console — read-only client.
 *
 * No framework, no build step, no external request. Every fetch below is a
 * GET against this origin; the console has no route that writes, so there is
 * nothing here that could be tricked into performing one.
 *
 * Three rules the rendering follows:
 *
 *   Plain language first, the engine's own token second. "Held for approval"
 *   is what a person needs; VERIFY is what the audit record says. Both appear,
 *   and the canonical value is monospaced so it reads as a machine value
 *   rather than a sentence.
 *
 *   Nothing is drawn that the record does not contain. The chain spine breaks
 *   where `contiguous` is false rather than drawing an unbroken line over a
 *   gap, because an unbroken line would be a claim.
 *
 *   Raw JSON is never the primary presentation. It lives in the drawer, one
 *   keystroke away and not in the way.
 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

/* Plain language for the engine's canonical decisions. The canonical value is
 * always shown alongside; this replaces nothing in the audit record. */
const DECISION = {
  accept:   "Allowed to act without a human",
  verify:   "Held until a human approved it",
  escalate: "Routed to a higher authority",
  abstain:  "Refused, with nothing offered to approve",
};

/* Ordered by how much each constrains the agent. Drives the bar and the key. */
const DECISION_ORDER = ["accept", "verify", "escalate", "abstain"];

/* Events that are not decisions. Named so the ledger reads as a sequence of
 * things that happened rather than a list of enum values. */
/* The badge for an entry where a control fired. The sentence beneath says
 * what happened; this says what the system did about it, in one word. */
const STOP_LABEL = {
  execution_grant_refused:        "refused",
  execution_binding_refused:      "refused",
  execution_approval_invalidated: "voided",
};

const EVENT = {
  assessed:                        "Assessed",
  approved:                        "Approved by a human",
  execution_authorized:            "Execution authorized",
  execution_result:                "Executed",
  effect_verified:                 "Effect checked against the record",
  execution_grant_refused:         "Execution refused",
  execution_binding_refused:       "Execution refused",
  execution_approval_invalidated:  "Approval voided",
};

/* Every value that reaches innerHTML below passes through here. That is the
 * invariant this file rests on: escape at the interpolation, not at the
 * source, so a new field cannot be added without meeting it. Attributes are
 * the exception — see the allow-lists. */
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const when = (iso) => !iso ? "—" : String(iso).replace("T", " ").slice(0, 19);
const clock = (iso) => !iso ? "—" : String(iso).slice(11, 19);
const short = (v) => String(v ?? "").slice(0, 12);

/* `decision` and `env` land inside attributes, which esc() alone does not
 * make safe. Constrained to known values rather than trusted. */
const DECISIONS = new Set(DECISION_ORDER);
const ENVS = new Set(["prod", "staging", "dev", "test"]);
const safeDecision = (d) => DECISIONS.has(d) ? d : "";
const safeEnv = (e) => ENVS.has(e) ? e : "";

async function get(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error((body && body.error) || "The console could not load this.");
    error.correlationId = body && body.correlation_id;
    throw error;
  }
  return body;
}

function problem(node, error) {
  node.innerHTML =
    `<div class="problem">${esc(error.message)}` +
    (error.correlationId
      ? `<span class="cid">Reference ${esc(error.correlationId)} — this id is in the console log.</span>`
      : "") + `</div>`;
}

/* ── The assurance strip ────────────────────────────────────────────────
 * Always visible. It qualifies everything else on the page, so it is a
 * header rather than a destination.
 */

function renderStrip(a) {
  const items = [];
  const add = (state, label, value) => items.push(
    `<span class="strip-item" data-state="${state === "good" ? "ok" : esc(state)}">` +
    `<span class="strip-dot"></span>${esc(label)} <b>${esc(value)}</b></span>`);

  if (!a.reachable) {
    $("#strip").innerHTML =
      `<span class="strip-item" data-state="bad"><span class="strip-dot"></span>` +
      `<b>Control plane unreachable</b></span>`;
    return;
  }
  add("good", "engine", `REMORA ${a.core.version || "?"}`);
  add(a.runtime_mode === "production" ? "good" : "warn",
      "mode", a.runtime_mode || "unknown");
  add(a.tool_policy_enforced && a.tool_policy_pinned ? "good" : "bad",
      "tool policy",
      a.tool_policy_pinned ? "signed + pinned"
        : a.tool_policy_enforced ? "signed, NOT pinned" : "not enforced");
  if (a.audit.checked) {
    add(a.audit.verified ? "good" : "bad", "chain",
        `${a.audit.records ?? "?"} entries ${a.audit.verified ? "verified" : "NOT VERIFIED"}`);
  } else {
    add("warn", "chain", "not checked");
  }
  add("good", "credentials", "read-only");
  $("#strip").innerHTML = items.join("");
}

/* Deviations only. A list that always has entries is a list nobody reads. */
function renderAttention(items) {
  const box = $("#attention");
  if (!items || !items.length) { box.hidden = true; box.innerHTML = ""; return; }
  box.hidden = false;
  box.innerHTML =
    `<h2>${items.length === 1 ? "One thing needs attention" : `${items.length} things need attention`}</h2>` +
    `<ul>${items.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>`;
}

/* ── Decision mix ───────────────────────────────────────────────────────
 * The distribution rather than four stat cards: how the decisions divide is
 * the question worth asking of an autonomous agent.
 */

function renderMix(counts) {
  const total = DECISION_ORDER.reduce((n, d) => n + (counts[d] || 0), 0);
  const bar = $("#mix-bar");
  const key = $("#mix-key");

  if (!total) {
    bar.replaceChildren();
    key.replaceChildren(Object.assign(document.createElement("li"),
      { textContent: "No decisions recorded yet." }));
    bar.setAttribute("aria-label", "No decisions recorded yet");
    return;
  }

  // Sizes are set through the CSSOM, never through a style attribute. The
  // page's CSP is `style-src 'self'`, which silently drops inline style
  // attributes — the DOM keeps the attribute, the style never applies, and the
  // bar renders collapsed with no console error. Scripted style assignment is
  // not affected by that rule, so this is the one path that works without
  // weakening the policy. Do not reintroduce `style="…"` here.
  bar.replaceChildren(...DECISION_ORDER
    .filter((d) => counts[d] > 0)
    .map((d) => {
      const share = (counts[d] / total) * 100;
      const seg = document.createElement("span");
      seg.className = "mix-seg";
      seg.dataset.d = d;
      seg.style.width = `${share.toFixed(2)}%`;
      seg.title = `${d} — ${counts[d]} of ${total}`;
      // The count only fits above a threshold. Below it the segment is still
      // drawn and still in the key, which is where the number is guaranteed.
      seg.textContent = share > 9 ? String(counts[d]) : "";
      return seg;
    }));

  bar.setAttribute("aria-label", DECISION_ORDER
    .map((d) => `${d} ${counts[d] || 0}`).join(", "));

  key.replaceChildren(...DECISION_ORDER.map((d) => {
    const share = total ? Math.round((counts[d] || 0) / total * 100) : 0;
    const item = document.createElement("li");
    const swatch = document.createElement("i");
    swatch.style.background = `var(--${d})`;
    const name = document.createElement("span");
    name.textContent = `${d} `;
    const value = document.createElement("b");
    value.textContent = String(counts[d] || 0);
    const pct = document.createElement("span");
    pct.textContent = ` (${share}%)`;
    item.append(swatch, name, value, pct);
    return item;
  }));
}

/* ── The chain ──────────────────────────────────────────────────────────
 * The signature element: the audit chain drawn as a chain. A spine segment
 * per entry, dashed where the sequence actually jumps.
 */

let ledgerCache = [];

function entryHTML(e, previous) {
  // A break is real: the entry above is not this entry's immediate successor.
  const broken = previous && (previous.sequence_no - e.sequence_no) !== 1;
  const stop = Boolean(e.intervention);
  const d = safeDecision(e.decision);

  const bits = [
    `<span class="seq">#${esc(e.sequence_no)}</span>`,
    `<span class="when">${esc(clock(e.at))}</span>`,
  ];
  if (stop) {
    // Same grammar as a decision badge, because to an operator this IS the
    // verdict: the point at which a control refused the action.
    bits.push(`<span class="verdict verdict-stop">${esc(STOP_LABEL[e.event] || "refused")}</span>`);
  } else if (d) {
    bits.push(`<span class="verdict" data-d="${d}">${esc(e.decision)}</span>`);
  } else {
    bits.push(`<span class="event-name">${esc(EVENT[e.event] || e.event)}</span>`);
  }
  if (e.tool_name) bits.push(`<span class="tool">${esc(e.tool_name)}</span>`);
  if (e.target_environment) {
    bits.push(`<span class="env" data-env="${safeEnv(e.target_environment)}">` +
              `${esc(e.target_environment)}</span>`);
  }

  let body = "";
  if (stop) {
    body += `<p class="stop-line">${esc(e.intervention)}</p>`;
  } else if (d) {
    body += `<p class="entry-why">${esc(DECISION[e.decision] || "")}` +
            (e.reasons.length
              ? ` — <span class="reason">${esc(e.reasons.join(", "))}</span>`
              : "") + `</p>`;
  } else if (e.effect_status) {
    body += `<p class="entry-why">Outcome ` +
            `<span class="reason">${esc(e.effect_status)}</span></p>`;
  }

  // The whole row opens the entry. A real button rather than a click handler
  // on the <li>, so it is reachable by keyboard and announced as actionable.
  // The chain hash is not repeated here — the spine already says these are
  // links in a chain, and the proposal id is what an operator correlates on
  // when three consecutive entries belong to one run. Both hashes are in the
  // drawer.
  return (
    `<li class="entry" data-seq="${esc(e.sequence_no)}" ` +
    `data-decision="${d}" data-stop="${stop}" data-broken="${broken}">` +
    `<span class="entry-node" aria-hidden="true"></span>` +
    `<button class="entry-open" data-seq="${esc(e.sequence_no)}">` +
      `<span class="entry-main">` +
        `<span class="entry-top">${bits.join("")}</span>` +
        body +
      `</span>` +
      `<span class="entry-side">` +
        (e.proposal_id
          ? `<span class="corr">${esc(short(e.proposal_id))}</span>` : "") +
        `<span class="chev" aria-hidden="true">›</span>` +
      `</span>` +
    `</button>` +
    `</li>`
  );
}

function renderChain(ledger) {
  ledgerCache = ledger.entries;
  const list = $("#chain");

  if (!ledger.entries.length) {
    // Two different empties. A fresh install has governed nothing yet and
    // needs telling how to change that; a filter that matches nothing is a
    // finding in its own right and should not read as a broken page.
    list.innerHTML = ledger.total_entries === 0
      ? `<li class="empty"><b>Nothing has been governed yet.</b><br>` +
        `Run <code>python run.py scenarios</code> to exercise the six ` +
        `decision paths, then reload this page.</li>`
      : `<li class="empty">No control has refused anything. ` +
        `Every proposal so far was either allowed or held.</li>`;
    $("#chain-foot").textContent = "";
    return;
  }

  list.innerHTML = ledger.entries
    .map((e, i) => entryHTML(e, ledger.entries[i - 1]))
    .join("");

  const shown = ledger.entries.length;
  $("#chain-foot").textContent = ledger.contiguous
    ? `Showing the ${shown} most recent of ${ledger.total_entries} chain entries.`
    : `Showing ${shown} of ${ledger.total_entries} chain entries. The spine is ` +
      `dashed where the sequence is not consecutive — a filter is applied, or ` +
      `there is a gap.`;
}

/* ── Drawer ─────────────────────────────────────────────────────────── */

let lastFocus = null;

function openDrawer(title, rows) {
  lastFocus = document.activeElement;
  $("#drawer-title").textContent = title;
  $("#drawer-body").innerHTML =
    `<dl>${rows.map(([k, v]) =>
      `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>`;
  $("#drawer").hidden = false;
  $("#scrim").hidden = false;
  $("#drawer-close").focus();
}

function closeDrawer() {
  $("#drawer").hidden = true;
  $("#scrim").hidden = true;
  if (lastFocus) lastFocus.focus();
}

function showEntry(seq) {
  const e = ledgerCache.find((x) => String(x.sequence_no) === String(seq));
  if (!e) return;
  openDrawer(`Chain entry #${e.sequence_no}`, [
    ["Recorded at", when(e.at)],
    ["Event", EVENT[e.event] || e.event],
    ...(e.decision ? [["Decision", e.decision]] : []),
    ...(e.reasons.length ? [["Reasons", e.reasons.join("\n")]] : []),
    ...(e.intervention ? [["What the control did", e.intervention]] : []),
    ...(e.tool_name ? [["Tool", e.tool_name]] : []),
    ...(e.target_environment ? [["Target environment", e.target_environment]] : []),
    ...(e.tool_executed !== null && e.tool_executed !== undefined
      ? [["Tool performed a side effect", e.tool_executed ? "yes" : "no"]] : []),
    ...(e.effect_status ? [["Effect verification", e.effect_status]] : []),
    ["Actor", e.actor || "—"],
    ["Proposal", e.proposal_id || "—"],
    ["Entry hash", e.entry_hash],
    ["Links to previous", e.previous_hash || "— (first entry)"],
  ]);
}

/* ── Surfaces ───────────────────────────────────────────────────────── */

async function loadLedger(filter) {
  const box = $("#chain");
  try {
    const query = filter === "interventions" ? "?only=interventions&limit=60"
                                             : "?limit=60";
    const ledger = await get(`/api/ledger${query}`);
    renderMix(ledger.decision_counts);
    renderChain(ledger);
    $("#stopped-count").textContent = ledger.intervention_count || "";
    $("#nav-ledger-count").textContent = ledger.total_entries || "";
  } catch (error) {
    problem(box, error);
  }
}

async function loadOverviewBits() {
  try {
    const overview = await get("/api/overview");
    renderStrip(overview.assurance);
    renderAttention(overview.attention);
    $("#updated").textContent =
      `updated ${clock(overview.assurance.checked_at)}`;
  } catch (error) {
    problem($("#strip"), error);
  }
}

function fact(label, value, note, state) {
  return `<div class="fact"${state ? ` data-state="${
    ["good", "warn", "bad"].includes(state) ? state : ""}"` : ""}>` +
    `<dt>${esc(label)}</dt><dd>${esc(value)}` +
    (note ? `<small>${esc(note)}</small>` : "") + `</dd></div>`;
}

async function loadAssurance() {
  try {
    const a = await get("/api/assurance");
    $("#assurance-facts").innerHTML = [
      fact("Governance engine",
           `REMORA ${a.core.version || "?"}`,
           `commit ${a.core.commit || "?"} · ${a.core.release || "?"} (${a.core.status || "?"})`,
           "good"),
      fact("Runtime mode", a.runtime_mode || "unknown",
           "production enforces the fail-closed prerequisites",
           a.runtime_mode === "production" ? "good" : "warn"),
      fact("Surfaces served", (a.capabilities || []).join(", ") || "none",
           "a disabled surface is unmounted, not merely refused"),
      fact("Tool policy",
           a.tool_policy_pinned ? "signed + pinned"
             : a.tool_policy_enforced ? "signed, NOT pinned" : "not enforced",
           "a signature proves a bundle is authentic, never that it is current",
           a.tool_policy_pinned ? "good" : "bad"),
      fact("Audit chain",
           a.audit.checked
             ? `${a.audit.records ?? "?"} entries · ${a.audit.verified ? "verified" : "NOT VERIFIED"}`
             : "not checked",
           "each entry is hash-linked to the one before it",
           a.audit.checked ? (a.audit.verified ? "good" : "bad") : "warn"),
      fact("Console access", a.console_access,
           "one viewer token; no route here writes", "good"),
      fact("Database credentials", a.database_credential,
           "SELECT only, on both the record and the chain", "good"),
      fact("Checked at", when(a.checked_at), "this page holds no cache"),
    ].join("");
  } catch (error) {
    problem($("#assurance-facts"), error);
  }
}

/* A change reads as a transition. `{"from":"normal","to":"low"}` is the shape
 * the tool recorded; "normal → low" is what happened. Anything that is not a
 * from/to pair falls back to its key=value pairs rather than to raw JSON,
 * because a reader should never have to parse punctuation to read a table. */
function change(detail) {
  if (!detail || typeof detail !== "object") return "—";
  const keys = Object.keys(detail);
  if (!keys.length) return "—";
  if ("from" in detail && "to" in detail) return `${detail.from} → ${detail.to}`;
  return keys.map((k) => `${k} ${detail[k]}`).join(" · ");
}

async function loadRecords() {
  try {
    const data = await get("/api/records");

    $("#business-facts").innerHTML = [
      fact("Open work orders", data.open_count),
      fact("Closed", data.closed_count),
      fact("Governed changes", data.events.length,
           "writes this product recorded"),
    ].join("");

    const rows = $("#records tbody");
    rows.innerHTML = data.work_orders.length ? data.work_orders.map((w) =>
      `<tr><td class="id">${esc(w.wo_id)}</td><td>${esc(w.title)}</td>` +
      `<td class="id">${esc(w.asset_id)}</td>` +
      `<td><span class="pill">${esc(w.status)}</span></td>` +
      `<td><span class="pill" data-v="${["high", "critical"].includes(w.priority) ? esc(w.priority) : ""}">${esc(w.priority)}</span></td>` +
      `<td class="id">${esc(w.updated_by)}</td>` +
      `<td class="when">${esc(when(w.updated_at))}</td></tr>`).join("")
      : `<tr><td colspan="7" class="empty">No work orders.</td></tr>`;

    const history = $("#history tbody");
    history.innerHTML = data.events.length ? data.events.map((e) =>
      `<tr><td class="when">${esc(when(e.occurred_at))}</td>` +
      `<td class="id">${esc(e.wo_id)}</td>` +
      `<td class="id">${esc(e.tool_name)}</td>` +
      `<td class="id">${esc(change(e.detail))}</td></tr>`).join("")
      : `<tr><td colspan="4" class="empty">No governed changes yet.</td></tr>`;
  } catch (error) {
    problem($("#business-facts"), error);
  }
}

/* Proposal lookup. A lookup rather than a second feed: the ledger above is
 * already the feed, and this answers "tell me everything about that one". */
async function lookup(id) {
  const box = $("#lookup-result");
  box.innerHTML = `<p class="lede">Looking up ${esc(id)}…</p>`;
  try {
    const life = await get(`/api/proposals/${encodeURIComponent(id)}/lifecycle`);
    const events = life.events || life.lifecycle || [];
    box.innerHTML =
      `<div class="facts facts-wide">` +
      fact("Proposal", id) +
      fact("Recorded steps", Array.isArray(events) ? events.length : "—") +
      `</div>`;
    openDrawer(`Proposal ${id}`,
      [["Lifecycle", JSON.stringify(life, null, 2)]]);
  } catch (error) {
    problem(box, error);
  }
}

/* ── Wiring ─────────────────────────────────────────────────────────── */

const SURFACES = {
  ledger:    () => loadLedger(currentFilter),
  records:   loadRecords,
  assurance: loadAssurance,
};

let currentFilter = "all";

function show(name) {
  if (!SURFACES[name]) name = "ledger";
  $$(".panel").forEach((p) => { p.hidden = p.id !== `s-${name}`; });
  $$(".nav-item").forEach((b) => {
    const active = b.dataset.surface === name;
    b.classList.toggle("is-active", active);
    if (active) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  location.hash = name;
  SURFACES[name]();
}

$$(".nav-item").forEach((b) =>
  b.addEventListener("click", () => show(b.dataset.surface)));

$$(".filter").forEach((b) => b.addEventListener("click", () => {
  currentFilter = b.dataset.filter;
  $$(".filter").forEach((x) => x.classList.toggle("is-active", x === b));
  loadLedger(currentFilter);
}));

// Delegated: the chain is re-rendered on every load, so per-node listeners
// would have to be re-attached each time and one missed re-attach is a dead
// button nobody notices.
$("#chain").addEventListener("click", (event) => {
  const button = event.target.closest(".entry-open");
  if (button) showEntry(button.dataset.seq);
});

$("#lookup").addEventListener("submit", (event) => {
  event.preventDefault();
  const id = $("#pid").value.trim();
  if (id) lookup(id);
});

$("#drawer-close").addEventListener("click", closeDrawer);
$("#scrim").addEventListener("click", closeDrawer);

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("#drawer").hidden) closeDrawer();
  if (event.key === "/" && document.activeElement.tagName !== "INPUT") {
    event.preventDefault();
    show("ledger");
    $("#pid").focus();
  }
});

loadOverviewBits();
show((location.hash || "#ledger").slice(1));
setInterval(loadOverviewBits, 30000);
