# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""The benchmark instrument itself.

A benchmark is only worth its output if the scoring is right, so these score
the scorer. Three properties matter more than the rest:

  * the prediction pass must never open an answer key, or "blind" is a label;
  * a key must be sealed to the suite it answers, or it grades the wrong
    questions after an edit;
  * a documented limitation must be its own category, or the report is either
    misleadingly green or permanently red.
"""
from __future__ import annotations

import json

import pytest

from aae import benchmark

pytestmark = pytest.mark.usefixtures("live")


def _clients(live, stack):
    """Every role, because the multi-step scenarios need approvers.

    A harness holding one omnipotent token would never provoke the refusal
    that role separation is supposed to produce, so the roles are handed in
    separately and the engine gets to decline.
    """
    from remora.sdk import RemoraClient

    return {
        role: stack.enter_context(RemoraClient(live.api_url, token))
        for role, token in (("operator", live.token_agent),
                            ("viewer", live.token_viewer),
                            *live.approver_tokens.items())
    }


def _scored(**overrides) -> benchmark.Scored:
    base = dict(
        suite="s", case_id="c", scenario="sc",
        predicted="accept", reasons=(), probes="grounding", refusal_code="",
        expected_decision="accept", expected_refusal="", expected_reasons=(),
        because="b", known_gap="",
    )
    return benchmark.Scored(**(base | overrides))


# ── Blindness ──────────────────────────────────────────────────────────────

def test_the_prediction_pass_opens_no_answer_key(live, monkeypatch) -> None:
    """The property the whole split exists for.

    Asserted by making `load_key` explode: if predicting touches a key at all,
    this fails rather than quietly eroding into a benchmark that reads its own
    answers.
    """
    from remora.sdk import RemoraClient

    def refuse(*args, **kwargs):
        raise AssertionError("predict() opened an answer key")

    monkeypatch.setattr(benchmark, "load_key", refuse)
    with RemoraClient(live.api_url, live.token_agent) as client:
        run = benchmark.predict(client, only="autonomy")
    assert run["blind"] is True
    assert run["predictions"], "no predictions were produced"
    # And a prediction carries no expectation at all.
    for prediction in run["predictions"]:
        assert "expected_decision" not in prediction
        assert "because" not in prediction


def test_a_suite_declares_no_expected_outcome() -> None:
    """A suite that leaked its answers would make the split cosmetic."""
    forbidden = {"expect", "expected", "decision", "because", "known_gap"}
    for suite in benchmark.load_suites():
        for case in suite["cases"]:
            leaked = forbidden & set(case)
            assert not leaked, (
                f"{suite['suite']}/{case['id']} carries {sorted(leaked)}; "
                f"answers belong in benchmarks/keys/")


# ── Sealing ────────────────────────────────────────────────────────────────

def test_every_suite_has_a_key_sealed_to_it() -> None:
    for suite in benchmark.load_suites():
        key = benchmark.load_key(suite["suite"])
        assert key is not None, f"{suite['suite']} has no answer key"
        assert key["seals_suite_sha256"] == benchmark.suite_digest(suite), (
            f"{suite['suite']}: the key is sealed to a different version of "
            f"the suite. Re-seal deliberately, having re-derived the answers.")


def test_editing_a_call_breaks_the_seal() -> None:
    """The seal has to actually detect a changed question."""
    suite = benchmark.load_suites(only="autonomy")[0]
    before = benchmark.suite_digest(suite)
    suite["cases"][0]["call"]["arguments"]["wo_id"] = "WO-9999"
    assert benchmark.suite_digest(suite) != before


def test_editing_prose_does_not_break_the_seal() -> None:
    """Improving a description is not changing a question.

    Without this the seal would punish every clarification, and people would
    stop writing them.
    """
    suite = benchmark.load_suites(only="autonomy")[0]
    before = benchmark.suite_digest(suite)
    suite["description"] = "reworded"
    suite["cases"][0]["scenario"] = "reworded"
    assert benchmark.suite_digest(suite) == before


def test_a_broken_seal_is_not_scored_at_all(live) -> None:
    """A score across changed questions is a number with no meaning.

    Reporting it as a low score would be worse than reporting nothing, because
    a low score invites tuning.
    """
    from remora.sdk import RemoraClient

    with RemoraClient(live.api_url, live.token_agent) as client:
        run = benchmark.predict(client, only="autonomy")
    run["suite_digests"]["autonomy"] = "0" * 64

    report = benchmark.score(run)
    assert report["broken_seals"] == ["autonomy"]
    assert report["cases"] == 0, "a broken seal was graded anyway"


# ── Scoring ────────────────────────────────────────────────────────────────

def test_a_documented_gap_is_neither_a_pass_nor_a_regression() -> None:
    gap = _scored(predicted="verify", known_gap="upstream, docs/limitations.md")
    assert not gap.matched
    assert gap.is_known_gap
    assert not gap.is_regression


def test_an_undocumented_disagreement_is_a_regression() -> None:
    assert _scored(predicted="verify").is_regression


def test_a_gap_that_starts_behaving_correctly_is_announced() -> None:
    fixed = _scored(known_gap="upstream, docs/limitations.md")
    assert fixed.gap_closed and not fixed.is_known_gap


def test_reasons_are_a_subset_check_not_equality() -> None:
    assert _scored(expected_reasons=("a",), reasons=("a", "b")).matched
    assert not _scored(expected_reasons=("a",), reasons=("b",)).matched


def test_a_refusal_matches_only_the_code_the_key_names() -> None:
    """`refused` alone would pass for the wrong reason."""
    assert _scored(expected_decision="", expected_refusal="toolspec_unknown_tool",
                   predicted="refused",
                   refusal_code="toolspec_unknown_tool").matched
    assert not _scored(expected_decision="",
                       expected_refusal="toolspec_unknown_tool",
                       predicted="refused",
                       refusal_code="toolspec_arguments_schema_invalid").matched


def test_a_case_expecting_a_decision_fails_when_the_call_is_refused() -> None:
    result = _scored(predicted="refused", refusal_code="toolspec_unknown_tool")
    assert not result.matched
    assert "refused" in result.why_not()


def test_a_case_absent_from_its_key_is_reported_not_ignored(live) -> None:
    """Silence here would let a key cover half a suite and look complete."""
    from remora.sdk import RemoraClient

    with RemoraClient(live.api_url, live.token_agent) as client:
        run = benchmark.predict(client, only="autonomy")
    run["predictions"].append(dict(run["predictions"][0], case_id="not-in-key"))

    report = benchmark.score(run)
    assert "autonomy/not-in-key" in report["unanswered_cases"]


# ── The suites and keys ────────────────────────────────────────────────────

def _answers():
    for suite in benchmark.load_suites():
        key = benchmark.load_key(suite["suite"]) or {"answers": {}}
        for case in suite["cases"]:
            yield suite, case, key["answers"].get(case["id"])


def test_every_scenario_has_an_answer_that_argues_for_itself() -> None:
    """`because` is what separates a benchmark from a snapshot suite.

    Without it a key records what the engine did and defends it forever,
    including when it is wrong.
    """
    thin = [f"{s['suite']}/{c['id']}" for s, c, a in _answers()
            if not a or len(a.get("because", "").strip()) < 80]
    assert not thin, (
        f"these have no answer, or one that does not argue for itself: {thin}")


def test_every_scenario_declares_which_layer_it_probes() -> None:
    """A score with no layer says something is wrong, not what to fix."""
    for suite, case, _ in _answers():
        probes = case.get("probes", "")
        assert probes in benchmark.LAYERS, (
            f"{suite['suite']}/{case['id']} probes {probes!r}, which is not "
            f"one of {benchmark.LAYERS}")


def test_every_scenario_states_what_it_is() -> None:
    missing = [f"{s['suite']}/{c['id']}" for s, c, _ in _answers()
               if not c.get("scenario", "").strip()]
    assert not missing, f"scenarios with no description: {missing}"


def test_every_answer_expects_one_kind_of_outcome() -> None:
    """A decision or a refusal, never both — they are scored differently."""
    for suite, case, answer in _answers():
        if answer is None:
            continue
        where = f"{suite['suite']}/{case['id']}"
        assert not (answer.get("decision") and answer.get("refused")), (
            f"{where} expects a decision AND a refusal")
        assert answer.get("decision") or answer.get("refused"), (
            f"{where} expects nothing at all")


def test_every_known_gap_cites_where_it_is_documented() -> None:
    """Otherwise the field becomes a way to silence a case."""
    undocumented = [f"{s['suite']}/{c['id']}" for s, c, a in _answers()
                    if a and a.get("known_gap")
                    and "docs/" not in a["known_gap"]]
    assert not undocumented, f"known gaps citing nothing: {undocumented}"


def test_every_suite_declares_where_its_scenarios_come_from() -> None:
    """"Adapted from a public benchmark" is unverifiable unless written down."""
    for suite in benchmark.load_suites():
        provenance = suite.get("provenance") or {}
        assert provenance.get("origin"), (
            f"{suite['suite']} declares no provenance")
        assert provenance.get("domain"), (
            f"{suite['suite']} does not say which domain it is written for")


# ── Against the running deployment ─────────────────────────────────────────

def test_the_benchmark_runs_and_reports_no_regression(live) -> None:
    from contextlib import ExitStack

    with ExitStack() as stack:
        report = benchmark.run(_clients(live, stack))

    assert report["cases"] >= 20, "the suite has thinned out"
    assert not report["broken_seals"], (
        f"unsealed suites: {report['broken_seals']}")
    assert report["regressions"] == 0, (
        "the deployment disagrees with the sealed key and nothing documents "
        "why:\n" + "\n".join(f"  {r['suite']}/{r['case_id']}: {r['why_not']}"
                             for r in report["results"] if r["regression"]))
    closed = [r for r in report["results"] if r["gap_closed"]]
    assert not closed, (
        "a documented limitation has been fixed — update the key and "
        "docs/limitations.md: "
        + ", ".join(f"{r['suite']}/{r['case_id']}" for r in closed))


def test_the_report_carries_a_manifest_that_makes_it_comparable(live) -> None:
    """A score with no versions is a number, not a measurement.

    Following the benchmark-hygiene argument in OpenAI's "Separating signal
    from noise in coding evaluations": without the engine identity, the suite
    digests and the grader version, two runs that disagree cannot be
    diagnosed — you cannot tell whether the engine changed or the ruler did.
    """
    from remora.sdk import RemoraClient

    with RemoraClient(live.api_url, live.token_agent) as client:
        report = benchmark.run(client, only="autonomy")

    manifest = report["manifest"]
    assert manifest["grader_version"]
    assert manifest["engine"]["wheel_sha256"], "the engine is not identified"
    assert manifest["engine"]["release"]
    assert manifest["suites"]["autonomy"]["digest"]
    assert manifest["contamination"], "no contamination assessment"
    assert manifest["blind"], "the blind claim is not stated"
    json.dumps(report)   # an artifact has to survive being written down


def test_the_report_breaks_the_score_down_by_layer(live) -> None:
    """One accuracy figure hides which part of the system is weak."""
    from remora.sdk import RemoraClient

    with RemoraClient(live.api_url, live.token_agent) as client:
        report = benchmark.run(client)

    assert report["layers"], "no per-layer breakdown"
    assert set(report["layers"]) <= set(benchmark.LAYERS)
    assert sum(v["total"] for v in report["layers"].values()) == report["cases"]
    assert benchmark.format_report(report).strip()


# ── Selection ──────────────────────────────────────────────────────────────

def test_a_partial_run_records_that_it_was_partial(live) -> None:
    """A filtered 5/5 must never read as complete coverage.

    The selection travels with the report and the manifest still describes the
    whole corpus, so a reader can always see what was left out.
    """
    from contextlib import ExitStack

    with ExitStack() as stack:
        report = benchmark.run(_clients(live, stack),
                               benchmark.Selection(suites=("autonomy",)))

    assert report["selection"]["suites"] == ["autonomy"]
    assert report["selection"]["everything"] is False
    available = sum(s["cases"] for s in report["manifest"]["suites"].values())
    assert available > report["cases"], (
        "the manifest describes only what ran, so the report cannot show what "
        "was skipped")


def test_selecting_by_layer_runs_only_that_layer(live) -> None:
    """Deliberately a layer that covers only PART of its suite.

    The first version of this used `tool_contract`, which happens to cover
    every case in contract-enforcement — so the filtered suite was identical to
    the whole one, its seal matched by accident, and the test passed while
    `--layer role_separation` silently scored 0/0. A filter must not change the
    seal, and only a partial selection can show that.
    """
    from contextlib import ExitStack

    layer = "role_separation"
    whole = next(s for s in benchmark.load_suites()
                 if s["suite"] == "authority-separation")
    matching = [c for c in whole["cases"] if c["probes"] == layer]
    assert 0 < len(matching) < len(whole["cases"]), (
        "this test needs a layer covering only part of a suite")

    with ExitStack() as stack:
        report = benchmark.run(_clients(live, stack),
                               benchmark.Selection(layers=(layer,)))

    assert not report["broken_seals"], (
        f"filtering broke a seal: {report['broken_seals']}. The seal is over "
        f"the whole suite; a selection chooses which questions to ask, not "
        f"which questions the key answers.")
    assert report["cases"] == len(matching)
    assert set(report["layers"]) == {layer}


def test_an_unknown_suite_is_an_error_not_an_empty_run() -> None:
    """Silently running nothing would report 0/0 as a clean pass."""
    with pytest.raises(KeyError):
        benchmark.load_suites(benchmark.Selection(suites=("no-such-suite",)))


# ── Multi-step scenarios ───────────────────────────────────────────────────

def test_a_scenario_with_steps_actually_performs_them(live) -> None:
    """Role separation cannot be observed without attempting the act.

    If the steps silently did nothing, the suite would score full marks while
    testing that assessments happen — which the other suites already cover.
    """
    from contextlib import ExitStack

    with ExitStack() as stack:
        report = benchmark.run(
            _clients(live, stack),
            benchmark.Selection(suites=("authority-separation",)))

    with_steps = [r for r in report["results"] if r["steps"]]
    assert with_steps, "no governed acts were performed"

    # The refusals have to come from the engine, not from the harness.
    refusals = [s for r in with_steps for s in r["steps"] if not s["allowed"]]
    assert refusals, "nothing was refused, so nothing was constrained"
    assert any("cannot perform" in s["detail"] or "binding" in s["detail"]
               for s in refusals), (
        f"no refusal names an engine-side reason: "
        f"{[s['detail'] for s in refusals]}")


def test_a_declared_step_that_never_ran_is_not_a_pass() -> None:
    """A scenario whose acts never happened has tested nothing.

    Without this a deployment that stopped issuing review items would score
    full marks on the suite designed to catch exactly that.
    """
    never_ran = _scored(predicted="verify", expected_decision="verify",
                        expected_steps=({"act": "approve", "allowed": True},))
    assert not never_ran.matched
    assert "none happened" in never_ran.why_not()


def test_step_outcomes_are_scored_not_just_the_decision() -> None:
    approved = {"act": "approve", "as_role": "reviewer", "allowed": True,
                "detail": "approved"}
    executed = {"act": "execute", "as_role": "reviewer", "allowed": True,
                "detail": "execute"}
    key = ({"act": "approve", "allowed": True},
           {"act": "execute", "allowed": False})

    right = _scored(predicted="verify", expected_decision="verify",
                    steps=(approved, dict(executed, allowed=False)),
                    expected_steps=key)
    assert right.matched

    # The approver executed what it approved. The decision was still VERIFY,
    # so a decision-only check would call this a pass.
    wrong = _scored(predicted="verify", expected_decision="verify",
                    steps=(approved, executed), expected_steps=key)
    assert not wrong.matched


# ── The audit trail ────────────────────────────────────────────────────────

def test_the_run_records_every_chain_position_it_created(live) -> None:
    from contextlib import ExitStack

    with ExitStack() as stack:
        report = benchmark.run(
            _clients(live, stack),
            benchmark.Selection(suites=("authority-separation",)))

    trail = report["audit_trail"]
    assert trail, "the run created no audit trail"
    assert all(e["entry_hash"] and e["sequence_no"] for e in trail)
    # Ordered, so a reader can follow the run through the chain.
    numbers = [e["sequence_no"] for e in trail]
    assert numbers == sorted(numbers)


def test_the_trail_can_be_checked_against_the_chain(live) -> None:
    """The property that makes a published score auditable.

    Without it the score is the harness's word for what it did, and a harness
    that fabricated the whole run would produce an identical file.
    """
    from contextlib import ExitStack

    from remora.sdk import RemoraClient

    with ExitStack() as stack:
        report = benchmark.run(
            _clients(live, stack),
            benchmark.Selection(suites=("authority-separation",)))

    with RemoraClient(live.api_url, live.token_viewer) as viewer:
        check = benchmark.verify_trail(viewer, report)

    assert check["verified"], f"the trail did not verify: {check}"
    assert check["checked"] == len(report["audit_trail"])
    assert check["chain_valid"]


def test_a_forged_trail_is_detected(live) -> None:
    """The check has to actually catch a report that claims what did not happen."""
    from contextlib import ExitStack

    from remora.sdk import RemoraClient

    with ExitStack() as stack:
        report = benchmark.run(
            _clients(live, stack),
            benchmark.Selection(suites=("authority-separation",)))
    report["audit_trail"][0]["entry_hash"] = "0" * 64

    with RemoraClient(live.api_url, live.token_viewer) as viewer:
        check = benchmark.verify_trail(viewer, report)

    assert not check["verified"]
    assert check["mismatches"], "a forged entry hash went unnoticed"


def test_a_report_with_no_trail_does_not_verify_vacuously() -> None:
    """An empty trail must not read as 'nothing was wrong'."""
    from remora.sdk import RemoraClient  # noqa: F401  (unused, kept explicit)

    check = benchmark.verify_trail(None, {"audit_trail": []})
    assert not check["verified"]
    assert check["problem"]


# ── Layers ─────────────────────────────────────────────────────────────────

def test_no_declared_layer_is_unreachable() -> None:
    """A layer nothing can probe is a declaration wearing the clothes of a
    control, which is the exact confusion this product exists to remove. If a
    layer cannot be reached, it belongs in UNREACHABLE_LAYERS with a reason."""
    probed = {c["probes"] for s in benchmark.load_suites() for c in s["cases"]}
    unreachable = [layer for layer in benchmark.LAYERS if layer not in probed]
    assert not unreachable, (
        f"{unreachable} are declared layers that no scenario probes. Either "
        f"write a scenario or move them to UNREACHABLE_LAYERS with the reason.")
