/* AAE Lab — compose a call, act in a role, read the envelope.
 *
 * Same rules as the console's client: escape at every interpolation, never
 * emit a style attribute (the CSP drops it silently), and show the engine's
 * canonical value beside the plain-language reading rather than instead of it.
 *
 * One rule of its own: this file never decides anything. It submits calls and
 * renders answers. Every refusal you see rendered here came from the control
 * plane, not from a check in this file — a lab that pre-filtered what it would
 * submit would be demonstrating its own validation, which is not the thing
 * under test.
 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const DECISION_SAY = {
  accept:   "Allowed to act without a human.",
  verify:   "Held. A named human authority must release it before anything runs.",
  escalate: "Refused and routed to a higher authority than an ordinary reviewer.",
  abstain:  "Refused, with nothing offered for anyone to approve.",
};

const DECISIONS = new Set(["accept", "verify", "escalate", "abstain", "refused"]);
const safeD = (d) => DECISIONS.has(d) ? d : "";

/* The grounding signals, in the order the ACCEPT rule reads them, with what
 * each one actually asserts. Written out because "argument_values_supported:
 * true" tells an operator nothing on its own. */
const SIGNALS = {
  tool_matches_goal:
    "the tool's declared effect matches what the authority asked for",
  expected_effect_matches:
    "the effect the call would have matches the effect declared",
  argument_values_supported:
    "the argument values exist in the system of record",
  argument_values_grounded:
    "the argument values are anchored in real data, not invented",
};

let catalogue = null;
let lastOutcome = null;

async function get(path) {
  const r = await fetch(path, { headers: { Accept: "application/json" } });
  const body = await r.json().catch(() => null);
  if (!r.ok) throw new Error((body && body.error) || "request failed");
  return body;
}

async function post(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

/* ── The composer ───────────────────────────────────────────────────── */

function fillSelect(node, options, selected) {
  node.replaceChildren(...options.map(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    if (value === selected) option.selected = true;
    return option;
  }));
}

function currentTool() {
  return (catalogue.tools || []).find((t) => t.tool_id === $("#tool").value);
}

/* Argument inputs come from the tool's own signed schema, so the form can
 * never offer a field the spec does not declare — and the rules are shown
 * rather than enforced here, because watching the engine refuse a bad value
 * is the point. */
function renderArgs() {
  const tool = currentTool();
  const box = $("#args");
  const schema = (tool && tool.argument_schema) || {};
  const properties = schema.properties || {};
  const required = new Set(schema.required || []);

  const names = Object.keys(properties);
  if (!names.length) {
    box.replaceChildren(Object.assign(document.createElement("p"),
      { className: "hint", textContent: "This tool takes no arguments." }));
    return;
  }

  box.replaceChildren(...names.map((name) => {
    const property = properties[name] || {};
    const row = document.createElement("div");
    row.className = "arg-row";

    const label = document.createElement("label");
    label.className = "arg-name";
    label.setAttribute("for", `arg-${name}`);
    label.textContent = name;
    if (required.has(name)) {
      const star = document.createElement("span");
      star.className = "required";
      star.textContent = " required";
      label.append(star);
    }

    const input = document.createElement("input");
    input.id = `arg-${name}`;
    input.dataset.arg = name;
    input.type = "text";
    input.placeholder = property.enum ? property.enum.join(" | ") : "";

    const rule = document.createElement("span");
    rule.className = "arg-rule";
    rule.textContent = [
      property.type,
      property.pattern ? `pattern ${property.pattern}` : "",
      property.enum ? `one of ${property.enum.join(", ")}` : "",
      property.minLength ? `min ${property.minLength}` : "",
    ].filter(Boolean).join(" · ");

    row.append(label, input, rule);
    return row;
  }));
}

function onToolChange() {
  const tool = currentTool();
  $("#tool-note").textContent = tool
    ? `${tool.description} — ${tool.risk_tier} risk, ${tool.action_type}, ` +
      `runs in ${(tool.allowed_targets || []).join(", ") || "any environment"}`
    : "";
  renderArgs();
}

function onRoleChange() {
  $("#role-note").textContent = catalogue.roles[$("#role").value] || "";
}

function onIntentChange() {
  const order = (catalogue.work_orders || [])
    .find((w) => w.id === $("#intent").value);
  $("#intent-note").textContent = order
    ? order.task
    : "No authority cited. The engine has nothing to ground the call in.";
}

/* ── Presets ────────────────────────────────────────────────────────────
 * The benchmark scenarios, as one-click calls. They are already complete and
 * valid against this deployment's contract, so there is nothing to type.
 * Answer keys are not fetched and not shown: a preset that told you what the
 * engine ought to decide would stop this being a place to find out.
 */

function applyPreset(call) {
  $("#tool").value = call.tool;
  onToolChange();                       // rebuilds the argument inputs
  $("#intent").value = call.intent_ref || "";
  onIntentChange();
  $("#env").value = call.target_environment || "staging";

  const args = call.arguments || {};
  $$("#args input[data-arg]").forEach((input) => {
    const value = args[input.dataset.arg];
    input.value = value === undefined ? ""
      : typeof value === "string" ? value : JSON.stringify(value);
  });

  // Arguments the spec does not declare get a row of their own. The composer
  // builds its inputs from the signed schema, so without this the scenario
  // that smuggles an undeclared field would silently lose the field and
  // demonstrate nothing — which is what it did the first time. They are shown
  // rather than hidden, because seeing the extra field is the point.
  const declared = new Set($$("#args input[data-arg]")
    .map((i) => i.dataset.arg));
  const extras = Object.keys(args).filter((name) => !declared.has(name));
  if (extras.length) {
    $("#args").append(...extras.map((name) => {
      const row = document.createElement("div");
      row.className = "arg-row arg-undeclared";

      const label = document.createElement("label");
      label.className = "arg-name";
      label.setAttribute("for", `arg-${name}`);
      label.textContent = name;
      const flag = document.createElement("span");
      flag.className = "undeclared";
      flag.textContent = " not in the schema";
      label.append(flag);

      const input = document.createElement("input");
      input.id = `arg-${name}`;
      input.dataset.arg = name;
      input.type = "text";
      input.value = typeof args[name] === "string"
        ? args[name] : JSON.stringify(args[name]);

      const rule = document.createElement("span");
      rule.className = "arg-rule";
      rule.textContent =
        "the signed ToolSpec does not declare this field — it is sent anyway, "
        + "so the engine gets to decide";

      row.append(label, input, rule);
      return row;
    }));
  }

  $("#compose").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

let presetIndex = new Map();

async function loadPresets() {
  const suites = await get("/api/presets");
  const box = $("#presets");

  box.replaceChildren(...suites.map((suite) => {
    const group = document.createElement("section");
    group.className = "preset-group";

    const heading = document.createElement("h3");
    heading.textContent = suite.title;
    const sub = document.createElement("span");
    sub.textContent = suite.description;
    heading.append(sub);

    const row = document.createElement("div");
    row.className = "preset-row";
    row.append(...suite.cases.map((c) => {
      const key = `${suite.suite}/${c.id}`;
      presetIndex.set(key, c.call);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "preset";
      button.dataset.preset = key;
      button.title = c.scenario;
      if (c.probes) {
        const probe = document.createElement("span");
        probe.className = "probe";
        probe.textContent = c.probes.replace(/_/g, " ");
        button.append(probe);
      }
      button.append(document.createTextNode(c.scenario || c.id));
      return button;
    }));

    group.append(heading, row);
    return group;
  }));
}

$("#presets").addEventListener("click", (event) => {
  const button = event.target.closest(".preset");
  if (!button) return;
  const call = presetIndex.get(button.dataset.preset);
  if (call) applyPreset(call);
});

async function loadCatalogue() {
  catalogue = await get("/api/catalogue");

  fillSelect($("#role"), Object.keys(catalogue.roles).map((r) => [r, r]),
             "operator");
  fillSelect($("#tool"), catalogue.tools.map((t) => [t.tool_id, t.tool_id]));
  fillSelect($("#intent"),
             [["", "— none —"],
              ...catalogue.work_orders.map((w) => [w.id, `${w.id} · ${w.operation}`])]);
  fillSelect($("#env"), catalogue.environments.map((e) => [e, e]), "staging");

  onRoleChange();
  onToolChange();
  onIntentChange();
}

/* ── Rendering the envelope ─────────────────────────────────────────── */

function block(title, note, rows) {
  const pairs = rows.filter(([, v]) => v !== undefined && v !== "");
  if (!pairs.length) return "";
  return `<section class="env-block"><h3>${esc(title)}</h3>` +
    (note ? `<p>${esc(note)}</p>` : "") +
    `<dl>${pairs.map(([k, v]) => {
      const truth = v === true ? "true" : v === false ? "false"
                  : v === null ? "null" : "";
      const shown = v === true ? "yes" : v === false ? "NO"
                  : v === null ? "not evaluated" : v;
      return `<dt>${esc(k)}</dt>` +
             `<dd${truth ? ` data-truth="${truth}"` : ""}>${esc(shown)}</dd>`;
    }).join("")}</dl></section>`;
}

function renderOutcome(payload) {
  lastOutcome = payload;
  const box = $("#outcome");
  const body = payload.response || {};
  const refused = payload.http_status !== 200;

  const decision = refused ? "refused" : String(body.decision || "").toLowerCase();
  const say = refused
    ? "The deployment declined to assess this call at all."
    : (DECISION_SAY[decision] || "");

  const semantic = body.semantic || {};
  const plan = body.resolution_plan;
  const spec = body.toolspec || {};
  const audit = body.audit || {};

  let html =
    `<div class="outcome-head">` +
    `<span class="big-verdict" data-d="${safeD(decision)}">${esc(decision || "?")}</span>` +
    `<span class="verdict-say">${esc(say)}</span></div>`;

  html += `<div class="envelope">`;

  if (refused) {
    html += block("Why it was refused",
      "A contract violation, decided before any policy reasoning. There is " +
      "nothing to weigh when the call is not a valid call.",
      [["status", payload.http_status],
       ["detail", body.detail || body.error || JSON.stringify(body)]]);
  } else {
    html += block("What was submitted", "Exactly what this page sent.", [
      ["as role", payload.as_role],
      ["tool", payload.submitted.tool],
      ["arguments", JSON.stringify(payload.submitted.arguments)],
      ["under authority", payload.submitted.intent_ref || "— none —"],
      ["target", payload.submitted.target_environment],
    ]);

    html += block("The decision", "", [
      ["decision", body.decision],
      ["reasons", (body.reasons || []).join(", ")],
      ["proposal", body.proposal_id],
      ["tool-call hash", body.tool_call_hash],
    ]);

    html += block("Grounding signals",
      "The ACCEPT rule requires all four to be true. Any one false or " +
      "unevaluated and the call cannot be autonomous.",
      Object.keys(SIGNALS).map((k) => [
        `${k} — ${SIGNALS[k]}`,
        k in semantic ? semantic[k] : null]));

    html += block("Anchors",
      "What the decision was computed against. Change any of these and the " +
      "decision is about a different call.",
      [["intent authority hash", semantic.intent_authority_hash],
       ["tool contract bundle", semantic.tool_contract_bundle_hash],
       ["state hash", semantic.state_hash]]);

    html += block("Authorising ToolSpec",
      "Which signed spec permitted this, and the bundle it came from.",
      [["tool", spec.tool_id], ["version", spec.version],
       ["enforced", spec.enforced],
       ["spec hash", spec.hash], ["bundle digest", spec.bundle_digest]]);

    if (audit.entry_hash) {
      html += block("Position in the audit chain",
        "Written before you saw this. Assessing is a governed act.",
        [["sequence", audit.sequence_no], ["entry hash", audit.entry_hash]]);
    }

    if (plan) {
      html += `<div class="next-step"><h3>What a human must do next</h3>` +
        `<p>Required authority: <b>${esc(plan.required_role || "reviewer")}</b>. ` +
        `They must confirm: ${esc((plan.requirements || []).join(", "))}.</p>` +
        `<div class="actions">` +
        `<button class="primary" id="do-approve" data-item="${esc(body.review_item_id || "")}" ` +
        `data-role="${esc(plan.required_role || "reviewer")}">` +
        `Approve as ${esc(plan.required_role || "reviewer")}</button>` +
        `<button class="ghost" id="do-approve-wrong" data-item="${esc(body.review_item_id || "")}">` +
        `Try to approve as the operator</button>` +
        `</div></div>`;
    }
  }

  html += `<details class="raw-wrap"><summary>The whole response, unmodified` +
          `</summary><pre class="raw">${esc(JSON.stringify(payload, null, 2))}` +
          `</pre></details>`;
  html += `</div>`;

  box.innerHTML = html;
}

function appendResult(title, payload) {
  const box = $("#outcome");
  const wrap = document.createElement("div");
  const refused = payload.http_status !== 200;
  wrap.className = "env-block";
  wrap.innerHTML =
    `<h3>${esc(title)} — as ${esc(payload.as_role)}</h3>` +
    `<dl><dt>result</dt><dd data-truth="${refused ? "false" : "true"}">` +
    `${esc(refused
        ? `refused (${payload.http_status}): ` +
          ((payload.response || {}).detail || (payload.response || {}).error || "")
        : JSON.stringify(payload.response))}</dd></dl>`;
  box.append(wrap);
}

/* ── Submitting ─────────────────────────────────────────────────────── */

$("#compose").addEventListener("submit", async (event) => {
  event.preventDefault();
  // Values are sent as typed. A number-looking string stays a string unless
  // it parses as JSON, matching how the CLI reads k=v — one behaviour to
  // learn, not two.
  const args = {};
  $$("#args input[data-arg]").forEach((input) => {
    const raw = input.value.trim();
    if (raw === "") return;
    try { args[input.dataset.arg] = JSON.parse(raw); }
    catch { args[input.dataset.arg] = raw; }
  });

  $("#outcome").innerHTML = `<p class="empty">Assessing…</p>`;
  const payload = await post("/api/assess", {
    role: $("#role").value,
    tool: $("#tool").value,
    arguments: args,
    intent_ref: $("#intent").value || null,
    target_environment: $("#env").value,
  });
  renderOutcome(payload);
});

$("#outcome").addEventListener("click", async (event) => {
  const right = event.target.closest("#do-approve");
  const wrong = event.target.closest("#do-approve-wrong");
  if (!right && !wrong) return;

  const button = right || wrong;
  const role = right ? button.dataset.role : "operator";
  button.disabled = true;
  const payload = await post("/api/approve",
    { role, review_item_id: button.dataset.item });
  appendResult(wrong ? "Approval attempted by the wrong role" : "Approval",
               payload);
});

$("#reset").addEventListener("click", () => {
  $$("#args input[data-arg]").forEach((i) => { i.value = ""; });
  $("#outcome").innerHTML =
    `<p class="empty">Submit a call and the whole envelope appears here.</p>`;
});

$("#role").addEventListener("change", onRoleChange);
$("#tool").addEventListener("change", onToolChange);
$("#intent").addEventListener("change", onIntentChange);

/* ── Benchmarks ─────────────────────────────────────────────────────── */

function caseState(r) {
  if (r.gap_closed) return "closed";
  if (r.regression) return "regression";
  if (r.known_gap_open) return "known_gap";
  return "matched";
}

const CASE_TAG = {
  matched: "matched",
  known_gap: "known gap",
  regression: "regression",
  closed: "gap closed",
};

function renderBenchmark(report, suites) {
  const describe = Object.fromEntries(
    (suites || []).map((s) => [s.suite, s.description]));

  $("#scorecard").innerHTML = Object.keys(report.suites).sort().map((name) => {
    const s = report.suites[name];
    const state = s.regressions ? "bad" : s.known_gaps ? "gap" : "ok";
    return `<div class="score-row">` +
      `<span class="score-name">${esc(name)}</span>` +
      `<span class="score-desc">${esc(describe[name] || "")}</span>` +
      `<span class="score-num" data-state="${state}">` +
      `${esc(s.matched)}/${esc(s.total)}` +
      (s.known_gaps ? ` · ${esc(s.known_gaps)} known` : "") +
      (s.regressions ? ` · ${esc(s.regressions)} regression` : "") +
      `</span></div>`;
  }).join("");

  // Regressions first, then open gaps, then the rest — the order someone
  // reading a scorecard needs to act in.
  const rank = { regression: 0, closed: 1, known_gap: 2, matched: 3 };
  const rows = report.results.slice()
    .sort((a, b) => rank[caseState(a)] - rank[caseState(b)]);

  $("#bench-detail").innerHTML =
    `<ul class="case-list">${rows.map((r) => {
      const state = caseState(r);
      return `<li class="case" data-state="${state}">` +
        `<div class="case-top">` +
        `<span class="case-id">${esc(r.suite)}/${esc(r.case_id)}</span>` +
        `<span class="case-tag">${esc(CASE_TAG[state])}</span></div>` +
        `<p>${esc(r.given)}</p>` +
        (r.why_not ? `<span class="label">Result</span><p>${esc(r.why_not)}</p>` : "") +
        ((r.steps || []).length
          ? `<span class="label">Governed acts performed</span>` +
            `<p>${r.steps.map((s) =>
                `${esc(s.act)} as ${esc(s.as_role)} → ` +
                `<b class="${s.allowed ? "ok" : "no"}">` +
                `${s.allowed ? "allowed" : "refused"}</b>` +
                (s.detail ? ` (${esc(s.detail)})` : "")).join("<br>")}</p>`
          : "") +
        `<span class="label">Why that is the right answer</span>` +
        `<p>${esc(r.because)}</p>` +
        (r.known_gap ? `<span class="label">Known gap</span>` +
                       `<p>${esc(r.known_gap)}</p>` : "") +
        `</li>`;
    }).join("")}</ul>`;

  // Never one number. A single accuracy figure says something is wrong; the
  // layer says what to fix.
  const layers = report.layers || {};
  $("#layer-card").replaceChildren(...Object.keys(layers).sort().map((name) => {
    const s = layers[name];
    const row = document.createElement("div");
    row.className = "score-row";
    const label = document.createElement("span");
    label.className = "score-name";
    label.textContent = name.replace(/_/g, " ");
    const desc = document.createElement("span");
    desc.className = "score-desc";
    desc.textContent = "layer";
    const num = document.createElement("span");
    num.className = "score-num";
    num.dataset.state = s.regressions ? "bad" : s.known_gaps ? "gap" : "ok";
    num.textContent = `${s.matched}/${s.total}`
      + (s.known_gaps ? ` · ${s.known_gaps} known` : "");
    row.append(label, desc, num);
    return row;
  }));

  // What was run, out of what exists. A filtered 5/5 is not coverage.
  const available = Object.values((report.manifest || {}).suites || {})
    .reduce((n, s) => n + s.cases, 0);
  const selection = report.selection || {};
  const scope = selection.everything
    ? "every scenario"
    : `${(selection.suites || []).join(", ") || "a subset"}`;
  $("#bench-state").textContent =
    `${report.matched}/${report.cases} matched · ` +
    `${report.regressions} regression(s) · ` +
    `${report.known_gaps} known gap(s) open · ` +
    `ran ${scope} — ${report.cases} of ${available} available · ` +
    `${report.seconds}s`;
}

/* ── Choosing what to run ───────────────────────────────────────────────
 * A partial run must never read as a complete one, so the selection is shown
 * before the run, echoed in the score after it, and recorded in the report.
 */

let suiteIndex = [];
let lastReport = null;

function renderPicker(suites) {
  suiteIndex = suites;
  $("#suite-picker").replaceChildren(...suites.map((suite) => {
    const box = document.createElement("div");
    box.className = "pick-suite";

    const head = document.createElement("label");
    head.className = "pick-head";
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = true;
    check.dataset.suite = suite.suite;

    const name = document.createElement("span");
    name.className = "pick-name";
    name.textContent = suite.suite;

    const title = document.createElement("span");
    title.className = "pick-title";
    title.textContent = suite.title;

    head.append(check, name, title);

    // A suite that approves and executes changes the system of record. Saying
    // so before the click is the difference between a tool and a trap.
    if (suite.writes) {
      const warn = document.createElement("span");
      warn.className = "pick-writes";
      warn.textContent = "writes";
      warn.title = "This suite approves and executes: it changes the system "
                 + "of record. Every act is recorded in the audit trail.";
      head.append(warn);
    }

    const cases = document.createElement("div");
    cases.className = "pick-cases";
    cases.append(...suite.cases.map((c) => {
      const tag = document.createElement("span");
      tag.className = "pick-case";
      const probe = document.createElement("i");
      probe.textContent = (c.probes || "").replace(/_/g, " ");
      tag.append(probe, document.createTextNode(c.scenario || c.id));
      if (c.steps) {
        const steps = document.createElement("b");
        steps.textContent = `+${c.steps} act${c.steps > 1 ? "s" : ""}`;
        tag.append(steps);
      }
      return tag;
    }));

    box.append(head, cases);
    return box;
  }));
  updateWriteWarning();
}

function chosenSuites() {
  return $$("#suite-picker input[data-suite]")
    .filter((i) => i.checked).map((i) => i.dataset.suite);
}

function updateWriteWarning() {
  const chosen = new Set(chosenSuites());
  const writing = suiteIndex.filter((s) => s.writes && chosen.has(s.suite));
  const box = $("#write-warning");
  if (!writing.length) { box.hidden = true; return; }
  box.hidden = false;
  box.replaceChildren(
    Object.assign(document.createElement("b"),
      { textContent: "This run changes the system of record. " }),
    document.createTextNode(
      `${writing.map((s) => s.suite).join(", ")} approve and execute, because `
      + "role separation and payload binding cannot be observed without "
      + "performing the acts. Every one is recorded in the audit trail below."));
}

$("#suite-picker").addEventListener("change", updateWriteWarning);
$("#select-all").addEventListener("click", () => {
  $$("#suite-picker input[data-suite]").forEach((i) => { i.checked = true; });
  updateWriteWarning();
});
$("#select-none").addEventListener("click", () => {
  $$("#suite-picker input[data-suite]").forEach((i) => { i.checked = false; });
  updateWriteWarning();
});

/* ── The audit trail ────────────────────────────────────────────────────── */

function renderTrail(report) {
  const trail = report.audit_trail || [];
  $("#trail-section").hidden = !trail.length;
  if (!trail.length) return;

  $("#trail tbody").replaceChildren(...trail.map((entry) => {
    const row = document.createElement("tr");
    for (const [value, cls] of [
      [`#${entry.sequence_no}`, "num"],
      [entry.event, "id"],
      [entry.case, ""],
      [String(entry.entry_hash || "").slice(0, 24), "id"],
    ]) {
      const cell = document.createElement("td");
      if (cls) cell.className = cls;
      cell.textContent = value;
      row.append(cell);
    }
    return row;
  }));
  $("#trail-state").textContent =
    `${trail.length} governed act(s) recorded — not yet checked`;
}

$("#verify-trail").addEventListener("click", async () => {
  if (!lastReport) return;
  const button = $("#verify-trail");
  button.disabled = true;
  $("#trail-state").textContent = "re-reading each position from the chain…";
  try {
    const check = await post("/api/benchmark/verify-trail", lastReport);
    if (check.error) throw new Error(check.error);
    $("#trail-state").textContent = check.verified
      ? `${check.checked} entries confirmed against the chain `
        + `(${check.chain_records} records, chain verified)`
      : `NOT CONFIRMED — ${(check.mismatches || []).length} mismatch(es)`
        + (check.problem ? `: ${check.problem}` : "");
    $("#trail-state").dataset.state = check.verified ? "ok" : "bad";
  } catch (error) {
    $("#trail-state").textContent = `could not check: ${error.message}`;
    $("#trail-state").dataset.state = "bad";
  } finally {
    button.disabled = false;
  }
});

$("#run-bench").addEventListener("click", async () => {
  const button = $("#run-bench");
  const suites = chosenSuites();
  if (!suites.length) {
    $("#bench-state").textContent = "nothing selected";
    return;
  }
  button.disabled = true;
  $("#bench-state").textContent =
    `running ${suites.length} suite(s) against the deployment…`;
  $("#trail-state").textContent = "";
  delete $("#trail-state").dataset.state;
  try {
    const report = await post("/api/benchmark/run", { suites });
    if (report.error) throw new Error(report.error);
    lastReport = report;
    renderBenchmark(report, suiteIndex);
    renderTrail(report);
  } catch (error) {
    $("#bench-state").textContent = `could not run: ${error.message}`;
  } finally {
    button.disabled = false;
  }
});

/* ── Wiring ─────────────────────────────────────────────────────────── */

function show(name) {
  $$(".panel").forEach((p) => { p.hidden = p.id !== `s-${name}`; });
  $$(".nav-item").forEach((b) => {
    const active = b.dataset.surface === name;
    b.classList.toggle("is-active", active);
    if (active) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  location.hash = name;
}

$$(".nav-item").forEach((b) =>
  b.addEventListener("click", () => show(b.dataset.surface)));

// The console runs on its own port. Derived rather than hardcoded so the
// link survives the port probing in bootstrap_env.py.
$("#console-link").href =
  `${location.protocol}//${location.hostname}:${window.AAE_CONSOLE_PORT || 8089}/`;

async function loadPicker() {
  renderPicker(await get("/api/benchmark/suites"));
}

loadCatalogue()
  .then(loadPresets)
  .then(loadPicker)
  .catch((error) => {
    $("#outcome").innerHTML =
      `<p class="empty">Could not load the tool catalogue: ${esc(error.message)}</p>`;
  });
show((location.hash || "#compose").slice(1));
