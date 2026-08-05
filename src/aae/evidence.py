# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""Export what happened, in a form someone else can check.

An evidence bundle is only worth something if a third party can verify it
WITHOUT this product, this database, or our word for anything. So the export
does three things and refuses to pretend about any of them:

1. It writes REMORA's bundle exactly as returned. The manifest's hashes are
   computed over those precise JSON bytes, so re-shaping the sections here
   would break independent verification while looking tidier.

2. It re-verifies the tenant audit chain at export time and records the
   result IN the export — including when verification fails. An export that
   silently omitted a broken chain would be worse than no export: it would
   look like evidence.

3. It writes its own manifest over the files on disk, so the archive can be
   checked as an archive, not only section by section.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from remora.sdk import RemoraClient


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _plain(value: Any) -> Any:
    """Plain containers, recursively.

    The SDK freezes response mappings as ``MappingProxyType`` so a consumer
    cannot mutate a result and have it keep looking authoritative. Good
    property, and ``json.dumps`` refuses it — so an export written straight
    from a response raises TypeError partway through, after some files are
    already on disk.

    Converts rather than special-casing at the top level: the proxies are
    nested inside lifecycle events and evidence sections too, and a shallow
    fix would fail on the second-deepest one instead of the first.
    """
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _write_json(path: Path, payload: Any) -> str:
    # Canonical: sorted keys, no incidental whitespace. Two exports of the
    # same content must produce the same bytes, or the digests below describe
    # the formatter rather than the evidence.
    data = json.dumps(_plain(payload), sort_keys=True,
                      separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    path.write_bytes(data)
    return _sha256_bytes(data)


def export(
    client: RemoraClient,
    proposal_ids: list[str],
    out_dir: Path,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Write an evidence archive for the given proposals. Returns the manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    exported: list[str] = []
    failures: dict[str, str] = {}

    for proposal_id in proposal_ids:
        try:
            bundle = client.export_evidence(proposal_id)
        except Exception as exc:  # noqa: BLE001
            # Recorded, not dropped. A proposal that could not be exported is
            # a gap in the archive, and the archive must say so itself.
            failures[proposal_id] = f"{type(exc).__name__}: {exc}"
            continue
        name = f"proposal-{proposal_id}.json"
        files[name] = _write_json(out_dir / name, bundle)
        exported.append(proposal_id)

        try:
            trail = client.get_lifecycle(proposal_id)
        except Exception as exc:  # noqa: BLE001
            failures[f"{proposal_id}:lifecycle"] = f"{type(exc).__name__}: {exc}"
            continue
        lifecycle_name = f"lifecycle-{proposal_id}.json"
        files[lifecycle_name] = _write_json(out_dir / lifecycle_name, trail.raw)

    # The chain, verified at export time. Failure is recorded, never raised
    # past here: an operator exporting evidence during an incident needs the
    # export to complete AND to say the chain did not verify.
    try:
        verification = client.verify_audit_chain()
        chain: dict[str, Any] = {
            "verified": bool(verification.valid),
            "raw": dict(verification.raw or {}),
        }
    except Exception as exc:  # noqa: BLE001
        chain = {"verified": None,
                 "error": f"{type(exc).__name__}: {exc}",
                 "note": "the chain could not be verified at export time; "
                         "'verified': null means unknown, never 'clean'"}
    files["audit-chain-verification.json"] = _write_json(
        out_dir / "audit-chain-verification.json", chain)

    manifest = {
        "manifest_version": 1,
        "product": "assured-agent-execution",
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "proposals_exported": sorted(exported),
        "proposals_failed": failures,
        "audit_chain_verified": chain.get("verified"),
        "files": {name: f"sha256:{digest}" for name, digest in sorted(files.items())},
        "how_to_verify": (
            "Recompute SHA-256 over each file listed in 'files'. Each "
            "proposal bundle carries REMORA's own manifest over its sections; "
            "check that too. 'audit_chain_verified': null means the chain "
            "could not be checked at export time — it does not mean clean."
        ),
    }
    _write_json(out_dir / "manifest.json", manifest)
    return manifest
