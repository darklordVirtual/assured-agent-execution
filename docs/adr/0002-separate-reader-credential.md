# ADR 0002 — The effect reader holds SELECT, in a different process

**Accepted** 2026-08-06.

## Context

After a governed write, something must confirm the change reached the system
of record. REMORA deliberately never reaches into a customer's data, so
verification happens in the product's process — which means the product
chooses what that process can do.

## Decision

Two databases. Governance state and business data are separate, and the
work-order database grants two roles: `aae_worker` (SELECT/INSERT/UPDATE, no
DELETE anywhere) and `aae_reader` (SELECT only).

The reader runs in the CLI or the dashboard, not in the control plane. The
process that performs an action and the process that confirms it happened are
not the same process and do not hold the same grants.

## Consequences

A verifier that could write the state it verifies would be reporting on
itself. With one database that split would be a convention; with two and
explicit grants it is enforced by the database, so it survives a bug in the
reader.

Cost: a second Postgres instance, and the reader's port must be reachable from
wherever the reader runs. In this local profile it is published on loopback.

Every read-only connection is `autocommit`. Without it psycopg holds a
transaction open for the whole block, and one stalled verification blocks
`pg_dump` — which is how a scheduled backup ends up hanging in silence.
