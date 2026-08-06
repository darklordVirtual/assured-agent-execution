# Architecture

What runs where, and why each boundary is where it is. Every claim below is
checked by a test named at the end of its section — if you only want to know
whether something is true, run that test.

## The shape

```
                    ┌──────────────────────────────────────────┐
   agent /  ─────►  │  control plane                           │
   CLI              │  REMORA governance API, from the pinned  │
                    │  wheel. Execution surface only.          │
                    │                                          │
                    │  ┌────────────────────────────────────┐  │
                    │  │ ToolPack (this product's code)     │  │
                    │  │  registry.py   the callables       │  │
                    │  │  bundle.py     what they mean      │  │
                    │  │  tool_specs    signed authority    │  │
                    │  │  work_orders   signed intent       │  │
                    │  └────────────────────────────────────┘  │
                    └────────┬────────────────────┬────────────┘
                             │ writer             │
              ┌──────────────▼──────┐   ┌─────────▼──────────────┐
              │ control-plane-db    │   │ workorder-db           │
              │ audit chain, review │   │ THE SYSTEM OF RECORD   │
              │ queue, grant ledger,│   │ work_orders,           │
              │ outbox, envelopes   │   │ work_order_events      │
              └─────────────────────┘   └─────────┬──────────────┘
                                                  │ reader (SELECT only)
                                        ┌─────────▼──────────────┐
                                        │ postcondition reader   │
                                        │ runs in the CLI /      │
                                        │ console — NOT in the   │
                                        │ process that acted     │
                                        └────────────────────────┘
```

## Why the control plane is REMORA, not a wrapper

The wheel force-includes `servers/` and `schemas/` (REM-045). AAE's Dockerfile
installs that wheel and runs `uvicorn servers.api:app`. There is no REMORA
checkout in the build, no submodule, no `pip install git+`.

Running a package as a service is not importing it. The distinction matters
because the whole product boundary rests on it: **no AAE source file may
import `remora.policy`, `remora.enforcement`, `remora.governance` or
`servers`**, and that is enforced by a test that scans every product file,
not by discipline.

Building a thin wrapper instead would have meant reimplementing decision
semantics in the product — which is exactly how a product becomes an unstable
fork of a research repository.

*Checked by:* `tests/compatibility/test_sdk_contract.py::test_no_product_source_imports_an_internal_remora_namespace`

## Why two databases

Governance state and business data are separated because the postcondition
reader must be able to read the second on a credential that cannot write it.
With one database, that split would be a convention. With two, and with
`db/workorders/002_roles.sql` granting `aae_reader` nothing but `SELECT`, it
is a grant the database enforces.

The reader also runs in a **different process** from the one that acted. The
control plane holds the writer credential; the CLI and console hold the
reader. A verifier that could manufacture the state it is checking for is
reporting on itself, and no amount of care in the reader's own code fixes
that.

*Checked by:* `tests/e2e/test_security.py::test_the_reader_credential_cannot_write`,
`::test_the_reader_credential_cannot_delete_the_event_log`,
`::test_the_reader_credential_cannot_change_the_schema`

## Why the schema is applied by a one-shot migration

`workorder-migrate` runs under the admin role, before anything else connects,
and the worker and reader roles it creates hold no DDL rights. A running
product should not be able to change its own schema.

The control plane waits on `service_completed_successfully`, so a failed
migration means nothing starts — rather than a control plane running against a
half-applied schema.

REMORA's own tables (audit chain, review queue, grant ledger, outbox,
envelopes) are created lazily by core on first use. That is a core-owned
schema and a known gap: there is no versioned migration set upstream, so those
tables cannot be pre-provisioned or reviewed before an upgrade.

*Checked by:* `tests/e2e/test_decisions.py::test_the_system_of_record_was_migrated_not_conjured`

## The four declarations a deployment owns

Everything specific to *this* product lives in `toolpacks/work_order/`, and
the split between the four files is deliberate.

**`registry.py` — the callables.** Loaded once per process through
`REMORA_TOOL_REGISTRY_MODULE`. The dispatcher holds these functions and the
database credential they close over; a request payload can never add or
replace one.

**`tool_metadata.json` — the risk classification.** Data, not code. Code that
could classify itself would be a tool granting itself a risk tier. Its
resolved content is hashed into REMORA's policy bundle identity, so changing a
tier is a policy change that invalidates every outstanding execution lease —
not a config tweak.

**`bundle.py` — what the tools mean.** Each tool's signature and contract
(what it acts on, whether it mutates, what post-state it claims), plus the set
of identifiers that exist in the system of record, plus `resolve_intent`.

`resolve_intent` is the asymmetry the ACCEPT path rests on: the agent may name
which work order it acts under, and can never assert that the work order
exists or says what it claims. Resolution happens server-side against a file
this deployment controls.

**`tool_specs.signed.json` — the authority.** Argument schema, allowed
targets, credential scope, whether the effect can be read back. Signed by the
deployment via `python run.py sign`, which computes each `callable_digest` from the
source of the function actually registered and refuses to sign if a declared
tool has no callable or a registered callable has no spec.

*Checked by:* `tests/e2e/test_toolspec.py` (16 tests, including six tamper
shapes and a correctly-signed older bundle)

## How ACCEPT is reached, and why it is a conjunction

The policy-only execution kernel has no oracle and no trust score, so REMORA's
probabilistic ACCEPT cannot fire there — every call fell to VERIFY or ABSTAIN
regardless of how well-founded it was. `GROUNDED_READ_ACCEPT` is the
deterministic alternative, added upstream for this product.

All of these must hold, and `None` never satisfies any of them:

- positively declared read-only semantics, and no recorded safety concern
- `risk_tier` is `low`
- the target environment is not production, under any alias
- an intent authority **resolved server-side**
- `tool_matches_goal`, `expected_effect_matches`, `argument_values_supported`
  and `argument_values_grounded` are all `True`
- nothing required is missing or unvalidated

It asserts that the call is *the declared call, for a declared authority, over
declared data*. It does not assert the read returns a correct answer. That
distinction is why it is restricted to reads and why writes never reach it.

*Checked by:* `tests/e2e/test_decisions.py::test_removing_any_single_ground_removes_the_accept`
(parametrized over four ways of removing exactly one ground), and upstream by
`tests/test_grounded_read_accept.py` (46 tests, including a grid asserting the
flag can only ever move ABSTAIN → ACCEPT)

## Why there are three approver identities

REMORA's escalation contract decides, per decision, which role may release it:
a priority change takes a `reviewer`, a production close takes a
`domain_expert`, a purge takes `senior_authority`. The product reads
`required_role` off the decision and presents the matching identity, and
refuses loudly when it holds none.

None of them is `admin`. `admin` holds every capability including `execute`,
so an admin approver can approve its own proposal and then execute it. Both
this product's compose file and REMORA's own OT pilot claimed "the approver
token cannot execute" while configuring exactly that role; the end-to-end test
caught it by trying.

*Checked by:* `tests/e2e/test_security.py::test_the_approver_cannot_execute_what_it_approved`

## Why a refusal is an outcome, not an exception

`client.execute(...)` returns an `ExecutionResult` whose `outcome` is
`binding_refused` when the payload does not match the approved one. It does
not raise.

A caller that only wraps execution in `try/except` therefore treats a refusal
as success. This product's own scenario did exactly that on its first run and
reported "a payload the approver never saw was executed" — it had not been;
the system of record was untouched and the field said so.

Anything integrating with the SDK must read `outcome`.

*Checked by:* `tests/e2e/test_security.py::_assert_tampering_refused`

## Effect verification, and the statuses that are not findings

After a governed write, the reader declares the postcondition **from the
approved arguments** (declaring it afterwards from the result would make
verification a tautology), reads the target on the read-only credential, and
compares **only the declared delta**.

Only the declared delta, because a system of record has other legitimate
writers. If verification flagged every field it did not expect, every
concurrent update would surface as a mismatch and the signal would be noise
within a week.

| Status | Terminal | Means |
|---|---|---|
| `EFFECT_VERIFIED` | — | the record shows the approved delta |
| `EFFECT_MISMATCH` | **yes** | we looked, and it does not |
| `EFFECT_UNOBSERVABLE` | no | we could not see the object |
| `EFFECT_VERIFIER_FAILED` | no | the reader itself failed |
| `EFFECT_UNSUPPORTED` | no | no reader is declared for this tool |

The three non-terminal statuses are the ones that matter. A reader outage is
not evidence that an action failed — and if compensation were ever automated
on that signal, treating it as one would undo actions that succeeded.

REMORA stores the result as an **attestation by a named verifier**, not as a
proof of its own: verification runs in this product's process because the
reader holds the credentials, and only hashes cross the boundary.

*Checked by:* `tests/e2e/test_faults.py` (every fault, each asserting which
status it must produce)

## The console

A separate image that imports no REMORA module and holds no policy. It shells
out to the product CLI for the scenarios rather than keeping its own copy — a
console that could show green while the CLI showed red would leave nobody able
to say which one described the product.

Its posture panel exists because a console that only showed *activity* would
let a deployment look healthy while running unpinned, unenforced, or with a
chain that no longer verifies.
