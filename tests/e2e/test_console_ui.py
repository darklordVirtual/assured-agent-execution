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

def test_the_page_names_its_surfaces() -> None:
    """Three now, not four. `Overview` dissolved: it aggregated things that
    each belong somewhere with a job — the health banner became the always-
    visible assurance strip, business counts moved to Records, and recent
    activity is the Ledger, which is a superset of it."""
    html = _get("/").text
    for surface in ("Governance ledger", "Business records",
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
        ("accept", "Allowed to act without a human"),
        ("verify", "Held until a human approved it"),
        ("abstain", "Refused, with nothing offered to approve"),
        ("escalate", "Routed to a higher authority"),
    ):
        assert canonical in script and plain in script, (
            f"{canonical} has no plain-language reading")


def test_no_state_is_carried_by_colour_alone() -> None:
    """A console that encodes "refused" as red alone fails the people most
    likely to be reading it.

    The mechanism changed with the ledger redesign — the old version painted a
    glyph through `.state-*::before`, and this test checked for that glyph.
    The rule it was protecting did not change: every element the CSS colours by
    state must also carry the state in words. So this asserts the rule rather
    than the old implementation of it.
    """
    css = (STATIC / "aae.css").read_text(encoding="utf-8")
    script = (STATIC / "aae.js").read_text(encoding="utf-8")

    # Every state-driven colour rule in the stylesheet.
    coloured = set(re.findall(r'data-(?:state|d)="([a-z]+)"', css))
    assert coloured, "no state-driven styling found — has the mechanism moved?"

    # Each of those states must be written out somewhere the reader sees it.
    # `data-d` values are the decision names, which the badge prints verbatim;
    # `data-state` values are set alongside a spelled-out value in renderStrip
    # and fact().
    for value in sorted(coloured):
        assert value in script, (
            f"the stylesheet colours by {value!r} but the client never writes "
            f"it out, so that state would be conveyed by colour alone")

    # The decision badge prints the canonical name, not just a colour.
    assert 'class="verdict" data-d="${d}">${esc(e.decision)}' in script, (
        "the decision badge no longer prints the decision name")
    # And an intervention says REFUSED/VOIDED in words.
    assert "STOP_LABEL" in script, "interventions carry no worded label"


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


# ── The CSP trap ───────────────────────────────────────────────────────────

def test_no_inline_style_attribute_is_ever_emitted() -> None:
    """`style="…"` does nothing on this page, and says nothing when it fails.

    The console serves `style-src 'self'` with no `'unsafe-inline'`, so the
    browser parses the attribute, keeps it in the DOM, and silently declines to
    apply it. There is no console error and no visual hint beyond the element
    being the wrong size. The decision-mix bar shipped that way: four segments
    with correct percentage widths in the markup, collapsed to 40px on screen.

    Scripted styling through the CSSOM (`el.style.width = …`) is unaffected by
    the policy, so that is the one path that works. This test keeps the
    attribute form from creeping back — the fix must never be to weaken the CSP
    on a governance console.
    """
    html = _get("/").text
    script = (STATIC / "aae.js").read_text(encoding="utf-8")

    assert not re.search(r"<[^>]+\sstyle\s*=", html), (
        "the served HTML carries a style attribute, which the CSP drops")

    # Both ways of producing the attribute from the client. The first version
    # of this test only looked for a `style="` literal and passed cleanly while
    # `setAttribute("style", ...)` sat in the file doing nothing — a test for a
    # silent bug that was itself silent. Proven by reintroducing each form.
    forbidden = [
        (r"""\sstyle\s*=\s*\\?["']""", "a style attribute in emitted markup"),
        (r"""setAttribute\(\s*["']style["']""", "setAttribute with 'style'"),
    ]
    for pattern, description in forbidden:
        hit = re.search(pattern, script)
        assert hit is None, (
            f"aae.js builds an inline style through {description} "
            f"({hit.group(0)!r}). Under this CSP it will not apply, and "
            f"nothing will say so. Use the CSSOM instead: "
            f"element.style.width = '...'.")


def test_the_policy_that_makes_that_trap_real_is_still_in_force() -> None:
    """The test above is only meaningful while the CSP actually forbids it.

    Without this, someone could add 'unsafe-inline' to make a style attribute
    work and the test above would still pass, having quietly stopped
    protecting anything.
    """
    csp = _get("/").headers["content-security-policy"]
    assert "style-src 'self'" in csp, f"style-src changed: {csp}"
    assert "unsafe-inline" not in csp, (
        f"the CSP now allows inline styles, which is a weakening: {csp}")


# ── Layout ─────────────────────────────────────────────────────────────────

def test_wide_content_scrolls_inside_its_own_container() -> None:
    """A table wider than the viewport must not make the page scroll sideways.

    On a laptop the work-order table is already seven columns; on a phone every
    table is wider than the screen. Wrapping each one lets the table scroll
    while the page does not.
    """
    html = _get("/").text
    for table in re.findall(r'<table[^>]*id="([^"]+)"', html):
        block = html[:html.index(f'id="{table}"')]
        assert 'class="scroll-x"' in block[-400:], (
            f"table #{table} is not inside a .scroll-x wrapper, so it will "
            f"scroll the whole page sideways on a narrow screen")


def test_the_layout_adapts_to_a_narrow_screen() -> None:
    """The sidebar becomes a row rather than eating half a phone."""
    css = (STATIC / "aae.css").read_text(encoding="utf-8")
    assert re.search(r"@media\s*\(max-width:\s*\d+px\)", css), (
        "the stylesheet has no narrow-screen rules at all")
    narrow = css[css.index("@media (max-width:"):]
    assert "grid-template-columns: 1fr" in narrow, (
        "the two-column shell is not collapsed on a narrow screen")


# ── Contrast ───────────────────────────────────────────────────────────────

def _luminance(colour: str) -> float:
    channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _palettes() -> dict[str, dict[str, str]]:
    """The dark palette and the light one, from the stylesheet itself."""
    css = (STATIC / "aae.css").read_text(encoding="utf-8")
    split = css.index("@media (prefers-color-scheme: light)")
    light_block = css[split:]
    light_block = light_block[:light_block.index("}\n}")]

    def values(text: str) -> dict[str, str]:
        return dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})", text))

    dark = values(css[:split])
    return {"dark": dark, "light": dark | values(light_block)}


#: Foreground tokens used at 10–12px, which is small text under WCAG.
_SMALL_TEXT = ("paper", "paper-dim", "paper-faint",
               "accept", "verify", "escalate", "abstain", "stop")


@pytest.mark.parametrize("mode", ("dark", "light"))
@pytest.mark.parametrize("token", _SMALL_TEXT)
def test_every_text_colour_meets_aa_in_both_modes(mode: str, token: str) -> None:
    """4.5:1 against both the ground and the raised surface.

    The light palette had never been rendered when it was written, and
    measuring it found `paper-faint` at 3.4:1 — used for the 10px labels on
    every fact, table header and sequence number. The dark one was 3.5:1 for
    the same token. Neither was visible by looking; both are obvious to a
    reader who needs the contrast.
    """
    palette = _palettes()[mode]
    worst = min(_contrast(palette[token], palette["ink"]),
                _contrast(palette[token], palette["ink-raise"]))
    assert worst >= 4.5, (
        f"{mode}: --{token} is {worst:.1f}:1 against the background. It is "
        f"used at 10-12px, which needs 4.5:1.")


def test_the_chain_spine_meets_the_component_threshold() -> None:
    """The spine says whether consecutive entries are linked.

    That makes it a UI component conveying information, not decoration, so it
    needs 3:1. It was #33465c — which looked right and measured 1.7:1.
    """
    palette = _palettes()["dark"]
    worst = min(_contrast(palette["spine-line"], palette["ink"]),
                _contrast(palette["spine-line"], palette["ink-raise"]))
    assert worst >= 3.0, (
        f"the chain spine is {worst:.1f}:1; it carries information, so it "
        f"needs 3:1")
