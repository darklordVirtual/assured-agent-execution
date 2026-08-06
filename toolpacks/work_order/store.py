# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""The work-order system of record, and the two credentials that reach it.

This is product code. It imports no REMORA module at all — not even the SDK —
because it is the thing being governed, not the thing doing the governing.

Two connection factories, and the split is the point:

``writer()``
    Used by the tool callables the control plane dispatches. Every governed
    write travels on this credential.

``reader()``
    Used by the postcondition reader. ``SELECT`` only, enforced by the grants
    in ``db/workorders/002_roles.sql``, not by convention here. A verifier that
    could write would be reporting on itself.

Both refuse to fall back to the other's DSN. A missing reader DSN is a
configuration error, never "use the writer's, it works" — that substitution
would silently delete the property the whole split exists for, and nothing
downstream would look any different.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

WRITER_DSN_ENV = "AAE_WORKORDER_DSN"
READER_DSN_ENV = "AAE_WORKORDER_READER_DSN"


class WorkOrderStoreError(RuntimeError):
    """The system of record is not usable as configured."""


def _dsn(env_var: str, role: str) -> str:
    dsn = os.getenv(env_var, "").strip()
    if not dsn:
        raise WorkOrderStoreError(
            f"{env_var} is not set. The {role} credential has no fallback: "
            f"borrowing the other role's DSN would remove the separation "
            f"between acting on the system of record and reading it back."
        )
    return dsn


@contextmanager
def writer() -> Iterator[psycopg.Connection]:
    """Connection for governed writes. Commits on success, rolls back on error."""
    with psycopg.connect(_dsn(WRITER_DSN_ENV, "worker"),
                         row_factory=dict_row) as conn:
        yield conn


@contextmanager
def reader() -> Iterator[psycopg.Connection]:
    """Read-only connection for effect verification.

    ``autocommit=True`` matters more than it looks. Without it psycopg
    holds a transaction open for the whole block, and one verification
    that stalls then blocks ``pg_dump`` — which is how a scheduled
    backup ends up hanging in silence. A reader has nothing to keep a
    transaction for.

    ``read_only=True`` is belt to the grants' braces: if this process is ever
    handed a DSN with more rights than it should have, the transaction still
    refuses to write. Defence in depth is cheap here and the failure it
    prevents — a verifier that can manufacture the state it is checking for —
    is not recoverable after the fact.
    """
    with psycopg.connect(_dsn(READER_DSN_ENV, "reader"),
                         row_factory=dict_row, autocommit=True) as conn:
        conn.read_only = True
        yield conn


def fetch_work_order(conn: psycopg.Connection, wo_id: str) -> dict[str, Any] | None:
    """One work order, or ``None`` when the row does not exist.

    ``None`` means "no such row", which is different from "could not look".
    Callers must not collapse the two: the effect-verification contract routes
    them to different statuses (``EFFECT_MISMATCH`` vs ``EFFECT_UNOBSERVABLE``)
    and only one of them is evidence that something went wrong.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT wo_id, title, asset_id, status, priority, closed_reason,"
            "       updated_by, updated_at"
            "  FROM work_orders WHERE wo_id = %s",
            (wo_id,),
        )
        return cur.fetchone()


def record_event(
    conn: psycopg.Connection,
    *,
    wo_id: str,
    tool_name: str,
    actor: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append the product's own record of a governed write.

    Kept alongside REMORA's audit chain rather than instead of it.

    **It carries no proposal or execution id, and cannot yet.** REMORA's
    dispatcher invokes a tool as ``fn(arguments)`` — the callable is never
    told which proposal authorized it. So correlation between this log and
    the audit chain is by work order, tool name, actor and time, which is
    useful and is NOT the unambiguous join a shared id would give.

    The columns exist and stay NULL deliberately: an unfilled column is a
    visible gap, where dropping them would hide one. Closing this needs an
    execution context from the dispatcher, which is upstream work — a tool
    that chose its own correlation ids would be attesting to its own
    provenance.
    """
    from psycopg.types.json import Jsonb

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO work_order_events (wo_id, tool_name, actor, detail)"
            " VALUES (%s, %s, %s, %s)",
            (wo_id, tool_name, actor, Jsonb(detail or {})),
        )
