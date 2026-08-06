# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""The dashboard is read-only, and holds one credential.

It was neither. The first version received all five bearer tokens — including
`domain_expert` and `senior_authority` — and exposed an unauthenticated POST
that ran the scenarios, which perform real writes under those roles. There was
no login, no CSRF protection and no confirmation step. A presentation surface
carrying an approver credential is a high-value target wearing a low-value
label.

These tests hold the corrected shape in place. They check the running
container, not the compose file: what is deployed is the only thing that
matters.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.usefixtures("live")

_CONTAINER = "aae-console-1"


def _inspect() -> dict:
    if shutil.which("docker") is None:
        pytest.skip("docker is not on PATH")
    probe = subprocess.run(["docker", "inspect", _CONTAINER],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip(f"{_CONTAINER} is not running")
    return json.loads(probe.stdout)[0]


def _environment() -> dict[str, str]:
    out = {}
    for entry in _inspect()["Config"]["Env"]:
        key, _, value = entry.partition("=")
        out[key] = value
    return out


def _console_url(live) -> str:
    import os

    port = os.getenv("AAE_CONSOLE_PORT", "8089")
    return f"http://127.0.0.1:{port}"


# ── One credential, and it is the weakest one ──────────────────────────────

def test_the_dashboard_holds_exactly_one_token() -> None:
    tokens = [k for k in _environment() if k.startswith("AAE_TOKEN_")]
    assert tokens == ["AAE_TOKEN_VIEWER"], (
        f"the dashboard holds {tokens}; it needs read and nothing else")


@pytest.mark.parametrize("name", [
    "AAE_TOKEN_AGENT", "AAE_TOKEN_REVIEWER",
    "AAE_TOKEN_EXPERT", "AAE_TOKEN_SENIOR",
])
def test_no_privileged_credential_reaches_the_dashboard(name: str) -> None:
    """Each of these can approve or execute. None belongs on a display."""
    assert name not in _environment(), f"{name} is in the dashboard container"


def test_no_signing_key_reaches_the_dashboard() -> None:
    environment = _environment()
    leaked = [k for k in environment if "SIGNING_KEY" in k]
    assert not leaked, f"signing keys in the dashboard container: {leaked}"


def test_the_dashboard_reads_the_system_of_record_read_only() -> None:
    """It has a database DSN, and it must be the reader's."""
    dsn = _environment().get("AAE_WORKORDER_READER_DSN", "")
    assert dsn, "the dashboard has no reader DSN"
    assert "aae_reader" in dsn, (
        f"the dashboard connects as something other than aae_reader: "
        f"{dsn.split('@')[-1]}")
    assert "aae_worker" not in dsn


# ── Nothing here writes ────────────────────────────────────────────────────

def test_the_scenario_endpoint_is_gone(live) -> None:
    """It ran real writes under approver roles, unauthenticated.

    404 because the route does not exist — not 403 from a live handler that
    someone could re-enable with a config change.
    """
    import httpx

    response = httpx.post(f"{_console_url(live)}/api/scenarios", timeout=15)
    assert response.status_code == 404, (
        f"POST /api/scenarios answered {response.status_code}")


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_the_dashboard_exposes_no_mutating_route(live, method: str) -> None:
    """Every documented path, every mutating verb."""
    import httpx

    base = _console_url(live)
    for path in ("/", "/api/posture", "/api/work-orders"):
        response = httpx.request(method, f"{base}{path}", timeout=15)
        assert response.status_code in (404, 405), (
            f"{method} {path} answered {response.status_code}; a read-only "
            f"surface must not accept it")


# ── It still does its job ──────────────────────────────────────────────────

def test_the_assurance_panel_still_reports(live) -> None:
    """Dropping four tokens must not have cost the dashboard its purpose.

    `/api/posture` is the old path kept so an existing bookmark resolves; it
    now carries the current payload, which uses product terminology rather
    than the engine's internal names. A path that answered with the OLD shape
    would be a compatibility promise this product is not making.
    """
    import httpx

    for path in ("/api/assurance", "/api/posture"):
        body = httpx.get(f"{_console_url(live)}{path}", timeout=15).json()
        assert body["reachable"] is True, path
        assert body["runtime_mode"] == "production", path
        assert body["capabilities"] == ["execution"], path
        assert body["audit"]["verified"] is True, path
        assert body["tool_policy_pinned"] is True, path


def test_the_records_panel_still_reports(live) -> None:
    import httpx

    for path in ("/api/records", "/api/work-orders"):
        body = httpx.get(f"{_console_url(live)}{path}", timeout=15).json()
        assert len(body["work_orders"]) >= 4, path
        assert "events" in body, path


# ── The image itself ───────────────────────────────────────────────────────

def test_the_dashboard_image_does_not_ship_the_governance_engine() -> None:
    """It displays REMORA's decisions; it must not contain REMORA.

    The image installed the pinned wheel and the product package solely to
    shell out to the CLI for the scenarios. Moving that out let the whole
    dependency go with it — a read-only dashboard has no business carrying the
    engine it displays.
    """
    probe = subprocess.run(
        ["docker", "compose", "run", "--rm", "--no-deps",
         "--entrypoint", "python", "console", "-c",
         "import importlib.util as u; print(u.find_spec('remora') is not None)"],
        capture_output=True, text=True, timeout=300)
    if probe.returncode != 0:
        pytest.skip(f"could not inspect the image: {probe.stderr[:200]}")
    assert "True" not in probe.stdout, "remora is installed in the dashboard image"
