-- A credential that can read the governance record and nothing else.
--
-- The console shows what the deployment decided. Until now it could not: the
-- decisions live in the audit chain, in the control-plane database, and the
-- console held no credential for it. So the console displayed business records
-- and a health banner while the entire enforcement record — every assessment,
-- every refusal, every invalidated approval — sat unread one network away.
--
-- This grants SELECT and nothing more. Not a convenience: the console is a
-- presentation surface, and a presentation surface that can write the audit
-- chain can rewrite the evidence it exists to display.
--
-- Nothing here creates or alters a REMORA table. The core owns its schema and
-- creates it lazily at first use; this file only decides who may read it,
-- which is the deployment's business, not the core's. ALTER DEFAULT PRIVILEGES
-- is what makes that work without touching the schema: tables the core creates
-- *later* are covered automatically, so this runs before the control plane has
-- ever started and still holds afterwards.

\set ON_ERROR_STOP on

SELECT format(
  'CREATE ROLE aae_chain_reader LOGIN PASSWORD %L',
  :'chain_reader_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aae_chain_reader')
\gexec

-- Idempotent: the password is rotated on every apply, so a re-run after a
-- secret change converges rather than leaving the old one working.
SELECT format(
  'ALTER ROLE aae_chain_reader PASSWORD %L', :'chain_reader_password')
\gexec

GRANT CONNECT ON DATABASE remora TO aae_chain_reader;
GRANT USAGE ON SCHEMA public TO aae_chain_reader;

-- Existing tables, if the control plane has already run.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO aae_chain_reader;

-- And every table the core creates from here on. Without this line the grant
-- silently covers nothing on a fresh volume, because on a fresh volume there
-- are no tables yet — the failure mode is an empty console that looks correct.
ALTER DEFAULT PRIVILEGES FOR ROLE remora IN SCHEMA public
  GRANT SELECT ON TABLES TO aae_chain_reader;

-- Belt and braces: revoke anything that would let this role change the record.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
  ON ALL TABLES IN SCHEMA public FROM aae_chain_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE remora IN SCHEMA public
  REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLES FROM aae_chain_reader;

SELECT 'governance chain reader ready' AS status;
