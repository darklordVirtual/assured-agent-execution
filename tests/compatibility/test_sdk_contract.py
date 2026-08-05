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
    # FT-03/FT-04, present since core-candidate-2026.08.05.2.
    "ToolSpecIdentity",
    "PostconditionSpec", "EffectStatus", "EffectVerificationView",
    "build_postcondition", "verify_effect", "content_digest",
])
def test_required_symbol_is_importable(symbol: str) -> None:
    assert hasattr(sdk, symbol), f"required SDK symbol missing: {symbol}"


@pytest.mark.parametrize("operation", [
    "assess", "approve", "reject", "execute", "execute_accepted",
    "get_proposal", "get_lifecycle", "export_evidence", "verify_audit_chain",
    "record_effect",
])
def test_required_operation_exists_on_both_clients(operation: str) -> None:
    """Sync and async must not drift: an operation missing from one of them
    forces callers into a per-client branch the product plan forbids."""
    assert hasattr(sdk.RemoraClient, operation), f"sync client lacks {operation}"
    assert hasattr(sdk.AsyncRemoraClient, operation), (
        f"async client lacks {operation}"
    )


def test_effect_statuses_keep_unknown_apart_from_wrong() -> None:
    """The distinction this product's incident handling depends on.

    ``EFFECT_UNOBSERVABLE`` and ``EFFECT_VERIFIER_FAILED`` mean *we could
    not look*; only ``EFFECT_MISMATCH`` means *we looked and it was
    wrong*. If a future core made the unknowns terminal, this product
    would start closing incidents that are still open — so the property
    is asserted here rather than trusted.
    """
    assert {s.value for s in sdk.EffectStatus} == {
        "EFFECT_VERIFIED", "EFFECT_MISMATCH", "EFFECT_UNOBSERVABLE",
        "EFFECT_VERIFIER_FAILED", "EFFECT_UNSUPPORTED",
    }
    assert sdk.EffectStatus.UNOBSERVABLE.is_terminal is False
    assert sdk.EffectStatus.VERIFIER_FAILED.is_terminal is False
    assert sdk.EffectStatus.MISMATCH.is_terminal is True


def test_verification_compares_only_the_declared_delta() -> None:
    """A system of record has other legitimate writers. If core ever began
    reporting undeclared fields, every concurrent update would surface
    here as a mismatch and the signal would become noise."""
    spec = sdk.build_postcondition(
        tool_id="create_ticket", target_selector={"system": "ops"},
        expected_fields={"title": "approved title"},
    )
    result = sdk.verify_effect(
        spec, {"title": "approved title", "updated_by_someone_else": True},
        proposal_id="p-1", execution_id="e-1", toolspec_hash="0" * 64,
        verifier_identity="aae.contract_test/v1",
    )
    assert result.status is sdk.EffectStatus.VERIFIED


def test_an_unreadable_object_is_never_reported_as_a_mismatch() -> None:
    """Failing to READ a result is not evidence the wrong thing happened,
    and this product must never compensate on that basis."""
    spec = sdk.build_postcondition(
        tool_id="create_ticket", target_selector={}, expected_fields={"a": 1},
    )
    result = sdk.verify_effect(
        spec, None, proposal_id="p-1", execution_id="e-1",
        toolspec_hash="0" * 64, verifier_identity="aae.contract_test/v1",
    )
    assert result.status is sdk.EffectStatus.UNOBSERVABLE
    assert result.status.is_terminal is False


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
