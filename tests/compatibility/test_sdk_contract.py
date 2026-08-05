# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""The pinned REMORA SDK must be present, complete, and the only import.

Three properties this product depends on, each tested rather than assumed:

1. every symbol the pin promises is importable from ``remora.sdk``;
2. the operations the product plan requires exist on both clients;
3. no product source file imports an internal REMORA namespace.

(3) is the one that decays silently. A single ``from remora.policy import
...`` added under deadline pressure breaks the dependency direction the
whole product boundary rests on, and nothing else would notice.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LOCK = json.loads(
    (ROOT / "product" / "core-artifact-lock.json").read_text(encoding="utf-8")
)

sdk = pytest.importorskip(
    "remora.sdk",
    reason="the pinned REMORA wheel is not installed; run "
           "`python scripts/verify_core_pin.py --out dist` then install it",
)


def test_pinned_symbol_count_matches_the_installed_surface() -> None:
    """A drifted surface means the lock describes a different artifact."""
    assert len(sdk.__all__) == LOCK["supported_sdk_symbols"], (
        f"pin promises {LOCK['supported_sdk_symbols']} symbols, installed "
        f"SDK exports {len(sdk.__all__)}"
    )


@pytest.mark.parametrize("symbol", [
    # The surface AAE §12 requires. Listed explicitly, not derived from
    # __all__, so a REMORA-side removal fails here instead of silently
    # shrinking what this test checks.
    "RemoraClient", "AsyncRemoraClient",
    "ToolCall", "AssessmentResult", "ApprovalResult", "RejectionResult",
    "ExecutionResult", "AuditVerification", "ResolutionPlan",
    "ProposalView", "LifecycleTrail", "DecisionAction",
    "RemoraError", "AuthenticationError", "AuthorizationError",
    "InvalidRequestError", "ConflictError", "ApprovalExpiredError",
    "BindingRefusedError", "ReplayRefusedError",
    "UnknownExecutionStateError",
])
def test_required_symbol_is_importable(symbol: str) -> None:
    assert hasattr(sdk, symbol), f"required SDK symbol missing: {symbol}"


@pytest.mark.parametrize("operation", [
    "assess", "approve", "reject", "execute", "execute_accepted",
    "get_proposal", "get_lifecycle", "export_evidence", "verify_audit_chain",
])
def test_required_operation_exists_on_both_clients(operation: str) -> None:
    """Sync and async must not drift: an operation missing from one of them
    forces callers into a per-client branch the product plan forbids."""
    assert hasattr(sdk.RemoraClient, operation), f"sync client lacks {operation}"
    assert hasattr(sdk.AsyncRemoraClient, operation), (
        f"async client lacks {operation}"
    )


def test_effect_verification_is_honestly_absent() -> None:
    """AAE §12 also names verify_effect/EffectVerification. They depend on
    FT-04 postcondition verification, which is NOT in the pinned release.

    Asserting the absence keeps the gap visible: when FT-04 lands and a
    newer core is pinned, this test fails and forces the surface list
    above to be updated deliberately rather than drifting into place.
    """
    assert not hasattr(sdk, "EffectVerification"), (
        "EffectVerification is now available — update the required-symbol "
        "list and the known-limitations doc"
    )
    assert not hasattr(sdk.RemoraClient, "verify_effect")


_INTERNAL = re.compile(
    r"^\s*(?:from|import)\s+(remora\.(?:policy|enforcement|governance)|servers)\b",
    re.MULTILINE,
)


def test_no_product_source_imports_an_internal_remora_namespace() -> None:
    """The dependency direction, enforced rather than documented."""
    offenders: list[str] = []
    for directory in ("src", "toolpacks", "scripts", "tests"):
        base = ROOT / directory
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if path == Path(__file__):
                continue  # this file names them in a regex, deliberately
            if _INTERNAL.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, (
        "product code may only import remora.sdk; these reach into REMORA "
        f"internals: {offenders}"
    )
