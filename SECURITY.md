# Security

## Reporting a vulnerability

Email **support@luftfiber.no**. Please do not open a public issue for a
suspected vulnerability.

Include what you did, what happened, and what you expected. A proposal id, a
correlation id from the console, or the output of `python run.py doctor` is
usually enough to reproduce.

This is a pre-1.0 reference vertical maintained by one person. There is no
paid bounty and no guaranteed response window; findings are still welcome and
will be credited unless you prefer otherwise.

## What this project claims

Assured Agent Execution governs AI-agent tool calls through a pinned REMORA
core. [docs/security-model.md](docs/security-model.md) states, control by
control, what is **enforced** and by what — and separately, what is only
**declared**. If you find a control that is weaker than the document says, that
is a finding, and it is the kind we most want to hear about.

[docs/limitations.md](docs/limitations.md) lists what is known to be missing.
Anything there is a documented gap, not a vulnerability report — though a
demonstration that a listed gap is worse than described certainly is.

## What this project does not claim

No external security review has been performed. AAE does not claim that agents
are always correct, that all tools can be effect-verified, that safety is
guaranteed, or that bypass is impossible when an agent retains direct tool
credentials.

The local deployment profile is a **reference vertical**. It binds to
loopback, generates its own credentials, and is not hardened for exposure to a
network. Do not run it where untrusted parties can reach it.

## Scope

In scope:

- Bypassing a control the security model says is enforced — payload binding,
  role separation, single-use grants, the read-only verifier credential,
  ToolSpec signature or digest pinning, the import boundary.
- Getting the console to write anything, or to disclose a credential.
- Making the pin verification accept an artifact it should refuse.
- Anything that lets a governed action execute without leaving an audit
  record, or leave one that misstates what happened.

Out of scope:

- The pinned REMORA core itself — report those to
  [REMORA-research](https://github.com/darklordVirtual/REMORA-research).
- The reference work-order ToolPack treated as production software. It is an
  example integration with deliberately small, readable tools.
- Denial of service against a loopback-bound local deployment.
- Anything already listed in [docs/limitations.md](docs/limitations.md).

## Credentials in this repository

There are none. `python run.py up` generates this installation's signing keys,
database passwords and bearer tokens into `.env`, which is gitignored and has
never been committed. The signed ToolSpec bundle is generated per install and
is not tracked either.

If you believe a credential has been committed, that is itself a finding —
please report it.
