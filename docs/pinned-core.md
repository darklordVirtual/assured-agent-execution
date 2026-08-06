# The pinned core

AAE runs REMORA's governance engine. It consumes it as a versioned, hash-identified
artifact set — never from `master`, never as a submodule, never vendored.

This document is the whole story: what is pinned, what the current values are,
how they are verified, and what changing them involves.

## Why pin at all

REMORA is a research repository under active change. Installing from its main
branch would make this product an unstable fork of it: research changes would
arrive unannounced, and the product's behaviour would move with them.

A pin makes an upgrade a deliberate, reviewable act. The reasoning is recorded
in [adr/0001-pinned-remora-artifacts.md](adr/0001-pinned-remora-artifacts.md).

## What is pinned

Seven artifacts, each by SHA-256, in
[`product/core-artifact-lock.json`](../product/core-artifact-lock.json):

| Artifact | What it fixes |
|---|---|
| `remora-0.10.0-py3-none-any.whl` | the engine and the SDK |
| `core-release-manifest.json` | the core's own description of what it shipped |
| `openapi.json` | the REST contract |
| `public_api_v1.json` | the SDK's public surface — 36 symbols |
| `execution_lifecycle_v1.yaml` | the states a proposal can be in |
| `tool_spec_v1.yaml` | what a ToolSpec is |
| `postcondition_contract_v1.yaml` | what an effect status means |

The three contracts are pinned alongside the code deliberately. Agreeing on the
wheel while disagreeing about what a ToolSpec is, or what `EFFECT_MISMATCH`
means, is exactly the drift a pin exists to prevent.

## Current pin

```
release   core-candidate-2026.08.06.3   (prerelease, deliberately)
commit    aff4edf7ec61a29c6db4c56204284938a788780f
version   REMORA 0.10.0
```

`prerelease` is not an oversight. The release is pinnable, not blessed: no
external review has run against that build, and there is no signed image or
build provenance. The lock records why in `release_status_reason`, and
`verify_core_pin.py` prints it on every run rather than letting it pass
unnoticed.

## How the pin is verified

```bash
python scripts/verify_core_pin.py --out dist
```

It downloads the release assets and **refuses on any hash mismatch** — a pin
nobody verifies is a comment, not a control. It also verifies the copy of the
release manifest checked into this tree, because verifying only the download
left the in-tree file free to rot, and it did for two pin bumps.

Exit codes distinguish the two failures that matter: `1` means the artifact is
not what was pinned, `2` means the release could not be reached. A network
outage must never read as a tampered artifact.

Two more checks run without any network at all, as part of `python run.py check`:

- `test_pin_manifest_agreement.py` compares the hand-written lock against the
  release-generated manifest field by field — commit, version, every digest,
  the symbol count three ways, and the consumer contract. That test exists
  because the two disagreed for two pin bumps and nothing compared them.
- `test_sdk_contract.py` asserts every promised SDK symbol is importable, that
  both clients carry every required operation, and that no product file imports
  a namespace the lock classifies as internal.

The control-plane image verifies the wheel's digest a **third** time, inside
the build, against the lock it also copies in. That is not redundant: the first
check gates the download, and the second makes the image self-describing — an
image built from an unverified wheel fails its own build rather than shipping.

## Digests are content, not bytes

`verify_core_pin.py` normalises CRLF to LF before hashing text artifacts, so a
Windows checkout and a Linux one agree. The wheel is binary and hashed raw.

This makes the text digests *content* digests rather than digests of the exact
published bytes. `.gitattributes` forces LF throughout, so in practice the two
coincide — but if byte-level provenance is ever required, that distinction is
where to start.

## Upgrading

```bash
# 1. edit product/core-artifact-lock.json: release_tag, commit, wheel digest
# 2. take the release's own manifest, and pin its digest too
gh release download <tag> -R darklordVirtual/REMORA-research \
   -D dist -p core-release-manifest.json --clobber
cp dist/core-release-manifest.json product/core-release-manifest.json

# 3. verify before anything is built
python scripts/verify_core_pin.py --out dist
python run.py check        # the lock and the manifest must agree
python run.py up && python run.py verify
```

Step 2 is the one people skip. The manifest is the core's own account of what
it published; without pinning its digest, the copy in this tree can drift from
the release it names — which is precisely what happened before the agreement
test existed.

## The one dependency that is not pinned this way

`toolpacks/work_order/bundle.py` imports nine types from `remora.toolcall.*` to
declare what this deployment's tools mean. That namespace is not `remora.sdk`,
is not in the public-API snapshot, and is therefore not covered by the pin's
drift gate — no equivalent exists in the SDK, so a deployment cannot author its
own semantics without it.

`test_toolpack_authoring_surface.py` pins the exact symbols and their
constructor keywords, so an upstream rename fails in CI on a pin bump rather
than in a deployment as "every proposal abstains". Its last test asserts that
`remora.sdk` does **not** yet offer them: when that fails, it is the signal to
move the imports and delete the file.

## What the pin does not give you

The REMORA wheel is byte-pinned. The rest of this product's build is not: base
images are tags rather than digests, and the Python dependencies are floors
rather than a lockfile. `python run.py sbom` records what a given build actually
contained, which is a record, not reproducibility. See
[limitations.md](limitations.md).
