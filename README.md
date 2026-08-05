# Assured Agent Execution

**Deterministic governance and controlled execution for probabilistic AI agents**

Powered by REMORA.

> **Maturity:** Gate A bootstrap, version `0.1.0-dev`. This repository is not
> production-ready and does not yet provide an executable AAE v1 control plane.

Assured Agent Execution (AAE) is the narrow product surface for governing
third-party agent actions. A governed proposal receives exactly one of four
decisions: **ACCEPT**, **VERIFY**, **ABSTAIN**, or **ESCALATE**. The target
product binds authorization to the exact payload, dispatches through a
credential-isolated worker, verifies observable effects, and exports a
tamper-evident lifecycle record.

## Repository boundary

AAE consumes versioned REMORA artifacts. It never installs from
`REMORA-research/master` and never copies internal modules such as
`remora.policy`, `remora.enforcement`, `remora.governance`, or `servers.*`.

This repository pins a hash-identified REMORA artifact set —
release [`core-candidate-2026.08.06.1`](https://github.com/darklordVirtual/REMORA-research/releases/tag/core-candidate-2026.08.06.1),
built from a clean checkout of commit `4c85937`:

| Artifact | Pinned by |
|---|---|
| `remora-0.10.0-py3-none-any.whl` | SHA-256 |
| `openapi.json` | SHA-256 |
| `public_api_v1.json` (36 SDK symbols) | SHA-256 |
| `execution_lifecycle_v1.yaml` | SHA-256 |
| `tool_spec_v1.yaml` | SHA-256 |
| `postcondition_contract_v1.yaml` | SHA-256 |

The two frozen contracts are pinned alongside the code on purpose:
agreeing on the wheel while disagreeing about what a ToolSpec is, or what
an effect status means, is exactly the drift a pin exists to prevent.

`product/core-artifact-lock.json` holds the pin;
`scripts/verify_core_pin.py` downloads the release and **refuses on any
hash mismatch** — a pin nobody verifies is a comment, not a control.

The release is marked **prerelease deliberately**: it is pinnable, not
blessed. No external review has run against this build, Gate B is not
closed, and the signed control-plane image, SBOM and provenance do not
exist yet.

## Bootstrap validation

```bash
python scripts/verify_core_pin.py --out dist   # verify the pinned artifacts
python -m venv .venv && .venv/bin/pip install pytest "dist/remora-0.10.0-py3-none-any.whl[sdk]"
.venv/bin/python -m pytest tests/compatibility -q
```

That is the whole bootstrap today: fetch the pinned core, verify its
hashes, install the SDK from it, and prove the surface this product
depends on is really there. There is no `aae` package to run yet.

There is no Docker profile yet: a compose file that started nothing
governable would be a demo of a product that does not exist. It arrives
with the control plane in Gate C.

## Current release blockers

Closed in REMORA core on 2026-08-05 and available in the pinned release:

- ~~no product release manifest to pin~~ — published and hash-verified.
- ~~`execute_accepted` missing from the SDK~~ — present on both clients;
  the ACCEPT token now has a governed redemption path.
- ~~`ResolutionPlan` missing~~ — present, and ESCALATE now carries a
  strictly higher `required_role` than VERIFY.
- ~~lifecycle outbox states declared ahead of wiring~~ — wired, with the
  crash matrix executed as tests and reconciliation of stranded dispatches.
- ~~evidence export~~ — `export_evidence` returns a hashed manifest.

- ~~`verify_effect` / effect verification~~ — FT-04 landed. A postcondition
  is declared, verified against **the declared delta only**, and the record
  handed back with `record_effect`. Verification runs in this product's
  process because the reader holds the credentials; REMORA stores the
  result as an attestation by a named verifier, not as a proof of its own.
- ~~signed ToolSpec (FT-03)~~ — runtime enforcement is in the pinned core,
  and `ToolSpecIdentity` tells this product which spec authorized an action
  and whether specs are enforced at all.

Still open, and the reason this is not a release candidate:

- Effect verification covers tools that **declare a postcondition reader**.
  A tool without one reports `EFFECT_UNSUPPORTED`, recorded so the absence
  is visible — that is not a verified effect.
- **No AAE control plane, worker, ToolPack, console or migrations exist yet** —
  this repository is a verified pin plus its compatibility gate, nothing more.
- OIDC, worker isolation, backup/restore and external review remain open.

Those documents arrive with Gate C; until then this README is the
status, so there is nothing to drift out of sync with.

## Non-claims

AAE does not claim that agents are always correct, that all tools can be
effect-verified, that safety is guaranteed, or that bypass remains impossible
when agents retain direct tool credentials.

## License

Source-available under Business Source License 1.1. Commercial production use
requires separate written terms. See [LICENSING.md](LICENSING.md).
