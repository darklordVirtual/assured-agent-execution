# ADR 0004 — Run REMORA's API as a service rather than wrapping it

**Accepted** 2026-08-06. **Open follow-up.**

## Context

AAE needs a governance control plane. Three options: reimplement the decision
semantics, wrap the SDK in a bespoke server, or deploy REMORA's own API.

## Decision

Deploy it. The pinned wheel force-includes `servers/` and `schemas/`, so the
image installs the verified wheel and runs `uvicorn servers.api:app`. No
REMORA checkout, no submodule.

Running a package as a service is not importing it: no AAE source file may
import `remora.policy`, `remora.enforcement`, `remora.governance` or
`servers`, and a test scans every source file to enforce that.

## Consequences

Decision semantics stay in one place. Reimplementing them in the product is
how a product becomes an unstable fork of a research repo.

**The honest cost:** the lock classifies `servers` as an internal namespace,
and the Dockerfile's entrypoint names `servers.api:app`. The import-boundary
test scans Python files, not Dockerfiles, so it does not see this. AAE
therefore has a runtime dependency on a module the contract calls internal.

The fix is upstream: a stable entrypoint (`remora-control-plane` as a console
script) or a published control-plane image pinned by digest. Until one exists,
this ADR is where the dependency is recorded rather than left implied.
