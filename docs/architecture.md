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
not reimplement decision semantics. See
[adr/0001-pinned-remora-artifacts.md](adr/0001-pinned-remora-artifacts.md) and
[adr/0004-control-plane-boundary.md](adr/0004-control-plane-boundary.md).

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

**Postcondition reader** — runs in the CLI or the dashboard, not in the
control plane. Declares the expected delta from the approved arguments, reads
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
