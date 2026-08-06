# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""The four decisions, as things that actually happen.

Each scenario states what it expects BEFORE it runs, and the report says
whether the system did that. A scenario that does not behave as expected is a
FAILURE, not something to smooth over: the point is to find out.

    ACCEPT    a read, fully grounded under a signed work order, executes with
              no human in the loop
    VERIFY    a production write is held; an approver — who cannot execute —
              releases it; the effect is then verified against the system of
              record on a read-only credential
    ABSTAIN   the same read with no resolvable authority stops, and offers
              nothing to execute
    ESCALATE  a destructive tool is refused and routed to a strictly higher
              role than VERIFY

Plus the properties that make those four mean anything: the approver token
cannot execute, the agent token cannot approve, and an approval is welded to
the exact payload it approved.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterator

from remora.sdk import (
    AuthorizationError,
    BindingRefusedError,
    DecisionAction,
    RemoraClient,
    ToolCall,
)

from aae import postcondition
from aae.config import Config


@dataclass
class Outcome:
    name: str
    expected: str
    passed: bool
    detail: str
    steps: list[str] = field(default_factory=list)
    proposal_id: str | None = None


@contextmanager
def approver_for(cfg: Config, required_role: str | None
                 ) -> Iterator[tuple[str, RemoraClient]]:
    """A client holding exactly the authority this decision asked for.

    REMORA's escalation contract decides which role may release a given
    decision. Reading it off the decision — rather than holding one credential
    that can approve anything — is what makes an escalation mean something: a
    purge routed to `senior_authority` cannot be waved through by whoever
    happens to be on shift.
    """
    role, token = cfg.approver_for(required_role)
    with RemoraClient(cfg.api_url, token) as client:
        yield role, client


# ── ACCEPT ─────────────────────────────────────────────────────────────────

def scenario_accept(cfg: Config, agent: RemoraClient, _approver: RemoraClient) -> Outcome:
    """A grounded read executes with no human in the loop.

    Every signal is positive: read-only semantics, low risk, a work order the
    deployment resolved server-side, a tool whose contract matches that work
    order's intent, and an identifier that exists in the system of record.
    """
    steps: list[str] = []
    call = ToolCall(
        tool_name="read_work_order",
        arguments={"wo_id": "WO-1201"},
        target_environment="staging",
        intent_ref="WO-1201",
    )
    result = agent.assess(call)
    steps.append(f"assess → {result.action.value} ({', '.join(result.reasons)})")

    if result.action is not DecisionAction.ACCEPT:
        return Outcome("ACCEPT: grounded read under signed WO-1201", "accept",
                       False, f"expected accept, got {result.action.value}",
                       steps, result.proposal_id)
    if not result.execution_token:
        return Outcome("ACCEPT: grounded read under signed WO-1201", "accept",
                       False, "accepted but issued no execution token", steps,
                       result.proposal_id)

    executed = agent.execute_accepted(result.execution_token, call)
    steps.append(f"execute_accepted → {executed.outcome}")

    # Single use. A grant that survives its redemption is not a grant.
    replayed_refused = False
    try:
        agent.execute_accepted(result.execution_token, call)
    except Exception as exc:  # noqa: BLE001 — any refusal is the property
        replayed_refused = True
        steps.append(f"replay refused → {type(exc).__name__}")
    if not replayed_refused:
        return Outcome("ACCEPT: grounded read under signed WO-1201", "accept",
                       False, "the execution token was redeemable twice",
                       steps, result.proposal_id)

    return Outcome("ACCEPT: grounded read under signed WO-1201", "accept",
                   True, "executed without a human; the token was single-use",
                   steps, result.proposal_id)


# ── VERIFY (the full chain, including effect verification) ─────────────────

def scenario_verify(cfg: Config, agent: RemoraClient,
                    approver: RemoraClient) -> Outcome:
    """A production write is held, released by an approver, then confirmed."""
    steps: list[str] = []
    arguments = {"wo_id": "WO-1202", "reason": "alarm reviewed by shift lead"}
    call = ToolCall(
        tool_name="close_work_order", arguments=arguments,
        target_environment="prod", intent_ref="WO-1202",
    )
    result = agent.assess(call)
    steps.append(f"assess → {result.action.value} ({', '.join(result.reasons)})")

    if result.action is not DecisionAction.VERIFY:
        return Outcome("VERIFY: close WO-1202 under signed authority", "verify",
                       False, f"expected verify, got {result.action.value}",
                       steps, result.proposal_id)
    if not result.review_item_id:
        return Outcome("VERIFY: close WO-1202 under signed authority", "verify",
                       False, "held for review but enqueued no review item",
                       steps, result.proposal_id)

    # The agent must not be able to approve its own proposal.
    try:
        agent.approve(result.review_item_id)
        return Outcome("VERIFY: close WO-1202 under signed authority", "verify",
                       False, "the AGENT token was allowed to approve",
                       steps, result.proposal_id)
    except AuthorizationError:
        steps.append("agent token refused approval (correct)")

    required = (result.resolution_plan.required_role
                if result.resolution_plan else None)
    with approver_for(cfg, required) as (role, approver_client):
        approval = approver_client.approve(result.review_item_id,
                                           ttl_seconds=300)
    steps.append(f"{role} approved → {approval.status}")

    executed = agent.execute(result.review_item_id, call)
    steps.append(f"execute → {executed.outcome}")

    # ── Effect verification, on a credential that cannot write ────────────
    view = postcondition.verify(
        dsn=cfg.reader_dsn, tool_name="close_work_order", arguments=arguments,
        proposal_id=result.proposal_id,
        execution_id=str((executed.tool_execution or {}).get("execution_id")
                         or executed.proposal_id),
        toolspec_hash=(result.toolspec.hash if result.toolspec else "0" * 64),
    )
    steps.append(postcondition.describe(view))
    if view is None:
        return Outcome("VERIFY: close WO-1202 under signed authority", "verify",
                       False, "no postcondition reader was declared", steps,
                       result.proposal_id)

    recorded = agent.record_effect(result.proposal_id, view)
    steps.append(f"record_effect → {recorded.status.value} in the chain")

    passed = recorded.status.value == "EFFECT_VERIFIED"
    return Outcome(
        "VERIFY: close WO-1202 under signed authority", "verify", passed,
        "held, approved by a role that cannot execute, executed, and the "
        "effect confirmed by a reader that cannot write"
        if passed else f"effect ended as {recorded.status.value}",
        steps, result.proposal_id)


# ── ABSTAIN ────────────────────────────────────────────────────────────────

def scenario_abstain(cfg: Config, agent: RemoraClient,
                     _approver: RemoraClient) -> Outcome:
    """The same read, with an authority that does not resolve.

    This is the scenario that shows what the ACCEPT was actually resting on.
    Identical tool, identical arguments, identical risk classification — and
    it stops, because the work order it claims to act under is not one this
    deployment recognises.
    """
    steps: list[str] = []
    result = agent.assess(ToolCall(
        tool_name="read_work_order", arguments={"wo_id": "WO-1201"},
        target_environment="staging", intent_ref="WO-9999-NOT-ISSUED",
    ))
    steps.append(f"assess → {result.action.value} ({', '.join(result.reasons)})")

    if result.action is DecisionAction.ACCEPT:
        return Outcome("ABSTAIN: read under an authority that does not resolve",
                       "abstain", False,
                       "accepted a call whose claimed work order does not exist",
                       steps, result.proposal_id)
    if result.execution_token:
        return Outcome("ABSTAIN: read under an authority that does not resolve",
                       "abstain", False,
                       "did not accept, but still issued an execution token",
                       steps, result.proposal_id)

    passed = result.action is DecisionAction.ABSTAIN
    return Outcome(
        "ABSTAIN: read under an authority that does not resolve", "abstain",
        passed,
        "stopped, and offered nothing to execute" if passed
        else f"expected abstain, got {result.action.value} (also safe, but "
             f"not the documented behaviour)",
        steps, result.proposal_id)


# ── ESCALATE ───────────────────────────────────────────────────────────────

def scenario_escalate(cfg: Config, agent: RemoraClient,
                      _approver: RemoraClient) -> Outcome:
    """A destructive tool is refused and routed above the VERIFY role."""
    steps: list[str] = []
    result = agent.assess(ToolCall(
        tool_name="purge_work_order_history", arguments={"wo_id": "WO-1150"},
        target_environment="prod", intent_ref="MAINT-PURGE-Q3",
    ))
    steps.append(f"assess → {result.action.value} ({', '.join(result.reasons)})")

    if result.action is DecisionAction.ACCEPT:
        return Outcome("ESCALATE: purge work-order history", "escalate", False,
                       "accepted a destructive write", steps, result.proposal_id)

    plan = result.resolution_plan
    if plan is not None:
        steps.append(f"resolution plan → {plan.type}, "
                     f"required_role={plan.required_role}")

    passed = result.action is DecisionAction.ESCALATE
    return Outcome(
        "ESCALATE: purge work-order history", "escalate", passed,
        "refused and routed to a higher authority than VERIFY" if passed
        else f"expected escalate, got {result.action.value}",
        steps, result.proposal_id)


# ── Payload binding ────────────────────────────────────────────────────────

def scenario_payload_binding(cfg: Config, agent: RemoraClient,
                             approver: RemoraClient) -> Outcome:
    """An approval is welded to the exact arguments it approved.

    The realistic attack on a human-in-the-loop control is not to bypass the
    human — it is to get approval for one thing and execute another.
    """
    steps: list[str] = []
    approved = {"wo_id": "WO-1203", "priority": "high"}
    tampered = {"wo_id": "WO-1201", "priority": "high"}

    result = agent.assess(ToolCall(
        tool_name="set_work_order_priority", arguments=approved,
        target_environment="prod", intent_ref="WO-1203"))
    steps.append(f"assess → {result.action.value}")

    if not result.review_item_id:
        return Outcome("BINDING: approve one payload, execute another",
                       "binding_refused", False,
                       f"expected a held decision, got {result.action.value}",
                       steps, result.proposal_id)

    required = (result.resolution_plan.required_role
                if result.resolution_plan else None)
    with approver_for(cfg, required) as (role, approver_client):
        approver_client.approve(result.review_item_id, ttl_seconds=300)
    steps.append(f"{role} approved WO-1203 → high")

    # The refusal arrives as an OUTCOME, not an exception. Worth being
    # explicit about: a caller that only wrapped this in try/except would read
    # binding_refused as success and carry on. This scenario did exactly that
    # on its first run, which is the best argument for checking the field.
    try:
        executed = agent.execute(result.review_item_id, ToolCall(
            tool_name="set_work_order_priority", arguments=tampered,
            target_environment="prod", intent_ref="WO-1203"))
        steps.append(f"execute with a different payload → {executed.outcome}"
                     + (f" ({executed.detail})" if executed.detail else ""))
        refused = executed.outcome != "execute"
    except BindingRefusedError as exc:
        steps.append(f"execute with a different payload refused → "
                     f"{type(exc).__name__}")
        refused = True
    except Exception as exc:  # noqa: BLE001
        steps.append(f"refused → {type(exc).__name__}: {exc}")
        refused = True

    return Outcome(
        "BINDING: approve one payload, execute another", "binding_refused",
        refused,
        "the approval did not transfer to a different payload" if refused
        else "a payload the approver never saw was executed",
        steps, result.proposal_id)


# ── Role separation ────────────────────────────────────────────────────────

def scenario_role_separation(cfg: Config, agent: RemoraClient,
                             approver: RemoraClient) -> Outcome:
    """The approver may approve and may not execute.

    A token that can do both makes every other control in this list
    decorative, because one compromised credential is the whole chain.
    """
    steps: list[str] = []
    # Uses set_work_order_priority rather than create_work_order.
    #
    # Not a convenience: create_work_order is fully grounded here
    # (tool_matches_goal=True, expected_effect_matches=True, a resolved signed
    # authority) and still falls through every rule to default_safe_abstain —
    # no review item, no ResolutionPlan, no path an approver could take. A
    # medium-risk mutation matches neither the high-risk evidence rule nor the
    # ungrounded-arguments rule, so declaring a risk tier leaves it worse off
    # than leaving it unknown. Raised upstream; until it is settled, this
    # scenario tests role separation on a tool that reliably holds, and does
    # not pretend the gap is not there.
    arguments = {"wo_id": "WO-1203", "priority": "low"}
    call = ToolCall(tool_name="set_work_order_priority", arguments=arguments,
                    target_environment="prod", intent_ref="WO-1203")
    result = agent.assess(call)
    steps.append(f"assess → {result.action.value} "
                 f"({', '.join(result.reasons)})")

    if not result.review_item_id:
        return Outcome("ROLES: the approver cannot execute", "refused", False,
                       f"expected a held decision, got {result.action.value}",
                       steps, result.proposal_id)

    required = (result.resolution_plan.required_role
                if result.resolution_plan else None)
    with approver_for(cfg, required) as (role, approver_client):
        approver_client.approve(result.review_item_id, ttl_seconds=300)
        steps.append(f"{role} approved")
        try:
            executed = approver_client.execute(result.review_item_id, call)
            steps.append(f"{role} execute → {executed.outcome}"
                         + (f" ({executed.detail})" if executed.detail else ""))
            refused = executed.outcome != "execute"
        except AuthorizationError:
            steps.append(f"{role} token refused execution (correct)")
            refused = True

    return Outcome(
        "ROLES: the approver cannot execute", "refused", refused,
        "approval and execution are separate authorities" if refused
        else "the approver executed its own approval",
        steps, result.proposal_id)


SCENARIOS: list[Callable[[Config, RemoraClient, RemoraClient], Outcome]] = [
    scenario_accept,
    scenario_verify,
    scenario_abstain,
    scenario_escalate,
    scenario_payload_binding,
    scenario_role_separation,
]


def run_all(cfg: Config) -> list[Outcome]:
    outcomes: list[Outcome] = []
    # One agent client for every scenario; approvers are built per
    # decision from the authority that decision asked for, so no
    # long-lived client holds an approval capability it does not need.
    with RemoraClient(cfg.api_url, cfg.token_agent) as agent:
        for scenario in SCENARIOS:
            try:
                outcomes.append(scenario(cfg, agent, None))
            except Exception as exc:  # noqa: BLE001
                outcomes.append(Outcome(
                    scenario.__name__, "(ran to completion)", False,
                    f"{type(exc).__name__}: {exc}", []))
    return outcomes
