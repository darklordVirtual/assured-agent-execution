# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""AAE Assurance Console — a read-only view of what the deployment enforced.

A separate, small service that **never imports remora**. It talks to the
control plane over HTTP exactly as any other client would, so nothing about
the console can change the behaviour of the system it displays.

It is read-only and holds exactly one credential: the `viewer` token, whose
role grants `read` and nothing else. No route here writes.

That was not true of the first version. It held all five bearer tokens —
including `domain_expert` and `senior_authority` — and exposed an
unauthenticated POST that ran the scenarios, which perform real writes under
those roles. A presentation surface carrying an approver credential is a
high-value target wearing a low-value label. Running the scenarios is a CLI
action now, which is where a privileged credential belongs.

Four surfaces:

  Overview           is enforcement operational, does the audit verify, what
                     needs attention. A console showing only activity would
                     let a deployment look healthy while running unpinned.
  Decisions          look up a proposal: what was decided, why, who had to
                     approve it, and whether the effect was confirmed.
  Business records   the work orders, on the SELECT-only credential. Seeing
                     the change in the actual table is the difference between
                     believing the audit trail and checking it.
  System assurance   the technical evidence: verified engine, tool policy
                     protection, audit integrity, credential scope.

This module does five things and no more: fetch, validate, serve static files,
report failure legibly, and set security headers.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

API = os.environ.get("AAE_API_URL", "http://control-plane:8000")

#: One credential, read-only. Anything this console cannot do with `viewer`
#: is something it should not be doing.
VIEWER_TOKEN = os.environ.get("AAE_TOKEN_VIEWER", "")
READER_DSN = os.environ.get("AAE_WORKORDER_READER_DSN", "")

STATIC = Path(__file__).parent / "static"
LOCK_FILE = Path(__file__).parent / "core-artifact-lock.json"

app = FastAPI(title="AAE Assurance Console", docs_url=None, redoc_url=None)


# ── Security headers ───────────────────────────────────────────────────────

#: Everything is served from this origin: no CDN, no inline script, no inline
#: style, no frame, no form target. A console for a governance product should
#: not be able to load a third party's JavaScript even by accident.
_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = _CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    # This page shows governance state. A cached copy on a shared machine
    # outlives the session that was entitled to see it.
    response.headers["Cache-Control"] = "no-store"
    return response


# ── Failure, reported rather than leaked ───────────────────────────────────

class Problem(BaseModel):
    """A failure a person can act on, and an id they can quote.

    Exception text goes to the log, never to the browser: it names internal
    hosts, credentials and file paths, and a console is the wrong place to
    disclose any of them.
    """

    error: str
    correlation_id: str


def _problem(message: str, exc: Exception | None = None,
             status: int = 503) -> JSONResponse:
    correlation = uuid.uuid4().hex[:12]
    if exc is not None:
        print(f"[{correlation}] {type(exc).__name__}: {exc}", flush=True)
    return JSONResponse(
        Problem(error=message, correlation_id=correlation).model_dump(),
        status_code=status)


# ── Upstream ───────────────────────────────────────────────────────────────

def _get(path: str) -> tuple[int, Any]:
    try:
        response = httpx.get(
            f"{API}{path}", timeout=15,
            headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
        return response.status_code, response.json()
    except Exception as exc:  # noqa: BLE001
        print(f"upstream {path}: {type(exc).__name__}: {exc}", flush=True)
        return 0, None


def _lock() -> dict[str, Any]:
    try:
        return json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except OSError:
        # Reported as unknown rather than absent. A console that silently
        # showed nothing where the verified engine belongs looks fine while
        # telling you less than it should — which is exactly what happened
        # when this file stopped being copied into the image.
        return {}


def _reader():
    import psycopg
    from psycopg.rows import dict_row

    # autocommit: a reader has nothing to keep a transaction for, and one held
    # open blocks pg_dump — which is how a scheduled backup hangs in silence.
    return psycopg.connect(READER_DSN, row_factory=dict_row,
                           connect_timeout=5, autocommit=True)


def _clean(row: dict) -> dict:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v)
            for k, v in row.items()}


# ── Models ─────────────────────────────────────────────────────────────────

class CoreIdentity(BaseModel):
    version: str | None = None
    commit: str | None = None
    release: str | None = None
    status: str | None = None


class AuditIntegrity(BaseModel):
    checked: bool
    verified: bool | None = None
    records: int | None = None


class Assurance(BaseModel):
    """Everything that decides whether the activity elsewhere means anything."""

    reachable: bool
    runtime_mode: str | None = None
    capabilities: list[str] = []
    health: str | None = None
    core: CoreIdentity = CoreIdentity()
    tool_policy_enforced: bool = False
    tool_policy_pinned: bool = False
    audit: AuditIntegrity = AuditIntegrity(checked=False)
    console_access: str = "read-only"
    database_credential: str = "read-only"
    checked_at: str


class Overview(BaseModel):
    headline: str
    all_clear: bool
    attention: list[str]
    assurance: Assurance
    records: dict[str, int]


class WorkOrder(BaseModel):
    wo_id: str
    title: str
    asset_id: str
    status: str
    priority: str
    closed_reason: str | None = None
    updated_by: str
    updated_at: str


class RecordEvent(BaseModel):
    wo_id: str
    tool_name: str
    actor: str
    occurred_at: str
    detail: dict[str, Any] = {}


class Records(BaseModel):
    work_orders: list[WorkOrder]
    events: list[RecordEvent]
    open_count: int
    closed_count: int


# ── Assurance ──────────────────────────────────────────────────────────────

def _assurance() -> Assurance:
    status, root = _get("/")
    _, health = _get("/v1/health")
    chain_status, chain = _get("/v1/execution/audit/verify")
    lock = _lock()

    return Assurance(
        reachable=status == 200,
        runtime_mode=(root or {}).get("runtime_mode"),
        capabilities=(root or {}).get("surfaces", []),
        health=(health or {}).get("status"),
        core=CoreIdentity(
            version=lock.get("remora_core_version"),
            commit=(lock.get("remora_core_commit") or "")[:8] or None,
            release=lock.get("release_tag"),
            status=lock.get("release_status"),
        ),
        tool_policy_enforced=bool(os.environ.get("AAE_TOOLSPEC_CONFIGURED")),
        tool_policy_pinned=bool(os.environ.get("AAE_TOOLSPEC_PINNED")),
        audit=AuditIntegrity(
            checked=chain_status == 200,
            verified=(chain or {}).get("valid") if chain_status == 200 else None,
            records=((chain or {}).get("records_checked")
                     if chain_status == 200 else None),
        ),
        checked_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


@app.get("/api/assurance", response_model=Assurance)
def assurance() -> Assurance:
    return _assurance()


@app.get("/api/overview", response_model=Overview)
def overview() -> Any:
    state = _assurance()

    # Only actual deviations. A list that always has entries is a list nobody
    # reads.
    attention: list[str] = []
    if not state.reachable:
        attention.append("The control plane is not reachable.")
    if state.runtime_mode and state.runtime_mode != "production":
        attention.append(f"Running in {state.runtime_mode} mode: the "
                         f"fail-closed prerequisites are not binding.")
    if not state.tool_policy_enforced:
        attention.append("Tool policy protection is off: tool definitions are "
                         "not signature-checked.")
    elif not state.tool_policy_pinned:
        attention.append("Tool policy is signed but not pinned: a correctly "
                         "signed older definition would be accepted.")
    if state.audit.checked and state.audit.verified is False:
        attention.append("Audit integrity check FAILED.")
    if not state.audit.checked:
        attention.append("Audit integrity could not be checked.")

    counts = {"open": 0, "closed": 0, "cancelled": 0, "total": 0}
    try:
        with _reader() as conn, conn.cursor() as cur:
            cur.execute("SELECT status, count(*) AS n FROM work_orders "
                        "GROUP BY status")
            for row in cur.fetchall():
                counts[row["status"]] = row["n"]
                counts["total"] += row["n"]
    except Exception as exc:  # noqa: BLE001
        print(f"records: {type(exc).__name__}: {exc}", flush=True)
        attention.append("Business records are not reachable.")

    all_clear = not attention
    return Overview(
        headline=("All enforcement controls are operational" if all_clear
                  else f"{len(attention)} control(s) need attention"),
        all_clear=all_clear,
        attention=attention,
        assurance=state,
        records=counts,
    )


# ── Business records ───────────────────────────────────────────────────────

@app.get("/api/records", response_model=Records)
def records() -> Any:
    if not READER_DSN:
        return _problem("No database credential is configured for this console.")
    try:
        with _reader() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT wo_id, title, asset_id, status, priority,"
                "       closed_reason, updated_by, updated_at"
                "  FROM work_orders ORDER BY wo_id")
            orders = [_clean(r) for r in cur.fetchall()]
            cur.execute(
                "SELECT wo_id, tool_name, actor, detail, occurred_at"
                "  FROM work_order_events ORDER BY event_id DESC LIMIT 50")
            events = [_clean(r) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        return _problem("Business records are not reachable.", exc)

    return Records(
        work_orders=[WorkOrder(**o) for o in orders],
        events=[RecordEvent(**e) for e in events],
        open_count=sum(1 for o in orders if o["status"] == "open"),
        closed_count=sum(1 for o in orders if o["status"] == "closed"),
    )


# ── Decisions: look up a proposal ──────────────────────────────────────────
#
# A lookup, not a feed. The core returns a proposal when its id is known and
# cannot yet LIST proposals, and the business event log carries no proposal id
# because the dispatcher does not pass one to the tool. Assembling a "recent
# decisions" list from what is available would mean showing a reconstruction
# as though it were the record. See docs/limitations.md.

def _proxy(path: str, missing: str) -> Any:
    status, body = _get(path)
    if status == 404:
        return _problem(missing, status=404)
    if status != 200:
        return _problem("The control plane did not answer.")
    return JSONResponse(body)


@app.get("/api/proposals/{proposal_id}")
def proposal(proposal_id: str) -> Any:
    return _proxy(f"/v1/execution/proposals/{proposal_id}",
                  f"No proposal {proposal_id}.")


@app.get("/api/proposals/{proposal_id}/lifecycle")
def lifecycle(proposal_id: str) -> Any:
    return _proxy(f"/v1/execution/proposals/{proposal_id}/lifecycle",
                  f"No lifecycle for {proposal_id}.")


@app.get("/api/proposals/{proposal_id}/evidence")
def evidence(proposal_id: str) -> Any:
    return _proxy(f"/v1/execution/proposals/{proposal_id}/evidence",
                  f"No evidence bundle for {proposal_id}.")


# ── The old paths ──────────────────────────────────────────────────────────
# Kept so an existing bookmark or check resolves. They carry the CURRENT
# payload, not the old shape — a path answering with the old fields would be
# a compatibility promise this product is not making, and the terminology
# changed on purpose.

@app.get("/api/posture", response_model=Assurance)
def posture() -> Assurance:
    return _assurance()


@app.get("/api/work-orders", response_model=Records)
def work_orders() -> Any:
    return records()


# ── Static ─────────────────────────────────────────────────────────────────

app.mount("/assets", StaticFiles(directory=STATIC), name="assets")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", media_type="text/html")
