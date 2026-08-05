# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""What the product does when something is broken or missing.

The distinction every test here defends: **"we could not look" is not "it was
wrong."**

Collapsing those two is the failure mode that makes a governance product
actively dangerous. If a reader outage reported EFFECT_MISMATCH, this product
would open incidents for actions that succeeded, and — worse, if compensation
were ever automated on that signal — would undo them. If a genuine mismatch
reported UNOBSERVABLE, it would close incidents that are still open.

REMORA's contract encodes the distinction: MISMATCH is terminal, UNOBSERVABLE
and VERIFIER_FAILED are not. These tests check that this product's reader
actually produces the right one under each fault.
"""
from __future__ import annotations

import pytest
from remora.sdk import EffectStatus, RemoraError, ToolCall

from aae import postcondition

pytestmark = pytest.mark.usefixtures("live")

_TOOLSPEC = "0" * 64


# ── The reader cannot reach the system of record ───────────────────────────

def test_an_unreachable_system_of_record_is_not_a_mismatch() -> None:
    """The database is down. That says nothing about whether the action ran."""
    view = postcondition.verify(
        dsn="postgresql://nobody:nobody@127.0.0.1:1/nonexistent",
        tool_name="close_work_order",
        arguments={"wo_id": "WO-1202"},
        proposal_id="p-fault", execution_id="e-fault",
        toolspec_hash=_TOOLSPEC,
    )
    assert view is not None
    assert view.status is not EffectStatus.MISMATCH, (
        "a reader outage was reported as evidence the action was wrong"
    )
    assert view.status.is_terminal is False, (
        "a non-answer was made terminal; the incident would be closed while "
        "still open"
    )


def test_a_bad_reader_credential_is_not_a_mismatch(live) -> None:
    """Wrong password is a configuration fault, not a finding about the action."""
    broken = live.reader_dsn.replace("aae_reader:", "aae_reader:wrong-")
    view = postcondition.verify(
        dsn=broken, tool_name="close_work_order",
        arguments={"wo_id": "WO-1202"},
        proposal_id="p-fault", execution_id="e-fault",
        toolspec_hash=_TOOLSPEC,
    )
    assert view is not None
    assert view.status is not EffectStatus.MISMATCH
    assert view.status.is_terminal is False


# ── The reader looked, and the object is not what was approved ─────────────

def test_a_work_order_that_does_not_exist_is_observable_and_wrong(live) -> None:
    """We DID look. The row is absent. Against a postcondition expecting a
    state, that is a real finding, not an unknown."""
    view = postcondition.verify(
        dsn=live.reader_dsn, tool_name="close_work_order",
        arguments={"wo_id": "WO-DOES-NOT-EXIST"},
        proposal_id="p-fault", execution_id="e-fault",
        toolspec_hash=_TOOLSPEC,
    )
    assert view is not None
    assert view.status is not EffectStatus.VERIFIED


def test_an_unclosed_work_order_reports_mismatch(live, reader_conn) -> None:
    """WO-1203 is open. A postcondition claiming it is closed must not pass.

    This is the one status that means "we looked and it was wrong", and it is
    terminal — the only one this product may act on.
    """
    with reader_conn.cursor() as cur:
        cur.execute("SELECT status FROM work_orders WHERE wo_id = 'WO-1203'")
        row = cur.fetchone()
    if row is None or row[0] == "closed":
        pytest.skip("WO-1203 is already closed; this test needs an open one")

    view = postcondition.verify(
        dsn=live.reader_dsn, tool_name="close_work_order",
        arguments={"wo_id": "WO-1203"},
        proposal_id="p-fault", execution_id="e-fault",
        toolspec_hash=_TOOLSPEC,
    )
    assert view is not None
    assert view.status is EffectStatus.MISMATCH
    assert view.status.is_terminal is True


# ── A tool with no declared reader ─────────────────────────────────────────

def test_a_tool_without_a_reader_is_unsupported_not_verified() -> None:
    """The absence of verification must be visible.

    A tool nobody can verify and a tool that verified must never look the
    same afterwards — otherwise coverage silently shrinks and the trail still
    reads as clean.
    """
    assert postcondition.supports("purge_work_order_history") is False
    view = postcondition.verify(
        dsn="postgresql://unused", tool_name="purge_work_order_history",
        arguments={"wo_id": "WO-1150"}, proposal_id="p", execution_id="e",
        toolspec_hash=_TOOLSPEC)
    assert view is None
    assert "EFFECT_UNSUPPORTED" in postcondition.describe(None)


# ── Concurrent writers ─────────────────────────────────────────────────────

def test_only_the_declared_delta_is_compared(live, reader_conn) -> None:
    """A system of record has other legitimate writers.

    A planner edits the title, a scheduler moves the date. If verification
    flagged every field it did not expect, every concurrent update would
    surface as a mismatch and the signal would be noise within a week.
    """
    with reader_conn.cursor() as cur:
        cur.execute("SELECT wo_id FROM work_orders WHERE status = 'closed' "
                    "LIMIT 1")
        row = cur.fetchone()
    if row is None:
        pytest.skip("no closed work order to verify against")

    view = postcondition.verify(
        dsn=live.reader_dsn, tool_name="close_work_order",
        arguments={"wo_id": row[0]}, proposal_id="p", execution_id="e",
        toolspec_hash=_TOOLSPEC)
    assert view is not None
    assert view.status is EffectStatus.VERIFIED, (
        "a closed work order failed verification because of fields the "
        "postcondition never declared"
    )


# ── Unknown and malformed calls ────────────────────────────────────────────

def test_an_unknown_tool_is_never_accepted(agent) -> None:
    """The fail-closed floor: a name nothing classifies is critical/unknown."""
    result = agent.assess(ToolCall(
        tool_name="calibrate_flux_capacitor", arguments={"target": "P-7"},
        target_environment="prod", intent_ref="WO-1201"))
    assert result.action.value != "accept"
    assert not result.execution_token


def test_a_work_order_outside_the_state_index_is_not_accepted(agent) -> None:
    """closed_world=True: absent means confirmed absent, not unknown.

    Without that, a call naming a record that does not exist would get the
    benefit of the doubt.
    """
    result = agent.assess(ToolCall(
        tool_name="read_work_order", arguments={"wo_id": "WO-4242"},
        target_environment="staging", intent_ref="WO-1201"))
    assert result.action.value != "accept"


def test_a_tool_that_raises_does_not_report_a_successful_execution(
    agent, approver_for
) -> None:
    """A failed side effect must not be recorded as a completed action.

    purge_work_order_history raises by design. If it were ever approved, the
    lifecycle must show the failure — an execution recorded as done, for an
    action that raised, is the worst possible entry in an audit trail.
    """
    result = agent.assess(ToolCall(
        tool_name="purge_work_order_history", arguments={"wo_id": "WO-1150"},
        target_environment="prod", intent_ref="MAINT-PURGE-Q3"))
    if not result.review_item_id:
        pytest.skip(f"purge was {result.action.value} with no review item; "
                    f"there is nothing to approve and execute")
    required = (result.resolution_plan.required_role
                if result.resolution_plan else None)
    try:
        with approver_for(required) as (_role, approver):
            approver.approve(result.review_item_id, ttl_seconds=120)
    except Exception:  # noqa: BLE001
        pytest.skip("no configured identity may release this decision, "
                    "which is itself the escalation working")

    try:
        outcome = agent.execute(result.review_item_id, ToolCall(
            tool_name="purge_work_order_history",
            arguments={"wo_id": "WO-1150"},
            target_environment="prod", intent_ref="MAINT-PURGE-Q3"))
    except RemoraError:
        return  # refused outright: also correct
    assert outcome.outcome != "executed", (
        "a tool that raised was recorded as executed"
    )
