# Assured Agent Execution

**A reference runtime for governing what an AI agent is allowed to do.**

An agent proposes a tool call. AAE evaluates it through a pinned REMORA core,
routes it to one of four decisions, executes what is approved, and then checks
whether the expected effect actually occurred.

```
agent
  │ proposes a tool call
  ▼
AAE ── pinned REMORA control plane
  ├─ ACCEPT ────────────────► execute
  ├─ VERIFY ──► human review ► execute
  ├─ ABSTAIN ───────────────► stop
  └─ ESCALATE ──────────────► higher authority
                                   │
                                   ▼
                          verify the effect really happened
```

This repository holds the product integration, a reference ToolPack for
maintenance work orders, and a local Docker deployment. It does **not** contain
the REMORA research source tree — that is consumed as a hash-pinned release.

## Quickstart

Requires Docker, Python 3.11+ and `gh`.

```bash
git clone https://github.com/darklordVirtual/assured-agent-execution
cd assured-agent-execution
python run.py up
python run.py scenarios
```

Nothing to configure. `up` generates this installation's own keys, passwords
and tokens, picks free ports, and prints the URLs.

## What the reference scenario shows

```
[PASS] ACCEPT    grounded read under a signed work order → executes, token single-use
[PASS] VERIFY    production close → held → domain_expert approves → executes
                 → EFFECT_VERIFIED against the database, on a read-only credential
[PASS] ABSTAIN   same read, unresolvable authority → stops
[PASS] ESCALATE  destructive tool → required_role=senior_authority
[PASS] BINDING   approve one payload, execute another → binding_refused
[PASS] ROLES     the approver cannot execute
```

The VERIFY line is the one to read twice. A production write was held; the
agent that proposed it could not approve it; the identity the *decision* named
released it; and a separate process holding `SELECT` and nothing else then
confirmed the change was really in the database.

Check it yourself:

```bash
docker compose exec workorder-db psql -U wo_admin -d workorders \
  -c "SELECT wo_id, status, updated_by FROM work_orders ORDER BY wo_id"
```

Then try to get past it:
[docs/tutorials/attack-the-demo.md](docs/tutorials/attack-the-demo.md).

## How AAE relates to REMORA

REMORA is the governance engine — decision semantics, audit chain, execution
lifecycle. AAE is a **product integration and controlled execution profile**
on top of it.

AAE owns the deployment profile, the pin regime, the ToolPack, the CLI, the
postcondition reader, the evidence export and the demo dashboard. The control
plane itself is REMORA's API, installed from a wheel whose SHA-256 — along with
six other artifacts — is verified before anything trusts it:

```bash
python scripts/verify_core_pin.py --out dist   # refuses on any mismatch
```

## Repository layout

```
src/aae/       product code: CLI, config, verification, evidence
toolpacks/     the work-order reference ToolPack
db/            the reference system-of-record schema
docker/        images; docker-compose.yml is the local deployment
product/       the pinned REMORA artifact lock and release manifest
console/       local demonstration dashboard — not an operator console
tests/         contract, end-to-end, security, fault
```

## Commands

```bash
python run.py up | down | reset     # start · stop · stop and destroy volumes
python run.py check                 # contract tests, no Docker needed
python run.py verify                # everything, against a stack that must be up
python run.py scenarios             # the four decisions
python run.py sign | check-sign     # the signed ToolSpec bundle
python run.py backup | restore      # both databases and the bundle
python run.py sbom                  # what is inside the images
python run.py doctor                # what is pinned, served, reachable
```

## Maturity

**Local reference vertical.** It demonstrates pinned-core consumption,
role-separated approval and execution, signed ToolSpecs and effect
verification. It is not production-hardened and has had no external security
review.

[docs/limitations.md](docs/limitations.md) lists the known gaps and is kept
current. The short version: tools run inside the control-plane process,
ToolSpec signing is symmetric, there is no OIDC, and a fully grounded
medium-risk write currently abstains with no path forward — an open issue in
the core.

AAE does not claim that agents are always correct, that all tools can be
effect-verified, that safety is guaranteed, or that bypass is impossible when
an agent keeps direct tool credentials.

## Documentation

| | |
|---|---|
| [architecture.md](docs/architecture.md) | components, data flow, boundaries |
| [security-model.md](docs/security-model.md) | what is enforced, and by what |
| [limitations.md](docs/limitations.md) | known gaps |
| [operations.md](docs/operations.md) | signing, backup, evidence, upgrades |
| [tutorials/attack-the-demo.md](docs/tutorials/attack-the-demo.md) | try to get past the controls |
| [adr/](docs/adr/) | why the architecture is the way it is |

## License

Business Source License 1.1. Commercial production use beyond the Additional
Use Grant needs separate written terms — see [LICENSING.md](LICENSING.md).

The pinned REMORA core is a separate Licensed Work under the same licensor.
