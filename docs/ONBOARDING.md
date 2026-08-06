# Onboarding

For a developer who has never seen the REMORA research repository and should
not need to. Thirty minutes, and at the end you will have checked the product's
claims yourself rather than read them.

You need Docker, Python 3.11+, and the GitHub CLI (`gh`, authenticated) to
fetch the pinned core release.

---

> The `aae` commands below live in the virtualenv that `python run.py up`
> created. Either activate it — `.venv\Scripts\activate` on Windows,
> `source .venv/bin/activate` elsewhere — or use `python -m aae.cli ...`,
> which needs no activation.

## 1 — Install (5 min)

```bash
git clone https://github.com/darklordVirtual/assured-agent-execution
cd assured-agent-execution
python run.py up
```

Watch for three lines. They are the install proving things about itself.

```
pin verified: REMORA core 0.10.0 @ aff4edf7 (7 artifacts)
pinned core verified in-image: remora-0.10.0-py3-none-any.whl @ 4e8dac2af3fd46f7...
system of record at schema version 2
```

The first is the pin checked before anything trusts it. The second is the
image refusing to build from a wheel it was not given — deliberately the same
check twice, because the first gates the download and the second makes the
image self-describing. The third is your schema, applied by a one-shot
migration under an admin role, before the control plane was allowed to start.

`python run.py up` also wrote `.env` with this installation's own signing keys,
database passwords and bearer tokens. Nothing is shared with any other
install, and nothing is committed.

## 2 — Ask it what it is (1 min)

```bash
python run.py doctor
```

```
Assured Agent Execution
  pinned core   REMORA 0.10.0 @ aff4edf7 (core-candidate-2026.08.06.3, prerelease)
  api           http://127.0.0.1:8088
  control plane ok, mode=production, surfaces=execution
  system of record  reachable, 4 work order(s), read-only credential
```

`surfaces=execution` means the oracle-backed research API is **unmounted**,
not merely unused. No model key, no network egress. Confirm it:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8088/v1/assess
# 404 — the route does not exist here
```

## 3 — Watch the four decisions (5 min)

```bash
python run.py scenarios
```

Read the VERIFY block line by line. It is the entire product:

```
[PASS] VERIFY: close WO-1202 under signed authority
       assess → verify (evidence_insufficient)
       agent token refused approval (correct)
       domain_expert approved → approved
       execute → execute
       EFFECT_VERIFIED — the system of record shows the approved delta
       record_effect → EFFECT_VERIFIED in the chain
```

A production write was held. The agent that proposed it was refused the right
to approve it. The identity the *decision named* — not a generic approver —
released it. The operator executed. Then a **separate process holding a
`SELECT`-only credential** went and looked at the database.

## 4 — Check it in the database yourself (3 min)

Do not take the trace's word for it.

```bash
docker compose exec workorder-db \
  psql -U wo_admin -d workorders -c \
  "SELECT wo_id, status, updated_by FROM work_orders ORDER BY wo_id"
```

```
 WO-1150 | closed | seed
 WO-1201 | open   | seed
 WO-1202 | closed | aae.work_order_toolpack/v1
 WO-1203 | open   | aae.work_order_toolpack/v1
```

`WO-1202` is closed, and the row says which tool closed it. `updated_by` is
the product's own attribution, independent of REMORA's audit chain — two
records that agree are worth more than one, and two that disagree is a finding
you can act on.

## 5 — Try to get past it (10 min)

This is the part worth your time.

**Approve one payload, execute another.**

```bash
aae propose set_work_order_priority wo_id=WO-1203 priority=high --intent WO-1203
# note the review_item_id

aae approve <review_item_id>
# {"status": "approved", "approved_as": "reviewer", ...}

# now execute a DIFFERENT work order under that approval
aae execute <review_item_id> set_work_order_priority \
    wo_id=WO-1201 priority=high --intent WO-1203
```

```json
{ "outcome": "binding_refused",
  "detail": "tool-call hash differs from the approved payload" }
```

Two things to take from this.

**`execute` makes you restate the whole call**, not just the arguments. The
approval is bound to a hash over the tool name, the exact arguments, the
tenant and the target environment. Restating it is what lets the server detect
that you are executing something other than what was approved.

**It did not raise.** It returned an outcome. Anything you integrate with this
SDK must read the `outcome` field — a caller that only catches exceptions
treats a refusal as success, and this product's own scenario made exactly that
mistake on its first run. The CLI exits 1 on a refusal so a script cannot
repeat it.

**Act under an authority that does not exist.**

```bash
aae propose read_work_order wo_id=WO-1201 --intent WO-9999 --env staging
# "decision": "abstain"
```

Identical tool, identical arguments, identical risk classification as the
ACCEPT case. The only difference is that `WO-9999` is not a work order this
deployment issued.

**Edit the signed ToolSpec bundle.**

```bash
python - <<'EOF'
import json, pathlib
p = pathlib.Path("toolpacks/work_order/tool_specs.signed.json")
d = json.loads(p.read_text())
next(s for s in d["tool_specs"] if s["tool_id"] == "read_work_order") \
    ["allowed_targets"].append("prod")
p.write_text(json.dumps(d, indent=2))
EOF

python run.py check-sign
# BUNDLE REFUSED: toolspec_signature_invalid: the bundle signature does not
# verify; its content changed after signing

python run.py sign        # restore
```

**Run the whole attack suite.**

```bash
python run.py verify
```

126 tests. `tests/e2e/test_toolspec.py` takes the bundle this deployment
actually runs and attacks it six ways — widening allowed targets, rewriting
the description an agent reads, escalating credential scope, reclassifying a
destructive tool as routine, presenting a revoked signer, and presenting a
correctly-signed *older* bundle. That last one is the subtle one: every
signature check passes, because it really was signed by this deployment. Only
the pinned digest refuses it.

## 6 — Look at it (2 min)

<http://localhost:8089>

The posture panel is the point. A console that only showed activity would let
a deployment look healthy while running unpinned, unenforced, or with a chain
that no longer verifies.

## 7 — Export evidence (2 min)

```bash
python run.py scenarios --evidence-out ./evidence
cat evidence/manifest.json
```

Every file is listed with a SHA-256 you can recompute. The chain is
re-verified at export time and the result is recorded **in** the export,
including when it fails — an export that silently omitted a broken chain would
be worse than no export, because it would look like evidence.

`"audit_chain_verified": null` means the chain could not be checked. It never
means clean.

---

## Where to change things

| You want to… | Edit |
|---|---|
| Govern different tools | `toolpacks/work_order/registry.py` (callables), then `bundle.py` (what they mean), then `tool_specs.json`, then `python run.py sign` |
| Change a tool's risk tier | `toolpacks/work_order/tool_metadata.json`. This is a **policy change**: it moves REMORA's policy bundle hash and invalidates every outstanding execution lease |
| Issue a work order | `toolpacks/work_order/work_orders.json`. The whole file is hashed into every decision, so editing one entry is visible on every proposal in flight |
| Point at a real system of record | `toolpacks/work_order/store.py` and `db/workorders/002_roles.sql` — keep the two roles; the reader must not be able to write |
| Verify an effect for a new tool | `src/aae/postcondition.py`. A tool without an entry reports `EFFECT_UNSUPPORTED`, recorded — never silently "verified" |

After any change to `registry.py` or `tool_specs.json`, run `python run.py sign`:
signing recomputes each `callable_digest` from the deployed source and refuses
if a declared tool has no callable, or a registered callable has no spec.

## What you should not conclude

The product runs and proves the claims above. It is not production-hardened:
no external security review has run against it, the pinned core is a
deliberate prerelease, tools still execute inside the control-plane process
(the credential separation is at the database, not at a process boundary), and
there is no signed image, SBOM or build provenance.

[README.md](../README.md#known-gaps) lists every gap we know about, including
two that are upstream and open.
