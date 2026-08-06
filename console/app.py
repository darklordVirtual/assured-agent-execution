# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""Operator console for Assured Agent Execution.

Deliberately a separate, tiny service that **never imports remora**. It talks
to the control plane over HTTP exactly as any other client would, so nothing
about the demonstration surface can change the behaviour of the system being
demonstrated. It holds no policy and makes no decisions.

It is **read-only**, and holds exactly one credential: the `viewer` token,
whose role grants `read` and nothing else. It cannot propose, approve or
execute, and there is no endpoint here that writes anything.

That was not true of the first version. It held all five tokens — including
`domain_expert` and `senior_authority` — and exposed an unauthenticated POST
that ran the scenarios, which perform real writes under those roles. A
presentation surface with an approver credential is a high-value target
wearing a low-value label.

Running the scenarios is a CLI action (`python run.py scenarios`), which is
where a privileged credential belongs.

What it shows, and why each panel is on it:

  Posture      which core is pinned, which surfaces are served, whether
               ToolSpec enforcement is on, whether the audit chain verifies.
               A console that only showed activity would let a deployment
               look healthy while running unpinned and unenforced.
  Decisions    the four, each runnable, each showing the reasons the engine
               gave rather than a green tick.
  System of    the work orders themselves, read on the READ-ONLY credential.
  record       Seeing the effect in the actual table is the difference
               between believing the audit trail and checking it.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

API = os.environ.get("AAE_API_URL", "http://control-plane:8000")
#: One credential, read-only. Anything this console cannot do with `viewer`
#: is something it should not be doing.
VIEWER_TOKEN = os.environ.get("AAE_TOKEN_VIEWER", "")
READER_DSN = os.environ.get("AAE_WORKORDER_READER_DSN", "")

app = FastAPI(title="AAE Operator Console", docs_url=None, redoc_url=None)


def _get(path: str) -> tuple[int, Any]:
    try:
        response = httpx.get(
            f"{API}{path}", timeout=15,
            headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
        return response.status_code, response.json()
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


@app.get("/api/posture")
def posture() -> JSONResponse:
    """Everything that decides whether the activity below means anything."""
    status, root = _get("/")
    _, health = _get("/v1/health")
    chain_status, chain = _get("/v1/execution/audit/verify")

    lock: dict[str, Any] = {}
    try:
        with open("/console/core-artifact-lock.json", encoding="utf-8") as fh:
            lock = json.load(fh)
    except OSError:
        pass

    toolspec = {"configured": bool(os.environ.get("AAE_TOOLSPEC_CONFIGURED")),
                "pinned": bool(os.environ.get("AAE_TOOLSPEC_PINNED"))}

    return JSONResponse({
        "reachable": status == 200,
        "runtime_mode": root.get("runtime_mode"),
        "surfaces": root.get("surfaces", []),
        "health": health.get("status"),
        "core": {
            "version": lock.get("remora_core_version"),
            "commit": (lock.get("remora_core_commit") or "")[:8],
            "release": lock.get("release_tag"),
            "status": lock.get("release_status"),
        },
        "toolspec": toolspec,
        "audit_chain": {
            "checked": chain_status == 200,
            "valid": chain.get("valid") if chain_status == 200 else None,
            "records": chain.get("records_checked") if chain_status == 200 else None,
        },
    })


@app.get("/api/work-orders")
def work_orders() -> JSONResponse:
    """The system of record, on the credential that cannot write it."""
    if not READER_DSN:
        return JSONResponse({"error": "no reader DSN configured"}, status_code=503)
    try:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(READER_DSN, row_factory=dict_row,
                             connect_timeout=5, autocommit=True) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT wo_id, title, asset_id, status, priority,"
                    "       closed_reason, updated_by, updated_at"
                    "  FROM work_orders ORDER BY wo_id")
                rows = cur.fetchall()
                cur.execute(
                    "SELECT wo_id, tool_name, detail, occurred_at"
                    "  FROM work_order_events ORDER BY event_id DESC LIMIT 20")
                events = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"},
                            status_code=503)

    def clean(row: dict) -> dict:
        return {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                for k, v in row.items()}

    return JSONResponse({"work_orders": [clean(r) for r in rows],
                         "events": [clean(r) for r in events]})


_PAGE = """
<title>Assured Agent Execution — Operator Console</title>
<style>
  :root { color-scheme: light dark; --fg:#111; --dim:#666; --line:#d8d8d8;
          --ok:#0a7d32; --warn:#a15c00; --bad:#b3261e; --bg:#fff; --card:#fafafa; }
  @media (prefers-color-scheme: dark) {
    :root { --fg:#e8e8e8; --dim:#9a9a9a; --line:#333; --ok:#4ade80;
            --warn:#fbbf24; --bad:#f87171; --bg:#111; --card:#1a1a1a; } }
  * { box-sizing: border-box; }
  body { font: 14px/1.55 ui-sans-serif, system-ui, sans-serif; color: var(--fg);
         background: var(--bg); margin: 0; padding: 2rem 1.5rem; max-width: 1100px;
         margin-inline: auto; }
  h1 { font-size: 1.35rem; margin: 0 0 .25rem; }
  h2 { font-size: .95rem; text-transform: uppercase; letter-spacing: .07em;
       color: var(--dim); margin: 2rem 0 .75rem; font-weight: 600; }
  .sub { color: var(--dim); margin: 0 0 1.5rem; }
  .grid { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fit, minmax(210px,1fr)); }
  .card { border: 1px solid var(--line); border-radius: 8px; padding: .8rem 1rem;
          background: var(--card); }
  .card .k { color: var(--dim); font-size: .78rem; text-transform: uppercase;
             letter-spacing: .05em; }
  .card .v { font-size: 1.05rem; margin-top: .2rem; font-weight: 600; }
  .ok { color: var(--ok); } .warn { color: var(--warn); } .bad { color: var(--bad); }
  table { border-collapse: collapse; width: 100%; font-size: .88rem; }
  th, td { text-align: left; padding: .45rem .6rem; border-bottom: 1px solid var(--line); }
  th { color: var(--dim); font-weight: 600; font-size: .78rem; text-transform: uppercase;
       letter-spacing: .05em; }
  button { font: inherit; font-weight: 600; padding: .5rem 1.1rem; border-radius: 6px;
           border: 1px solid var(--line); background: var(--card); color: var(--fg);
           cursor: pointer; }
  button:hover { border-color: var(--fg); }
  button[disabled] { opacity: .5; cursor: progress; }
  pre { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
        padding: 1rem; overflow-x: auto; font-size: .82rem; line-height: 1.5;
        white-space: pre-wrap; }
  .note { color: var(--dim); font-size: .84rem; border-left: 2px solid var(--line);
          padding-left: .8rem; margin: 1rem 0; }
  .wrap { overflow-x: auto; }
</style>

<h1>Assured Agent Execution</h1>
<p class="sub">Operator console. Holds no policy, makes no decisions, and imports
no REMORA module — it reads this deployment over HTTP like any other client.</p>

<h2>Posture</h2>
<div class="grid" id="posture"><div class="card"><div class="v">loading…</div></div></div>

<p class="note">These are the facts that decide whether anything below means
anything. A deployment can be busy and still be running unpinned, unenforced,
or with a chain that no longer verifies.</p>

<h2>The four decisions</h2>
<p class="sub">Run them from a shell. This console is read-only and holds no
credential that could approve or execute — running the scenarios needs both.</p>
<pre>python run.py scenarios</pre>
<p class="note">Then reload this page: the system of record below shows what
the run actually changed.</p>

<h2>System of record</h2>
<p class="sub">Read on the <strong>read-only</strong> credential — the same one
the postcondition reader holds. Seeing the effect in the actual table is the
difference between believing the audit trail and checking it.</p>
<div class="wrap"><table id="wo"><tbody><tr><td>loading…</td></tr></tbody></table></div>

<h2>Recent governed writes</h2>
<div class="wrap"><table id="events"><tbody><tr><td>loading…</td></tr></tbody></table></div>

<p class="note">Read-only. This console holds one credential — the
<code>viewer</code> role, which grants <code>read</code> and nothing else — and
exposes no endpoint that writes. A real deployment puts an identity provider in
front of it; this local profile does not, which is why it binds to loopback.</p>

<script>
const esc = s => String(s ?? "").replace(/[&<>]/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));

function card(k, v, cls) {
  return `<div class="card"><div class="k">${esc(k)}</div>
          <div class="v ${cls||""}">${esc(v)}</div></div>`;
}

async function loadPosture() {
  const r = await fetch("/api/posture"); const p = await r.json();
  const chain = p.audit_chain || {};
  const chainCls = chain.valid === true ? "ok" : chain.valid === false ? "bad" : "warn";
  const chainTxt = chain.valid === true ? `verified (${chain.records ?? "?"} records)`
                 : chain.valid === false ? "DOES NOT VERIFY" : "not checked";
  document.getElementById("posture").innerHTML = [
    card("control plane", p.reachable ? (p.health || "ok") : "unreachable",
         p.reachable ? "ok" : "bad"),
    card("runtime mode", p.runtime_mode || "?",
         p.runtime_mode === "production" ? "ok" : "warn"),
    card("surfaces served", (p.surfaces || []).join(", ") || "?",
         (p.surfaces||[]).includes("assess") ? "warn" : "ok"),
    card("pinned core", `${p.core.version || "?"} @ ${p.core.commit || "?"}`, ""),
    card("release", `${p.core.release || "?"} (${p.core.status || "?"})`,
         p.core.status === "stable" ? "ok" : "warn"),
    card("toolspec enforcement",
         p.toolspec.configured ? (p.toolspec.pinned ? "signed + pinned" : "signed, NOT pinned")
                               : "off",
         p.toolspec.configured ? (p.toolspec.pinned ? "ok" : "warn") : "bad"),
    card("audit chain", chainTxt, chainCls),
  ].join("");
}

async function loadWorkOrders() {
  const r = await fetch("/api/work-orders"); const d = await r.json();
  if (d.error) {
    document.getElementById("wo").innerHTML =
      `<tbody><tr><td class="bad">${esc(d.error)}</td></tr></tbody>`;
    return;
  }
  document.getElementById("wo").innerHTML =
    `<thead><tr><th>work order</th><th>title</th><th>asset</th><th>status</th>
     <th>priority</th><th>last written by</th></tr></thead><tbody>` +
    d.work_orders.map(w => `<tr><td><code>${esc(w.wo_id)}</code></td>
      <td>${esc(w.title)}</td><td>${esc(w.asset_id)}</td>
      <td class="${w.status === "closed" ? "ok" : ""}">${esc(w.status)}</td>
      <td>${esc(w.priority)}</td><td>${esc(w.updated_by)}</td></tr>`).join("") +
    `</tbody>`;
  document.getElementById("events").innerHTML = d.events.length
    ? `<thead><tr><th>when</th><th>work order</th><th>tool</th><th>detail</th></tr></thead><tbody>` +
      d.events.map(e => `<tr><td>${esc((e.occurred_at||"").replace("T"," ").slice(0,19))}</td>
        <td><code>${esc(e.wo_id)}</code></td><td><code>${esc(e.tool_name)}</code></td>
        <td>${esc(JSON.stringify(e.detail))}</td></tr>`).join("") + `</tbody>`
    : `<tbody><tr><td>no governed write has happened yet</td></tr></tbody>`;
}


loadPosture(); loadWorkOrders();
setInterval(loadWorkOrders, 15000);
setInterval(loadPosture, 15000);
</script>
"""


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(_PAGE)
