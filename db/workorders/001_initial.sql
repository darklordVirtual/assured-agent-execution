-- The work-order system of record.
--
-- This schema belongs to the PRODUCT, not to REMORA. It is the business data
-- the governed tools act on, and the thing a postcondition reader looks at to
-- decide whether an approved action actually happened. REMORA's own tables
-- (execution outbox, tenant chain, control plane, PEP ledger) are core-owned
-- and are not created here.
--
-- Two roles, because credential separation is the point:
--
--   aae_worker   the control plane's tool callables. Reads and writes work
--                orders. This is the credential an agent's action ultimately
--                travels on.
--   aae_reader   the postcondition reader. SELECT only. It answers "did the
--                approved effect happen?" and must not be able to make the
--                answer true. A reader that could write is not evidence.
--
-- Applied by the `wo-migrate` one-shot service in docker-compose.yml before
-- the control plane starts.

BEGIN;

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_orders (
    wo_id         TEXT PRIMARY KEY,
    title         TEXT        NOT NULL,
    asset_id      TEXT        NOT NULL,
    -- 'open' | 'closed' | 'cancelled'. Constrained rather than free text so a
    -- postcondition comparing against 'closed' cannot pass on 'Closed ' or a
    -- typo that never occurs in this column.
    status        TEXT        NOT NULL
                  CHECK (status IN ('open', 'closed', 'cancelled')),
    priority      TEXT        NOT NULL DEFAULT 'normal'
                  CHECK (priority IN ('low', 'normal', 'high')),
    closed_reason TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Who last wrote this row. Set by the tool from the governed execution's
    -- actor identity, so the system of record carries its own attribution
    -- independent of REMORA's audit chain. Two records that disagree is a
    -- finding; one record that cannot be cross-checked is not.
    updated_by    TEXT        NOT NULL DEFAULT 'unknown'
);

CREATE INDEX IF NOT EXISTS work_orders_status_idx ON work_orders (status);

-- An append-only log of every write a governed tool made, keyed to the
-- execution that made it. This is the product's own record; it exists so a
-- mismatch between "REMORA says this executed" and "the system of record shows
-- it" is detectable from either side.
CREATE TABLE IF NOT EXISTS work_order_events (
    event_id     BIGSERIAL PRIMARY KEY,
    wo_id        TEXT        NOT NULL,
    tool_name    TEXT        NOT NULL,
    execution_id TEXT,
    proposal_id  TEXT,
    actor        TEXT        NOT NULL,
    detail       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS work_order_events_wo_idx
    ON work_order_events (wo_id, occurred_at DESC);

-- Seed data. Deliberately small and named, so every scenario in the docs
-- points at a row an operator can go and look at.
INSERT INTO work_orders (wo_id, title, asset_id, status, priority, updated_by)
VALUES
    ('WO-1201', 'Inspect discharge pressure loop after morning ramp',
     'PIC-101', 'open',   'normal', 'seed'),
    ('WO-1202', 'Acknowledge level alarm on tank T-3',
     'LT-410', 'open',   'high',   'seed'),
    ('WO-1203', 'Replace pump seal identified on the morning round',
     'P-7',    'open',   'normal', 'seed'),
    ('WO-1150', 'Quarterly vibration survey',
     'VT-330', 'closed', 'low',    'seed')
ON CONFLICT (wo_id) DO NOTHING;

INSERT INTO schema_version (version, description)
VALUES (1, 'work orders, event log, two roles')
ON CONFLICT (version) DO NOTHING;

COMMIT;
