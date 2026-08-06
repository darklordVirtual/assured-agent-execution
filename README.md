# Assured Agent Execution

**An AI agent proposes an action. This decides whether it happens.**

Every proposal gets exactly one of four answers — **ACCEPT**, **VERIFY**,
**ABSTAIN**, **ESCALATE** — and the answer is bound to the exact payload, the
exact tool, and a work order the agent cannot forge. After a write, a separate
process on a read-only credential goes and checks the change actually landed.

Powered by [REMORA](https://github.com/darklordVirtual/REMORA-research).

```bash
git clone https://github.com/darklordVirtual/assured-agent-execution
cd assured-agent-execution
python run.py up          # ~3 min: verifies the pinned core, generates keys, builds, migrates
python run.py scenarios
```

No configuration. `run.py up` generates this installation's own signing keys,
database passwords and bearer tokens, picks free ports, and prints the URLs.
You need Docker, Python 3.11+, and `gh` (to fetch the pinned core release).

## What you'll see

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

6/6 scenarios behaved as documented
```

The VERIFY block is the whole product. A production write was held. The agent
that proposed it was refused the right to approve it. The identity the
*decision* named — a domain expert, not a generic approver — released it. The
operator executed. Then a different process, holding `SELECT` and nothing
else, opened the database and confirmed the change was really there.

Don't take its word for it:

```bash
docker compose exec workorder-db psql -U wo_admin -d workorders \
  -c "SELECT wo_id, status, updated_by FROM work_orders ORDER BY wo_id"
```

`WO-1202` is closed, and the row names the tool that closed it.

Then open the console the run printed — usually <http://localhost:8089>.

## Try to break it

```bash
# Approve one work order, execute a different one
aae propose set_work_order_priority wo_id=WO-1203 priority=high --intent WO-1203
aae approve <review_item_id>
aae execute <review_item_id> set_work_order_priority wo_id=WO-1201 priority=high --intent WO-1203
#  → binding_refused: tool-call hash differs from the approved payload

# Act under a work order nobody issued
aae propose read_work_order wo_id=WO-1201 --intent WO-9999 --env staging
#  → abstain   (identical tool, arguments and risk tier as the ACCEPT above)

# Edit the signed ToolSpec bundle, then ask it to verify
python run.py check-sign
#  → toolspec_signature_invalid: its content changed after signing
```

Then ask what happened to any proposal:

```bash
aae lifecycle <proposal-id>
```
```
proposal 194fd239-0d01-419d-9350-0574fab190c9
state    ASSESSED

  #235  assessed → accept (grounded_read_accept)
        tool      read_work_order
        target    staging
        actor     cred-a0c954378408
        toolspec  ac53494d…
        authority d51f3c4a…
        payload   a75ba66b…
        chain     4e48948b…
```

Which signed ToolSpec authorized it, which work order it acted under, the hash
the approval is welded to, and its position in the tamper-evident chain.
`--json` gives the unabridged record.

`aae` lives in the venv `run.py up` created; `python -m aae.cli` works without
activating it.

`python run.py verify` runs all 176 tests. Among them,
`tests/e2e/test_toolspec.py` attacks the bundle this deployment is actually
running — six tamper shapes, a revoked signer, and a correctly-signed *older*
bundle. Every signature check passes on that last one, because it really was
signed here. Only the pinned digest refuses it.

## What each boundary buys

| Boundary | What it prevents |
|---|---|
| Risk tiers live in a data file, hashed into REMORA's policy identity | A tool cannot classify itself, and relabelling one invalidates every execution lease issued before the relabel |
| Work-order authority is resolved **server-side** | The agent names which authority it acts under; it can never assert that the authority exists or says what it claims |
| The ToolSpec bundle is signed, and its digest pinned | Argument schemas, allowed targets and credential scopes cannot be edited by the thing they constrain — and a signature proves authenticity, never currency |
| Approval is bound to the exact tool-call hash | Getting a yes for one payload and executing another. The realistic attack is never "bypass the human" |
| Five roles, none of them `admin` | One credential that can both approve and execute makes every other control decorative |
| The effect reader holds `SELECT` and nothing else | A verifier that could write the state it verifies is reporting on itself |
| Containers drop **all** capabilities, run read-only, refuse privilege escalation, and publish on loopback only | A compromise that cannot persist across a restart, cannot escalate, and was never on the LAN to begin with |
| Backups carry both databases and the signed bundle — and **never** the signing keys | An archive holding the chain *and* the key that signs it lets its holder forge a history that verifies perfectly |
| `UNOBSERVABLE` and `VERIFIER_FAILED` are non-terminal; only `MISMATCH` is | "We could not look" is not "it was wrong" — one closes incidents that are still open, the other opens incidents for actions that succeeded |

[docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) explains the design, and names the
test that checks each claim.

## The dependency direction

The control plane **is** REMORA's governance API, installed from a
hash-verified wheel and run as a service. No REMORA checkout, no submodule.
Running a package is not importing one: no file here may import
`remora.policy`, `remora.enforcement`, `remora.governance` or `servers`, and a
test scans every source file to make sure none does.

Seven artifacts are pinned by SHA-256 — the wheel, the release manifest, the
OpenAPI document, the SDK surface, and three frozen contracts.
`scripts/verify_core_pin.py` refuses on any mismatch, because a pin nobody
verifies is a comment rather than a control.

## Maturity

Version `0.1.0-dev`. It installs, runs, and proves the claims above on one
command. It is **not** production-ready:

- A fully grounded *medium*-risk write falls through to ABSTAIN with no review
  item and no path an approver could take. Declaring a risk tier currently
  leaves a tool worse off than leaving it unknown. Open upstream.
- `callable_digest` is signed and true, but REMORA 0.10.0 records it without
  checking it at dispatch.
- ToolSpec signing is HMAC, so the process that verifies holds the key that
  signs. Asymmetric signing is deferred to v2 upstream.
- Tools run inside the control-plane process. The credential separation is at
  the database, not at a process boundary.
- No external security review, no OIDC, no signed image, and no build
  provenance. `python run.py sbom` inventories the Python tree in each
  image and says plainly what it leaves out. The pinned core is a
  deliberate prerelease.

AAE does not claim agents are always correct, that all tools can be
effect-verified, that safety is guaranteed, or that bypass is impossible when
an agent keeps direct tool credentials.

## License

Source-available under Business Source License 1.1; commercial production use
needs separate written terms. See [LICENSING.md](LICENSING.md).

The pinned REMORA core is a **separate** Licensed Work — an AAE license grants
nothing in REMORA, and vice versa. A deployment runs both.
