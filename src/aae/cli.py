# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""The product CLI.

    aae doctor                       what is running, and what is configured
    aae scenarios                    run the four decisions end to end
    aae propose <tool> k=v ...       submit one governed call
    aae approve <review-item-id>     release a held decision (approver token)
    aae execute <review-item-id> <tool> k=v ...
    aae lifecycle <proposal-id>      the ordered event trail
    aae verify-effect <proposal-id> <tool> k=v ...
    aae evidence export --out DIR [proposal-id ...]

Each command uses the narrowest token that can perform it. ``approve`` uses
the approver; everything else uses the agent. A CLI holding one omnipotent
token would be simpler and would stop demonstrating role separation, which is
one of the things this product is for.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from aae._io import force_utf8_output
from aae.config import Config, ConfigError
from aae.evidence import plain

EXIT_OK = 0
EXIT_FAILED = 1        # the system answered, and the answer is a failure
EXIT_UNAVAILABLE = 2   # we could not reach it — kept distinct on purpose
EXIT_CONFIG = 78


def _kv(pairs: list[str]) -> dict[str, Any]:
    """Parse ``k=v`` arguments. Values are strings unless valid JSON."""
    out: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"argument must be key=value, got {pair!r}")
        key, _, raw = pair.partition("=")
        try:
            out[key] = json.loads(raw)
        except ValueError:
            out[key] = raw
    return out


def _client(cfg: Config, role: str = "agent"):
    from remora.sdk import RemoraClient

    if role in ("agent", "viewer"):
        token = {"agent": cfg.token_agent, "viewer": cfg.token_viewer}[role]
    else:
        # An approver identity is chosen by the role a DECISION requires,
        # never by a caller's preference — see Config.approver_for.
        _, token = cfg.approver_for(role)
    return RemoraClient(cfg.api_url, token)


# ── doctor ─────────────────────────────────────────────────────────────────

def cmd_doctor(cfg: Config, args: argparse.Namespace) -> int:
    import psycopg
    from remora.sdk import RemoraUnavailableError

    lock = json.loads((Path(__file__).resolve().parents[2] /
                       "product" / "core-artifact-lock.json"
                       ).read_text(encoding="utf-8"))
    print("Assured Agent Execution")
    print(f"  pinned core   REMORA {lock['remora_core_version']} @ "
          f"{lock['remora_core_commit'][:8]} ({lock['release_tag']}, "
          f"{lock['release_status']})")
    print(f"  api           {cfg.api_url}")

    ok = True
    try:
        with _client(cfg, "viewer") as client:
            root = client._request("GET", "/")  # noqa: SLF001 — diagnostics
        print(f"  control plane {root.get('status')}, "
              f"mode={root.get('runtime_mode')}, "
              f"surfaces={','.join(root.get('surfaces', []))}")
    except RemoraUnavailableError as exc:
        print(f"  control plane UNREACHABLE — {exc}")
        ok = False
    except Exception as exc:  # noqa: BLE001
        print(f"  control plane error — {type(exc).__name__}: {exc}")
        ok = False

    # The reader credential, checked as a reader: it must connect AND must
    # not be able to write. A doctor that only proved connectivity would miss
    # the property that matters.
    try:
        with psycopg.connect(cfg.reader_dsn, connect_timeout=5,
                             autocommit=True) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM work_orders")
                count = cur.fetchone()[0]
        print(f"  system of record  reachable, {count} work order(s), "
              f"read-only credential")
    except psycopg.Error as exc:
        print(f"  system of record  UNREACHABLE — {exc}".strip())
        ok = False

    if not ok:
        print("\n  not healthy. try: python run.py up")
    return EXIT_OK if ok else EXIT_UNAVAILABLE


# ── scenarios ──────────────────────────────────────────────────────────────

def cmd_scenarios(cfg: Config, args: argparse.Namespace) -> int:
    from aae import scenarios

    outcomes = scenarios.run_all(cfg)
    print()
    for outcome in outcomes:
        mark = "PASS" if outcome.passed else "FAIL"
        print(f"  [{mark}] {outcome.name}")
        for step in outcome.steps:
            print(f"         {step}")
        print(f"         → {outcome.detail}")
        print()

    failed = [o for o in outcomes if not o.passed]
    print(f"  {len(outcomes) - len(failed)}/{len(outcomes)} scenarios behaved "
          f"as documented")
    if failed:
        print("\n  FAILURES:")
        for outcome in failed:
            print(f"    - {outcome.name}: {outcome.detail}")
        return EXIT_FAILED
    if args.evidence_out:
        from aae import evidence

        ids = [o.proposal_id for o in outcomes if o.proposal_id]
        with _client(cfg) as client:
            manifest = evidence.export(client, ids, Path(args.evidence_out))
        print(f"\n  evidence for {len(manifest['proposals_exported'])} "
              f"proposal(s) → {args.evidence_out}")
    return EXIT_OK


# ── single operations ──────────────────────────────────────────────────────

def cmd_propose(cfg: Config, args: argparse.Namespace) -> int:
    from remora.sdk import ToolCall

    with _client(cfg) as client:
        result = client.assess(ToolCall(
            tool_name=args.tool, arguments=_kv(args.args),
            target_environment=args.env, intent_ref=args.intent))
    print(json.dumps({
        "proposal_id": result.proposal_id,
        "decision": result.action.value,
        "reasons": list(result.reasons),
        "review_item_id": result.review_item_id,
        "has_execution_token": bool(result.execution_token),
        "required_role": (result.resolution_plan.required_role
                          if result.resolution_plan else None),
    }, indent=2))
    return EXIT_OK


def cmd_approve(cfg: Config, args: argparse.Namespace) -> int:
    """Release a held decision using the authority that decision requires.

    --as is for the case where the required role is not derivable from the
    review item alone. It cannot be used to approve with a WEAKER identity
    than the decision asked for — Config.approver_for refuses a role it has
    no token for, and the server refuses a token whose role is insufficient.
    """
    role, token = cfg.approver_for(args.as_role)
    from remora.sdk import RemoraClient

    with RemoraClient(cfg.api_url, token) as client:
        approval = client.approve(args.review_item_id, ttl_seconds=args.ttl)
    print(json.dumps({"status": approval.status,
                      "approved_as": role,
                      "proposal_id": approval.proposal_id,
                      "expires_at": approval.expires_at}, indent=2))
    return EXIT_OK


def cmd_execute(cfg: Config, args: argparse.Namespace) -> int:
    """Execute an approved proposal, or redeem an ACCEPT token.

    The whole tool call has to be restated, not just the arguments: the
    approval is bound to a hash over the tool name, the exact arguments, the
    tenant and the target environment. Restating it is what lets the server
    detect that you are executing something other than what was approved —
    which is the point, and the reason this command does not simply look the
    call up by id.
    """
    from remora.sdk import ToolCall

    call = ToolCall(tool_name=args.tool, arguments=_kv(args.args),
                    target_environment=args.env, intent_ref=args.intent)
    with _client(cfg) as client:
        result = (client.execute_accepted(json.loads(args.token), call)
                  if args.accepted
                  else client.execute(args.review_item_id, call))
    from aae.execution import read_verdict

    verdict = read_verdict(result)
    print(json.dumps({"proposal_id": result.proposal_id,
                      "outcome": verdict.outcome,
                      "tool_executed": verdict.executed,
                      "refusal_reason": verdict.refusal_reason or None,
                      "error": verdict.error or None,
                      "detail": verdict.detail,
                      "summary": verdict.describe()}, indent=2))
    # Two fields, two questions. `outcome` says the governed step ran;
    # `tool_execution.executed` says the tool acted. A refusal arrives as an
    # OUTCOME rather than an exception, and a tool that RAISES still reports
    # outcome="execute" with the nonce burned. Exiting 0 on either would let a
    # script record a failed action as a completed one.
    return EXIT_OK if verdict.acted else EXIT_FAILED


#: Fields worth surfacing per event, in the order an operator reads them.
#: Everything else stays in --json: a wall of raw dict is not a trail anyone
#: can follow, and this command exists to be read.
_TRAIL_FIELDS = (
    ("tool_name", "tool"),
    ("target_environment", "target"),
    ("actor", "actor"),
    ("required_role", "role"),
    ("toolspec_hash", "toolspec"),
    ("intent_authority_hash", "authority"),
    ("tool_call_hash", "payload"),
    ("outcome", "outcome"),
    ("status", "effect"),
)


def _short(value: object) -> str:
    """Hashes truncated, everything else as-is.

    A 64-character hex string carries one useful bit in a trail — whether it
    matches the one on the line above — and eight characters carry it fine.
    """
    text = str(value)
    if len(text) == 64 and all(c in "0123456789abcdef" for c in text):
        return text[:8] + "…"
    return text


def cmd_lifecycle(cfg: Config, args: argparse.Namespace) -> int:
    """The ordered event trail for one proposal."""
    with _client(cfg, "viewer") as client:
        trail = client.get_lifecycle(args.proposal_id)

    if args.json:
        print(json.dumps(plain(trail.raw), indent=2, default=str))
        return EXIT_OK

    print(f"proposal {trail.proposal_id}")
    print(f"state    {trail.current_state}")
    for event in trail.events:
        payload = dict(event.get("payload") or {})
        merged = {**payload, **{k: v for k, v in event.items()
                                if k != "payload"}}
        name = merged.get("event", "?")
        decision = merged.get("decision")
        reasons = ", ".join(merged.get("reasons") or [])
        headline = name if not decision else f"{name} → {decision}"
        if reasons:
            headline += f" ({reasons})"
        print()
        print(f"  #{merged.get('sequence_no', '?')}  {headline}")
        for key, label in _TRAIL_FIELDS:
            if (value := merged.get(key)) not in (None, "", []):
                print(f"        {label:9s} {_short(value)}")
        if entry := merged.get("entry_hash"):
            print(f"        {'chain':9s} {_short(entry)}")
    return EXIT_OK


def cmd_verify_effect(cfg: Config, args: argparse.Namespace) -> int:
    from aae import postcondition

    arguments = _kv(args.args)
    if not postcondition.supports(args.tool):
        print(postcondition.describe(None))
        return EXIT_OK

    with _client(cfg) as client:
        view = postcondition.verify(
            dsn=cfg.reader_dsn, tool_name=args.tool, arguments=arguments,
            proposal_id=args.proposal_id, execution_id=args.execution_id or
            args.proposal_id, toolspec_hash=args.toolspec_hash or "0" * 64)
        print(postcondition.describe(view))
        if view is not None and not args.no_record:
            recorded = client.record_effect(args.proposal_id, view)
            print(f"recorded in the chain as {recorded.status.value}")
    # MISMATCH specifically — not `is_terminal`.
    #
    # `is_terminal` means "this is a settled answer", and VERIFIED and
    # UNSUPPORTED are both settled. Branching on it made a successfully
    # verified effect exit 1, so a CI job wired to this command would have
    # failed on every effect it confirmed. Conflating "final" with "bad" is
    # the exact class of mistake this product exists to prevent, and a test
    # now pins the distinction.
    from remora.sdk import EffectStatus

    if view is not None and view.status is EffectStatus.MISMATCH:
        return EXIT_FAILED
    return EXIT_OK


def cmd_evidence(cfg: Config, args: argparse.Namespace) -> int:
    from aae import evidence

    with _client(cfg) as client:
        manifest = evidence.export(client, args.proposal_ids, Path(args.out))
    print(json.dumps(manifest, indent=2))
    if manifest["proposals_failed"]:
        return EXIT_FAILED
    return EXIT_OK


# ── wiring ─────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aae", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="what is running and configured")
    doctor.set_defaults(func=cmd_doctor)

    scen = sub.add_parser("scenarios", help="run the four decisions end to end")
    scen.add_argument("--evidence-out", help="also export evidence to this directory")
    scen.set_defaults(func=cmd_scenarios)

    propose = sub.add_parser("propose", help="submit one governed tool call")
    propose.add_argument("tool")
    propose.add_argument("args", nargs="*", metavar="key=value")
    propose.add_argument("--intent", help="work-order id this call acts under")
    propose.add_argument("--env", default="prod")
    propose.set_defaults(func=cmd_propose)

    approve = sub.add_parser("approve", help="release a held decision")
    approve.add_argument("review_item_id")
    approve.add_argument("--ttl", type=int, default=900)
    approve.add_argument("--as", dest="as_role", default=None,
                         metavar="ROLE",
                         help="approver role to use (reviewer, domain_expert, "
                              "senior_authority); defaults to reviewer")
    approve.set_defaults(func=cmd_approve)

    execute = sub.add_parser(
        "execute", help="execute an approved proposal",
        description="The approval is bound to a hash over the tool name, the "
                    "exact arguments, the tenant and the target environment, "
                    "so all of it must be restated here.")
    execute.add_argument("review_item_id",
                         help="the review item the approval released")
    execute.add_argument("tool", help="the tool that was approved")
    execute.add_argument("args", nargs="*", metavar="key=value")
    execute.add_argument("--intent", help="work-order id this call acts under")
    execute.add_argument("--env", default="prod")
    execute.add_argument("--accepted", action="store_true",
                         help="redeem an ACCEPT token instead of an approval")
    execute.add_argument("--token", default="{}",
                         help="the execution_token JSON, with --accepted")
    execute.set_defaults(func=cmd_execute)

    lifecycle = sub.add_parser("lifecycle", help="the ordered event trail")
    lifecycle.add_argument("proposal_id")
    lifecycle.add_argument("--json", action="store_true",
                           help="the full record, unabridged")
    lifecycle.set_defaults(func=cmd_lifecycle)

    effect = sub.add_parser("verify-effect",
                            help="read the system of record and compare")
    effect.add_argument("proposal_id")
    effect.add_argument("tool")
    effect.add_argument("args", nargs="*", metavar="key=value")
    effect.add_argument("--execution-id")
    effect.add_argument("--toolspec-hash")
    effect.add_argument("--no-record", action="store_true")
    effect.set_defaults(func=cmd_verify_effect)

    ev = sub.add_parser("evidence", help="export evidence")
    ev_sub = ev.add_subparsers(dest="evidence_command", required=True)
    ev_export = ev_sub.add_parser("export")
    ev_export.add_argument("proposal_ids", nargs="*")
    ev_export.add_argument("--out", required=True)
    ev_export.set_defaults(func=cmd_evidence)

    return parser


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    args = build_parser().parse_args(argv)
    try:
        cfg = Config.from_env()
    except ConfigError as exc:
        print(f"aae: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    try:
        return args.func(cfg, args)
    except Exception as exc:  # noqa: BLE001
        from remora.sdk import RemoraUnavailableError

        if isinstance(exc, RemoraUnavailableError):
            print(f"aae: control plane unreachable — {exc}", file=sys.stderr)
            return EXIT_UNAVAILABLE
        print(f"aae: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
