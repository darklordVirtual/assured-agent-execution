# Limitations

Known gaps, stated here rather than discovered later. Each says who owns it.

## Upstream, in the REMORA core

**A fully grounded medium-risk write abstains with no way forward.** It falls
through every rule to `default_safe_abstain`: no review item, no
`ResolutionPlan`, nothing an approver could release — for an action a signed
work order authorizes. High risk hits the evidence rule; ungrounded arguments
hit theirs; medium-and-grounded matches neither. Declaring a risk tier
currently leaves a tool *worse off* than leaving it unknown.

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

**No way to list proposals.** The core returns a proposal when its identifier
is known and offers no paginated listing, so the console's Decisions surface is
a lookup rather than a feed. It cannot be reconstructed locally either: the
business event log carries no proposal id, because the dispatcher passes none
to the tool. Assembling a "recent decisions" list from what is available would
mean showing a reconstruction as though it were the record.

The contract this needs:

```
GET /v1/execution/proposals?limit=50&cursor=…&decision=verify&state=review_pending
```

Read access, tenant-isolated, cursor-paginated, projected from the audit chain
rather than a second source of truth, frozen in OpenAPI and exposed as
`list_proposals()` in the SDK. Once that is pinned in a core release, Decisions
can show recent activity, pending attention and filtering.

**No versioned migrations for the core schema.** REMORA's tables are created
lazily at first use, so they cannot be pre-provisioned or reviewed before an
upgrade. Only the *product's* schema is migrated here.

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

**Product dependencies are not pinned to the standard the core is.** The
REMORA wheel is byte-pinned; base images are tags, not digests, and the Python
dependencies are floors rather than a lockfile. `python run.py sbom` records
what a given build actually contained, which is a record, not reproducibility.

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
