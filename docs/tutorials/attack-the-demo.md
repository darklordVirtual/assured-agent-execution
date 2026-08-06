# Try to get past it

Fifteen minutes. Everything below runs against the stack `python run.py up`
started, and every attempt is one that fails.

`aae` lives in the virtualenv `up` created; `python -m aae.cli` works without
activating it.

## 1 — Approve one thing, execute another

The realistic attack on a human-in-the-loop control is never to bypass the
human. It is to get a yes for one payload and execute a different one.

```bash
aae propose set_work_order_priority wo_id=WO-1203 priority=high --intent WO-1203
aae approve <review_item_id>

aae execute <review_item_id> set_work_order_priority \
    wo_id=WO-1201 priority=high --intent WO-1203
```

```json
{ "outcome": "binding_refused",
  "detail": "tool-call hash differs from the approved payload" }
```

Two things to notice. `execute` makes you restate the *whole* call — the
approval is bound to a hash over the tool name, the exact arguments, the tenant
and the environment. And it **did not raise**: the refusal is a field. A caller
that only catches exceptions treats it as success, which is why the CLI exits 1.

## 2 — Act under a work order nobody issued

```bash
aae propose read_work_order wo_id=WO-1201 --intent WO-9999 --env staging
```

`abstain`. Identical tool, arguments and risk tier as the call that gets
ACCEPT — the only difference is that `WO-9999` is not a work order this
deployment issued, and the agent does not get to assert otherwise.

## 3 — Edit the signed ToolSpec

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
# BUNDLE REFUSED: toolspec_signature_invalid

python run.py sign      # restore
```

## 4 — Approve as yourself

```bash
aae propose close_work_order wo_id=WO-1202 --intent WO-1202
# then approve with the agent's own token — the CLI will not let you, and
# neither will the server: the operator role has no review capability.
```

## 5 — Have the approver do the work

The approver approves and then executes. Both `reviewer` and `domain_expert`
hold `review` and `read` — and not `execute`. One credential that could do both
would make every other control decorative.

`tests/e2e/test_security.py::test_the_approver_cannot_execute_what_it_approved`
runs exactly this.

## 6 — Write through the verifier

```bash
docker compose exec workorder-db psql \
  "postgresql://aae_reader:$(grep AAE_READER_PASSWORD .env | cut -d= -f2)@localhost/workorders" \
  -c "UPDATE work_orders SET status='closed' WHERE wo_id='WO-1201'"
# ERROR: permission denied for table work_orders
```

The reader holds `SELECT` and nothing else. A verifier that could make the
answer true would be reporting on itself.

## 7 — The whole suite

```bash
python run.py verify
```

Among these, `test_toolspec.py` attacks the bundle this deployment is actually
running: six tamper shapes, a revoked signer, an untrusted signer, the wrong
key — and a correctly-signed *older* bundle. That last one passes every
signature check, because it really was signed here. Only the pinned digest
refuses it.

## What you should not conclude

Everything above is a control this repository enforces and tests. It is a
reference vertical, not a hardened product: no external security review, tools
still run inside the control-plane process, and ToolSpec signing is symmetric.
[../limitations.md](../limitations.md) is the full list.
