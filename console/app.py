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

Three surfaces, plus a strip that qualifies all of them.

  Assurance strip    verified engine, runtime mode, tool policy, audit
                     integrity, credential scope — in the masthead, on every
                     screen. It was a separate page, which meant an operator
                     could read activity all day and never see that the
                     deployment was running unpinned.
  Ledger             what the agent attempted and what the controls did,
                     straight from the signed audit chain. Filterable to the
                     entries where a control refused something, because that
                     is the question an operator actually arrives with.
  Records            the work orders, on the SELECT-only credential. Seeing
                     the change in the actual table is the difference between
                     believing the audit trail and checking it.
  Assurance          the same evidence as the strip, in full, with the
                     reasoning behind each line.

Two read-only database credentials, both SELECT and neither able to write:
one on the system of record, one on the governance chain.

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

#: SELECT-only on the governance database. Lets the console read the audit
#: chain — the record of what was decided — without being able to change it.
CHAIN_DSN = os.environ.get("AAE_CHAIN_READER_DSN", "")
CHAIN_TENANT = os.environ.get("AAE_CHAIN_TENANT", "aae-work-orders")

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


# ── The ledger: what was attempted, and what the controls did ──────────────
#
# Read straight from the tenant audit chain on a SELECT-only credential. This
# is the governance record itself, not a reconstruction of one: every row is a
# signed, hash-linked entry the control plane wrote as it decided.
#
# The earlier note here said a decisions feed could not be built honestly.
# That was true of the two sources it considered — the SDK cannot enumerate
# proposals, and the business event log carries no proposal id — and it was
# wrong to stop there, because the chain has always held all of it. The result
# was a console that showed a health banner and four work orders while the
# entire enforcement record sat unread one network away.

#: Events where a control stopped something. These are the reason the product
#: exists, so they are named rather than inferred from a decision value: an
#: assessment that says `escalate` was never executed, while a
#: `execution_binding_refused` means an approved action was caught being
#: swapped at the last moment. Different facts, shown differently.
_INTERVENTIONS = {
    "execution_binding_refused":
        "The payload did not match what was approved.",
    "execution_grant_refused":
        "The grant was not valid for this call.",
    "execution_approval_invalidated":
        "Re-assessment at execution came back stricter than the approval.",
}

#: Ordered by how much they constrain the agent. The UI reads this order.
_DECISIONS = ("accept", "verify", "escalate", "abstain")


class LedgerEntry(BaseModel):
    """One link in the chain, projected to what an operator needs to read."""

    sequence_no: int
    at: str
    event: str
    #: Present on assessments; absent on approvals and executions.
    decision: str | None = None
    reasons: list[str] = []
    tool_name: str | None = None
    target_environment: str | None = None
    actor: str | None = None
    proposal_id: str | None = None
    #: Set when this entry records a control stopping something.
    intervention: str | None = None
    #: Whether the tool actually performed its side effect, where known.
    tool_executed: bool | None = None
    effect_status: str | None = None
    entry_hash: str
    previous_hash: str | None = None


class Ledger(BaseModel):
    entries: list[LedgerEntry]
    #: Whole-chain totals, not totals of the page above.
    total_entries: int
    decision_counts: dict[str, int]
    intervention_count: int
    #: True when consecutive `sequence_no` values in `entries` are contiguous.
    #: A break is either the page boundary or a gap, and the UI must not draw
    #: an unbroken chain over one.
    contiguous: bool


def _chain_reader():
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(CHAIN_DSN, row_factory=dict_row,
                           connect_timeout=5, autocommit=True)


def _ledger_entry(row: dict) -> LedgerEntry:
    payload = row["payload"] or {}
    event = payload.get("event") or "unknown"
    return LedgerEntry(
        sequence_no=row["sequence_no"],
        at=str(row["timestamp"]),
        event=event,
        decision=payload.get("decision"),
        reasons=list(payload.get("reasons") or []),
        tool_name=payload.get("tool_name") or payload.get("tool_id"),
        target_environment=payload.get("target_environment"),
        actor=payload.get("actor"),
        proposal_id=payload.get("proposal_id"),
        intervention=_INTERVENTIONS.get(event),
        tool_executed=payload.get("tool_executed"),
        effect_status=payload.get("status") if event == "effect_verified"
        else None,
        entry_hash=row["entry_hash"],
        previous_hash=row["previous_hash"],
    )


@app.get("/api/ledger", response_model=Ledger)
def ledger(limit: int = 60, only: str = "") -> Any:
    """The governance record, newest first.

    ``only=interventions`` narrows to the entries where a control refused
    something — the question an operator actually arrives with.
    """
    if not CHAIN_DSN:
        return _problem("No governance-database credential is configured.")
    limit = max(1, min(limit, 200))

    where, params = "WHERE tenant_id = %s", [CHAIN_TENANT]
    if only == "interventions":
        where += " AND payload->>'event' = ANY(%s)"
        params.append(list(_INTERVENTIONS))

    try:
        with _chain_reader() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT sequence_no, timestamp, payload, entry_hash,"
                "       previous_hash FROM tenant_chain_entry "
                f"{where} ORDER BY sequence_no DESC LIMIT %s",
                [*params, limit])
            rows = cur.fetchall()

            # Totals over the whole chain, so the summary does not silently
            # describe only the page above it.
            cur.execute(
                "SELECT payload->>'decision' AS decision, count(*) AS n"
                "  FROM tenant_chain_entry WHERE tenant_id = %s"
                "   AND payload->>'decision' IS NOT NULL GROUP BY 1",
                [CHAIN_TENANT])
            counts = {r["decision"]: r["n"] for r in cur.fetchall()}
            cur.execute(
                "SELECT count(*) AS n FROM tenant_chain_entry"
                " WHERE tenant_id = %s AND payload->>'event' = ANY(%s)",
                [CHAIN_TENANT, list(_INTERVENTIONS)])
            interventions = cur.fetchone()["n"]
            cur.execute(
                "SELECT count(*) AS n FROM tenant_chain_entry"
                " WHERE tenant_id = %s", [CHAIN_TENANT])
            total = cur.fetchone()["n"]
    except Exception as exc:  # noqa: BLE001
        return _problem("The governance record is not reachable.", exc)

    entries = [_ledger_entry(r) for r in rows]
    numbers = [e.sequence_no for e in entries]
    contiguous = all(a - b == 1 for a, b in zip(numbers, numbers[1:]))

    return Ledger(
        entries=entries,
        total_entries=total,
        decision_counts={d: counts.get(d, 0) for d in _DECISIONS},
        intervention_count=interventions,
        contiguous=contiguous,
    )


# ── Decisions: look up one proposal ────────────────────────────────────────

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
