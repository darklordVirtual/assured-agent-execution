# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""Score a deployment's decisions against a sealed answer key.

Two files per benchmark, and the split is the whole point.

A **suite** (``benchmarks/suites/``) holds the scenarios and the calls. It
carries no expected outcome, so a run can be executed, and the deployment
tuned, without anyone seeing what the right answers are.

A **key** (``benchmarks/keys/``) holds the labels and the reasoning for them.
It is opened only at scoring time, and it records the SHA-256 of the suite it
answers — so a suite edited after the key was written scores as *broken seal*
rather than quietly grading against the wrong questions.

The first version of this module put the expected answer in the case file, and
I wrote those expectations after watching what the engine returned. That is
author bias whatever the stated reasoning: an expectation derived from an
observation defends the observation. Splitting the files does not remove the
bias by itself — a key written by the same person still can — but it makes the
order of operations checkable, and it lets a key be written by someone who has
never run the engine.

Blind, precisely: the prediction pass never reads a key. It is not secrecy.
Anyone with the repository can read ``benchmarks/keys/``. What it buys is that
tuning against the key becomes a deliberate act rather than an accident.

Every suite declares where its scenarios come from in ``provenance``. Where a
scenario class is taken from published work, the citation is in the file and
the port is described, because "we adapted a public benchmark" is unverifiable
unless the adaptation is written down.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import time
from typing import Any

_HERE = pathlib.Path(__file__).resolve()


def _root() -> pathlib.Path:
    """The benchmarks directory, in either layout.

    In the repository this module is ``src/aae/benchmark.py`` and the
    benchmarks sit two levels up; in the lab image the package is at
    ``/app/aae`` and they sit one level up. Deriving one and assuming it holds
    gave "no benchmark cases at /benchmarks" in the container while working
    locally, so both are tried and the environment wins over either.
    """
    override = os.environ.get("AAE_BENCHMARK_DIR", "").strip()
    if override:
        return pathlib.Path(override)
    for parent in _HERE.parents[1:4]:
        if (parent / "benchmarks" / "suites").is_dir():
            return parent / "benchmarks"
    return _HERE.parents[2] / "benchmarks"


BENCHMARK_DIR = _root()
SUITES_DIR = BENCHMARK_DIR / "suites"
KEYS_DIR = BENCHMARK_DIR / "keys"


# ── Sealing ────────────────────────────────────────────────────────────────

def suite_digest(suite: dict[str, Any]) -> str:
    """SHA-256 over the scenarios a key answers.

    Only the parts a key is an answer to: the case ids and the calls. Prose —
    titles, descriptions, provenance notes — can be improved without breaking
    a seal, because editing a comment is not editing a question.
    """
    sealed = [
        {"id": c["id"], "call": c["call"]}
        for c in sorted(suite["cases"], key=lambda c: c["id"])
    ]
    payload = json.dumps(sealed, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── Loading ────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Selection:
    """Which scenarios to run.

    Three independent filters, intersected. Every one is recorded in the run
    so a partial result can never be mistaken for a full one: a score of 5/5
    means nothing unless you can see it was 5 out of a possible 22.
    """

    suites: tuple[str, ...] = ()
    layers: tuple[str, ...] = ()
    cases: tuple[str, ...] = ()

    @property
    def is_everything(self) -> bool:
        return not (self.suites or self.layers or self.cases)

    def wants_suite(self, name: str) -> bool:
        return not self.suites or name in self.suites

    def wants_case(self, suite: str, case: dict[str, Any]) -> bool:
        if self.layers and case.get("probes") not in self.layers:
            return False
        if self.cases and not (case["id"] in self.cases
                               or f"{suite}/{case['id']}" in self.cases):
            return False
        return True

    def as_dict(self) -> dict[str, Any]:
        return {"suites": list(self.suites), "layers": list(self.layers),
                "cases": list(self.cases), "everything": self.is_everything}


def _as_selection(only: Selection | str | None) -> Selection:
    if only is None:
        return Selection()
    if isinstance(only, Selection):
        return only
    return Selection(suites=(only,))


def load_suites(only: Selection | str | None = None) -> list[dict[str, Any]]:
    """The scenarios, filtered. Never touches a key."""
    if not SUITES_DIR.is_dir():
        raise FileNotFoundError(f"no benchmark suites at {SUITES_DIR}")
    selection = _as_selection(only)

    everything = []
    for path in sorted(SUITES_DIR.glob("*.json")):
        suite = json.loads(path.read_text(encoding="utf-8"))
        suite.setdefault("suite", path.stem)
        everything.append(suite)

    known = {s["suite"] for s in everything}
    unknown = [name for name in selection.suites if name not in known]
    if unknown:
        raise KeyError(f"no suite named {unknown}. Available: {sorted(known)}")

    suites = []
    for suite in everything:
        if not selection.wants_suite(suite["suite"]):
            continue
        cases = [c for c in suite["cases"]
                 if selection.wants_case(suite["suite"], c)]
        if not cases:
            continue
        # The seal is over the WHOLE suite, always. Computing it from a
        # filtered case list produced a different digest, which read as a
        # broken seal and dropped the suite from scoring entirely — so
        # `--layer role_separation` reported 0/0 while running fine. A filter
        # selects which questions to ask; it does not change which questions
        # the key answers.
        suites.append(suite | {"cases": cases,
                               "_sealed_digest": suite_digest(suite)})
    return suites


def load_key(suite_name: str) -> dict[str, Any] | None:
    """The answers. Called at scoring time and nowhere else."""
    path = KEYS_DIR / f"{suite_name}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ── Predicting ─────────────────────────────────────────────────────────────

#: Where a disagreement lives. Reporting "12/15" says a benchmark found
#: something; saying which layer says what to fix. Adapted from the
#: interaction-centric failure taxonomy in arXiv:2607.28802, narrowed to the
#: layers this product actually has — AAE has no model and no planner in its
#: decision path, so naming those would be borrowed vocabulary.
LAYERS = (
    "tool_contract",    # the signed ToolSpec: schema, targets, credentials
    "intent_authority", # resolving and binding the work order
    "grounding",        # the semantic signals the ACCEPT rule reads
    "risk_policy",      # tier, reversibility, environment
    "role_separation",  # who may approve, who may execute — needs `then` steps
    "payload_binding",  # an approval that does not transfer to another payload
)
#: `verifier` was declared here and probed by nothing, because effect
#: verification needs the postcondition reader, which lives in the CLI and
#: holds a database credential the harness does not. A layer that no scenario
#: can reach is a declaration wearing the clothes of a control — the exact
#: thing this product exists to distinguish — so it is named as absent rather
#: than left as a permanent zero. Covered by tests/e2e/test_decisions.py and
#: the VERIFY scenario until the harness can reach it.
UNREACHABLE_LAYERS = ("verifier",)


@dataclasses.dataclass(frozen=True)
class StepResult:
    """One governed act after the assessment, and what came back.

    `allowed` is the only thing scored. Whether an approval or an execution
    was permitted is the question a role-separation scenario asks; the detail
    is carried so a disagreement can be read without re-running.
    """

    act: str
    as_role: str
    allowed: bool
    detail: str = ""
    #: Where this act landed in the tenant audit chain.
    chain_sequence: int | None = None
    chain_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class Prediction:
    """What the deployment did with one scenario. No judgement attached."""

    suite: str
    case_id: str
    scenario: str
    decision: str
    reasons: tuple[str, ...]
    required_role: str | None
    #: Which layer this scenario is designed to exercise. Declared in the
    #: suite, so it is known before the answer is.
    probes: str = ""
    refusal_code: str = ""
    error: str = ""
    #: The governed acts a multi-step scenario performed after assessing.
    steps: tuple[StepResult, ...] = ()
    #: The engine's own record of this assessment. Not the harness's word for
    #: what happened — the chain entry the control plane wrote independently,
    #: which an auditor can look up and recompute.
    proposal_id: str = ""
    chain_sequence: int | None = None
    chain_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self) | {
            "reasons": list(self.reasons),
            "steps": [s.as_dict() for s in self.steps],
        }


def _tool_call(call: dict[str, Any]):
    from remora.sdk import ToolCall

    return ToolCall(
        tool_name=call["tool"],
        arguments=call.get("arguments", {}),
        target_environment=call.get("target_environment", "staging"),
        intent_ref=call.get("intent_ref"),
    )


def _audit_of(raw: Any) -> tuple[int | None, str]:
    audit = (raw or {}).get("audit") or {}
    return audit.get("sequence_no"), audit.get("entry_hash") or ""


def _run_steps(clients, case, result, call) -> tuple[StepResult, ...]:
    """Perform the governed acts a scenario declares after its assessment.

    Only reachable for scenarios the engine held: there is nothing to approve
    when it never offered a review item. A step whose role has no configured
    token is recorded as not allowed with the reason stated, rather than
    skipped — a silently skipped step in a benchmark is a case that scores
    without testing anything.
    """
    steps: list[StepResult] = []
    item = getattr(result, "review_item_id", None)
    if not item:
        return ()

    for step in case.get("then", []):
        act = step.get("act", "")
        # "required" means the role the engine itself named, which is the only
        # correct answer to "who may release this" — a scenario that hardcoded
        # a role would stop testing escalation the moment escalation changed.
        role = step.get("as", "required")
        if role == "required":
            role = getattr(result.resolution_plan, "required_role", None) \
                or "reviewer"

        client = clients.get(role)
        if client is None:
            steps.append(StepResult(act=act, as_role=role, allowed=False,
                                    detail=f"no token configured for {role!r}"))
            continue

        try:
            if act == "approve":
                outcome = client.approve(item)
                sequence, digest = _audit_of(getattr(outcome, "raw", None))
                steps.append(StepResult(
                    act=act, as_role=role, allowed=True,
                    detail=str(getattr(outcome, "status", "")),
                    chain_sequence=sequence, chain_hash=digest))
            elif act == "execute":
                # A step may restate the call with a different payload; that is
                # how payload binding is probed.
                payload = dict(call)
                payload["arguments"] = {**call.get("arguments", {}),
                                        **step.get("arguments", {})}
                outcome = client.execute(item, _tool_call(payload))
                sequence, digest = _audit_of(getattr(outcome, "raw", None))
                settled = str(getattr(outcome, "outcome", "")).lower()
                steps.append(StepResult(
                    act=act, as_role=role,
                    # `execute` is the only outcome that means the governed
                    # step proceeded. binding_refused and approval_invalidated
                    # are the engine declining, which is what several of these
                    # scenarios are asking it to do.
                    allowed=settled == "execute",
                    detail=settled or "no outcome",
                    chain_sequence=sequence, chain_hash=digest))
            else:
                steps.append(StepResult(act=act, as_role=role, allowed=False,
                                        detail=f"unknown act {act!r}"))
        except Exception as exc:  # noqa: BLE001
            steps.append(StepResult(act=act, as_role=role, allowed=False,
                                    detail=f"{type(exc).__name__}: {exc}"))
    return tuple(steps)


def _predict_case(clients, suite: str, case: dict[str, Any]) -> Prediction:
    call = case["call"]
    common = dict(suite=suite, case_id=case["id"],
                  scenario=case.get("scenario", ""),
                  probes=case.get("probes", ""))
    agent = clients["operator"]
    try:
        result = agent.assess(_tool_call(call))
    except Exception as exc:  # noqa: BLE001
        # A refusal is an outcome, not a crash. Contract codes arrive as
        # "toolspec_unknown_tool: no signed spec exists for ...", and the token
        # before the colon is the rule that fired. Naming it matters: "refused"
        # alone would let a key pass for the wrong reason.
        text = str(exc)
        code = text.split(":", 1)[0].strip() if ":" in text else ""
        if " " in code:                      # a sentence, not a code
            code = ""
        return Prediction(**common, decision="refused", reasons=(),
                          required_role=None, refusal_code=code,
                          error=f"{type(exc).__name__}: {text}")

    plan = result.resolution_plan
    sequence, digest = _audit_of(result.raw)
    return Prediction(
        **common,
        decision=str(result.action).rsplit(".", 1)[-1].lower(),
        reasons=tuple(str(r).rsplit(".", 1)[-1].lower()
                      for r in (result.reasons or [])),
        required_role=getattr(plan, "required_role", None),
        steps=_run_steps(clients, case, result, call),
        proposal_id=result.proposal_id or "",
        chain_sequence=sequence,
        chain_hash=digest,
    )


#: Bumped when the scoring rules change. A score is only comparable to
#: another score produced by the same grader, and "we improved the grader" is
#: indistinguishable from "the engine improved" unless the version is recorded.
GRADER_VERSION = "2"


def manifest() -> dict[str, Any]:
    """What a result needs alongside it to still mean something next month.

    Following the benchmark-hygiene checklist in OpenAI's "Separating signal
    from noise in coding evaluations" (2026): a score with no dataset version,
    no grader version and no environment identity is a number, not a
    measurement. Two runs that disagree are then impossible to diagnose.
    """
    # Deliberately the whole corpus, not the selection: a report must show
    # what was available as well as what was run, or a filtered 5/5 reads as
    # complete coverage.
    suites = {}
    for suite in load_suites():
        suites[suite["suite"]] = {
            "cases": len(suite["cases"]),
            "digest": suite_digest(suite),
            "provenance": suite.get("provenance", {}),
        }

    pinned: dict[str, Any] = {}
    for parent in _HERE.parents[1:4]:
        lock = parent / "product" / "core-artifact-lock.json"
        if lock.is_file():
            data = json.loads(lock.read_text(encoding="utf-8"))
            pinned = {
                "release": data.get("release_tag"),
                "version": data.get("remora_core_version"),
                "commit": (data.get("remora_core_commit") or "")[:12],
                "wheel_sha256": (data.get("wheel") or {}).get("sha256"),
            }
            break

    return {
        "grader_version": GRADER_VERSION,
        "suites": suites,
        "engine": pinned,
        # Contamination: these scenarios are written for this deployment's own
        # ToolPack and have never been published, so no model was trained on
        # them. That is a property of authorship, not a scan — stated as such.
        "contamination": (
            "Scenarios are authored against this deployment's own ToolPack and "
            "have not been published. No public dataset is vendored here. "
            "Where a scenario class is taken from published work, the suite's "
            "`provenance` names the source and describes the port."),
        "blind": (
            "The prediction pass never opens an answer key. Keys live in "
            "benchmarks/keys/ and are readable by anyone with the repository; "
            "the split makes tuning against them a deliberate act, not a "
            "cryptographic secret."),
    }


def predict(clients, only: Selection | str | None = None) -> dict[str, Any]:
    """Run the selected scenarios and record what happened. Opens no key.

    `clients` maps a role name to an SDK client. Most scenarios need only
    `operator`; the role-separation ones need an approver too, and giving the
    harness the roles rather than one omnipotent token is what lets it observe
    a refusal instead of never provoking one.

    Scenarios that declare `then` steps DO write: an approval and an execution
    are governed acts with real effects. That is the price of reaching the
    role-separation layer at all, and it is why the run records every chain
    position it created — see `audit_trail`.
    """
    started = time.time()
    if not isinstance(clients, dict):        # a bare client is the operator
        clients = {"operator": clients}

    selection = _as_selection(only)
    predictions: list[Prediction] = []
    seals: dict[str, str] = {}
    for suite in load_suites(selection):
        seals[suite["suite"]] = suite.get("_sealed_digest")             or suite_digest(suite)
        for case in suite["cases"]:
            predictions.append(_predict_case(clients, suite["suite"], case))

    # Every chain entry this run caused, in order. Not the harness's account of
    # what it did — the sequence numbers and hashes the control plane wrote
    # independently, which an auditor can look up and recompute without
    # trusting this report at all.
    trail = []
    for prediction in predictions:
        if prediction.chain_sequence is not None:
            trail.append({
                "sequence_no": prediction.chain_sequence,
                "entry_hash": prediction.chain_hash,
                "event": "assessed",
                "case": f"{prediction.suite}/{prediction.case_id}",
                "proposal_id": prediction.proposal_id,
            })
        for step in prediction.steps:
            if step.chain_sequence is not None:
                trail.append({
                    "sequence_no": step.chain_sequence,
                    "entry_hash": step.chain_hash,
                    "event": step.act,
                    "case": f"{prediction.suite}/{prediction.case_id}",
                    "proposal_id": prediction.proposal_id,
                })
    trail.sort(key=lambda e: e["sequence_no"])

    return {
        "blind": True,
        "manifest": manifest(),
        "selection": selection.as_dict(),
        "suite_digests": seals,
        "audit_trail": trail,
        "seconds": round(time.time() - started, 2),
        "predictions": [p.as_dict() for p in predictions],
    }


# ── Scoring ────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Scored:
    suite: str
    case_id: str
    scenario: str
    predicted: str
    reasons: tuple[str, ...]
    probes: str
    refusal_code: str
    #: From the key.
    expected_decision: str
    expected_refusal: str
    expected_reasons: tuple[str, ...]
    because: str
    known_gap: str
    #: What the governed acts actually did, and what the key said they should.
    steps: tuple[dict[str, Any], ...] = ()
    expected_steps: tuple[dict[str, Any], ...] = ()

    @property
    def steps_matched(self) -> bool:
        """Every declared step permitted exactly what the key says.

        A scenario that declares steps and performed none has not passed by
        default: it means the engine never offered a review item, so the acts
        the key describes never happened and nothing was tested.
        """
        if not self.expected_steps:
            return True
        if len(self.steps) != len(self.expected_steps):
            return False
        return all(
            actual["act"] == expected["act"]
            and actual["allowed"] is bool(expected["allowed"])
            for actual, expected in zip(self.steps, self.expected_steps))

    @property
    def matched(self) -> bool:
        if self.expected_refusal:
            return self.refusal_code == self.expected_refusal
        if self.refusal_code or self.predicted == "refused":
            return False
        return (self.predicted == self.expected_decision
                and set(self.expected_reasons) <= set(self.reasons)
                and self.steps_matched)

    @property
    def is_known_gap(self) -> bool:
        """A documented limitation behaving as documented.

        Neither a pass nor a regression. Scored as a pass it disappears from
        the report and stops being worked on; scored as a failure the run is
        permanently red and people learn to ignore it.
        """
        return bool(self.known_gap) and not self.matched

    @property
    def gap_closed(self) -> bool:
        return bool(self.known_gap) and self.matched

    @property
    def is_regression(self) -> bool:
        return not self.matched and not self.known_gap

    def why_not(self) -> str:
        if self.expected_refusal and not self.refusal_code:
            return (f"the key says this call should be refused with "
                    f"{self.expected_refusal!r}; it was assessed and returned "
                    f"{self.predicted.upper()}")
        if self.expected_refusal:
            return (f"the key says {self.expected_refusal!r}; "
                    f"got {self.refusal_code!r}")
        if self.refusal_code or self.predicted == "refused":
            return (f"the key says {self.expected_decision.upper()}; the call "
                    f"was refused ({self.refusal_code or 'no code'})")
        if self.predicted != self.expected_decision:
            return (f"the key says {self.expected_decision.upper()}; "
                    f"got {self.predicted.upper()} "
                    f"({', '.join(self.reasons) or 'no reason given'})")
        if not self.steps_matched:
            if not self.steps:
                return ("the key expects "
                        f"{len(self.expected_steps)} governed act(s) after the "
                        "assessment, and none happened — the engine offered no "
                        "review item, so nothing was tested")
            told = "; ".join(
                f"{s['act']} as {s['as_role']} was "
                f"{'allowed' if s['allowed'] else 'refused'}"
                f" ({s['detail']})" for s in self.steps)
            want = "; ".join(
                f"{s['act']} should be "
                f"{'allowed' if s['allowed'] else 'refused'}"
                for s in self.expected_steps)
            return f"the decision was right, but: {told}. The key says: {want}"
        missing = sorted(set(self.expected_reasons) - set(self.reasons))
        return (f"{self.predicted.upper()} was right, but not for the reason "
                f"the key gives: expected {', '.join(missing)}; "
                f"got {', '.join(self.reasons) or 'none'}")

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self) | {
            "reasons": list(self.reasons),
            "expected_reasons": list(self.expected_reasons),
            "steps": list(self.steps),
            "expected_steps": list(self.expected_steps),
            "matched": self.matched,
            "known_gap_open": self.is_known_gap,
            "gap_closed": self.gap_closed,
            "regression": self.is_regression,
            "why_not": None if self.matched else self.why_not(),
        }


def score(run: dict[str, Any]) -> dict[str, Any]:
    """Open the keys and grade a completed prediction pass.

    A suite whose digest no longer matches its key is not graded at all. A
    broken seal means the questions changed after the answers were written,
    and a score computed across that is a number with no meaning — reporting
    it as a low score would be worse than reporting nothing.
    """
    keys = {name: load_key(name) for name in run["suite_digests"]}

    broken, unkeyed = [], []
    for name, digest in run["suite_digests"].items():
        key = keys.get(name)
        if key is None:
            unkeyed.append(name)
        elif key.get("seals_suite_sha256") != digest:
            broken.append(name)

    scored: list[Scored] = []
    for prediction in run["predictions"]:
        key = keys.get(prediction["suite"])
        if key is None or prediction["suite"] in broken:
            continue
        answer = key["answers"].get(prediction["case_id"])
        if answer is None:
            continue
        scored.append(Scored(
            suite=prediction["suite"],
            case_id=prediction["case_id"],
            scenario=prediction["scenario"],
            predicted=prediction["decision"],
            reasons=tuple(prediction["reasons"]),
            probes=prediction.get("probes", ""),
            refusal_code=prediction["refusal_code"],
            expected_decision=str(answer.get("decision", "")).lower(),
            expected_refusal=str(answer.get("refused", "")),
            expected_reasons=tuple(answer.get("reasons", [])),
            because=answer.get("because", ""),
            known_gap=answer.get("known_gap", ""),
            steps=tuple(prediction.get("steps", ())),
            expected_steps=tuple(answer.get("steps", ())),
        ))

    by_suite: dict[str, dict[str, int]] = {}
    for s in scored:
        bucket = by_suite.setdefault(
            s.suite,
            {"matched": 0, "total": 0, "known_gaps": 0, "regressions": 0})
        bucket["total"] += 1
        bucket["matched"] += int(s.matched)
        bucket["known_gaps"] += int(s.is_known_gap)
        bucket["regressions"] += int(s.is_regression)

    # A case with no answer is not scored, and silence about that would let a
    # key quietly cover half a suite while the report looked complete.
    answered = {(s.suite, s.case_id) for s in scored}
    unanswered = [f"{p['suite']}/{p['case_id']}" for p in run["predictions"]
                  if (p["suite"], p["case_id"]) not in answered
                  and p["suite"] not in broken and p["suite"] not in unkeyed]

    # Per layer as well as per suite. A single accuracy figure hides which
    # part of the system is weak, and "more scaffolding" is the wrong answer
    # to every question it can be asked — see arXiv:2607.05775.
    by_layer: dict[str, dict[str, int]] = {}
    for s in scored:
        bucket = by_layer.setdefault(
            s.probes or "unclassified",
            {"matched": 0, "total": 0, "known_gaps": 0, "regressions": 0})
        bucket["total"] += 1
        bucket["matched"] += int(s.matched)
        bucket["known_gaps"] += int(s.is_known_gap)
        bucket["regressions"] += int(s.is_regression)

    return {
        "cases": len(scored),
        "matched": sum(1 for s in scored if s.matched),
        "known_gaps": sum(1 for s in scored if s.is_known_gap),
        "regressions": sum(1 for s in scored if s.is_regression),
        "gaps_closed": sum(1 for s in scored if s.gap_closed),
        "suites": by_suite,
        "layers": by_layer,
        "manifest": run.get("manifest", {}),
        "selection": run.get("selection", {}),
        "audit_trail": run.get("audit_trail", []),
        "broken_seals": broken,
        "unkeyed_suites": unkeyed,
        "unanswered_cases": unanswered,
        "seconds": run.get("seconds", 0),
        "results": [s.as_dict() for s in scored],
    }


def run(clients, only: Selection | str | None = None) -> dict[str, Any]:
    """Predict, then score. The ordinary path."""
    return score(predict(clients, only))


def verify_trail(client, report: dict[str, Any]) -> dict[str, Any]:
    """Check a report's audit trail against the chain the engine wrote.

    This is what makes a benchmark result auditable rather than merely
    published. The report claims a set of governed acts happened at particular
    chain positions; this reads those positions back from the control plane,
    confirms the entry hashes still match, and confirms the chain as a whole
    still verifies.

    Without it a score is the harness's word for what it did — and a harness
    that fabricated the entire run would produce a byte-identical file. With
    it, a reviewer who trusts nothing in this repository can still check the
    claim, because the entries were written by the control plane before the
    report existed.
    """
    trail = report.get("audit_trail") or []
    if not trail:
        return {"checked": 0, "verified": False,
                "problem": "the report carries no audit trail"}

    # The chain's own integrity first. Matching entries inside a chain that
    # does not verify would prove nothing at all.
    try:
        chain = client.verify_audit_chain()
        raw = chain if isinstance(chain, dict) else getattr(chain, "raw", {})
        chain_valid = bool(raw.get("valid"))
        records = raw.get("records_checked")
    except Exception as exc:  # noqa: BLE001
        return {"checked": 0, "verified": False,
                "problem": f"the chain could not be verified: "
                           f"{type(exc).__name__}: {exc}"}

    by_proposal: dict[str, list[dict[str, Any]]] = {}
    for entry in trail:
        if entry.get("proposal_id"):
            by_proposal.setdefault(entry["proposal_id"], []).append(entry)

    mismatches: list[dict[str, Any]] = []
    checked = 0
    for proposal_id, entries in by_proposal.items():
        try:
            lifecycle = client.get_lifecycle(proposal_id)
            body = lifecycle if isinstance(lifecycle, dict) \
                else getattr(lifecycle, "raw", {}) or {}
            events = body.get("events", [])
        except Exception as exc:  # noqa: BLE001
            mismatches.append({"proposal_id": proposal_id,
                               "problem": f"{type(exc).__name__}: {exc}"})
            continue

        on_chain = {e.get("sequence_no"): e.get("entry_hash") for e in events}
        for entry in entries:
            checked += 1
            actual = on_chain.get(entry["sequence_no"])
            if actual != entry["entry_hash"]:
                mismatches.append({
                    "case": entry["case"],
                    "sequence_no": entry["sequence_no"],
                    "reported": entry["entry_hash"][:16],
                    "on_chain": (actual or "absent")[:16],
                    "problem": "the chain does not hold that entry at that "
                               "position",
                })

    return {
        "checked": checked,
        "chain_valid": chain_valid,
        "chain_records": records,
        "mismatches": mismatches,
        "verified": bool(chain_valid) and not mismatches and checked > 0,
    }


# ── Reporting ──────────────────────────────────────────────────────────────

def _block(lines: list[str], heading: str, results: list[dict[str, Any]],
           tail: str = "") -> None:
    if not results:
        return
    lines += ["", f"  {heading}", ""]
    for r in results:
        lines.append(f"    {r['suite']}/{r['case_id']}")
        lines.append(f"      scenario  {r['scenario']}")
        lines.append(f"      result    "
                     f"{r['why_not'] or 'matches the key'}")
        # The key's reasoning prints with the disagreement on purpose. The next
        # question is always "which is wrong, the engine or the key?", and it
        # cannot be answered without seeing the argument.
        lines.append(f"      key says  {r['because']}")
        if r.get("known_gap"):
            lines.append(f"      known     {r['known_gap']}")
        lines.append("")
    if tail:
        lines += [f"  {tail}", ""]


def format_report(report: dict[str, Any]) -> str:
    """A scorecard first, then only what needs a decision from a person."""
    lines = ["", "  Governance benchmark  (blind: the run never read a key)", ""]

    for suite, s in sorted(report["suites"].items()):
        mark = "OK  " if s["regressions"] == 0 else "FAIL"
        note = ""
        if s["known_gaps"]:
            note = (f"   {s['known_gaps']} known gap"
                    f"{'s' if s['known_gaps'] != 1 else ''}")
        lines.append(f"    [{mark}] {suite:<26} {s['matched']}/{s['total']}{note}")

    if report.get("layers"):
        lines += ["", "  By layer — where the weakness is, not just how much", ""]
        for layer, s in sorted(report["layers"].items()):
            note = f"   {s['known_gaps']} known" if s["known_gaps"] else ""
            lines.append(f"    {layer:<20} {s['matched']}/{s['total']}{note}")

    if report["broken_seals"]:
        lines += ["",
                  "  BROKEN SEAL — not scored, because the questions changed "
                  "after the answers were written:", ""]
        for name in report["broken_seals"]:
            lines.append(f"    {name}")
        lines += ["", "  Re-seal deliberately: python run.py bench --reseal "
                      f"--suite {report['broken_seals'][0]}", ""]

    if report["unkeyed_suites"]:
        lines += ["", "  No answer key, so not scored:", ""]
        lines += [f"    {n}" for n in report["unkeyed_suites"]] + [""]

    if report["unanswered_cases"]:
        lines += ["", "  In a suite but absent from its key, so not scored:", ""]
        lines += [f"    {c}" for c in report["unanswered_cases"]] + [""]

    results = report["results"]
    _block(lines, "Regressions — the deployment disagrees with the key",
           [r for r in results if r["regression"]],
           "Either the engine decided wrongly, or the key is wrong. "
           "Read what the key says and decide which.")
    _block(lines, "Known gaps — open, and behaving as documented",
           [r for r in results if r["known_gap_open"]])
    _block(lines, "Gaps that have CLOSED — update the key and the limitation",
           [r for r in results if r["gap_closed"]])

    plural = "s" if report["regressions"] != 1 else ""
    lines += ["",
              f"  {report['matched']}/{report['cases']} decisions matched the "
              f"sealed key",
              f"  {report['regressions']} regression{plural}, "
              f"{report['known_gaps']} known gap(s) open"
              + (f", {report['gaps_closed']} closed"
                 if report["gaps_closed"] else ""),
              f"  ({report['seconds']}s)"]

    engine = (report.get("manifest") or {}).get("engine") or {}
    if engine:
        lines.append(f"  engine REMORA {engine.get('version')} "
                     f"@ {engine.get('commit')} ({engine.get('release')}) · "
                     f"grader v{report['manifest']['grader_version']}")
    lines.append("")
    return "\n".join(lines)
