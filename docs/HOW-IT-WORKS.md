# How it works

Each section names the test that checks its claim. If you only want to know
whether something is true, run that test.

```
                  ┌────────────────────────────────────────┐
  agent  ──────►  │  control plane — REMORA's governance    │
                  │  API, from the pinned wheel             │
                  │                                        │
                  │  toolpacks/work_order/                 │
                  │    registry.py    the callables        │
                  │    bundle.py      what they mean       │
                  │    tool_metadata  how risky they are   │
                  │    tool_specs     signed authority     │
                  │    work_orders    signed intent        │
                  └──────┬───────────────────┬─────────────┘
                writer   │                   │
          ┌──────────────▼───┐   ┌───────────▼────────────┐
          │ control-plane-db │   │ workorder-db           │
          │ audit chain,     │   │ THE SYSTEM OF RECORD   │
          │ grants, outbox   │   └───────────┬────────────┘
          └──────────────────┘   reader,     │ SELECT only
                                 ┌───────────▼────────────┐
                                 │ postcondition reader   │
                                 │ a DIFFERENT process    │
                                 └────────────────────────┘
```

## Four declarations, deliberately separate

**`registry.py`** holds the callables and the database credential they close
over. The dispatcher owns them; a request payload can never add or replace one.

**`tool_metadata.json`** holds the risk tiers. Data, not code — code that
could classify itself would be a tool granting itself a risk tier. Its content
is hashed into REMORA's policy identity, so changing a tier is a policy change
that invalidates outstanding execution leases, not a config tweak.

**`bundle.py`** declares what each tool *means*: what it acts on, whether it
mutates, what post-state it claims, and which identifiers exist in the system
of record. It also resolves `intent_ref` **server-side**, against a file this
deployment controls. That asymmetry is what an ACCEPT rests on: the agent may
name a work order and can never assert that it exists.

**`tool_specs.signed.json`** is the authority to call each tool — argument
schema, allowed targets, credential scope. `python run.py sign` computes each
`callable_digest` from the source of the function actually registered, and
refuses to sign if a declared tool has no callable or a registered callable
has no spec.

*Checked by* `tests/e2e/test_toolspec.py` — 16 tests including six tamper
shapes and a correctly-signed older bundle.

## Why ACCEPT is a conjunction

REMORA's probabilistic ACCEPT cannot fire on the execution surface: that
kernel has no oracle and no trust score. `GROUNDED_READ_ACCEPT` is the
deterministic alternative, added upstream for this product. All of these must
hold, and `None` never satisfies any of them:

- positively declared read-only semantics, no recorded safety concern
- `risk_tier` is `low`, and the environment is not production under any alias
- an intent authority resolved server-side
- `tool_matches_goal`, `expected_effect_matches`, `argument_values_supported`
  and `argument_values_grounded` all `True`
- nothing required is missing or unvalidated

It asserts the call is *the declared call, for a declared authority, over
declared data*. It does not assert the read returns a correct answer — which
is why writes never reach it.

*Checked by* `test_decisions.py::test_removing_any_single_ground_removes_the_accept`,
and upstream by a grid asserting the flag can only move ABSTAIN → ACCEPT.

## Why three approver identities

REMORA decides, per decision, which role may release it: a priority change
takes a `reviewer`, a production close a `domain_expert`, a purge
`senior_authority`. The product reads `required_role` off the decision and
presents the matching identity, refusing loudly when it holds none.

None of them is `admin`. `admin` holds every capability including `execute`,
so an admin approver can approve its own proposal and then execute it. This
product's compose file and REMORA's own pilot both claimed "the approver
cannot execute" while configuring exactly that role — the end-to-end test
caught it by trying.

*Checked by* `test_security.py::test_the_approver_cannot_execute_what_it_approved`

## Why two databases

Governance state and business data are separated so the effect reader can read
the second on a credential that cannot write it. With one database that split
would be a convention; with two, and grants that give `aae_reader` nothing but
`SELECT`, the database enforces it. The reader also runs in a *different
process* from the one that acted.

The schema is applied by a one-shot migration under an admin role before
anything else connects, and the worker and reader roles it creates hold no DDL
rights. A running product should not be able to change its own schema.

*Checked by* `test_security.py::test_the_reader_credential_cannot_write` and
its two siblings.

## A refusal is an outcome, not an exception

`client.execute(...)` returns an `ExecutionResult` whose `outcome` is
`binding_refused` when the payload does not match. It does **not** raise.

A caller that only wraps execution in `try/except` therefore reads a refusal
as success. This product's own scenario did exactly that on its first run and
reported an execution that had never happened. Anything integrating with the
SDK must read `outcome`; the CLI exits 1 on a refusal so a script cannot
repeat the mistake.

## Effect statuses, and which one is a finding

After a governed write the reader declares the postcondition **from the
approved arguments** — declaring it from the result afterwards would make
verification a tautology — reads the target on the read-only credential, and
compares **only the declared delta**. Only the declared delta, because a
system of record has other legitimate writers; flagging unexpected fields
would turn every concurrent update into a mismatch and the signal into noise.

| Status | `is_terminal` | Means | A finding? |
|---|---|---|---|
| `EFFECT_VERIFIED` | yes | the record shows the approved delta | no |
| `EFFECT_MISMATCH` | yes | we looked, and it does not | **yes** |
| `EFFECT_UNOBSERVABLE` | no | we could not see the object | no |
| `EFFECT_VERIFIER_FAILED` | no | the reader itself failed | no |
| `EFFECT_UNSUPPORTED` | yes | no reader is declared for this tool | no |

Two different questions, and conflating them is a trap this product fell into
itself. `is_terminal` asks *is this a settled answer?* — VERIFIED and
UNSUPPORTED are settled and perfectly fine. Only `MISMATCH` is a finding.

The CLI's `verify-effect` originally exited non-zero on `is_terminal`, so a CI
job wired to it would have failed on every effect it successfully confirmed.
`test_cli.py::test_verify_effect_exits_nonzero_only_on_a_mismatch` pins it.

The two non-terminal statuses matter for the opposite reason: a reader outage
is not evidence an action failed, and if compensation were ever automated on
that signal, treating it as one would undo actions that succeeded.

REMORA stores the result as an *attestation by a named verifier*, not a proof
of its own: verification runs here because the reader holds the credentials,
and only hashes cross the boundary.

*Checked by* `tests/e2e/test_faults.py` — every fault asserts which status it
must produce.

## Operating it

```bash
python run.py sign        # re-sign after changing registry.py or tool_specs.json
python run.py check-sign  # verify without re-signing
python run.py doctor      # what is pinned, served, reachable
python run.py verify      # all 160 tests
aae lifecycle <id>        # the event trail, --json for everything
aae evidence export --out ./evidence <proposal-id> ...
```

**The pinned digest is the part people skip.** A signature proves a bundle is
authentic; it says nothing about whether it is current. A correctly-signed
older bundle — one where a tool was cheaper, or a target wider — passes every
signature check. `run.py sign` writes the pin, and a test fails if you are
running signed-but-unpinned.

**Rotating a signing key** does not invalidate what it already signed; it
makes those signatures unverifiable, which is worse unless you meant it.
Export the evidence you need first. Database volumes also keep the old
passwords, so a rotation needs `python run.py down`.

**Evidence archives** carry a manifest hashing every file, and the chain
verification taken at export time — including when it fails. Two exports of
one proposal differ in exactly one field (`manifest.exported_at`), so an outer
file digest identifies *that export*, not the evidence. `"audit_chain_verified":
null` means the chain could not be checked; it never means clean.

## Deployment hardening

Every container drops **all** capabilities, refuses privilege escalation, and
carries a memory and PID ceiling. The two application containers run with a
read-only root filesystem and a 64 MB tmpfs; the databases write only their
data volumes. Published ports bind `127.0.0.1`, so nothing is on the network.
`/metrics` requires a bearer token — `/v1/metrics` is the authenticated view,
and `AAE_PUBLIC_METRICS=1` opts back in for a local scrape.

None of this was true until a survey of the running containers found writable
root filesystems, the full default capability set, no resource ceiling, ports
on every interface, and an unauthenticated Prometheus endpoint — all inherited
from an upstream pilot whose own comments said "local pilot only".

*Checked by* `tests/e2e/test_hardening.py`, which asserts against
`docker inspect` and live HTTP rather than against the compose file: what is
running is the only thing that protects anyone.

## Upgrading the pinned core

Edit `product/core-artifact-lock.json`, copy in the release's manifest, then
`python scripts/verify_core_pin.py --out dist` and `python run.py compat`.
`test_pin_manifest_agreement.py` compares the hand-written lock against the
release-generated manifest field by field — that test exists because the
in-tree manifest was stale for two pin bumps and nothing noticed, because
nothing compared them.
