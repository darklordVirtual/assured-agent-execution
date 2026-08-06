# Architecture

```
                  ┌────────────────────────────────────────┐
  agent  ──────►  │  control plane                          │
  CLI             │  REMORA's governance API, from the      │
                  │  pinned wheel. Execution surface only.  │
                  │                                        │
                  │  toolpacks/work_order/                 │
                  │    registry.py    the callables        │
                  │    bundle.py      what they mean       │
                  │    tool_metadata  how risky they are   │
                  │    tool_specs     signed authority     │
                  │    work_orders    intent fixture       │
                  └──────┬───────────────────┬─────────────┘
                writer   │                   │
          ┌──────────────▼───┐   ┌───────────▼────────────┐
          │ control-plane-db │   │ workorder-db           │
          │ audit chain,     │   │ the system of record   │
          │ grants, outbox   │   └───────────┬────────────┘
          └──────────────────┘   reader,     │ SELECT only
                                 ┌───────────▼────────────┐
                                 │ postcondition reader   │
                                 │ a different process    │
                                 └────────────────────────┘
```

## Components

**Control plane** — REMORA's `servers.api`, run from the pinned wheel. AAE
supplies the configuration, the ToolPack and the deployment profile; it does
not reimplement decision semantics. What is pinned and how it is verified: [pinned-core.md](pinned-core.md). Why
it is done this way: [adr/0001](adr/0001-pinned-remora-artifacts.md) and
[adr/0004](adr/0004-control-plane-boundary.md).

**ToolPack** — everything specific to this integration. Four declarations,
split because they are owned differently:

| File | Holds | Why separate |
|---|---|---|
| `registry.py` | the callables and their database credential | the dispatcher owns them; a request can never add one |
| `tool_metadata.json` | risk tier, action type, domain | data, not code — hashed into REMORA's policy identity |
| `bundle.py` | tool contracts, the state index, intent resolution | what a tool *means*, for the grounding checks |
| `tool_specs.json` | argument schema, allowed targets, credential scope | signed; the authority to call each tool |

These four overlap and can drift. Consistency between them is tested, not
assumed — see [limitations.md](limitations.md#parallel-declarations).

**System of record** — a separate database holding the work orders the tools
act on. Separate so the effect reader can hold a credential that cannot write
it. See [adr/0002-separate-reader-credential.md](adr/0002-separate-reader-credential.md).

**Assurance console** — a read-only surface answering one question in three
parts: what has the agent been trying to do, what did the controls do about
it, and can any of that be relied on. FastAPI serving typed JSON and three
static files; no framework, no build step, and every asset from this origin
under a strict Content-Security-Policy.

The **Ledger** reads the signed audit chain directly, so it shows the
governance record rather than a summary of it, and can be filtered to the
entries where a control actually refused something. **Records** shows the
system of record. The **assurance strip** — engine, mode, tool policy, chain
integrity — sits in the masthead on every screen, because it qualifies
everything else: it was a separate page until it became clear an operator
could read activity all day without ever visiting it.

The console holds one bearer token (`viewer`) and two database credentials,
both `SELECT`-only: one on the system of record, one on the governance chain
([`db/controlplane/001_chain_reader.sql`](../db/controlplane/001_chain_reader.sql)).
It exposes no route that writes. A presentation surface able to write the
audit chain could rewrite the evidence it exists to display.

**Lab** — a demonstration and test surface, and the only container that can
act. Compose a governed tool call, choose which role submits it, and read the
whole decision envelope: the grounding signals, the anchors the decision was
computed against, the signed ToolSpec that authorised it, the resolution plan,
and the chain position. It also runs the [benchmark suites](benchmarks.md).

It holds every role token and has no login, which is exactly what a real
deployment must not do — and why it is a separate service, on its own image and
port, rather than a tab in the console. That separation is what keeps the
console's report about itself worth reading: a process that can approve its own
proposals cannot credibly say `console_access: read-only`.

Choosing a role in the lab selects which credential is presented; it grants
nothing. The control plane enforces role separation against the lab exactly as
against any other client, and `test_lab.py` proves it by having the operator
try to approve its own proposal and asserting the refusal comes back in the
engine's words.

**Postcondition reader** — runs in the CLI, not in the control plane. Declares the expected delta from the approved arguments, reads
the target, compares only the declared fields.

## Data flow

1. The agent proposes a `ToolCall` naming an `intent_ref`.
2. The control plane resolves that reference **server-side** against the
   deployment's own fixture, builds a semantic observation, and decides.
3. ACCEPT issues a single-use execution token. VERIFY enqueues a review item
   and names the role that may release it.
4. Execution restates the whole call; the server refuses if its hash differs
   from what was approved.
5. The reader confirms the effect and hands an attestation back to REMORA,
   which records it as a statement by a named verifier.

## Two fields, two questions

An `ExecutionResult` answers separately:

- `outcome` — did the governed step proceed (`execute`, `binding_refused`,
  `approval_invalidated`, …)
- `tool_execution.executed` — did the tool actually perform its side effect

They disagree exactly when it matters. A tool that raises produces
`outcome="execute"` with `executed=False` and the one-time grant burned. Only
`src/aae/execution.py` reads these, so nothing else can conflate them.

## Boundaries

| Boundary | Enforced by |
|---|---|
| No product file imports a REMORA internal namespace | a test scanning every source file |
| The reader cannot write the system of record | database grants |
| Approval is bound to the exact payload | a hash recomputed at execution |
| Approver roles cannot execute | the role's capability set |
| Containers hold no capabilities and no writable root | compose, asserted against `docker inspect` |

One quarantined exception: `bundle.py` imports nine types from
`remora.toolcall.*` because `remora.sdk` offers no ToolPack-authoring surface.
It is pinned symbol-by-symbol in
`tests/compatibility/test_toolpack_authoring_surface.py`, whose last test fails
when the SDK grows them — the signal to move the imports and delete the file.
