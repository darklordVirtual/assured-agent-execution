-- Credential separation for the work-order system of record.
--
-- Split from 001 because it is the file an operator adapts when they point AAE
-- at a real system of record instead of the bundled one: the grants matter,
-- the table definitions are ours.
--
-- This file is psql-specific, not plain SQL. It takes the two passwords as
-- psql variables and builds the role statements with \gexec:
--
--     psql ... -v worker_password=... -v reader_password=... -f 002_roles.sql
--
-- The passwords never appear here and never reach a shell argument list; they
-- come from .env, which scripts/bootstrap_env.py generated for this
-- installation alone.
--
-- (A first attempt used DO blocks reading current_setting('aae.worker_password').
-- psql does not substitute variables inside dollar-quoted strings, and its
-- -v names cannot contain a dot, so that combination could never have worked.
-- \gexec is the mechanism that actually composes a statement from a variable.)

BEGIN;

-- The control plane's tool callables. Every governed write travels on this
-- credential. It holds no DDL rights: the schema is applied by this migration
-- step, under the admin role, before this role is ever used. A running product
-- should not be able to change its own schema.
SELECT 'CREATE ROLE aae_worker LOGIN'
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aae_worker')
\gexec

ALTER ROLE aae_worker LOGIN PASSWORD :'worker_password';

GRANT CONNECT ON DATABASE workorders TO aae_worker;
GRANT USAGE  ON SCHEMA public TO aae_worker;
GRANT SELECT, INSERT, UPDATE ON work_orders        TO aae_worker;
GRANT SELECT, INSERT         ON work_order_events  TO aae_worker;
GRANT USAGE ON SEQUENCE work_order_events_event_id_seq TO aae_worker;
-- Deliberately no DELETE anywhere: a governed system whose worker can erase
-- the evidence of what it did is not governed. Cancellation is a status.

-- The postcondition reader. SELECT only, and that is the whole point: it
-- answers "did the approved effect actually happen?" and must not be able to
-- make the answer true. A reader holding write credentials would turn effect
-- verification into a statement about itself.
SELECT 'CREATE ROLE aae_reader LOGIN'
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aae_reader')
\gexec

ALTER ROLE aae_reader LOGIN PASSWORD :'reader_password';

GRANT CONNECT ON DATABASE workorders TO aae_reader;
GRANT USAGE  ON SCHEMA public TO aae_reader;
GRANT SELECT ON work_orders       TO aae_reader;
GRANT SELECT ON work_order_events TO aae_reader;
-- The schema version too: `aae doctor` reports which migration the
-- system of record is at, and a product that cannot state that has no
-- way to tell an operator it is talking to an older schema than it
-- expects. Version metadata discloses nothing about the work orders.
GRANT SELECT ON schema_version    TO aae_reader;
GRANT SELECT ON schema_version    TO aae_worker;

-- Future tables must not silently grant themselves to either role.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM aae_worker;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM aae_reader;

INSERT INTO schema_version (version, description)
VALUES (2, 'worker and reader roles, reader is SELECT only')
ON CONFLICT (version) DO NOTHING;

COMMIT;
