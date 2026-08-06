# ADR 0001 — Consume REMORA as hash-pinned release artifacts

**Accepted** 2026-08-05.

## Context

AAE is a product built on REMORA, a research repository under active change.
Installing from `master`, vendoring the source, or using a submodule all make
the product an unstable fork: research changes arrive unannounced, and the
product's behaviour changes with them.

## Decision

Consume a versioned, hash-identified artifact set. Seven SHA-256 digests are
recorded in `product/core-artifact-lock.json`: the wheel, the release
manifest, the OpenAPI document, the SDK public-API snapshot, and three frozen
contracts. `scripts/verify_core_pin.py` downloads them and refuses on any
mismatch.

The contracts are pinned alongside the code deliberately. Agreeing on the
wheel while disagreeing about what a ToolSpec is, or what an effect status
means, is exactly the drift a pin exists to prevent.

## Consequences

Upgrades are explicit and reviewable. A pin bump is a diff.

The lock is hand-written, so it can disagree with the release it names — it
did, for two bumps, naming a stale commit and symbol count. Hence
`test_pin_manifest_agreement.py`, which compares the two field by field on
JSON alone, before anything is downloaded.

Digests are computed over LF-normalised text so a Windows and a Linux checkout
agree; the wheel is hashed raw. This makes them content digests rather than
digests of the exact published bytes — acceptable while `.gitattributes`
forces LF, and worth separating if byte-level provenance is ever required.
