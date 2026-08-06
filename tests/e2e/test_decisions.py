# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""The four decisions, and the deployment facts they depend on.

The scenarios themselves live in ``aae.scenarios`` because they are also what
``aae scenarios`` runs — an operator and CI must exercise the same code, or
the demo and the test can disagree about what the product does.

What this file adds around them is the *why*: the deployment facts each
decision rests on, checked separately, so a failure says which assumption
broke rather than only that something did.
"""
from __future__ import annotations

import pytest
from remora.sdk import DecisionAction, ToolCall

from aae import scenarios

pytestmark = pytest.mark.usefixtures("live")


# ── The deployment facts the decisions rest on ─────────────────────────────

def test_the_control_plane_serves_only_the_execution_surface(viewer) -> None:
    """No oracle, no retrieval store, no egress. Asserted, not assumed."""
    root = viewer._request("GET", "/")  # noqa: SLF001
    assert root["surfaces"] == ["execution"]
    assert root["runtime_mode"] == "production"


def test_the_assess_research_surface_is_not_reachable(viewer) -> None:
    """Unmounted, not merely unused. A 404 because the route is absent."""
    from remora.sdk import NotFoundError, RemoraError

    with pytest.raises((NotFoundError, RemoraError)):
        viewer._request("POST", "/v1/assess", json={"question": "x"})  # noqa: SLF001


def test_the_system_of_record_was_migrated_not_conjured(reader_conn) -> None:
    """The schema came from db/workorders/, applied under the admin role.

    If the tables had appeared some other way, the version row would not be
    there — and neither would the grants the reader is connected on.
    """
    with reader_conn.cursor() as cur:
        cur.execute("SELECT max(version) FROM schema_version")
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT count(*) FROM work_orders")
        assert cur.fetchone()[0] >= 4


def test_the_audit_chain_verifies(viewer) -> None:
    verification = viewer.verify_audit_chain()
    assert verification.valid, f"audit chain did not verify: {verification.raw}"


# ── The four decisions ─────────────────────────────────────────────────────

def test_a_grounded_read_is_accepted_and_executes(seeded, agent) -> None:
    outcome = scenarios.accept(seeded, agent)
    assert outcome.passed, f"{outcome.detail}\n  " + "\n  ".join(outcome.steps)


def test_a_production_write_is_held_approved_executed_and_verified(
    seeded, agent
) -> None:
    """The full Gate C vertical in one test.

    assess → held → approved by a role that cannot execute → executed →
    effect read back on a credential that cannot write → attested in the
    chain.
    """
    outcome = scenarios.verify(seeded, agent)
    assert outcome.passed, f"{outcome.detail}\n  " + "\n  ".join(outcome.steps)


def test_a_call_with_no_resolvable_authority_abstains(
    seeded, agent
) -> None:
    outcome = scenarios.abstain(seeded, agent)
    assert outcome.passed, f"{outcome.detail}\n  " + "\n  ".join(outcome.steps)


def test_a_destructive_tool_escalates(seeded, agent) -> None:
    outcome = scenarios.escalate(seeded, agent)
    assert outcome.passed, f"{outcome.detail}\n  " + "\n  ".join(outcome.steps)


# ── What the ACCEPT was actually resting on ────────────────────────────────

@pytest.mark.parametrize("change, why", [
    ({"intent_ref": None},
     "no authority was claimed at all"),
    ({"intent_ref": "WO-9999-NOT-ISSUED"},
     "the claimed authority does not resolve"),
    ({"arguments": {"wo_id": "WO-4242"}},
     "the work order is not in the system of record"),
    ({"target_environment": "prod"},
     "production is excluded even for reads"),
])
def test_removing_any_single_ground_removes_the_accept(
    agent, change: dict, why: str
) -> None:
    """The ACCEPT is a conjunction, and every clause carries weight.

    A rule where one condition is decorative is a rule nobody can reason
    about — and an operator asked to trust autonomous execution is entitled
    to know exactly what it turns on.
    """
    call_args = {
        "tool_name": "read_work_order",
        "arguments": {"wo_id": "WO-1201"},
        "target_environment": "staging",
        "intent_ref": "WO-1201",
    }
    call_args.update(change)

    baseline = agent.assess(ToolCall(
        tool_name="read_work_order", arguments={"wo_id": "WO-1201"},
        target_environment="staging", intent_ref="WO-1201"))
    if baseline.action is not DecisionAction.ACCEPT:
        pytest.skip("the baseline read does not ACCEPT here; nothing to remove")

    # Two shapes of refusal, one property. With a signed ToolSpec bundle
    # configured, enforcement is strict: a target the spec does not
    # allow is refused outright rather than assessed and declined. Both
    # are correct — the test asserts what must never happen, not which
    # mechanism prevented it.
    from remora.sdk import RemoraError

    try:
        result = agent.assess(ToolCall(**call_args))
    except RemoraError:
        return  # refused before a decision was reached
    assert result.action is not DecisionAction.ACCEPT, (
        f"still accepted although {why}"
    )
    assert not result.execution_token


# ── Lifecycle and evidence ─────────────────────────────────────────────────

def test_every_decision_leaves_a_readable_lifecycle(agent, viewer) -> None:
    result = agent.assess(ToolCall(
        tool_name="read_work_order", arguments={"wo_id": "WO-1201"},
        target_environment="staging", intent_ref="WO-1201"))
    trail = viewer.get_lifecycle(result.proposal_id)
    assert trail.proposal_id == result.proposal_id
    assert trail.events, "a decision was made and no lifecycle event recorded"


def test_evidence_export_carries_a_hashed_manifest(agent, tmp_path) -> None:
    from aae import evidence

    result = agent.assess(ToolCall(
        tool_name="read_work_order", arguments={"wo_id": "WO-1201"},
        target_environment="staging", intent_ref="WO-1201"))

    manifest = evidence.export(agent, [result.proposal_id], tmp_path,
                               generated_at="2026-08-06T00:00:00+00:00")
    assert manifest["proposals_exported"] == [result.proposal_id]
    assert not manifest["proposals_failed"]
    assert manifest["audit_chain_verified"] is True

    # Every listed file must exist and hash to what the manifest claims.
    import hashlib

    for name, declared in manifest["files"].items():
        path = tmp_path / name
        assert path.is_file(), f"manifest lists {name}, which was not written"
        actual = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == declared, f"{name} does not match its manifest digest"


def test_two_exports_of_one_proposal_differ_only_by_when_they_were_taken(
    agent,
) -> None:
    """The evidence is stable; the export timestamp is not, and should not be.

    A first version of this test asserted byte-identical exports and failed.
    The cause turned out to be correct behaviour: REMORA stamps
    ``manifest.exported_at`` into each bundle, so two exports of one proposal
    legitimately differ. That matters for how a digest may be used — an outer
    file hash identifies THAT export, not the evidence — so the property
    worth asserting is that nothing else moves.

    If a second field ever starts varying, this fails and names it.
    """
    result = agent.assess(ToolCall(
        tool_name="read_work_order", arguments={"wo_id": "WO-1201"},
        target_environment="staging", intent_ref="WO-1201"))

    first = dict(agent.export_evidence(result.proposal_id))
    second = dict(agent.export_evidence(result.proposal_id))

    def flatten(payload, prefix=""):
        flat = {}
        for key, value in payload.items():
            if isinstance(value, dict):
                flat.update(flatten(value, f"{prefix}{key}."))
            else:
                flat[f"{prefix}{key}"] = value
        return flat

    a, b = flatten(first), flatten(second)
    differing = {k for k in a | b if a.get(k) != b.get(k)}
    assert differing == {"manifest.exported_at"}, (
        f"evidence content is not stable across exports: "
        f"{sorted(differing - {'manifest.exported_at'})}"
    )


def test_a_reexport_of_the_same_proposal_carries_the_same_sections(
    agent, tmp_path
) -> None:
    """The lifecycle and audit sections a third party checks must not drift."""
    from aae import evidence

    result = agent.assess(ToolCall(
        tool_name="read_work_order", arguments={"wo_id": "WO-1201"},
        target_environment="staging", intent_ref="WO-1201"))

    first = evidence.export(agent, [result.proposal_id], tmp_path / "a",
                            generated_at="2026-08-06T00:00:00+00:00")
    second = evidence.export(agent, [result.proposal_id], tmp_path / "b",
                             generated_at="2026-08-06T00:00:00+00:00")
    assert first["proposals_exported"] == second["proposals_exported"]
    assert first["audit_chain_verified"] == second["audit_chain_verified"]
    assert set(first["files"]) == set(second["files"])
    # The lifecycle section has no export stamp, so it IS byte-stable.
    lifecycle = f"lifecycle-{result.proposal_id}.json"
    assert first["files"][lifecycle] == second["files"][lifecycle]
