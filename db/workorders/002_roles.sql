-- Credential separation for the work-order system of record.
--
-- Split from 001 because it is the file an operator adapts when they point AAE
-- at a real system of record instead of the bundled one: the grants matter,
-- the table definitions are ours.
--
-- The passwords here come from the environment (psql -v), never from this
-- file. docker-compose.yml generates them into .env on first run, so no two
-- installations share a credential even locally.

BEGIN;

-- The control plane's tool callables. Every governed write travels on this
-- credential. It cannot create or drop anything: the schema is applied by the
-- migration step, under a different role, before this role is ever used.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aae_worker') THEN
        EXECUTE format('CREATE ROLE aae_worker LOGIN PASSWORD %L',
                       current_setting('aae.worker_password'));
    ELSE
        EXECUTE format('ALTER ROLE aae_worker LOGIN PASSWORD %L',
                       current_setting('aae.worker_password'));
    END IF;
END $$;

GRANT CONNECT ON DATABASE workorders TO aae_worker;
GRANT USAGE  ON SCHEMA public TO aae_worker;
GRANT SELECT, INSERT, UPDATE ON work_orders       TO aae_worker;
GRANT SELECT, INSERT          ON work_order_events TO aae_worker;
GRANT USAGE ON SEQUENCE work_order_events_event_id_seq TO aae_worker;
-- Deliberately no DELETE anywhere: a governed system whose worker can erase
-- the evidence of what it did is not governed. Cancellation is a status.

-- The postcondition reader. SELECT only, and that is the whole point: it
-- answers "did the approved effect actually happen?" and must not be able to
-- make the answer true. A reader holding write credentials would turn effect
-- verification into a statement about itself.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aae_reader') THEN
        EXECUTE format('CREATE ROLE aae_reader LOGIN PASSWORD %L',
                       current_setting('aae.reader_password'));
    ELSE
        EXECUTE format('ALTER ROLE aae_reader LOGIN PASSWORD %L',
                       current_setting('aae.reader_password'));
    END IF;
END $$;

GRANT CONNECT ON DATABASE workorders TO aae_reader;
GRANT USAGE  ON SCHEMA public TO aae_reader;
GRANT SELECT ON work_orders        TO aae_reader;
GRANT SELECT ON work_order_events  TO aae_reader;

-- Future tables must not silently grant themselves to either role.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM aae_worker;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM aae_reader;

INSERT INTO schema_version (version, description)
VALUES (2, 'worker and reader roles, reader is SELECT only')
ON CONFLICT (version) DO NOTHING;

COMMIT;
