# Build on this

Governing your own tools, from a clone to a first governed call.

You need Docker, Python 3.12, and `gh` authenticated against GitHub — the
pinned core is fetched from a release, not from PyPI.

## 1. Run what is already here

```bash
python run.py up
python run.py scenarios      # 6/6
python run.py bench          # scores the decisions against a sealed key
```

Open the **Lab** URL that `up` prints. Load a preset, press Assess, and read
the envelope: the grounding signals, the anchors the decision was computed
against, the signed ToolSpec that authorised it, and where it landed in the
audit chain.

Do that before writing anything. Every mistake below is easier to recognise
once you have seen a correct decision in full.

## 2. What a deployment actually owns

The engine is REMORA, consumed as a hash-pinned release. You do not modify it.
What you write is four files, and they are separate because they are owned
differently:

| File | Holds | Why it is its own file |
|---|---|---|
| `registry.py` | the callables and their database credential | the dispatcher owns them, so a request can never add one |
| `tool_metadata.json` | risk tier, action type, reversibility | data, not code — its hash folds into the engine's policy identity |
| `bundle.py` | what a tool *means*: contracts, state index, intent resolution | the grounding checks read this |
| `tool_specs.json` | argument schema, allowed targets, credential scope | signed; this is the authority to call each tool |

The split is the point. A tool that could classify its own risk would classify
itself as low.

## 3. Add a tool

Work in `toolpacks/work_order/` and copy the shape of `read_work_order`.

**The callable.** A plain function in `registry.py`. It receives arguments and
nothing else — no proposal id, no execution context. That is a
[documented limitation](limitations.md), not something to work around.

**The classification.** An entry in `tool_metadata.json`:

```json
"close_work_order": {
  "risk_tier": "high",
  "action_type": "production_write",
  "domain": "maintenance",
  "rollback_available": true,
  "schema_valid": true
}
```

Be honest about `rollback_available`. It is the difference between VERIFY and
ESCALATE, and declaring a reversal that does not exist is how an approver
signs off on something believing it can be undone.

**The meaning.** A contract in `bundle.py` saying what the tool does to what.
Without it the engine cannot tell whether the call matches the authority, and
everything abstains.

**The authority.** A spec in `tool_specs.json`. Note what is and is not
enforced: `required`, `additionalProperties` and `type` are; `enum` and
`pattern` are recorded and ignored. That is
[an upstream gap](limitations.md), and until it closes, treat value
constraints as documentation.

Then re-sign — the spec file is signed and its digest is pinned:

```bash
python run.py sign
docker compose up -d --force-recreate control-plane
```

## 4. Give it an authority to act under

Nothing runs without one. `work_orders.json` is the deployment's intent
fixture: the server-side record of what someone actually asked for. The agent
cites an id; the engine resolves it itself and never trusts the agent's copy.

The whole file's SHA-256 becomes the intent authority hash, so any edit is
visible on every proposal in flight. That is tamper-evidence, not a signature —
see [security-model.md](security-model.md).

## 5. Prove it behaves

Write the scenario before you run it. In `benchmarks/suites/`:

```json
{
  "id": "closing-under-its-own-authority",
  "scenario": "A close of exactly the work order the authority names.",
  "probes": "risk_policy",
  "call": {
    "tool": "close_work_order",
    "arguments": {"wo_id": "WO-1202", "reason": "reviewed"},
    "intent_ref": "WO-1202",
    "target_environment": "prod"
  }
}
```

Then the answer, in `benchmarks/keys/`, **before running it**, argued from the
rules rather than from what the engine did:

```json
"closing-under-its-own-authority": {
  "decision": "verify",
  "because": "High risk with rollback available: consequential enough to need a human, reversible enough that one suffices.",
  "authored_blind": true
}
```

Re-seal and run:

```bash
python run.py bench --case closing-under-its-own-authority
```

If the engine disagrees, that is the benchmark working. Either your reasoning
is wrong or the engine is, and you now have the argument written down to decide
which. Doing it the other way round — running first, then recording what
happened — produces a test that defends whatever the engine does, including
when it is wrong. That is not a hypothetical: the `enum` gap above was found
exactly this way, by a key that was written first and turned out to be right
about what *should* happen.

## 6. Ship it

```bash
python run.py verify     # contracts, end-to-end, and the benchmark
```

CI runs the same thing, plus a check that the benchmark report matches the
audit chain.

## Where things go wrong

**Everything abstains.** The tool has no semantic contract in `bundle.py`, or
the authority does not resolve. Check the Lab's envelope — the grounding
signals name which one.

**Every call is refused with `toolspec_*`.** The bundle was not re-signed after
an edit, or its pinned digest is stale. `python run.py sign`, then recreate the
control plane.

**A medium risk tier abstains with no way forward.** Known: a fully grounded
medium-risk write matches no rule. Declare `high` and say why, as
`tool_metadata.json` does for `create_work_order`.

**An approved ESCALATE will not execute.** Known and upstream — the review
queue authorizes only a fresh ACCEPT or VERIFY, so an escalation is approved
and then voided. See [limitations.md](limitations.md).

## Two boundaries worth keeping

The console holds one viewer token and cannot act. The lab holds every role and
can. They are separate services because a process that can approve its own
proposals cannot credibly report `console_access: read-only` about itself.

And when something is genuinely broken in the engine, fix it in REMORA and move
the pin — not here. A local workaround makes this repository a fork of the core
with extra steps, which is the failure this pin exists to prevent.
