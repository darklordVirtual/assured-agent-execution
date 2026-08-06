# Assured Agent Execution

**Deterministic governance and controlled execution for probabilistic AI agents**

Powered by REMORA.

> **Maturity:** `0.1.0-dev`, Gate C vertical. The product installs, runs and
> proves its own claims on one command. It is **not** production-ready: no
> external security review has run against it, the pinned core is a
> deliberate prerelease, and there is no signed image, SBOM or build
> provenance. See [Non-claims](#non-claims).

A governed proposal receives exactly one of four decisions — **ACCEPT**,
**VERIFY**, **ABSTAIN**, **ESCALATE**. AAE binds authorization to the exact
payload, dispatches through credentials the agent never holds, reads the
effect back on a credential that cannot write it, and exports a tamper-evident
record.

## Install

Requires Docker, Python 3.11+, and the GitHub CLI (`gh`, to fetch the pinned
core release).

```bash
git clone https://github.com/darklordVirtual/assured-agent-execution
cd assured-agent-execution
make up          # verify the pin, generate secrets, sign, build, migrate, start
make scenarios   # run the four decisions against what you just started
```

There is nothing to fill in first. `make up` generates this installation's
signing keys, database passwords and bearer tokens into `.env`, so no two
installs share a credential — not even two developers on the same team.

Then open the console at <http://localhost:8089>.

## What `make scenarios` proves

```
[PASS] ACCEPT: grounded read under signed WO-1201
       assess → accept (grounded_read_accept)
       execute_accepted → execute
       replay refused → ReplayRefusedError

[PASS] VERIFY: close WO-1202 under signed authority
       assess → verify (evidence_insufficient)
       agent token refused approval (correct)
       domain_expert approved → approved
       execute → execute
       EFFECT_VERIFIED — the system of record shows the approved delta
       record_effect → EFFECT_VERIFIED in the chain

[PASS] ABSTAIN: read under an authority that does not resolve
[PASS] ESCALATE: purge work-order history → required_role=senior_authority
[PASS] BINDING: approve one payload, execute another → binding_refused
[PASS] ROLES: the approver cannot execute
```

Read the VERIFY trace closely, because it is the whole product in six lines: a
production write was held; the agent that proposed it was refused the right to
approve it; the identity the *decision* named — not a generic approver —
released it; the operator executed; and a separate process holding a
`SELECT`-only credential went and looked at the database to confirm the
approved change had actually happened.

## What each boundary buys

| Boundary | What it prevents |
|---|---|
| Risk classification lives in `tool_metadata.json`, hashed into REMORA's policy identity | A tool cannot classify itself, and relabelling one invalidates every execution lease issued before the relabel |
| Work-order authority is resolved **server-side** from a file the deployment controls | The agent names which authority it acts under and can never assert that the authority exists or says what it claims |
| ToolSpec bundle signed with a deployment key (`make sign`) | Argument schemas, allowed targets and credential scopes cannot be edited by the thing they constrain — and the **pinned digest** refuses a correctly-signed *older* bundle, because a signature proves authenticity, never currency |
| Approval is bound to the exact tool-call hash | Getting a yes for one payload and executing another — the realistic attack on human-in-the-loop, which is never "bypass the human" |
| `operator` / `reviewer` / `domain_expert` / `senior_authority` / `viewer`, none of them `admin` | One credential that can both approve and execute makes every other control decorative |
| The postcondition reader holds `SELECT` and nothing else | A verifier that could write the state it verifies is reporting on itself |
| `EFFECT_UNOBSERVABLE` / `EFFECT_VERIFIER_FAILED` are non-terminal; only `EFFECT_MISMATCH` is | "We could not look" is not "it was wrong". Collapsing them would close incidents that are still open, or open incidents for actions that succeeded |

## The dependency direction

AAE consumes versioned REMORA artifacts. It never installs from
`REMORA-research/master` and never imports `remora.policy`,
`remora.enforcement`, `remora.governance` or `servers`.

The control plane **is** REMORA's governance API, installed from the pinned
wheel and run as a service. `servers/` and `schemas/` ship inside that wheel,
so running it is *deploying* the core, not importing it — and
`tests/compatibility` enforces that no AAE source file crosses the line.

This repository pins release
[`core-candidate-2026.08.06.3`](https://github.com/darklordVirtual/REMORA-research/releases/tag/core-candidate-2026.08.06.3),
built from a clean checkout of `aff4edf`:

| Artifact | Pinned by |
|---|---|
| `remora-0.10.0-py3-none-any.whl` | SHA-256 |
| `core-release-manifest.json` | SHA-256 |
| `openapi.json` | SHA-256 |
| `public_api_v1.json` (36 SDK symbols) | SHA-256 |
| `execution_lifecycle_v1.yaml` | SHA-256 |
| `tool_spec_v1.yaml` | SHA-256 |
| `postcondition_contract_v1.yaml` | SHA-256 |

`scripts/verify_core_pin.py` downloads them and **refuses on any hash
mismatch** — a pin nobody verifies is a comment, not a control. It also
verifies the copy checked into this tree, because verifying only the download
left the in-tree manifest free to rot, and it did for two pin bumps.

`tests/compatibility/test_pin_manifest_agreement.py` then compares the
hand-written lock against the release-generated manifest field by field, so
the two cannot disagree silently again.

One dependency is *not* on the stable namespace and is not hidden:
`toolpacks/work_order/bundle.py` imports nine types from `remora.toolcall.*`
to declare what this deployment's tools mean, because no equivalent exists in
`remora.sdk`. `test_toolpack_authoring_surface.py` pins the exact symbols and
constructor keywords, and its last test asserts `remora.sdk` does not yet
offer them — when that test fails, it is the signal to move the imports.

## Verify it yourself

```bash
make verify      # pin + 71 contract tests + 55 end-to-end tests
make check-sign  # verify the signed ToolSpec bundle without re-signing
aae doctor       # what is pinned, what is served, what is reachable
```

The end-to-end suite includes `tests/e2e/test_toolspec.py`, which takes the
bundle this deployment actually runs and attacks it — widening allowed
targets, rewriting the description an agent reads, escalating credential
scope, reclassifying a destructive tool as routine, presenting a revoked
signer, and presenting a correctly-signed older bundle. Every one is refused.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — what runs where, and why each
  boundary is where it is
- [docs/ONBOARDING.md](docs/ONBOARDING.md) — install, run and verify in 30
  minutes without reading the research repository
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — signing, rotation, evidence
  export, and what to do when an effect does not verify

## Known gaps

Stated here rather than discovered later.

- **A fully grounded medium-risk mutation abstains.** It falls through every
  rule to `default_safe_abstain`: no review item, no `ResolutionPlan`, no path
  an approver could take, for an action a signed work order authorizes. High
  risk hits `evidence_insufficient`; ungrounded arguments hit their own rule;
  medium-and-grounded matches neither, so declaring a risk tier leaves a tool
  *worse off* than leaving it unknown. Raised upstream.
- **`callable_digest` is recorded, not enforced.** `ToolSpecBundle.verify_callable`
  exists in REMORA 0.10.0 and nothing calls it at dispatch. AAE computes the
  real source digest and a test asserts it matches, so the field is true — but
  until core wires the check, a swapped callable is caught by the policy bundle
  hash over the module source, not by the spec.
- **HMAC signing is symmetric.** The process that verifies the ToolSpec bundle
  holds the key that signs it. "The agent cannot sign its own spec" is only as
  strong as the agent's inability to read the control plane's environment. The
  frozen contract defers asymmetric signing to v2.
- **Tools run in the control-plane process.** `GovernedToolDispatcher` holds
  the callables, so the credential separation that exists is at the *database*
  (writer vs reader), not at a process boundary. A credential-isolated worker
  needs a remote dispatch boundary REMORA does not yet have.
- **Effect verification covers tools that declare a reader.** A tool without
  one reports `EFFECT_UNSUPPORTED`, recorded so the absence is visible — that
  is not a verified effect.
- No OIDC, no backup/restore procedure, no external review, no signed image,
  no SBOM, no build provenance.

## Non-claims

AAE does not claim that agents are always correct, that all tools can be
effect-verified, that safety is guaranteed, or that bypass remains impossible
when agents retain direct tool credentials.

## License

Source-available under Business Source License 1.1. Commercial production use
requires separate written terms. See [LICENSING.md](LICENSING.md).

The pinned REMORA core is a **separate** Licensed Work under its own copy of
the same license: an AAE license grants nothing in REMORA, and vice versa. A
deployment runs both and needs to be permitted under both.
