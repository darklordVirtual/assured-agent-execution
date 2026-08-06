# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""Did the approved action actually happen?

This is the postcondition reader. It runs HERE, in the product's process, on
the product's read-only credential — never inside REMORA. The core is explicit
about why: it never reaches into a customer's system of record, so what it
stores is an attestation by a named verifier, not a proof of its own. Only
hashes cross the boundary; the observed values stay on this side.

Three properties this file exists to keep true:

1. **The reader cannot write.** It connects as ``aae_reader``, which holds
   ``SELECT`` and nothing else, inside a read-only transaction. A verifier
   that could make the answer true is reporting on itself.

2. **"Could not look" is never "was wrong".** A missing row, an unreachable
   database and a failed query are all UNOBSERVABLE or VERIFIER_FAILED —
   non-terminal, incident stays open. Only a row that is present and does not
   match the declared delta is MISMATCH. Collapsing these would let this
   product close incidents that are still open, or compensate for an action
   that in fact succeeded.

3. **Only the declared delta is compared.** A work order has other legitimate
   writers — a planner changes the title, a scheduler moves the date. If
   verification flagged every field it did not expect, every concurrent update
   would surface as a mismatch and the signal would become noise within a
   week.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row
from remora.sdk import (
    EffectStatus,
    EffectVerificationView,
    PostconditionSpec,
    build_postcondition,
    verify_effect,
)

#: Recorded in the chain as the verifier's identity. Versioned: a later reader
#: with different semantics must be distinguishable in the record from this
#: one, or an old attestation silently acquires a new meaning.
VERIFIER_IDENTITY = "aae.work_order_reader/v1"

#: Tools whose effect this reader can observe. A tool absent from here has no
#: declared reader, which the contract reports as EFFECT_UNSUPPORTED —
#: recorded, so the ABSENCE of verification is visible. An unverified effect
#: that leaves no trace is indistinguishable from a verified one.
_POSTCONDITIONS: dict[str, dict[str, Any]] = {
    "close_work_order": {
        "expected_fields": {"status": "closed"},
        "id_argument": "wo_id",
    },
    "create_work_order": {
        "expected_fields": {"status": "open"},
        "id_argument": "wo_id",
    },
    "set_work_order_priority": {
        # The expected value comes from the approved arguments, not from a
        # constant: what "correct" means here IS the payload that was
        # authorized. Filled in by declare().
        "expected_fields": {},
        "id_argument": "wo_id",
        "from_arguments": {"priority": "priority"},
    },
}


class ReaderUnavailable(RuntimeError):
    """The reader could not reach the system of record.

    Deliberately distinct from "the object was not found". The caller turns
    this into VERIFIER_FAILED, which is not terminal — an outage is not
    evidence that anything went wrong with the action.
    """


@dataclass(frozen=True)
class Observation:
    """What the reader saw, and whether it managed to look at all."""

    fields: dict[str, Any] | None
    looked: bool
    detail: str = ""


def supports(tool_name: str) -> bool:
    return tool_name in _POSTCONDITIONS


def declare(tool_name: str, arguments: dict[str, Any]) -> PostconditionSpec | None:
    """The delta this approved call is expected to produce.

    Declared BEFORE execution, from the approved arguments. Declaring it
    afterwards from the result would make verification a tautology: the
    expectation would be whatever happened.
    """
    rule = _POSTCONDITIONS.get(tool_name)
    if rule is None:
        return None

    expected = dict(rule["expected_fields"])
    for field, argument in rule.get("from_arguments", {}).items():
        if argument in arguments:
            expected[field] = arguments[argument]

    target_id = str(arguments.get(rule["id_argument"], "")).strip()
    return build_postcondition(
        tool_id=tool_name,
        target_selector={"system": "aae.work_orders", "wo_id": target_id},
        expected_fields=expected,
        reader=VERIFIER_IDENTITY,
        observation_deadline_seconds=30,
    )


def observe(dsn: str, spec: PostconditionSpec) -> Observation:
    """Read the target object on the read-only credential."""
    wo_id = str(spec.target_selector.get("wo_id", "")).strip()
    if not wo_id:
        return Observation(fields=None, looked=True,
                           detail="the postcondition names no target work order")
    try:
        with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10,
                             autocommit=True) as conn:
            # Belt to the grants' braces. If this process is ever handed a DSN
            # with more rights than it should have, the transaction still
            # refuses to write.
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT wo_id, title, asset_id, status, priority,"
                    "       closed_reason, updated_by"
                    "  FROM work_orders WHERE wo_id = %s", (wo_id,))
                row = cur.fetchone()
    except psycopg.Error as exc:
        # Not a mismatch. We could not look.
        raise ReaderUnavailable(
            f"could not read the system of record: "
            f"{type(exc).__name__}: {exc}") from exc

    if row is None:
        # We DID look, and the object is not there. That is observable and,
        # against a postcondition expecting a state, it is a mismatch.
        return Observation(fields=None, looked=True,
                           detail=f"work order {wo_id} does not exist")
    return Observation(fields=dict(row), looked=True)


def verify(
    *,
    dsn: str,
    tool_name: str,
    arguments: dict[str, Any],
    proposal_id: str,
    execution_id: str,
    toolspec_hash: str,
) -> EffectVerificationView | None:
    """Declare, observe and compare. ``None`` when no reader is declared.

    ``None`` is not "verified": the caller must record EFFECT_UNSUPPORTED so
    the absence is in the trail. A tool nobody can verify and a tool that
    verified must never look the same afterwards.
    """
    spec = declare(tool_name, arguments)
    if spec is None:
        return None

    try:
        observation = observe(dsn, spec)
    except ReaderUnavailable as exc:
        # VERIFIER_FAILED, not MISMATCH: the reader broke, the action did not.
        # Non-terminal by contract, so the incident stays open.
        return verify_effect(
            spec, None, proposal_id=proposal_id, execution_id=execution_id,
            toolspec_hash=toolspec_hash,
            verifier_identity=f"{VERIFIER_IDENTITY}#unavailable:{exc}"[:200],
        )

    return verify_effect(
        spec, observation.fields, proposal_id=proposal_id,
        execution_id=execution_id, toolspec_hash=toolspec_hash,
        verifier_identity=VERIFIER_IDENTITY,
    )


def describe(view: EffectVerificationView | None) -> str:
    """One line an operator can act on, including when nothing was verified."""
    if view is None:
        return ("EFFECT_UNSUPPORTED — no postcondition reader is declared for "
                "this tool, so nothing confirms the effect happened")
    status = view.status
    if status is EffectStatus.VERIFIED:
        return "EFFECT_VERIFIED — the system of record shows the approved delta"
    if status is EffectStatus.MISMATCH:
        return ("EFFECT_MISMATCH — we looked, and the system of record does "
                "NOT show the approved delta. Terminal: this needs a human.")
    if status is EffectStatus.UNOBSERVABLE:
        return ("EFFECT_UNOBSERVABLE — we could not see the object. This is "
                "not evidence the action failed; the incident stays open.")
    if status is EffectStatus.VERIFIER_FAILED:
        return ("EFFECT_VERIFIER_FAILED — the reader itself failed. Not "
                "evidence about the action; the incident stays open.")
    return f"{status.value} — recorded"
