# Limitations

Known gaps, stated here rather than discovered later. Each says who owns it.

## Upstream, in the REMORA core

**A fully grounded medium-risk write abstains with no way forward.** It falls
through every rule to `default_safe_abstain`: no review item, no
`ResolutionPlan`, nothing an approver could release — for an action a signed
work order authorizes. High risk hits the evidence rule; ungrounded arguments
hit theirs; medium-and-grounded matches neither. Declaring a risk tier
currently leaves a tool *worse off* than leaving it unknown.

No tool in this deployment is declared `medium`, precisely because of this —
see `_medium_tier_note` in `toolpacks/work_order/tool_metadata.json`. That is a
classification choice made to route around a core gap, not evidence that the
gap is closed.

**Tools receive no execution context.** The dispatcher calls a tool as
`fn(arguments)`, so a callable is never told which proposal authorized it.
The product's own event log therefore cannot record `proposal_id` or
`execution_id`, and correlation with the audit chain is by work order, tool,
actor and time — useful, and not an unambiguous join. The columns exist and
stay NULL, because an unfilled column is a visible gap where a removed one
would be hidden.

**`callable_digest` is recorded but not enforced.** `verify_callable` exists
and nothing calls it at dispatch.

**No ToolPack-authoring surface in `remora.sdk`.** `bundle.py` imports nine
types from `remora.toolcall.*`. Pinned symbol-by-symbol so an upstream change
fails in CI rather than in a deployment.

**No way to list proposals over the API.** The core returns a proposal when its
identifier is known and offers no paginated listing, so a client that has only
HTTP can look one up and cannot browse them.

This used to be recorded here as a reason the console could show no decision
feed at all, which was wrong, and the console was the poorer for it for weeks:
it showed a health banner and four work orders while every assessment, refusal
and voided approval sat unread in the audit chain. The Ledger now reads that
chain directly, on a SELECT-only credential
([`db/controlplane/001_chain_reader.sql`](../db/controlplane/001_chain_reader.sql)).
That is the record itself, not a reconstruction — but it is a database
dependency, not an API, so any other client still has the original problem.

The contract that would remove it:

```
GET /v1/execution/proposals?limit=50&cursor=…&decision=verify&state=review_pending
```

Read access, tenant-isolated, cursor-paginated, projected from the audit chain
rather than a second source of truth, frozen in OpenAPI and exposed as
`list_proposals()` in the SDK.

**No versioned migrations for the core schema.** REMORA's tables are created
lazily at first use with `CREATE TABLE IF NOT EXISTS`, so they cannot be
pre-provisioned or reviewed before an upgrade. Worse, that statement is a
*no-op* against a volume that already has the tables: a later core release that
adds a column gets none, silently. Only the product's own schema is migrated
(`db/workorders/`).

Writing core DDL here would be exactly the local workaround this product
avoids, so the fix belongs upstream. What the product does own is the pin, so
it records which core release initialised the volume
([`core_schema.py`](../src/aae/core_schema.py), one table in AAE's own
namespace) and `python run.py doctor` reports a mismatch loudly instead of
serving on a schema nobody has checked. That is a detector, not a migration.

**An approved ESCALATE can never execute.** `review_queue.py` authorizes only
a fresh `ACCEPT` or `VERIFY`. Escalation decisions are deterministic, so the
re-assessment at execution returns `ESCALATE` again, falls outside that set and
re-queues the item — every time. The approval is *accepted* first (`"status":
"approved"`), then voided as `execution_approval_invalidated`. Verified by
walking it: `create_work_order` under authority WO-1310, approved as
`domain_expert`, in both `staging` and `prod`.

The routing is right and the acceptance is the defect: escalation asks a named
authority to decide, that authority decides, and the decision cannot be acted
on. It is visible and auditable rather than silent — the invalidation is a
chain entry — but the operator's only real path is to act out of band. Fixing
this belongs upstream in `remora/governance/`, not here; a local bypass would
be exactly the workaround this product refuses to build.

**A read authority does not bind to the record it names.** Holding the signed
authority for WO-1201 and reading WO-1202, WO-1203 or WO-1150 returns
`accept` / `grounded_read_accept` for every one, with the same
`intent_authority_hash`. The four grounding signals assert that a value is real
and that the verb matches — not that the value is the one the authority named.
So one signed read authority permits reading every record the tool can reach.

Low harm for maintenance reads, a confidentiality hole wherever records differ
in sensitivity. The data to bind them exists on both sides already: the ToolSpec
maps `wo_id` to `target_resource`, and the authority carries
`source_spans: ["work order WO-1201"]`. Nothing consumes it. Found by
`benchmarks/cases/autonomy.json`, which keeps failing until it is fixed.

**Value constraints in a signed argument schema are not enforced.**
Structural clauses are; value clauses are not. Measured against
`set_work_order_priority`, whose spec declares
`{"type": "string", "enum": ["low", "normal", "high"]}`:

| Clause | Enforced |
|---|---|
| `required` | yes |
| `additionalProperties` | yes |
| `type` | yes |
| `enum` | **no** |
| `pattern` | **no** |

So `priority: "catastrophic"` and `wo_id: "'; DROP TABLE work_orders; --"` are
both assessed rather than refused. They reach VERIFY, so nothing autonomous
happens with them and the callables use parameterised SQL — the exposure is
bounded. What is not bounded is the reading: a deployment author writing an
`enum` or a `pattern` into a spec, signing it, and seeing the other clauses
honoured has no way to learn that two of them are decorative.

Found by `benchmarks/suites/contract-enforcement.json`. The `enum` half was
found *blind* — the answer key predicted a refusal, the engine returned VERIFY,
and the disagreement is what widened the finding from `pattern` alone to the
structural/value split. Belongs upstream.

**ToolSpec signing is symmetric.** The verifier holds the signing key.

## In this repository

**Tools run inside the control-plane process.** Credential separation is at
the database — writer versus reader — not at a process boundary. A
credential-isolated worker needs a remote dispatch boundary that does not
exist upstream. This is the next architectural milestone.

**Parallel declarations.** Risk tier and action type appear in both
`tool_metadata.json` and `tool_specs.json`; semantic effects in both
`bundle.py` and `tool_specs.json`; known identifiers in both `bundle.py` and
the SQL seed. Signing checks that every registered tool has a spec and that
each `callable_digest` matches the deployed source. It does **not** yet check
that the risk tier, action type and semantic contract agree across files. The
intended fix is one canonical declaration that generates the rest.

**No OIDC.** Bearer tokens with real role separation, generated per install.

**Product dependencies are pinned by version, not by bytes.** The REMORA wheel
is byte-pinned by SHA-256, and base images are now digest-pinned. The Python
dependencies are exact versions in `docker/requirements.lock` rather than a
hash-locked set: `--require-hashes` would also fix the artifact bytes and needs
the full transitive closure resolved. So two builds of the same commit install
the same *versions*, which closes the drift gap but not a compromised-index
one. `python run.py sbom` records what a given build actually contained.

**The assurance console has no user identity.** It is read-only and holds only
the `viewer` token — it cannot propose, approve or execute, and exposes no
route that writes — but there is no login, no per-user audit trail and no
authorization beyond the deployment's own network boundary. It binds to
loopback. Anyone who can reach the port sees everything it shows.

An earlier version held all five tokens and exposed an unauthenticated POST
that ran the scenarios under approver roles. Running them is a CLI action now.

**No screenshot or visual regression testing.** The console's contracts are
asserted over HTTP and against the static files; nothing renders a browser, so
layout and contrast are reviewed by eye rather than by CI.

**No signed image, no build provenance, no external security review.** The
pinned core is a deliberate prerelease.

## Deliberately not fixed

**The audit chain is never reset.** `python run.py reseed` returns the
reference work orders to their seeded state so the demo is repeatable, and
leaves the chain alone: erasing the record of what was decided in order to make
a demonstration repeatable would defeat the thing being demonstrated.
`python run.py reset` removes the volumes when you actually want that.
