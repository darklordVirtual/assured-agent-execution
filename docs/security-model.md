# Security model

What is enforced, what is merely declared, and by what. A control nobody
checks is a comment; a declaration presented as a control is worse.

## Enforced

| Property | Enforced by | Checked by |
|---|---|---|
| Approval binds to the exact payload | tool-call hash recomputed at execution | `test_security.py::_assert_tampering_refused` |
| An approver cannot execute | REMORA role capabilities (`reviewer`, `domain_expert`, `senior_authority` — never `admin`) | `test_the_approver_cannot_execute_what_it_approved` |
| An agent cannot approve its own proposal | the operator role has no `review` capability | `test_the_agent_cannot_approve_its_own_proposal` |
| An execution grant is single-use | the durable one-time-grant ledger | `test_an_accept_token_cannot_be_redeemed_twice` |
| The effect reader cannot write | `GRANT SELECT` only, plus a read-only transaction | `test_the_reader_credential_cannot_write` |
| A tool cannot classify itself | risk tiers live in a data file hashed into the policy identity | upstream `test_deployment_tool_registry.py` |
| A ToolSpec cannot be edited by what it constrains | HMAC signature over the bundle | `test_toolspec.py`, six tamper shapes |
| A correctly-signed *older* bundle is refused | the pinned bundle digest | `test_a_correctly_signed_older_bundle_is_refused_by_the_pin` |
| Metrics need a token | `REMORA_PROMETHEUS_PUBLIC=0` | `test_prometheus_metrics_require_a_token` |
| Containers hold no capabilities, no writable root, bounded memory and PIDs | compose | `test_hardening.py`, against `docker inspect` |
| Nothing is published beyond loopback | `127.0.0.1` port bindings | `test_published_ports_bind_loopback_only` |
| The pinned core is what it claims | seven SHA-256 digests | `verify_core_pin.py`, refuses on mismatch |

## Declared, not enforced

These are statements a call can be checked *against*. Nothing in this
deployment makes them true.

**`network_policy: {"egress": "none"}`** in a ToolSpec declares that a tool
*requires* no outbound network. The compose profile does not use an internal
network or an egress firewall. The reference tools genuinely need none, so the
declaration is accurate — but it is not a network control, and a tool that
started making outbound calls would not be stopped by it.

**`credential_scope`** declares which scopes a tool may use. REMORA refuses a
dispatch requesting more than the spec allows; it does not attest that the
callable only uses what it declared.

**`callable_digest`** is computed from the deployed source and signed, and a
test asserts it matches. REMORA 0.10.0 records it without checking it at
dispatch, so a swapped callable is currently caught by the policy bundle hash
over the module source, not by this field.

**`work_orders.json`** is a *deployment-controlled authority fixture*, not a
cryptographically signed document. Its whole-file SHA-256 becomes the intent
authority hash, so an edit is visible on every proposal in flight — that is
tamper-evidence, not a signature.

## Trust assumptions

- **The signing key is not reachable by the agent.** HMAC is symmetric, so the
  process that verifies the ToolSpec bundle holds the key that signs it. If an
  attacker can read the control plane's environment, ToolSpec signing gives
  nothing. Asymmetric signing is deferred upstream.
- **The deployment controls the intent fixture and the ToolPack.** Both are
  supplied out of band; an agent that could write either could grant itself
  authority.
- **The database grants are correct.** The reader's inability to write is a
  grant, not a code path, so it survives a bug in the reader — but not a
  misapplied migration.

## Not claimed

AAE does not claim that agents are correct, that every tool can be
effect-verified, that safety is guaranteed, or that bypass is impossible when
an agent retains direct tool credentials. Tools currently run inside the
control-plane process, so credential separation is at the database, not at a
process boundary — see [limitations.md](limitations.md).

## Reporting

Security issues: support@luftfiber.no. This is a pre-1.0 reference vertical
with no external review; please treat findings accordingly.
