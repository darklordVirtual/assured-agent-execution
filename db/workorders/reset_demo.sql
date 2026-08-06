-- Return the reference work orders to their seeded state.
--
-- The scenarios mutate the system of record: they close WO-1202 and change
-- WO-1203's priority, which is the point — the effect has to be real for
-- effect verification to mean anything. But that makes a second run a
-- different run, and a demo whose second run behaves differently from its
-- first is a demo nobody can trust twice.
--
-- Applied by `python run.py scenarios` before each run. `--no-reset` skips it
-- when you want to see the accumulated state instead.
--
-- This resets the BUSINESS data only. It deliberately does NOT touch REMORA's
-- audit chain: erasing the record of what was decided in order to make a demo
-- repeatable would defeat the product being demonstrated. The chain grows,
-- and `python run.py reset` is what removes it.

BEGIN;

UPDATE work_orders SET
    status        = 'open',
    priority      = CASE wo_id WHEN 'WO-1202' THEN 'high' ELSE 'normal' END,
    closed_reason = NULL,
    updated_by    = 'seed',
    updated_at    = now()
 WHERE wo_id IN ('WO-1201', 'WO-1202', 'WO-1203');

UPDATE work_orders SET
    status = 'closed', priority = 'low', updated_by = 'seed'
 WHERE wo_id = 'WO-1150';

-- WO-1310 is created by a scenario when the medium-risk write path opens up.
DELETE FROM work_orders WHERE wo_id = 'WO-1310';

-- The product's own event log is business data too, and starts empty.
DELETE FROM work_order_events;

COMMIT;
