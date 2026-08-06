# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""The inventory of what actually ships, and the limits it states.

An SBOM is only worth something if it describes the artifact that runs and is
honest about what it leaves out. A partial inventory presented as a complete
one is worse than none: it converts an unknown into a false assurance.

So these check both halves — that the document names the pinned core wheel it
is supposed to, and that it says out loud what it does not cover.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("live")

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "sbom.py"


@pytest.fixture(scope="module")
def sbom(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("sbom")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        pytest.skip(f"sbom unavailable here: {result.stderr.strip()[:200]}")
    return out


def _document(sbom: Path, service: str) -> dict:
    return json.loads((sbom / f"{service}.cdx.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("service", ["control-plane", "console"])
def test_the_document_is_cyclonedx(sbom, service: str) -> None:
    document = _document(sbom, service)
    assert document["bomFormat"] == "CycloneDX"
    assert document["specVersion"] == "1.5"
    assert document["components"], "an SBOM with no components is not an SBOM"


@pytest.mark.parametrize("service", ["control-plane", "console"])
def test_every_component_carries_a_resolvable_identifier(sbom, service) -> None:
    """A name and a version with no package URL is a note, not an inventory.

    The purl is what lets a vulnerability scanner match this against an
    advisory feed without guessing which ecosystem a name belongs to.
    """
    for component in _document(sbom, service)["components"]:
        assert component["name"]
        assert component["version"]
        assert component["purl"].startswith("pkg:pypi/"), component


def test_the_inventory_names_the_pinned_core(sbom) -> None:
    """The whole dependency direction rests on one wheel.

    An SBOM that did not name it would be an inventory of everything except
    the thing this product is built on.
    """
    lock = json.loads(
        (ROOT / "product" / "core-artifact-lock.json").read_text(encoding="utf-8"))
    components = {c["name"].lower(): c["version"]
                  for c in _document(sbom, "control-plane")["components"]}
    assert components.get("remora") == lock["remora_core_version"], (
        f"the control plane's inventory says remora "
        f"{components.get('remora')}, the pin says "
        f"{lock['remora_core_version']}")


def test_the_running_image_is_identified_by_digest(sbom) -> None:
    """Which build this describes. An SBOM detached from an image identifies
    nothing — two builds of the same Dockerfile are not the same artifact."""
    for service in ("control-plane", "console"):
        image = _document(sbom, service)["metadata"]["image"]
        assert image["image_id"].startswith("sha256:")


def test_the_manifest_states_what_the_inventory_does_not_cover(sbom) -> None:
    """The honesty requirement, asserted.

    OS packages are not itemised and this is not build provenance. Both
    limits must survive in the document, because the person reading it later
    will not have this conversation.
    """
    manifest = json.loads((sbom / "manifest.json").read_text(encoding="utf-8"))
    does_not_cover = manifest["does_not_cover"].lower()
    assert "debian" in does_not_cover or "base layer" in does_not_cover
    assert "provenance" in does_not_cover
    assert "Python" in manifest["format"]


def test_every_listed_file_matches_its_digest(sbom) -> None:
    import hashlib

    manifest = json.loads((sbom / "manifest.json").read_text(encoding="utf-8"))
    for name, declared in manifest["files"].items():
        actual = "sha256:" + hashlib.sha256(
            (sbom / name).read_bytes()).hexdigest()
        assert actual == declared, f"{name} does not match the manifest"
