# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""The lab can act. These tests hold the line on what that must not cost.

The lab exists because a demonstration needs to try the system in each role.
It holds every role token — the exact posture that was removed from the console
as a P0. That is acceptable here only while three things stay true:

  * the console keeps its single read-only credential, so its own report about
    itself stays believable;
  * the lab cannot talk its way past the engine — choosing a role selects a
    credential, it does not grant that credential's authority;
  * the lab is unmistakably labelled, so nobody demonstrates from the wrong tab.

Each of those is a test below.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.usefixtures("live")

ROOT = Path(__file__).resolve().parents[2]


def _lab_url() -> str:
    return f"http://127.0.0.1:{os.getenv('AAE_LAB_PORT', '8090')}"


def _get(path: str) -> httpx.Response:
    try:
        return httpx.get(f"{_lab_url()}{path}", timeout=30)
    except httpx.HTTPError as exc:
        pytest.skip(f"lab not reachable: {exc}")


def _post(path: str, body: dict) -> httpx.Response:
    try:
        return httpx.post(f"{_lab_url()}{path}", json=body, timeout=60)
    except httpx.HTTPError as exc:
        pytest.skip(f"lab not reachable: {exc}")


def _inspect(container: str) -> dict:
    if shutil.which("docker") is None:
        pytest.skip("docker is not on PATH")
    probe = subprocess.run(["docker", "inspect", container],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip(f"{container} is not running")
    return json.loads(probe.stdout)[0]


def _environment(container: str) -> dict[str, str]:
    out = {}
    for entry in _inspect(container)["Config"]["Env"]:
        key, _, value = entry.partition("=")
        out[key] = value
    return out


# ── The separation that makes the console's own claims worth anything ──────

def test_the_console_still_holds_one_read_only_token() -> None:
    """The lab must not have relaxed the console.

    This is the property the lab was built as a separate service to protect:
    the console reports `console_access: read-only` about itself, and that
    report is worthless if the same process can approve.
    """
    tokens = [k for k in _environment("aae-console-1")
              if k.startswith("AAE_TOKEN_")]
    assert tokens == ["AAE_TOKEN_VIEWER"], (
        f"the console now holds {tokens}. The lab exists so that it does not.")


def test_the_lab_and_the_console_are_different_images() -> None:
    lab = _inspect("aae-lab-1")["Image"]
    console = _inspect("aae-console-1")["Image"]
    assert lab != console, (
        "the lab and the console run the same image, so the console image now "
        "carries the governance engine and every role token")


def test_the_console_image_still_carries_no_remora_package() -> None:
    probe = subprocess.run(
        ["docker", "compose", "exec", "-T", "console", "python", "-c",
         "import importlib.util;"
         "print('present' if importlib.util.find_spec('remora') else 'absent')"],
        cwd=ROOT, capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("console container not available")
    assert "absent" in probe.stdout, (
        "REMORA is installed in the console image again")


# ── The lab cannot grant what it does not hold ─────────────────────────────

def test_choosing_a_role_selects_a_credential_and_grants_no_authority() -> None:
    """The demonstration that matters most.

    An operator token cannot approve, and the refusal must come from the
    control plane rather than from a check inside the lab — otherwise the lab
    is demonstrating its own validation, which is not the system under test.
    """
    assessed = _post("/api/assess", {
        "role": "operator", "tool": "set_work_order_priority",
        "arguments": {"wo_id": "WO-1203", "priority": "high"},
        "intent_ref": "WO-1203", "target_environment": "prod"}).json()
    body = assessed["response"]
    item = body.get("review_item_id")
    if not item:
        pytest.skip(f"no review item to approve: {body.get('decision')}")

    refused = _post("/api/approve",
                    {"role": "operator", "review_item_id": item}).json()
    assert refused["http_status"] == 403, (
        f"the operator approved its own proposal: {refused}")
    # The engine's own words, not a message the lab composed.
    detail = json.dumps(refused["response"]).lower()
    assert "operator" in detail and "review" in detail, (
        f"the refusal did not come from the control plane: {refused}")

    allowed = _post("/api/approve",
                    {"role": "reviewer", "review_item_id": item}).json()
    assert allowed["http_status"] == 200, (
        f"the reviewer could not approve what it is for: {allowed}")


def test_the_lab_submits_what_it_is_given_without_pre_filtering() -> None:
    """A call the ToolSpec forbids must reach the engine and be refused there.

    If the lab validated first, a demonstration of contract enforcement would
    be a demonstration of the lab's form validation instead.
    """
    result = _post("/api/assess", {
        "role": "operator", "tool": "create_work_order",
        "arguments": {"wo_id": "WO-1310", "asset_id": "P-7",
                      "bypass_review": True},
        "intent_ref": "WO-1310", "target_environment": "staging"}).json()
    assert result["http_status"] != 200
    assert "toolspec" in json.dumps(result["response"]).lower(), (
        f"the refusal did not come from the ToolSpec layer: {result}")


# ── It says what it is ─────────────────────────────────────────────────────

def test_the_page_says_it_can_act_and_has_no_login() -> None:
    """Someone on the wrong tab must notice within a second."""
    html = _get("/").text
    assert "Can act" in html, "the lab does not announce that it can act"
    lowered = html.lower()
    assert "demonstration" in lowered
    assert "no login" in lowered, (
        "the page does not disclose that it has no authentication")


def test_the_lab_sets_the_same_security_headers_as_the_console() -> None:
    """Being able to act is not a reason to serve a weaker page."""
    headers = _get("/").headers
    csp = headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"


def test_the_lab_client_never_emits_an_inline_style() -> None:
    """Same CSP trap as the console: the attribute is dropped in silence."""
    script = (ROOT / "console" / "static" / "lab.js").read_text(encoding="utf-8")
    for pattern, description in (
        (r"""\sstyle\s*=\s*\\?["']""", "a style attribute in emitted markup"),
        (r"""setAttribute\(\s*["']style["']""", "setAttribute with 'style'"),
    ):
        hit = re.search(pattern, script)
        assert hit is None, (
            f"lab.js builds an inline style through {description} "
            f"({hit.group(0)!r}); the CSP drops it without saying so")


# ── The catalogue comes from the deployment, not from a copy ───────────────

def test_the_composer_offers_the_tools_the_engine_enforces() -> None:
    """A lab offering a different tool set demonstrates a different system."""
    catalogue = _get("/api/catalogue").json()
    offered = {t["tool_id"] for t in catalogue["tools"]}
    specs = json.loads((ROOT / "toolpacks" / "work_order" / "tool_specs.json")
                       .read_text(encoding="utf-8"))["tool_specs"]
    declared = {s["tool_id"] for s in specs}
    assert offered == declared, (
        f"the lab offers tools the deployment does not declare "
        f"({sorted(offered - declared)}) or omits ones it does "
        f"({sorted(declared - offered)})")


def test_the_presets_are_complete_calls_that_need_no_typing() -> None:
    """The point of a preset: nothing left to fill in.

    A preset missing a required argument is worse than no preset, because it
    looks ready and produces a contract refusal that reads like a governance
    decision.
    """
    presets = _get("/api/presets").json()
    assert presets, "no presets are served"

    specs = {s["tool_id"]: s for s in json.loads(
        (ROOT / "toolpacks" / "work_order" / "tool_specs.json")
        .read_text(encoding="utf-8"))["tool_specs"]}

    for suite in presets:
        for case in suite["cases"]:
            call = case["call"]
            assert call.get("tool"), f"{case['id']} names no tool"
            spec = specs.get(call["tool"])
            if spec is None:
                continue      # deliberately unknown tools are a scenario
            required = set(spec["argument_schema"].get("required", []))
            supplied = set(call.get("arguments", {}))
            # A scenario may deliberately omit a required argument; it must say
            # so in its own description rather than look like an oversight.
            if required - supplied:
                assert "no " in case["scenario"].lower()                     or "missing" in case["scenario"].lower(), (
                    f"{suite['suite']}/{case['id']} omits "
                    f"{sorted(required - supplied)} without saying why")


def test_the_presets_never_reveal_an_answer() -> None:
    """A preset that told you the expected decision would defeat the blind run.

    The lab is where someone finds out what the engine does. Serving the key
    alongside the scenario would turn that into reading it off a card.
    """
    body = _get("/api/presets").text.lower()
    for leak in ('"decision"', '"because"', '"expect"', '"known_gap"',
                 '"refused"'):
        assert leak not in body, (
            f"the preset endpoint serves {leak}, which is answer-key material")


def test_the_benchmark_runs_from_the_lab_with_the_same_verdict(live) -> None:
    """The browser and the CLI must not disagree about the score.

    Both sides need every role. The multi-step scenarios can only observe a
    refusal by attempting the act with the wrong identity, so a comparison run
    holding one token would score four regressions for a reason that has
    nothing to do with the engine.
    """
    from contextlib import ExitStack

    from remora.sdk import RemoraClient

    from aae import benchmark

    # One suite, and one that does not write: this test runs the corpus twice,
    # and doubling the approvals and executions to compare two scores would be
    # a side effect nobody asked this test for.
    body = {"suites": ["contract-enforcement"]}
    over_http = _post("/api/benchmark/run", body).json()

    with ExitStack() as stack:
        clients = {
            role: stack.enter_context(RemoraClient(live.api_url, token))
            for role, token in (("operator", live.token_agent),
                                ("viewer", live.token_viewer),
                                *live.approver_tokens.items())
        }
        in_process = benchmark.run(
            clients, benchmark.Selection(suites=("contract-enforcement",)))

    assert over_http["cases"] == in_process["cases"]
    assert over_http["regressions"] == in_process["regressions"]
    assert over_http["known_gaps"] == in_process["known_gaps"]


def test_a_writing_suite_is_flagged_before_it_is_run() -> None:
    """The lab must say which suites change the system of record.

    Discovering afterwards that a benchmark approved and executed against real
    records is the kind of surprise that makes people stop trusting a tool.
    """
    suites = _get("/api/benchmark/suites").json()
    writing = [s for s in suites if s.get("writes")]
    assert writing, "no suite is marked as writing, but one performs acts"
    for suite in suites:
        performs = any(c.get("steps") for c in suite["cases"])
        assert suite["writes"] == performs, (
            f"{suite['suite']} declares writes={suite['writes']} but "
            f"{'has' if performs else 'has no'} governed acts")
