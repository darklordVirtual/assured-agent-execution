# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""The four decisions, as things that actually happen.

Each scenario states what it expects before it runs, and the report says
whether the system did that. A scenario that misbehaves is a FAILURE, never
smoothed over: the point is to find out.

These are also what ``aae scenarios`` runs and what ``tests/e2e`` asserts, so
the demo and the test cannot disagree about what the product does.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterator

from remora.sdk import AuthorizationError, DecisionAction, RemoraClient, ToolCall

from aae import postcondition
from aae.config import Config


@dataclass
class Outcome:
    name: str
    passed: bool
    detail: str
    steps: list[str] = field(default_factory=list)
    proposal_id: str | None = None


class Run:
    """One scenario in progress: its name, its trace, and how it ends.

    Exists so a scenario reads as a sequence of steps and one verdict, instead
    of repeating its own title at every early return — which the first version
    did eighteen times.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.steps: list[str] = []
        self.proposal_id: str | None = None

    def step(self, message: str) -> None:
        self.steps.append(message)

    def saw(self, result) -> None:
        """Record an assessment, and remember which proposal it was."""
        self.proposal_id = result.proposal_id
        self.step(f"assess → {result.action.value} "
                  f"({', '.join(result.reasons)})")

    def ok(self, detail: str) -> Outcome:
        return Outcome(self.name, True, detail, self.steps, self.proposal_id)

    def failed(self, detail: str) -> Outcome:
        return Outcome(self.name, False, detail, self.steps, self.proposal_id)


@contextmanager
def approver_for(cfg: Config, required_role: str | None
                 ) -> Iterator[tuple[str, RemoraClient]]:
    """A client holding exactly the authority this decision asked for.

    REMORA decides per decision which role may release it. Reading that off
    the decision — rather than holding one credential that can approve
    anything — is what makes an escalation mean something.
    """
    role, token = cfg.approver_for(required_role)
    with RemoraClient(cfg.api_url, token) as client:
        yield role, client


def _required(result) -> str | None:
    return (result.resolution_plan.required_role
            if result.resolution_plan else None)


# ── ACCEPT ─────────────────────────────────────────────────────────────────

def accept(cfg: Config, agent: RemoraClient) -> Outcome:
    """A grounded read executes with no human in the loop.

    Every signal is positive: read-only semantics, low risk, a work order the
    deployment resolved server-side, a tool whose contract matches that work
    order's intent, and an identifier that exists in the system of record.
    """
    run = Run("ACCEPT: grounded read under signed WO-1201")
    call = ToolCall(tool_name="read_work_order", arguments={"wo_id": "WO-1201"},
                    target_environment="staging", intent_ref="WO-1201")
    result = agent.assess(call)
    run.saw(result)

    if result.action is not DecisionAction.ACCEPT:
        return run.failed(f"expected accept, got {result.action.value}")
    if not result.execution_token:
        return run.failed("accepted but issued no execution token")

    executed = agent.execute_accepted(result.execution_token, call)
    run.step(f"execute_accepted → {executed.outcome}")

    # Single use. A grant that survives its redemption is not a grant.
    try:
        agent.execute_accepted(result.execution_token, call)
    except Exception as exc:  # noqa: BLE001 — any refusal is the property
        run.step(f"replay refused → {type(exc).__name__}")
    else:
        return run.failed("the execution token was redeemable twice")

    return run.ok("executed without a human; the token was single-use")


# ── VERIFY, the full chain including effect verification ───────────────────

def verify(cfg: Config, agent: RemoraClient) -> Outcome:
    """A production write is held, released by an approver, then confirmed."""
    run = Run("VERIFY: close WO-1202 under signed authority")
    arguments = {"wo_id": "WO-1202", "reason": "alarm reviewed by shift lead"}
    call = ToolCall(tool_name="close_work_order", arguments=arguments,
                    target_environment="prod", intent_ref="WO-1202")
    result = agent.assess(call)
    run.saw(result)

    if result.action is not DecisionAction.VERIFY:
        return run.failed(f"expected verify, got {result.action.value}")
    if not result.review_item_id:
        return run.failed("held for review but enqueued no review item")

    # The agent must not be able to approve its own proposal.
    try:
        agent.approve(result.review_item_id)
        return run.failed("the AGENT token was allowed to approve")
    except AuthorizationError:
        run.step("agent token refused approval (correct)")

    with approver_for(cfg, _required(result)) as (role, approver):
        approval = approver.approve(result.review_item_id, ttl_seconds=300)
    run.step(f"{role} approved → {approval.status}")

    executed = agent.execute(result.review_item_id, call)
    run.step(f"execute → {executed.outcome}")

    # Effect verification, on a credential that cannot write.
    view = postcondition.verify(
        dsn=cfg.reader_dsn, tool_name="close_work_order", arguments=arguments,
        proposal_id=result.proposal_id,
        execution_id=str((executed.tool_execution or {}).get("execution_id")
                         or executed.proposal_id),
        toolspec_hash=(result.toolspec.hash if result.toolspec else "0" * 64))
    run.step(postcondition.describe(view))
    if view is None:
        return run.failed("no postcondition reader was declared")

    recorded = agent.record_effect(result.proposal_id, view)
    run.step(f"record_effect → {recorded.status.value} in the chain")
    if recorded.status.value != "EFFECT_VERIFIED":
        return run.failed(f"effect ended as {recorded.status.value}")

    return run.ok("held, approved by a role that cannot execute, executed, "
                  "and the effect confirmed by a reader that cannot write")


# ── ABSTAIN ────────────────────────────────────────────────────────────────

def abstain(cfg: Config, agent: RemoraClient) -> Outcome:
    """The same read as ACCEPT, under an authority that does not resolve.

    This is the scenario that shows what the ACCEPT was resting on. Identical
    tool, identical arguments, identical classification — and it stops.
    """
    run = Run("ABSTAIN: read under an authority that does not resolve")
    result = agent.assess(ToolCall(
        tool_name="read_work_order", arguments={"wo_id": "WO-1201"},
        target_environment="staging", intent_ref="WO-9999-NOT-ISSUED"))
    run.saw(result)

    if result.action is DecisionAction.ACCEPT:
        return run.failed("accepted a call whose work order does not exist")
    if result.execution_token:
        return run.failed("did not accept, but still issued a token")
    if result.action is not DecisionAction.ABSTAIN:
        return run.failed(f"expected abstain, got {result.action.value} — "
                          f"also safe, but not the documented behaviour")

    return run.ok("stopped, and offered nothing to execute")


# ── ESCALATE ───────────────────────────────────────────────────────────────

def escalate(cfg: Config, agent: RemoraClient) -> Outcome:
    """A destructive tool is refused and routed above the VERIFY role."""
    run = Run("ESCALATE: purge work-order history")
    result = agent.assess(ToolCall(
        tool_name="purge_work_order_history", arguments={"wo_id": "WO-1150"},
        target_environment="prod", intent_ref="MAINT-PURGE-Q3"))
    run.saw(result)

    if plan := result.resolution_plan:
        run.step(f"resolution plan → {plan.type}, "
                 f"required_role={plan.required_role}")
    if result.action is not DecisionAction.ESCALATE:
        return run.failed(f"expected escalate, got {result.action.value}")

    return run.ok("refused and routed to a higher authority than VERIFY")


# ── Payload binding ────────────────────────────────────────────────────────

def payload_binding(cfg: Config, agent: RemoraClient) -> Outcome:
    """An approval is welded to the exact arguments it approved.

    The realistic attack on a human-in-the-loop control is never to bypass the
    human — it is to get approval for one thing and execute another.
    """
    run = Run("BINDING: approve one payload, execute another")
    approved = {"wo_id": "WO-1203", "priority": "high"}
    tampered = {"wo_id": "WO-1201", "priority": "high"}

    result = agent.assess(ToolCall(
        tool_name="set_work_order_priority", arguments=approved,
        target_environment="prod", intent_ref="WO-1203"))
    run.saw(result)
    if not result.review_item_id:
        return run.failed(f"expected a held decision, got {result.action.value}")

    with approver_for(cfg, _required(result)) as (role, approver):
        approver.approve(result.review_item_id, ttl_seconds=300)
    run.step(f"{role} approved WO-1203 → high")

    # The refusal arrives as an OUTCOME, not an exception. A caller that only
    # wrapped this in try/except would read binding_refused as success — this
    # scenario did exactly that on its first run.
    try:
        executed = agent.execute(result.review_item_id, ToolCall(
            tool_name="set_work_order_priority", arguments=tampered,
            target_environment="prod", intent_ref="WO-1203"))
        run.step(f"execute with a different payload → {executed.outcome}"
                 + (f" ({executed.detail})" if executed.detail else ""))
        if executed.outcome == "execute":
            return run.failed("a payload the approver never saw was executed")
    except Exception as exc:  # noqa: BLE001
        run.step(f"refused → {type(exc).__name__}")

    return run.ok("the approval did not transfer to a different payload")


# ── Role separation ────────────────────────────────────────────────────────

def role_separation(cfg: Config, agent: RemoraClient) -> Outcome:
    """The approver may approve and may not execute.

    A token that can do both makes every other control decorative, because one
    compromised credential is then the whole chain.
    """
    run = Run("ROLES: the approver cannot execute")
    arguments = {"wo_id": "WO-1203", "priority": "low"}
    call = ToolCall(tool_name="set_work_order_priority", arguments=arguments,
                    target_environment="prod", intent_ref="WO-1203")
    result = agent.assess(call)
    run.saw(result)
    if not result.review_item_id:
        return run.failed(f"expected a held decision, got {result.action.value}")

    with approver_for(cfg, _required(result)) as (role, approver):
        approver.approve(result.review_item_id, ttl_seconds=300)
        run.step(f"{role} approved")
        try:
            executed = approver.execute(result.review_item_id, call)
        except AuthorizationError:
            run.step(f"{role} token refused execution (correct)")
            return run.ok("approval and execution are separate authorities")

    run.step(f"{role} execute → {executed.outcome}")
    if executed.outcome == "execute":
        return run.failed(f"the {role} identity both approved and executed")
    return run.ok("approval and execution are separate authorities")


SCENARIOS: list[Callable[[Config, RemoraClient], Outcome]] = [
    accept, verify, abstain, escalate, payload_binding, role_separation,
]


def run_all(cfg: Config) -> list[Outcome]:
    """Run them all against one agent client.

    Approvers are built per decision from the authority that decision asked
    for, so no long-lived client holds a capability it does not need.
    """
    outcomes: list[Outcome] = []
    with RemoraClient(cfg.api_url, cfg.token_agent) as agent:
        for scenario in SCENARIOS:
            try:
                outcomes.append(scenario(cfg, agent))
            except Exception as exc:  # noqa: BLE001
                outcomes.append(Outcome(
                    scenario.__name__, False, f"{type(exc).__name__}: {exc}"))
    return outcomes
