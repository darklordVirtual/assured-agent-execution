# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""The callables REMORA dispatches, and the credentials they close over.

Loaded once per process through ``REMORA_TOOL_REGISTRY_MODULE``. The contract
is one function::

    def register_tools(register: Callable[[str, Callable], None]) -> None

The dispatcher — not the agent — holds these callables. A request payload can
never add or replace one, which is why the work-order database credential
lives here and not anywhere an agent can reach.

Five tools, chosen so the product's four decisions are each reachable by a
real action rather than a contrived one:

    read_work_order          low / read              → ACCEPT when grounded
    set_work_order_priority  low / write             → VERIFY (writes never
                                                       auto-accept)
    create_work_order        medium / write          → VERIFY
    close_work_order         high / production_write → VERIFY, then effect
                                                       verification
    purge_work_order_history critical / destructive  → ESCALATE

The classifications themselves are NOT here — they are declared in
``tool_metadata.json`` and hashed into REMORA's policy identity. Code that
could classify itself would be a tool granting itself a risk tier.
"""
from __future__ import annotations

from typing import Any, Callable

from toolpacks.work_order import store

#: Recorded on every write in the product's own event log. REMORA's audit
#: chain records the governed actor separately; this is the system of record's
#: independent attribution, and the two are meant to be cross-checkable.
_ACTOR = "aae.work_order_toolpack/v1"


class ToolArgumentError(ValueError):
    """The call cannot be performed as asked.

    Raised rather than returning an error payload: an exception surfaces as a
    failed execution in the lifecycle, where a payload that merely says
    "failed" inside a successful execution would record the action as done.
    """


def _require(arguments: dict[str, Any], name: str) -> str:
    value = str(arguments.get(name, "")).strip()
    if not value:
        raise ToolArgumentError(f"{name} is required")
    return value


def read_work_order(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return one work order. No side effect, and none is recorded."""
    wo_id = _require(arguments, "wo_id")
    with store.reader() as conn:
        row = store.fetch_work_order(conn, wo_id)
    if row is None:
        raise ToolArgumentError(f"no such work order: {wo_id}")
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v)
            for k, v in row.items()}


def set_work_order_priority(arguments: dict[str, Any]) -> dict[str, Any]:
    wo_id = _require(arguments, "wo_id")
    priority = _require(arguments, "priority").lower()
    if priority not in {"low", "normal", "high"}:
        raise ToolArgumentError(
            f"priority must be low, normal or high; got {priority!r}")
    with store.writer() as conn:
        before = store.fetch_work_order(conn, wo_id)
        if before is None:
            raise ToolArgumentError(f"no such work order: {wo_id}")
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE work_orders SET priority = %s, updated_at = now(),"
                "       updated_by = %s WHERE wo_id = %s",
                (priority, _ACTOR, wo_id))
        store.record_event(conn, wo_id=wo_id, tool_name="set_work_order_priority",
                           actor=_ACTOR,
                           detail={"from": before["priority"], "to": priority})
    return {"wo_id": wo_id, "priority": priority}


def create_work_order(arguments: dict[str, Any]) -> dict[str, Any]:
    wo_id = _require(arguments, "wo_id")
    title = _require(arguments, "title")
    asset_id = _require(arguments, "asset_id")
    with store.writer() as conn:
        if store.fetch_work_order(conn, wo_id) is not None:
            # Not idempotent-by-silence: an approval was granted to CREATE
            # this work order, and quietly returning the existing one would
            # report an action as done that this approval did not perform.
            raise ToolArgumentError(f"work order already exists: {wo_id}")
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO work_orders (wo_id, title, asset_id, status,"
                "                         priority, updated_by)"
                " VALUES (%s, %s, %s, 'open', 'normal', %s)",
                (wo_id, title, asset_id, _ACTOR))
        store.record_event(conn, wo_id=wo_id, tool_name="create_work_order",
                           actor=_ACTOR, detail={"title": title,
                                                 "asset_id": asset_id})
    return {"wo_id": wo_id, "status": "open", "title": title,
            "asset_id": asset_id}


def close_work_order(arguments: dict[str, Any]) -> dict[str, Any]:
    """Close a work order. The tool the effect-verification scenario uses.

    Returns the post-state fields the declared postcondition names, so the
    reader has something to compare against — but the reader does NOT trust
    this return value. It goes back to the database on its own credential.
    A tool reporting its own success is not evidence that it succeeded.
    """
    wo_id = _require(arguments, "wo_id")
    reason = str(arguments.get("reason", "")).strip() or "closed by governed agent"
    with store.writer() as conn:
        before = store.fetch_work_order(conn, wo_id)
        if before is None:
            raise ToolArgumentError(f"no such work order: {wo_id}")
        if before["status"] != "open":
            raise ToolArgumentError(
                f"work order {wo_id} is {before['status']}, not open")
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE work_orders SET status = 'closed', closed_reason = %s,"
                "       updated_at = now(), updated_by = %s WHERE wo_id = %s",
                (reason, _ACTOR, wo_id))
        store.record_event(conn, wo_id=wo_id, tool_name="close_work_order",
                           actor=_ACTOR, detail={"reason": reason})
    return {"wo_id": wo_id, "status": "closed", "closed_reason": reason}


def purge_work_order_history(arguments: dict[str, Any]) -> dict[str, Any]:
    """Declared, classified critical, and deliberately not implemented.

    It exists so the ESCALATE scenario runs against a tool the deployment
    genuinely declares rather than a name invented for the demo — an unknown
    name would be refused by the fail-closed floor instead, which tests a
    different property.

    The worker role holds no DELETE grant, so even an approved call cannot
    perform it. Raising here makes that explicit rather than surfacing as a
    permission error that reads like a misconfiguration.
    """
    raise ToolArgumentError(
        "purge_work_order_history is declared but not implemented: the "
        "work-order role holds no DELETE grant, because a governed system "
        "whose worker can erase the record of what it did is not governed."
    )


def register_tools(register: Callable[[str, Callable[[Any], Any]], None]) -> None:
    for name, fn in (
        ("read_work_order", read_work_order),
        ("set_work_order_priority", set_work_order_priority),
        ("create_work_order", create_work_order),
        ("close_work_order", close_work_order),
        ("purge_work_order_history", purge_work_order_history),
    ):
        register(name, fn)
