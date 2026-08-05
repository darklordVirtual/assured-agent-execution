# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""The ToolPack's dependency on an unstable REMORA namespace, pinned.

``toolpacks/work_order/bundle.py`` imports nine types from ``remora.toolcall.*``
to declare what this deployment's tools mean. That namespace is:

- **not** ``remora.sdk``, the stable namespace the lock names;
- **not** in ``public_api_v1.json``, so no snapshot gate covers it;
- **not** in ``forbidden_namespaces`` either — it is undeclared territory.

No equivalent exists in ``remora.sdk``, so a deployment cannot author its own
semantics without reaching into it. The right upstream fix is a declarative
ToolPack format, so a deployment writes data instead of Python; until that
exists, the honest thing is to make the dependency visible and gated rather
than to let it sit in an import block where nobody would notice it decaying.

So: this file asserts the exact surface used, including the constructor
keywords. An upstream rename, a moved module, or a changed signature fails
here — loudly, in CI, on a pin bump — instead of failing in a deployment as
"every proposal abstains" with no obvious cause.

The zero-symbol assertion at the bottom is the one that will change: when the
SDK grows this surface, it fails, and that failure is the signal to delete
this file and import from ``remora.sdk`` instead.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LOCK = json.loads(
    (ROOT / "product" / "core-artifact-lock.json").read_text(encoding="utf-8")
)

pytest.importorskip(
    "remora.toolcall.semantic_bundle",
    reason="the pinned REMORA wheel is not installed; run "
           "`python scripts/verify_core_pin.py --out dist` then install it",
)

#: module path -> symbols this product imports from it.
_AUTHORING_SURFACE = {
    "remora.toolcall.routing.compatibility": ("CoverageScope", "StateIndex"),
    "remora.toolcall.routing.goal_match": ("TaskIntent",),
    "remora.toolcall.routing.tool_contract": (
        "ToolContract", "ToolContractRegistry"),
    "remora.toolcall.routing.tool_registry": ("ToolRegistry", "ToolSignature"),
    "remora.toolcall.semantic_bundle": ("ResolvedIntent", "SemanticBundle"),
}


@pytest.mark.parametrize("module_path, symbols", sorted(_AUTHORING_SURFACE.items()))
def test_the_authoring_symbols_are_where_the_toolpack_expects(
    module_path: str, symbols: tuple[str, ...]
) -> None:
    import importlib

    module = importlib.import_module(module_path)
    missing = [name for name in symbols if not hasattr(module, name)]
    assert not missing, (
        f"{module_path} no longer provides {missing}. "
        f"toolpacks/work_order/bundle.py imports these to declare tool "
        f"semantics; without them this deployment can ground nothing and "
        f"every proposal abstains."
    )


@pytest.mark.parametrize("type_name, required_keywords", [
    ("ToolSignature", ("name", "effect", "required_params")),
    ("ToolContract", ("tool", "capability", "effect", "resource_type",
                      "mutation", "argument_roles", "state_delta")),
    ("TaskIntent", ("operation", "resource_type", "requested_effect",
                    "target_entities", "source_spans", "action_spans",
                    "proposed_by")),
    ("ResolvedIntent", ("intent", "task_text", "authority")),
    ("CoverageScope", ()),
])
def test_the_constructor_keywords_the_toolpack_passes_still_exist(
    type_name: str, required_keywords: tuple[str, ...]
) -> None:
    """Presence is not enough — the ToolPack passes these by keyword.

    A renamed field would import fine and fail at bundle construction, which
    in a container means the control plane refuses to start with a TypeError
    rather than a statement about what changed.
    """
    import importlib

    module_path = next(
        path for path, names in _AUTHORING_SURFACE.items() if type_name in names)
    cls = getattr(importlib.import_module(module_path), type_name)
    parameters = set(inspect.signature(cls).parameters)
    missing = [kw for kw in required_keywords if kw not in parameters]
    assert not missing, (
        f"{type_name} no longer accepts {missing}; "
        f"toolpacks/work_order/bundle.py passes them by keyword"
    )


def test_state_index_still_builds_from_values_with_scopes() -> None:
    """The closed-world grounding the whole ACCEPT path rests on.

    If ``closed_world`` stopped meaning "absent is confirmed absent", a call
    naming a work order that does not exist would become merely unknown — and
    unknown does not block the way confirmed-absent does.
    """
    from remora.toolcall.routing.compatibility import CoverageScope, StateIndex

    index = StateIndex.from_values(
        {"WO-1201"},
        (CoverageScope("maintenance", frozenset({"wo_id"}), closed_world=True),),
    )
    assert index.status("maintenance", "wo_id", "WO-1201").value == "supported"
    assert index.status("maintenance", "wo_id", "WO-9999").value == "unsupported"


def test_the_toolpack_still_imports_nothing_forbidden() -> None:
    """The declared boundary, checked against this specific module.

    ``test_sdk_contract.py`` scans every product file for the forbidden
    namespaces. This narrows it to the one file that deliberately reaches
    outside ``remora.sdk``, so its exemption stays exactly as wide as stated.
    """
    source = (ROOT / "toolpacks" / "work_order" / "bundle.py").read_text(
        encoding="utf-8")
    for namespace in LOCK["forbidden_namespaces"]:
        assert f"import {namespace}" not in source
        assert f"from {namespace}" not in source


def test_the_sdk_still_does_not_offer_this_surface() -> None:
    """The upstream gap, asserted so its closure is noticed.

    When ``remora.sdk`` grows these types, this test fails. That failure is
    not a regression — it is the signal to delete this file and import from
    the stable, snapshot-gated namespace instead.
    """
    import remora.sdk as sdk

    every_symbol = {name for names in _AUTHORING_SURFACE.values() for name in names}
    available = sorted(name for name in every_symbol if hasattr(sdk, name))
    assert not available, (
        f"remora.sdk now exports {available}. The ToolPack no longer needs to "
        f"reach into remora.toolcall for them: move the imports in "
        f"toolpacks/work_order/bundle.py to remora.sdk and delete this file."
    )
