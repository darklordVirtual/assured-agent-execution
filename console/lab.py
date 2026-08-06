# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""AAE Lab — compose a tool call, act in a role, read the whole envelope.

A DEMONSTRATION AND TEST SURFACE. It holds every role token this deployment
has and can propose, approve and execute. That is the opposite of the console,
which holds one viewer token and cannot act, and the separation is the whole
point of running two services instead of adding a tab.

The console's job is to tell you whether the deployment can be trusted. A
process that can approve its own proposals cannot credibly report
`console_access: read-only` about itself — the claim and the capability would
live in the same place, and the claim would be worth nothing. So the acting
surface is a different container, on a different port, with a different label,
and the console never gains a credential it does not need.

There is no login here. Roles are chosen from a dropdown, which is right for a
lab and wrong for anything else: the identity of an approver is exactly what a
real deployment must not let a caller pick. It is stated in the banner rather
than left for someone to discover.

What it is for:

  1. Seeing what a tool call actually contained, and the decision envelope the
     engine produced for it — the semantic grounding signals, the ToolSpec that
     authorised it, the resolution plan, the chain position.
  2. Trying the system as each role, including the refusals: an agent that
     cannot approve, an approver that cannot execute.
  3. Running the benchmark suites and reading the scorecard.

Not for: production, shared hosts, or anything holding real data.
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
STATIC = Path(__file__).parent / "static"

#: Every role this deployment defines, and the token that carries it. The lab
#: is the one place all five sit together; see the module docstring for why
#: that is acceptable here and nowhere else.
ROLES: dict[str, str] = {
    name: os.environ.get(env, "")
    for name, env in (
        ("operator", "AAE_TOKEN_AGENT"),
        ("reviewer", "AAE_TOKEN_REVIEWER"),
        ("domain_expert", "AAE_TOKEN_EXPERT"),
        ("senior_authority", "AAE_TOKEN_SENIOR"),
        ("viewer", "AAE_TOKEN_VIEWER"),
    )
}

#: What each role is allowed to do, for the UI to explain rather than for it to
#: enforce. Enforcement is the control plane's, and one of the things worth
#: demonstrating here is that the lab cannot talk its way past it: pick
#: `reviewer` and press Execute, and the refusal comes from the engine.
ROLE_NOTES: dict[str, str] = {
    "operator": "Proposes and executes. Cannot approve — not even its own.",
    "reviewer": "Approves ordinary holds. Cannot execute what it approved.",
    "domain_expert": "Approves what needs domain judgement.",
    "senior_authority": "Approves what was escalated past a reviewer.",
    "viewer": "Reads. Nothing else.",
}

app = FastAPI(title="AAE Lab", docs_url=None, redoc_url=None)

_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = _CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


class Problem(BaseModel):
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


def _call(method: str, path: str, role: str,
          body: dict | None = None) -> tuple[int, Any]:
    """Speak to the control plane as `role`, and report what came back.

    A refusal is data here, not an exception. Watching the engine decline is
    half of what this surface is for, so a 403 is returned to the browser and
    rendered as an outcome rather than raised as an error.
    """
    token = ROLES.get(role, "")
    if not token:
        return 0, {"error": f"no token configured for role {role!r}"}
    try:
        response = httpx.request(
            method, f"{API}{path}", timeout=30,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json=body)
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, {"error": response.text[:500]}
    except Exception as exc:  # noqa: BLE001
        print(f"lab {method} {path} as {role}: {type(exc).__name__}: {exc}",
              flush=True)
        return 0, {"error": "the control plane did not answer"}


# ── What can be tried ──────────────────────────────────────────────────────

class Catalogue(BaseModel):
    """Everything the composer needs, read from the deployment's own files.

    Not a hardcoded list: the tools, their argument schemas and the signed work
    orders are what this deployment declared. A lab showing a different set
    from the one the engine enforces would demonstrate the wrong system.
    """

    roles: dict[str, str]
    tools: list[dict[str, Any]]
    work_orders: list[dict[str, Any]]
    environments: list[str]


def _read(path: str) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return {}


@app.get("/api/catalogue", response_model=Catalogue)
def catalogue() -> Catalogue:
    specs = _read(os.environ.get("AAE_TOOLSPEC_FILE", "")).get("tool_specs", [])
    orders = _read(os.environ.get("AAE_WORK_ORDER_AUTHORITY_FILE", ""))

    tools = [{
        "tool_id": s["tool_id"],
        "description": s.get("description", ""),
        "risk_tier": s.get("risk_tier"),
        "action_type": s.get("action_type"),
        "effect": (s.get("semantic_contract") or {}).get("effect"),
        "argument_schema": s.get("argument_schema", {}),
        "allowed_targets": s.get("allowed_target_environments", []),
    } for s in specs]

    return Catalogue(
        roles=ROLE_NOTES,
        tools=tools,
        work_orders=[{"id": k, "task": v.get("task_text", ""),
                      "operation": v.get("operation")}
                     for k, v in orders.items() if not k.startswith("_")],
        environments=sorted({e for t in tools
                             for e in (t["allowed_targets"] or [])}
                            | {"staging", "prod"}),
    )


# ── Acting ─────────────────────────────────────────────────────────────────

class Attempt(BaseModel):
    role: str
    tool: str
    arguments: dict[str, Any] = {}
    intent_ref: str | None = None
    target_environment: str = "staging"


@app.post("/api/assess")
def assess(attempt: Attempt) -> Any:
    """Submit a call and return the engine's answer in full.

    The response is passed through unmodified. The envelope — grounding
    signals, ToolSpec identity, resolution plan, chain position — is the
    artifact worth looking at, and summarising it here would put this service's
    reading of it between the operator and the record.
    """
    status, body = _call("POST", "/v1/execution/assess", attempt.role, {
        "tool_name": attempt.tool,
        "arguments": attempt.arguments,
        "target_environment": attempt.target_environment,
        "intent_ref": attempt.intent_ref,
    })
    return JSONResponse({
        "http_status": status,
        "as_role": attempt.role,
        "submitted": attempt.model_dump(),
        "response": body,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    })


class Release(BaseModel):
    role: str
    review_item_id: str


@app.post("/api/approve")
def approve(release: Release) -> Any:
    status, body = _call("POST", "/v1/execution/approve", release.role,
                         {"item_id": release.review_item_id})
    return JSONResponse({"http_status": status, "as_role": release.role,
                         "response": body})


class Dispatch(BaseModel):
    role: str
    review_item_id: str
    tool: str
    arguments: dict[str, Any] = {}
    intent_ref: str | None = None
    target_environment: str = "staging"


@app.post("/api/execute")
def execute(dispatch: Dispatch) -> Any:
    status, body = _call("POST", "/v1/execution/execute", dispatch.role, {
        "item_id": dispatch.review_item_id,
        "tool_name": dispatch.tool,
        "arguments": dispatch.arguments,
        "target_environment": dispatch.target_environment,
        "intent_ref": dispatch.intent_ref,
    })
    return JSONResponse({"http_status": status, "as_role": dispatch.role,
                         "response": body})


@app.get("/api/proposals/{proposal_id}/lifecycle")
def lifecycle(proposal_id: str) -> Any:
    status, body = _call(
        "GET", f"/v1/execution/proposals/{proposal_id}/lifecycle", "viewer")
    if status != 200:
        return _problem(f"No lifecycle for {proposal_id}.", status=404)
    return JSONResponse(body)


# ── Benchmarks ─────────────────────────────────────────────────────────────

@app.get("/api/presets")
def presets() -> Any:
    """The benchmark scenarios, as one-click calls.

    Filling in arguments by hand is the wrong kind of work: it makes the easy
    part slow and teaches nothing, and a typo reads as a governance decision.
    Every scenario the benchmark declares is already a complete, valid call
    against this deployment's contract, so they are the presets.

    Scenarios only. The answer key is not served here and not read: a preset
    that told you what the engine ought to decide would stop the lab being a
    place to find out.
    """
    try:
        from aae import benchmark

        out = []
        for suite in benchmark.load_suites():
            out.append({
                "suite": suite["suite"],
                "title": suite.get("title", suite["suite"]),
                "description": suite.get("description", ""),
                "cases": [{
                    "id": c["id"],
                    "scenario": c.get("scenario", ""),
                    "probes": c.get("probes", ""),
                    "call": c["call"],
                } for c in suite["cases"]],
            })
        return JSONResponse(out)
    except Exception as exc:  # noqa: BLE001
        return _problem("The scenarios could not be read.", exc)


@app.get("/api/benchmark/suites")
def suites() -> Any:
    """The declared suites, without running anything."""
    try:
        from aae import benchmark

        return JSONResponse([
            {"suite": s["suite"],
             "title": s.get("title", s["suite"]),
             "description": s.get("description", ""),
             "provenance": s.get("provenance", {}),
             "writes": bool(any(c.get("then") for c in s["cases"])),
             "layers": sorted({c.get("probes", "") for c in s["cases"]}),
             "cases": [{"id": c["id"], "scenario": c.get("scenario", ""),
                        "probes": c.get("probes", ""),
                        "steps": len(c.get("then", []))}
                       for c in s["cases"]]}
            for s in benchmark.load_suites()])
    except Exception as exc:  # noqa: BLE001
        return _problem("The benchmark cases could not be read.", exc)


class BenchRequest(BaseModel):
    """Which scenarios to run. Empty means all of them."""

    suites: list[str] = []
    layers: list[str] = []
    cases: list[str] = []


@app.post("/api/benchmark/run")
def run_benchmark(request: BenchRequest | None = None) -> Any:
    """Score the selected scenarios against the running deployment.

    Every role, because the role-separation scenarios can only observe a
    refusal by attempting the act with the wrong identity — a harness holding
    one omnipotent token would never provoke one.

    Scenarios that declare `then` steps DO write: an approval and an execution
    are governed acts with real effects on the system of record. That is the
    price of reaching those layers at all, and it is why the report carries
    every chain position the run created.
    """
    from contextlib import ExitStack

    try:
        from aae import benchmark
        from remora.sdk import RemoraClient

        chosen = request or BenchRequest()
        selection = benchmark.Selection(
            suites=tuple(chosen.suites),
            layers=tuple(chosen.layers),
            cases=tuple(chosen.cases),
        )
        with ExitStack() as stack:
            clients = {
                role: stack.enter_context(RemoraClient(API, token))
                for role, token in ROLES.items() if token
            }
            return JSONResponse(benchmark.run(clients, only=selection))
    except Exception as exc:  # noqa: BLE001
        return _problem("The benchmark could not be run.", exc)


@app.post("/api/benchmark/verify-trail")
def verify_trail(report: dict[str, Any]) -> Any:
    """Re-read the report's chain positions and confirm them.

    The report claims a set of governed acts happened at particular positions
    in the audit chain. This reads them back and checks the hashes still match.
    Without it the score is the harness's word for what it did; with it, a
    reviewer who trusts nothing here can still check the claim.
    """
    try:
        from aae import benchmark
        from remora.sdk import RemoraClient

        with RemoraClient(API, ROLES["viewer"]) as viewer:
            return JSONResponse(benchmark.verify_trail(viewer, report))
    except Exception as exc:  # noqa: BLE001
        return _problem("The audit trail could not be checked.", exc)


# ── Static ─────────────────────────────────────────────────────────────────

app.mount("/assets", StaticFiles(directory=STATIC), name="assets")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "lab.html", media_type="text/html")
