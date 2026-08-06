# Benchmarks

Scenarios in one file, answers in another, and the run never opens the answers.

```bash
python run.py bench                       # predict, then score
python run.py bench --suite autonomy      # one suite
aae bench --predict-only --out preds.json # blind: no key is opened at all
```

Also in the Lab's **Benchmarks** tab, and every scenario is a one-click preset
on **Try a call**.

## Why the split

The first version of this put the expected answer next to the call, and those
expectations were written after watching what the engine returned. That is
author bias whatever reasoning is attached: an expectation derived from an
observation defends the observation.

Now a **suite** (`benchmarks/suites/`) holds only scenarios and calls, and a
**key** (`benchmarks/keys/`) holds the labels. `predict()` never reads a key —
there is a test that makes `load_key` throw during a prediction pass, so the
claim is enforced rather than asserted.

Blind, precisely: **this is not secrecy.** Anyone with the repository can read
`benchmarks/keys/`. What the split buys is that tuning against the answers
becomes a deliberate act rather than an accident, and that a key can be written
by someone who has never run the engine.

It already paid for itself. Of the scenarios whose keys were written before
they were ever submitted, two matched and one did not: the key predicted that a
value outside a declared `enum` would be refused, the engine assessed it, and
probing every clause afterwards gave the real shape — **structural clauses are
enforced, value clauses are not**. That finding is in
[limitations.md](limitations.md) and would not exist if the answer had been
written after the run.

## Sealing

A key records the SHA-256 of the suite it answers, over the case ids and the
calls only. Editing a call breaks the seal; rewording a description does not,
because improving a comment is not changing a question.

A suite whose seal is broken is **not scored at all**. Grading changed
questions against old answers produces a number with no meaning, and reporting
it as a low score would be worse than reporting nothing — a low score invites
tuning.

## Three outcomes, not two

| | Meaning | Fails the run |
|---|---|---|
| **matched** | the deployment did what the key argues for | no |
| **known gap** | a documented limitation, behaving as documented | no |
| **regression** | a disagreement nothing accounts for | **yes** |

The middle row is what makes the instrument usable. Scored as a pass, a known
limitation vanishes from the report and stops being worked on; scored as a
failure, the run is permanently red and people learn to ignore it.

A `known_gap` must cite where the limitation is documented, or the field
becomes a way to silence a case. A gap that starts *passing* is reported
separately and loudly: the limitation is fixed, and both the key and
`limitations.md` have become wrong.

## Never one number

Every result is broken down by the layer the scenario probes —
`tool_contract`, `intent_authority`, `grounding`, `risk_policy`,
`role_separation`, `payload_binding`. A single accuracy figure says something
is wrong; the layer says what to fix. The taxonomy is adapted from
[arXiv:2607.28802](https://arxiv.org/abs/2607.28802), narrowed to the layers
this product actually has — AAE has no model and no planner in its decision
path, so naming those would be borrowed vocabulary.

Reporting only total accuracy is the failure
[arXiv:2607.05775](https://arxiv.org/abs/2607.05775) catalogues across 27
studies.

`verifier` was a seventh layer here and nothing probed it, because effect
verification needs the postcondition reader — which lives in the CLI and holds
a database credential the harness does not. A layer no scenario can reach is a
declaration wearing the clothes of a control, so it is named in
`UNREACHABLE_LAYERS` with the reason rather than left as a permanent zero.
There is a test that fails if any *declared* layer goes unprobed.

## The run manifest

Every report carries what it needs to still mean something next month: the
engine's release, commit and wheel digest, the digest of each suite, the grader
version, the contamination assessment and the blindness claim. Two runs that
disagree are otherwise impossible to diagnose — you cannot tell whether the
engine changed or the ruler did. The checklist is from OpenAI's *Separating
signal from noise in coding evaluations* (2026).

## Why not AgentHarm, AgentDojo or ShieldAgent-Bench

They were considered and rejected, and the reason is not effort.

Those benchmarks evaluate an **LLM agent's choice**: a natural-language prompt
goes in, the agent picks tools, and a grader judges whether it should have
refused. AAE has no model anywhere in its decision path. It receives a
structured `ToolCall` with an `intent_ref` and applies deterministic rules.
Piping those datasets in would produce a number that reads like a safety score
and measures nothing about this system.

There is also a hygiene reason not to vendor them. AgentHarm ships a
`canary_guid` precisely so its contents do not end up copied into public
repositories, and its licence is not one that invites redistribution.

What *does* transfer is the **scenario class** — malformed and over-specified
arguments, calls aimed outside a declared scope, actions distinguished by
reversibility. Each suite's `provenance` block names the published work its
scenarios instantiate and states that the calls are this deployment's own,
because a contract check is only meaningful against the contract actually
deployed. "We adapted a public benchmark" is unverifiable unless the adaptation
is written down.

The honest summary: **no public benchmark currently targets policy-engine
decisions on structured tool calls.** If one appears, the adapter work is
mapping its calls onto a ToolPack and writing a key — the harness here already
takes both.

## Choosing what to run

```bash
python run.py bench --list                       # what exists
python run.py bench --suite autonomy             # repeatable
python run.py bench --layer tool_contract        # by layer
python run.py bench --case unknown-tool          # one scenario
```

Filters intersect, and the selection travels with the report while the
manifest keeps describing the whole corpus. A filtered `5/5` is not coverage,
so the scorecard says `ran autonomy — 6 of 22 available` rather than leaving
the reader to assume.

## Scenarios that write

Most suites only assess. `authority-separation` approves and executes, because
role separation and payload binding cannot be observed without performing the
act — an engine that decides correctly and then lets anyone act on the decision
has governed nothing.

That is a real change to the system of record, so it is declared: the suite's
`provenance.writes` says so, the lab marks the suite **writes** before you
select it, and a banner appears above the run button. A benchmark that quietly
mutated production data would be the last one anybody ran.

## The audit trail

Every governed act a run performs is recorded by the control plane in the
tenant audit chain, independently of this harness. The report carries those
positions:

```json
{"sequence_no": 471, "entry_hash": "11bb3ecd…", "event": "approve",
 "case": "authority-separation/the-approver-cannot-execute-what-it-approved"}
```

and the trail can be checked:

```bash
python run.py bench --json > report.json
aae bench --verify-trail report.json
```

which re-reads each position from the chain, confirms the entry hash still
matches, and confirms the chain as a whole verifies. Exit 1 on any mismatch.

This is what separates an auditable result from a published one. Without it, a
score is the harness's word for what it did — and a harness that fabricated the
entire run would produce a byte-identical file. With it, a reviewer who trusts
nothing in this repository can still check the claim, because the entries were
written by the control plane before the report existed. Forging one entry hash
is detected; there is a test that does exactly that.

The trail records acts that returned a chain position. An act the engine
refused outright raises before one is issued, so it appears in the scenario's
step results and not in the trail — the score and the trail answer different
questions, and neither is the other's summary.

## Adding a scenario

1. Add it to a file in `benchmarks/suites/`, with a `scenario`, a `call`, and
   the `probes` layer.
2. Write the answer in `benchmarks/keys/<suite>.json` **before running it**,
   arguing from the rules. Mark it `authored_blind: true`.
3. Re-seal: the key's `seals_suite_sha256` must match `suite_digest()`.
4. Run. If the engine disagrees, that is the benchmark working — decide
   whether the engine or the key is wrong, and write down which.

`test_benchmark.py` checks that suites leak no answers, that every scenario has
a key entry arguing for itself in more than a line, that every scenario names a
valid layer, that every known gap cites documentation, and that every suite
declares its provenance.
