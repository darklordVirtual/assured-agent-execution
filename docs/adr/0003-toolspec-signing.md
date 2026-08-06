# ADR 0003 — Sign the ToolSpec bundle, and pin its digest

**Accepted** 2026-08-06.

## Context

A ToolSpec is the authority to call a tool: argument schema, allowed
environments, credential scope, whether the effect can be read back. If an
agent could edit it, governance would be checking the agent's own claims about
itself.

## Decision

HMAC-SHA256 over the bundle, with a key the deployment holds and generates per
installation. `run.py sign` computes each `callable_digest` from the source of
the function actually registered, and refuses to sign if a declared tool has no
callable or a registered callable has no spec.

The bundle **digest** is pinned separately in `.env`.

## Consequences

Editing any field — widening allowed targets, rewriting the description an
agent reads, escalating credential scope, reclassifying a destructive tool —
invalidates the signature.

The pin is the non-obvious half. A signature proves a bundle is authentic and
says nothing about whether it is current; a correctly-signed *older* bundle
passes every signature check. Only the pinned digest refuses it.

HMAC is symmetric, so the verifying process holds the signing key. "The agent
cannot sign its own spec" is therefore only as strong as the agent's inability
to read the control plane's environment. Asymmetric signing is deferred
upstream, and this limit is stated in the security model rather than implied
away.

The signed bundle is generated, not source: gitignored, produced at install.
Committing it would mean shipping a signature no other installation's key can
verify, and making a routine install dirty the working tree.
