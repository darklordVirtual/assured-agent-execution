# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""The CLI surface, exercised.

An untested command is worse than no command: it looks like a capability, and
nobody finds out otherwise until an operator reaches for it during an
incident. Two commands here had zero references anywhere in the repo —
``lifecycle`` and ``verify-effect`` — and one of them printed a raw dict.

These run the real binary against the running stack. Anything that changes
the CLI's exit codes or output shape fails here, which is where a change to
a user-facing contract should fail.
"""
from __future__ import annotations

import json

import pytest

from aae import cli

pytestmark = pytest.mark.usefixtures("live")


def _run(capsys, *argv: str) -> tuple[int, str]:
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


def _propose(capsys, **overrides) -> dict:
    args = ["propose", "read_work_order", "wo_id=WO-1201",
            "--intent", "WO-1201", "--env", "staging"]
    code, out = _run(capsys, *args)
    assert code == cli.EXIT_OK
    return json.loads(out)


# ── doctor ─────────────────────────────────────────────────────────────────

def test_doctor_reports_the_pin_and_the_reader_credential(capsys) -> None:
    code, out = _run(capsys, "doctor")
    assert code == cli.EXIT_OK
    assert "pinned core" in out
    assert "surfaces=execution" in out
    assert "read-only credential" in out


# ── propose ────────────────────────────────────────────────────────────────

def test_propose_reports_the_decision_and_its_reasons(capsys) -> None:
    result = _propose(capsys)
    assert result["decision"] in {"accept", "verify", "abstain", "escalate"}
    assert result["reasons"]
    assert result["proposal_id"]


# ── lifecycle ──────────────────────────────────────────────────────────────

def test_lifecycle_renders_a_trail_an_operator_can_read(capsys) -> None:
    """Not a raw dict.

    The first version printed the whole event mapping on one line — every
    hash at full length, every internal key — which is a record, not a trail.
    A trail names what happened, to which tool, under which authority.
    """
    proposal = _propose(capsys)
    code, out = _run(capsys, "lifecycle", proposal["proposal_id"])

    assert code == cli.EXIT_OK
    assert proposal["proposal_id"] in out
    assert "state" in out
    assert "read_work_order" in out
    # Hashes are truncated: 64 hex characters carry one useful bit in a trail.
    assert "…" in out
    assert "'payload':" not in out, "the raw event mapping leaked into the trail"


def test_lifecycle_json_keeps_the_full_record(capsys) -> None:
    """The abridged view must not be the only one available.

    An operator reconciling an incident needs every field; a person reading a
    trail needs nine. Both, rather than a compromise that serves neither.
    """
    proposal = _propose(capsys)
    code, out = _run(capsys, "lifecycle", proposal["proposal_id"], "--json")

    assert code == cli.EXIT_OK
    record = json.loads(out)
    assert record, "the --json view is empty"
    assert "…" not in out, "the full record must not be truncated"


# ── verify-effect ──────────────────────────────────────────────────────────

def test_verify_effect_reports_a_verified_effect(seeded, capsys) -> None:
    """WO-1150, which the seed data closes — not a work order some other
    scenario happened to close first.

    An earlier version used WO-1202 and passed only when a scenario had run
    before it. That is a test whose result depends on execution order, which
    is a test that will eventually lie in one direction or the other.
    """
    proposal = _propose(capsys)
    code, out = _run(capsys, "verify-effect", proposal["proposal_id"],
                     "close_work_order", "wo_id=WO-1150", "--no-record")
    assert code == cli.EXIT_OK
    assert "EFFECT_VERIFIED" in out


def test_verify_effect_exits_nonzero_only_on_a_mismatch(seeded, capsys) -> None:
    """The exit code is the contract a CI job branches on.

    Only MISMATCH means "we looked and it was wrong". The unknowns must exit
    0, because a job that failed on "we could not look" would train people to
    ignore it — and a job that passed on a real mismatch is worse.
    """
    proposal = _propose(capsys)
    code, out = _run(capsys, "verify-effect", proposal["proposal_id"],
                     "close_work_order", "wo_id=WO-1203", "--no-record")
    assert "EFFECT_MISMATCH" in out
    assert code == cli.EXIT_FAILED


def test_a_tool_with_no_reader_is_reported_as_unsupported(capsys) -> None:
    proposal = _propose(capsys)
    code, out = _run(capsys, "verify-effect", proposal["proposal_id"],
                     "purge_work_order_history", "wo_id=WO-1150")
    assert code == cli.EXIT_OK
    assert "EFFECT_UNSUPPORTED" in out


# ── evidence ───────────────────────────────────────────────────────────────

def test_evidence_export_writes_a_verifiable_archive(capsys, tmp_path) -> None:
    import hashlib

    proposal = _propose(capsys)
    code, out = _run(capsys, "evidence", "export",
                     proposal["proposal_id"], "--out", str(tmp_path))
    assert code == cli.EXIT_OK

    manifest = json.loads(out)
    assert manifest["proposals_exported"] == [proposal["proposal_id"]]
    for name, declared in manifest["files"].items():
        actual = "sha256:" + hashlib.sha256(
            (tmp_path / name).read_bytes()).hexdigest()
        assert actual == declared, f"{name} does not match its manifest digest"
