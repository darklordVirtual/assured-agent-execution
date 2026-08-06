# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""Verify the pinned REMORA core artifacts before anything trusts them.

The product's dependency direction only holds if the pin is *checked*.
This downloads the pinned release assets and refuses on any hash
mismatch — a pin nobody verifies is a comment, not a control.

Usage::

    python scripts/verify_core_pin.py                # verify, don't keep
    python scripts/verify_core_pin.py --out dist/    # verify and keep

Exit codes: 0 verified, 1 mismatch or missing asset, 2 network/tooling
failure (kept distinct so CI can tell "the artifact is wrong" from "we
could not reach GitHub").
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "product" / "core-artifact-lock.json"
VENDORED_MANIFEST = ROOT / "product" / "core-release-manifest.json"

#: asset filename -> lock key holding its expected digest
_TEXT_ASSETS = {
    "openapi.json": "openapi_sha256",
    "public_api_v1.json": "sdk_public_api_sha256",
    "execution_lifecycle_v1.yaml": "execution_lifecycle_schema_sha256",
    # The core's own description of what it shipped. Unverified, it was a
    # stale copy for two pin bumps: it still named commit f3e58db and 28
    # SDK symbols while the lock had moved to 4c85937 and 36. A manifest
    # nobody checks decays exactly like a pin nobody checks.
    "core-release-manifest.json": "core_release_manifest_sha256",
    # The two frozen contracts. Verifying the wheel but not these would
    # let the product agree with core about the CODE and still disagree
    # about what a ToolSpec is or what an effect status means.
    "tool_spec_v1.yaml": "tool_spec_schema_sha256",
    "postcondition_contract_v1.yaml": "postcondition_contract_schema_sha256",
}


def _sha256(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix in {".json", ".yaml", ".yml", ".md", ".txt"}:
        # LF-normalized for text so a Windows checkout and a Linux one
        # agree; the wheel is binary and hashed raw.
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _utf8() -> None:
    """UTF-8 output, from the one shared implementation."""
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "src"))
    from aae._io import force_utf8_output

    force_utf8_output()


def main() -> int:
    _utf8()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="keep the verified assets in this directory")
    args = parser.parse_args()

    if not LOCK.is_file():
        print(f"ERROR: no pin to verify at {LOCK}", file=sys.stderr)
        return 1
    lock = json.loads(LOCK.read_text(encoding="utf-8"))

    target = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="aae-pin-"))
    target.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["gh", "release", "download", lock["release_tag"],
             "-R", lock["release_repo"], "-D", str(target), "--clobber"],
            check=True, capture_output=True, text=True,
        )
    except FileNotFoundError:
        print("ERROR: the GitHub CLI (gh) is required to fetch the pinned "
              "release", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: could not download release "
              f"{lock['release_tag']}: {exc.stderr.strip()}", file=sys.stderr)
        return 2

    problems: list[str] = []
    wheel = target / lock["wheel"]["filename"]
    if not wheel.is_file():
        problems.append(f"missing asset: {wheel.name}")
    elif (actual := _sha256(wheel)) != lock["wheel"]["sha256"]:
        problems.append(
            f"{wheel.name}: expected {lock['wheel']['sha256']}, got {actual}"
        )

    for filename, key in _TEXT_ASSETS.items():
        path = target / filename
        if not path.is_file():
            problems.append(f"missing asset: {filename}")
            continue
        if (actual := _sha256(path)) != lock[key]:
            problems.append(f"{filename}: expected {lock[key]}, got {actual}")

    # The copy checked into this repository must BE the released manifest,
    # not a snapshot of some earlier one. Verifying the download alone left
    # the in-tree file free to rot, and it did.
    if not VENDORED_MANIFEST.is_file():
        problems.append(
            f"missing in-tree manifest: {VENDORED_MANIFEST.relative_to(ROOT)}")
    elif (actual := _sha256(VENDORED_MANIFEST)) != lock[
            "core_release_manifest_sha256"]:
        problems.append(
            f"{VENDORED_MANIFEST.relative_to(ROOT)} is not the manifest of "
            f"{lock['release_tag']}: expected "
            f"{lock['core_release_manifest_sha256']}, got {actual}")

    if problems:
        print("PIN VERIFICATION FAILED — the artifacts are not what this "
              "product pinned:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"pin verified: REMORA core {lock['remora_core_version']} "
          f"@ {lock['remora_core_commit'][:8]} "
          f"({len(_TEXT_ASSETS) + 1} artifacts)")
    if lock.get("release_status") != "stable":
        print(f"NOTE: pinned release is {lock.get('release_status')!r} — "
              f"{lock.get('release_status_reason', '')}")
    if args.out:
        print(f"assets kept in {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
