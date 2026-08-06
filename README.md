# Assured Agent Execution

[![CI](https://github.com/darklordVirtual/assured-agent-execution/actions/workflows/pr.yml/badge.svg)](https://github.com/darklordVirtual/assured-agent-execution/actions/workflows/pr.yml) [![Core: REMORA, pinned](https://img.shields.io/badge/core-REMORA_pinned-6ea8fe.svg)](https://github.com/darklordVirtual/REMORA-research) [![Paper (PDF)](https://img.shields.io/badge/paper-PDF-b31b1b.svg)](https://github.com/darklordVirtual/REMORA-research/blob/master/paper/remora_paper.pdf) [![License: BUSL-1.1](https://img.shields.io/badge/License-BUSL--1.1-blue.svg)](LICENSING.md)

**Controlled execution for AI-agent tool calls.**

Powered by [REMORA](https://github.com/darklordVirtual/REMORA-research).

Assured Agent Execution (AAE) governs how AI agents interact with tools and
operational systems.

Every proposed tool call receives one of four decisions:

| Decision | Action |
| --- | --- |
| **ACCEPT** | Execute automatically |
| **VERIFY** | Require approval |
| **ABSTAIN** | Stop |
| **ESCALATE** | Route to a higher authority |

AAE binds authorization to the exact tool call, separates approval from
execution, verifies selected effects against the system of record, and records
the complete execution lifecycle.

## Architecture

```mermaid
flowchart LR
    A[AI agent or application] --> B[AAE control plane]

    R[Pinned REMORA core] --> B
    T[Signed ToolPack] --> B
    I[Authority source] --> B

    B --> D{Decision}

    D -->|ACCEPT| X[Execute]
    D -->|VERIFY| V[Required approval]
    D -->|ABSTAIN| S[Stop]
    D -->|ESCALATE| H[Higher authority]

    V --> X
    H --> V

    X --> O[System of record]
    O --> P[Read-only effect verification]

    B --> E[Audit and evidence]
    P --> E
```

## Core capabilities

* Four-way decision routing
* Exact-payload approval binding
* Separate identities for proposing, approving and executing
* Deployment-controlled ToolSpecs
* Pinned and verified REMORA artifacts
* Read-only postcondition verification
* Replay-resistant execution grants
* Lifecycle and evidence export

## Quickstart

### Requirements

* Docker with Docker Compose
* Python 3.11 or newer
* Authenticated GitHub CLI (`gh`)

```bash
git clone https://github.com/darklordVirtual/assured-agent-execution
cd assured-agent-execution

python run.py up
python run.py scenarios
```

`python run.py up`:

1. fetches and verifies the pinned REMORA artifacts;
2. creates installation-specific credentials;
3. signs the ToolPack;
4. builds the containers;
5. applies the database migrations;
6. starts the control plane and dashboard.

The API and dashboard URLs are printed when startup completes.

## Reference scenarios

The included work-order ToolPack demonstrates the complete execution flow:

```text
ACCEPT     Grounded read executes automatically
VERIFY     Production write requires approval
ABSTAIN    Unresolved authority stops the request
ESCALATE   Destructive action requires senior authority
BINDING    Approval cannot be reused for another payload
ROLES      An approver cannot execute its own approval
```

The main governed path is:

```text
propose → assess → approve → execute → verify effect → record
```

To try to get past these controls yourself, see
[Attack the demo](docs/tutorials/attack-the-demo.md).

## REMORA integration

AAE runs REMORA's governance engine, consumed as a fixed set of released
artifacts rather than from its main branch. Every artifact is verified before
anything uses it, and the install refuses on a mismatch.

Which artifacts, the current values, and how to move to a newer release:
[The pinned core](docs/pinned-core.md).

## Repository structure

```text
src/aae/                  CLI, configuration, evidence and verification
toolpacks/work_order/     Reference ToolPack
db/workorders/            Example system-of-record schema
console/                  Local demonstration dashboard
docker/                   Container definitions
product/                  Pinned REMORA artifacts and metadata
tests/compatibility/      Core compatibility tests
tests/e2e/                Execution and security tests
docs/                     Architecture, security model and operations
```

## Commands

```bash
python run.py up          # Start the stack
python run.py scenarios   # Run the reference scenarios
python run.py doctor      # Inspect the deployment
python run.py check-sign  # Verify the ToolPack
python run.py check       # Contract tests, no Docker required
python run.py verify      # Contract and end-to-end tests
python run.py down        # Stop the stack
python run.py reset       # Stop and remove all volumes
```

`backup`, `restore`, `sbom`, `sign` and `reseed` are documented in
[Operations](docs/operations.md).

## Documentation

* [Architecture](docs/architecture.md) — components, data flow and boundaries
* [The pinned core](docs/pinned-core.md) — what is pinned, and how it is verified
* [Security model](docs/security-model.md) — what is enforced, and by what
* [Limitations](docs/limitations.md) — known gaps
* [Operations](docs/operations.md) — signing, backup, evidence and upgrades
* [Attack the demo](docs/tutorials/attack-the-demo.md) — try to get past the controls
* [Decision records](docs/adr/) — why the architecture is the way it is

## Non-claims

AAE does not claim that agents are always correct, that all tools can be
effect-verified, that safety is guaranteed, or that bypass is impossible when
an agent retains direct tool credentials.

## License

Source-available under the Business Source License 1.1.

See [LICENSING.md](LICENSING.md) for permitted use and commercial terms. The
pinned REMORA core is a separate Licensed Work under the same licensor.
