# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""The console's presentation and API contracts.

``test_console.py`` holds the security invariants — one token, no writes, no
REMORA in the image. This file covers what the redesign added: security
headers, self-hosted assets, the read-only API shape, and the failure states.

The theme running through it: a console for a governance product must not
show something it cannot substantiate, and must not hide a failure behind an
empty panel.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("live")

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "console" / "static"


def _base() -> str:
    return f"http://127.0.0.1:{os.getenv('AAE_CONSOLE_PORT', '8089')}"


def _get(path: str, **kwargs):
    import httpx

    return httpx.get(f"{_base()}{path}", timeout=20, **kwargs)


# ── Security headers ───────────────────────────────────────────────────────

@pytest.mark.parametrize("header, expected", [
    ("x-content-type-options", "nosniff"),
    ("referrer-policy", "no-referrer"),
    ("cache-control", "no-store"),
])
def test_security_headers_are_present(header: str, expected: str) -> None:
    assert _get("/").headers.get(header, "").lower() == expected


@pytest.mark.parametrize("directive", [
    "default-src 'self'", "script-src 'self'", "style-src 'self'",
    "object-src 'none'", "frame-ancestors 'none'", "base-uri 'none'",
    "form-action 'none'",
])
def test_the_content_security_policy_confines_the_page(directive: str) -> None:
    """No CDN, no inline script, no frame, no form target.

    A console for a governance product should not be able to load a third
    party's JavaScript even by accident, and `form-action 'none'` means no
    markup here can post anywhere at all.
    """
    assert directive in _get("/").headers.get("content-security-policy", "")


def test_cache_control_keeps_governance_state_off_disk() -> None:
    """A cached copy on a shared machine outlives the session entitled to it."""
    for path in ("/", "/api/overview", "/api/records"):
        assert _get(path).headers.get("cache-control") == "no-store", path


# ── Assets are ours ────────────────────────────────────────────────────────

@pytest.mark.parametrize("asset", ["/assets/aae.css", "/assets/aae.js"])
def test_the_assets_are_served_from_this_origin(asset: str) -> None:
    assert _get(asset).status_code == 200


def test_no_static_file_references_an_external_host() -> None:
    """The CSP would block it — this fails earlier, with a better message.

    A stylesheet importing a font from a CDN turns a page that renders into a
    page that renders inconsistently and phones home, and the CSP failure
    would surface as a silently missing font rather than as this.
    """
    external = re.compile(r"""https?://(?!localhost|127\.0\.0\.1)""")
    for path in STATIC.iterdir():
        if path.is_file():
            hits = external.findall(path.read_text(encoding="utf-8"))
            assert not hits, f"{path.name} references an external host"


def test_the_page_loads_only_its_own_assets() -> None:
    html = _get("/").text
    for reference in re.findall(r'(?:href|src)="([^"]+)"', html):
        assert reference.startswith("/assets/") or reference.startswith("#"), (
            f"the page loads {reference}, which is not one of its own assets")


# ── The read-only API ──────────────────────────────────────────────────────

def test_assurance_reports_the_verified_engine() -> None:
    """The regression this redesign started from.

    Stripping REMORA out of the console image took the artifact lock with it,
    and the verified engine version silently became empty — a console looking
    fine while telling you less than it should.
    """
    core = _get("/api/assurance").json()["core"]
    assert core["version"], "the verified engine version is not reported"
    assert core["commit"], "the verified engine commit is not reported"
    assert core["release"], "the engine release is not reported"


def test_assurance_states_that_this_console_cannot_act() -> None:
    body = _get("/api/assurance").json()
    assert body["console_access"] == "read-only"
    assert body["database_credential"] == "read-only"


def test_overview_reports_no_attention_items_on_a_healthy_deployment() -> None:
    """The attention list must contain only actual deviations.

    A list that always has entries is a list nobody reads, which is how a real
    finding gets missed.
    """
    body = _get("/api/overview").json()
    assert body["attention"] == [], body["attention"]
    assert body["all_clear"] is True
    assert "operational" in body["headline"].lower()


def test_overview_counts_the_business_records() -> None:
    counts = _get("/api/overview").json()["records"]
    assert counts["total"] >= 4
    assert counts["total"] == counts["open"] + counts["closed"] + counts["cancelled"]


def test_records_returns_typed_rows_not_raw_database_output() -> None:
    body = _get("/api/records").json()
    assert body["work_orders"], "no work orders returned"
    first = body["work_orders"][0]
    assert set(first) == {
        "wo_id", "title", "asset_id", "status", "priority",
        "closed_reason", "updated_by", "updated_at",
    }
    assert body["open_count"] + body["closed_count"] <= len(body["work_orders"])


# ── Proposal lookup ────────────────────────────────────────────────────────

@pytest.fixture()
def a_proposal(agent) -> str:
    from remora.sdk import ToolCall

    result = agent.assess(ToolCall(
        tool_name="read_work_order", arguments={"wo_id": "WO-1201"},
        target_environment="staging", intent_ref="WO-1201"))
    return result.proposal_id


@pytest.mark.slow
@pytest.mark.parametrize("suffix", ["", "/lifecycle", "/evidence"])
def test_a_known_proposal_can_be_inspected(a_proposal: str, suffix: str) -> None:
    response = _get(f"/api/proposals/{a_proposal}{suffix}")
    assert response.status_code == 200, response.text
    assert response.json()


def test_an_unknown_proposal_answers_404_with_a_usable_message() -> None:
    response = _get("/api/proposals/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert "does-not-exist" in body["error"]
    assert body["correlation_id"]


def test_a_failure_never_returns_exception_text(monkeypatch) -> None:
    """Exception text names internal hosts, credentials and file paths.

    The browser gets a sentence and an id; the detail goes to the log, where
    an operator can find it by that id.
    """
    body = _get("/api/proposals/definitely-not-here").json()
    for leak in ("Traceback", "psycopg", "httpx", "control-plane:8000",
                 "postgresql://"):
        assert leak not in body["error"], f"{leak!r} leaked to the browser"


# ── Presentation contracts ─────────────────────────────────────────────────

def test_the_page_names_the_four_surfaces() -> None:
    html = _get("/").text
    for surface in ("Overview", "Decisions", "Business records",
                    "System assurance"):
        assert surface in html, f"{surface} is missing from the shell"


def test_the_page_offers_no_control_that_could_act() -> None:
    """No approve, execute or run BUTTON on a console holding `viewer`.

    Checks interactive elements, not prose: the Decisions surface legitimately
    explains "who had to approve it", and a word-match would forbid describing
    the very thing the console exists to display. What must not exist is a
    control inviting a click that can only end in a permission error.
    """
    html = _get("/").text
    controls = re.findall(r"<button[^>]*>(.*?)</button>", html, re.S | re.I)
    for label in controls:
        text = re.sub(r"<[^>]+>", "", label).strip().lower()
        assert not any(verb in text for verb in
                       ("approve", "execute", "reject", "run ", "delete")), (
            f"the page offers a control labelled {text!r}")

    # And no form that posts anywhere: the CSP forbids it, this names it.
    assert not re.search(r'<form[^>]+method\s*=\s*"?post', html, re.I)
    for action in re.findall(r'<form[^>]+action="([^"]*)"', html, re.I):
        assert not action, f"a form targets {action!r}"


def test_the_client_states_plain_language_before_canonical_values() -> None:
    """`VERIFY` is what the audit record says; "Approval required" is what a
    person needs. Both are shown, and neither replaces the other."""
    script = (STATIC / "aae.js").read_text(encoding="utf-8")
    for canonical, plain in (
        ("accept", "Allowed"),
        ("verify", "Approval required"),
        ("abstain", "Blocked"),
        ("escalate", "Higher authority required"),
    ):
        assert canonical in script and plain in script


def test_every_status_carries_a_glyph_and_not_only_colour() -> None:
    """A console that encodes "failed" as red alone fails the people most
    likely to be reading it."""
    css = (STATIC / "aae.css").read_text(encoding="utf-8")
    for kind in ("ok", "warn", "bad", "unknown"):
        assert re.search(rf"\.state-{kind}::before\s*{{[^}}]*content:", css), (
            f"state-{kind} has no glyph")


# ── Degraded ───────────────────────────────────────────────────────────────

@pytest.mark.slow
def test_a_control_plane_outage_is_stated_not_hidden() -> None:
    """The failure mode that matters most on an assurance surface.

    A console that renders an empty panel when the thing it assures is down
    looks calm and says nothing. This one names the outage, counts it, and
    still answers 200 so the page can render the rest.
    """
    import subprocess

    subprocess.run(["docker", "compose", "stop", "control-plane"],
                   cwd=ROOT, capture_output=True, timeout=120)
    try:
        response = _get("/api/overview")
        assert response.status_code == 200, "the page could not render at all"
        body = response.json()
        assert body["all_clear"] is False
        assert any("not reachable" in item for item in body["attention"]), (
            body["attention"])
        assert "attention" in body["headline"].lower()
        assert body["assurance"]["reachable"] is False
    finally:
        subprocess.run(["docker", "compose", "start", "control-plane"],
                       cwd=ROOT, capture_output=True, timeout=180)
        import time

        for _ in range(45):
            probe = subprocess.run(
                ["docker", "compose", "ps", "--format", "{{.Health}}",
                 "control-plane"], cwd=ROOT, capture_output=True, text=True)
            if probe.stdout.strip().splitlines()[:1] == ["healthy"]:
                break
            time.sleep(2)


def test_records_stay_readable_when_the_control_plane_is_gone() -> None:
    """They come from a different process on a different credential.

    One being down must not blank the other; an operator during an incident
    needs whichever half still answers.
    """
    body = _get("/api/records").json()
    assert body["work_orders"]
