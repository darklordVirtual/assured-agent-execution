# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""Did the governance step proceed, and did the tool actually do it?

Two different questions, and an `ExecutionResult` answers them in two
different fields:

    outcome                   the governed execute step: authorized, dispatched
    tool_execution.executed   the tool itself: did the side effect happen

They disagree exactly when it matters most. A tool that raises produces::

    outcome       = "execute"
    tool_execution = {"executed": False,
                      "refusal_reason": "tool_failed_nonce_burned",
                      "error": "... work order WO-1202 is closed, not open"}

The governed step ran correctly — it was authorized, dispatched, and the
one-time grant was burned. The action did not happen. A caller that reads only
`outcome` records a failed action as a completed one, which is the worst
possible entry in an audit trail.

This module exists so nothing in the product reads one field and means the
other. It was written after a scenario passed for exactly that reason.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ExecutionVerdict", "read_verdict"]


@dataclass(frozen=True)
class ExecutionVerdict:
    """What a governed execute call actually accomplished."""

    #: The governed step: "execute", "binding_refused", "expired", ...
    outcome: str
    #: Did the tool perform its side effect? ``None`` when the response
    #: carries no tool_execution block — unknown, never assumed true.
    executed: bool | None
    #: Why not, when the tool did not run or failed part-way.
    refusal_reason: str
    #: The tool's own error, when it raised.
    error: str
    detail: str

    @property
    def acted(self) -> bool:
        """True only on a confirmed side effect. Unknown is never True."""
        return self.outcome == "execute" and self.executed is True

    @property
    def state_is_unknown(self) -> bool:
        """The nonce was burned and the tool failed part-way.

        Neither "it happened" nor "it did not" — the case an operator must be
        told about rather than have summarised into a boolean.
        """
        return self.refusal_reason == "tool_failed_nonce_burned"

    def describe(self) -> str:
        if self.acted:
            return f"{self.outcome} — the tool performed its side effect"
        if self.state_is_unknown:
            return (f"{self.outcome}, but the TOOL FAILED after the grant was "
                    f"burned — state unknown: {self.error or self.refusal_reason}")
        if self.executed is False:
            return (f"{self.outcome}, and the tool did not act: "
                    f"{self.refusal_reason or self.detail or 'no reason given'}")
        return f"{self.outcome} — no tool_execution block; whether it acted is unknown"


def read_verdict(result) -> ExecutionVerdict:
    """Read both fields off an SDK ``ExecutionResult``."""
    block = dict(result.tool_execution or {})
    executed = block.get("executed")
    return ExecutionVerdict(
        outcome=str(result.outcome or ""),
        executed=bool(executed) if executed is not None else None,
        refusal_reason=str(block.get("refusal_reason") or ""),
        error=str(block.get("error") or ""),
        detail=str(result.detail or ""),
    )
