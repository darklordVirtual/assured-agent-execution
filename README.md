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

The bootstrap pins three public contract artifacts from REMORA commit
`534ace63d11760056f10ec114920c2424a8ecf4d`:

- OpenAPI contract
- declared execution lifecycle schema
- public SDK API snapshot

The pin is evidence of compatibility work, not a REMORA product release.
The required wheel, signed control-plane image, SBOM, provenance, and release
manifest do not yet exist.

## Bootstrap validation

```bash
python -m pip install -e ".[dev]"
python -m aae diagnostics
pytest
```

Docker validates the bootstrap profile and starts PostgreSQL:

```bash
cp .env.example .env
docker compose -f deploy/docker-compose.yml up --build
```

This is not the final five-minute product quickstart. It intentionally
refuses release-readiness while core blockers remain.

## Current release blockers

- REMORA has no product release manifest for AAE to pin.
- `execute_accepted(execution_token, tool_call)` is not in the public SDK.
- `ResolutionPlan` and `EffectVerification` are not in the public SDK snapshot.
- Lifecycle outbox states are declared ahead of runtime wiring.
- EFFECT verification states are absent until the postcondition contract lands.
- Signed ToolSpec runtime enforcement is not yet a consumable release artifact.
- OIDC, worker isolation, recovery, evidence export, and external review remain open.

See [implementation status](docs/implementation-status.md) and
[known limitations](docs/known-limitations.md).

## Non-claims

AAE does not claim that agents are always correct, that all tools can be
effect-verified, that safety is guaranteed, or that bypass remains impossible
when agents retain direct tool credentials.

## License

Source-available under Business Source License 1.1. Commercial production use
requires separate written terms. See [LICENSING.md](LICENSING.md).
