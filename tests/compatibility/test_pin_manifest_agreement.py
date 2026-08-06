# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""The lock and the core's own manifest must describe the same release.

Two files claim to say what this product consumes: ``core-artifact-lock.json``
(written here, by hand, on every pin bump) and ``core-release-manifest.json``
(written by REMORA's release job, copied in verbatim). Hash-verifying the
manifest proves it is the file the release published. It does NOT prove the
hand-written lock agrees with it — and for two pin bumps it did not: the
manifest still named commit ``f3e58db`` and 28 SDK symbols while the lock had
moved to ``4c85937`` and 36.

Nothing failed, because nothing compared them. This does.

These tests read JSON only. They run without the wheel installed, so a
disagreement is caught before anything is built or downloaded.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LOCK = json.loads(
    (ROOT / "product" / "core-artifact-lock.json").read_text(encoding="utf-8")
)
MANIFEST = json.loads(
    (ROOT / "product" / "core-release-manifest.json").read_text(encoding="utf-8")
)


def test_the_same_core_commit_is_named_by_both() -> None:
    assert LOCK["remora_core_commit"] == MANIFEST["git_commit"], (
        "the lock pins a different core commit than the manifest it ships "
        "beside — one of them is stale"
    )


def test_the_same_core_version_is_named_by_both() -> None:
    assert LOCK["remora_core_version"] == MANIFEST["remora_core_version"]


@pytest.mark.parametrize("lock_key, artifact", [
    ("openapi_sha256", "openapi"),
    ("sdk_public_api_sha256", "sdk_public_api"),
    ("execution_lifecycle_schema_sha256", "execution_lifecycle_schema"),
    ("tool_spec_schema_sha256", "tool_spec_schema"),
    ("postcondition_contract_schema_sha256", "postcondition_contract_schema"),
])
def test_artifact_digest_agrees_with_the_manifest(
    lock_key: str, artifact: str
) -> None:
    """Every contract artifact the lock pins is the one the release built.

    A digest present in only one of the two files is the interesting case:
    ``tool_spec_schema`` and ``postcondition_contract_schema`` were pinned
    here before the manifest carried them at all.
    """
    assert artifact in MANIFEST["artifacts"], (
        f"the manifest does not describe {artifact}; the lock pins a digest "
        f"for an artifact the release does not claim to have published"
    )
    assert LOCK[lock_key] == MANIFEST["artifacts"][artifact]["sha256"]


def test_the_wheel_is_the_same_file_in_both() -> None:
    wheel = MANIFEST["artifacts"]["wheel"]
    assert LOCK["wheel"]["filename"] == wheel["filename"]
    assert LOCK["wheel"]["sha256"] == wheel["sha256"]


def test_the_symbol_count_agrees_three_ways() -> None:
    """The lock's count, the manifest's count, and the symbols themselves.

    ``supported_sdk_symbols`` is what ``test_sdk_contract.py`` asserts the
    installed wheel exports. If it drifts from the manifest, that test starts
    measuring the installed SDK against a number nobody maintains.
    """
    declared = LOCK["supported_sdk_symbols"]
    assert declared == MANIFEST["artifacts"]["sdk_public_api"]["symbol_count"]
    assert declared == len(MANIFEST["sdk"]["symbols"])


def test_the_consumer_contract_is_the_one_this_product_enforces() -> None:
    """The import boundary is only meaningful if both sides name it the same.

    ``test_sdk_contract.py`` refuses product imports of these namespaces. If
    core widened the internal set and the lock kept the old list, the boundary
    test would keep passing while no longer covering the boundary.
    """
    contract = MANIFEST["consumer_contract"]
    assert LOCK["stable_namespace"] == contract["stable_namespace"]
    assert set(LOCK["forbidden_namespaces"]) == set(
        contract["internal_namespaces"]
    ), (
        "the lock's forbidden namespaces are not the manifest's internal "
        "namespaces; the import-boundary test is checking the wrong set"
    )


def test_a_dirty_build_is_not_recorded_as_clean() -> None:
    """``built_from_clean_worktree`` is a claim about the release, not a wish.

    It may only be true here if the release job said so.
    """
    if LOCK.get("built_from_clean_worktree"):
        assert MANIFEST.get("worktree_clean") is True, (
            "the lock claims a clean-worktree build; the manifest does not"
        )


def test_a_prerelease_pin_says_why() -> None:
    """A status without a reason is a status nobody can act on.

    This product deliberately pins prereleases. That is defensible only while
    the file states what is unproven about the build it pinned.
    """
    if LOCK.get("release_status") != "stable":
        assert LOCK.get("release_status_reason", "").strip(), (
            "a non-stable pin must record why it is not stable"
        )


# ── The document that quotes the pin ───────────────────────────────────────

def test_the_pinned_core_document_quotes_the_current_pin() -> None:
    """docs/pinned-core.md names a release, a commit and a version.

    A reference document that quotes values goes stale the moment they move,
    and a stale one is worse than none: it looks authoritative. This binds it
    to the lock, so a pin bump that forgets the document fails here rather
    than misleading the next reader.
    """
    doc = (ROOT / "docs" / "pinned-core.md").read_text(encoding="utf-8")
    for field, value in (
        ("release_tag", LOCK["release_tag"]),
        ("remora_core_commit", LOCK["remora_core_commit"]),
        ("remora_core_version", LOCK["remora_core_version"]),
        ("wheel filename", LOCK["wheel"]["filename"]),
    ):
        assert value in doc, (
            f"docs/pinned-core.md does not name the current {field} "
            f"({value}). Update the document with the pin.")


def test_the_pinned_core_document_names_every_pinned_artifact() -> None:
    """Seven digests in the lock, seven artifacts in the table."""
    doc = (ROOT / "docs" / "pinned-core.md").read_text(encoding="utf-8")
    for filename in ("core-release-manifest.json", "openapi.json",
                     "public_api_v1.json", "execution_lifecycle_v1.yaml",
                     "tool_spec_v1.yaml", "postcondition_contract_v1.yaml"):
        assert filename in doc, f"{filename} is pinned but not documented"

    declared = sum(1 for key in LOCK if key.endswith("_sha256")) + 1
    assert f"{declared} artifacts" in doc or "Seven artifacts" in doc, (
        f"the lock pins {declared} artifacts; the document does not say so")


def test_the_document_states_the_prerelease_status() -> None:
    """A reader must not have to open the lock to learn the pin is not blessed."""
    if LOCK.get("release_status") == "stable":
        pytest.skip("the pin is stable; nothing to disclose")
    doc = (ROOT / "docs" / "pinned-core.md").read_text(encoding="utf-8")
    assert "prerelease" in doc.lower()
