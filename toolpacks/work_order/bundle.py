# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""What this deployment's tools MEAN, and which authority a call acts under.

Loaded through ``REMORA_SEMANTIC_BUNDLE_MODULE``. Two functions:

``build_semantic_bundle()``
    The declarations REMORA reasons over: each tool's signature, its contract
    (what it acts on, whether it mutates, what post-state it claims), and the
    set of identifiers that exist in the system of record. Together these are
    what let the engine answer "is this the declared call, for this task, on
    real data?" instead of guessing.

``resolve_intent(intent_ref)``
    Turns a work-order id into a resolved authority. This runs SERVER-SIDE,
    against a file this deployment controls. The agent may name which work
    order it acts under; it can never assert that the work order exists or
    says what it claims. That asymmetry is what makes an ACCEPT meaningful —
    see ``intent_authority_present`` in the core.

## A declared dependency on an unstable namespace

Everything below imports from ``remora.toolcall.*``. That is NOT the pinned
stable namespace (``remora.sdk``), and it is not covered by the SDK's
public-API snapshot gate — but no equivalent exists in ``remora.sdk`` today,
so a deployment cannot author its own semantics without it.

Rather than pretend the dependency is fine, ``tests/compatibility/
test_toolpack_authoring_surface.py`` pins the exact symbols and signatures
used here. An upstream change breaks that test loudly instead of breaking a
deployment quietly. The upstream fix — a declarative ToolPack format, so a
deployment writes data rather than Python — is tracked; this module is what
it would replace.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from remora.toolcall.routing.compatibility import CoverageScope, StateIndex
from remora.toolcall.routing.goal_match import TaskIntent
from remora.toolcall.routing.tool_contract import ToolContract, ToolContractRegistry
from remora.toolcall.routing.tool_registry import ToolRegistry, ToolSignature
from remora.toolcall.semantic_bundle import ResolvedIntent, SemanticBundle

#: Identifiers the system of record contains. Seeded by
#: ``db/workorders/001_initial.sql``; a call naming anything else is
#: ungrounded, and the engine routes it to VERIFY rather than acting on a
#: record that does not exist.
#:
#: Static here on purpose. Reading it live would make governance decisions
#: depend on a database round-trip whose failure mode is "everything looks
#: ungrounded", and would let the set drift between the assess and the
#: execute of a single proposal.
#: Work orders that exist in the system of record today.
KNOWN_WORK_ORDERS = frozenset({"WO-1201", "WO-1202", "WO-1203", "WO-1150"})

#: Work orders a signed authority AUTHORIZES but that do not exist yet.
#:
#: A create is the one operation whose target must NOT pre-exist, so under
#: closed-world grounding it would otherwise be permanently ungrounded — the
#: deployment would be unable to create anything through a governed path,
#: which is not caution, it is a modelling mistake.
#:
#: These are still declared identifiers: WO-1310 is named in a signed work
#: order this deployment issued. An id in neither set remains confirmed
#: absent, so "create anything you like" is not what this buys.
AUTHORIZED_FUTURE_WORK_ORDERS = frozenset({"WO-1310"})

KNOWN_ASSETS = frozenset({"PIC-101", "LT-410", "P-7", "VT-330"})


def build_semantic_bundle() -> SemanticBundle:
    registry = ToolRegistry(signatures={
        "read_work_order": ToolSignature(
            name="read_work_order", effect="read",
            required_params=("wo_id",)),
        "set_work_order_priority": ToolSignature(
            name="set_work_order_priority", effect="write",
            required_params=("wo_id", "priority")),
        "create_work_order": ToolSignature(
            name="create_work_order", effect="write",
            required_params=("wo_id", "title", "asset_id")),
        "close_work_order": ToolSignature(
            name="close_work_order", effect="write",
            required_params=("wo_id",)),
        "purge_work_order_history": ToolSignature(
            name="purge_work_order_history", effect="write",
            required_params=("wo_id",)),
    })

    contracts = ToolContractRegistry([
        ToolContract(
            tool="read_work_order", capability="maintenance",
            effect="read", resource_type="work_order", mutation=False,
            argument_roles={"wo_id": "target_resource"}),
        ToolContract(
            tool="set_work_order_priority", capability="maintenance",
            effect="update", resource_type="work_order", mutation=True,
            argument_roles={"wo_id": "target_resource"},
            state_delta={"work_order.priority": "changed"}),
        ToolContract(
            tool="create_work_order", capability="maintenance",
            effect="create", resource_type="work_order", mutation=True,
            argument_roles={"wo_id": "target_resource"},
            state_delta={"work_order.status": "open"}),
        ToolContract(
            tool="close_work_order", capability="maintenance",
            effect="close", resource_type="work_order", mutation=True,
            argument_roles={"wo_id": "target_resource"},
            state_delta={"work_order.status": "closed"}),
        ToolContract(
            tool="purge_work_order_history", capability="maintenance",
            effect="delete", resource_type="work_order", mutation=True,
            argument_roles={"wo_id": "target_resource"},
            state_delta={"work_order.history": "removed"}),
    ])

    # closed_world=True: an identifier absent from this index is confirmed
    # absent, not merely unknown. That is what lets a call naming WO-9999 be
    # routed as ungrounded instead of being given the benefit of the doubt —
    # and it is only honest because this deployment owns the whole namespace.
    state = StateIndex.from_values(
        set(KNOWN_WORK_ORDERS | AUTHORIZED_FUTURE_WORK_ORDERS | KNOWN_ASSETS),
        (CoverageScope("maintenance",
                       frozenset({"wo_id", "asset_id"}),
                       closed_world=True),),
    )
    return SemanticBundle.build(registry=registry, contracts=contracts,
                                validators=None, state=state)


def resolve_intent(intent_ref: str) -> ResolvedIntent | None:
    """Resolve a work-order id against this deployment's authority file.

    ``None`` on anything that does not resolve cleanly — an unknown id, an
    unreadable file, a malformed entry. ``None`` means "no authority was
    established", which the core records as an absent intent authority and
    which no ACCEPT can be built on. Returning a partially-populated intent
    would be worse than returning nothing: it would look like authority.

    The authority hash covers the WHOLE file, not the single entry. Editing
    any work order changes what every concurrent proposal is acting under,
    and that must be visible in the audit record rather than scoped away.
    """
    source = os.environ.get("AAE_WORK_ORDER_AUTHORITY_FILE", "").strip()
    if not source or not intent_ref:
        return None
    try:
        raw = Path(source).read_bytes()
        table = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(table, dict):
        return None

    entry = table.get(intent_ref)
    if not isinstance(entry, dict) or not str(entry.get("task_text", "")).strip():
        return None

    authority = f"signed_work_order:{hashlib.sha256(raw).hexdigest()}"
    return ResolvedIntent(
        intent=TaskIntent(
            operation=str(entry.get("operation", "")),
            resource_type=str(entry.get("resource_type", "")),
            requested_effect=str(entry.get("requested_effect", "")),
            target_entities=tuple(entry.get("target_entities", ())),
            source_spans=tuple(entry.get("source_spans", ())),
            action_spans=tuple(entry.get("action_spans", ())),
            proposed_by=authority),
        task_text=str(entry["task_text"]),
        authority=authority,
    )
