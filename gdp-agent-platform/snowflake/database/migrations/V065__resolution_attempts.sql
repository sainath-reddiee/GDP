-- V065: a failure the engine cannot process is not retried forever. ATTEMPTS counts the claims that ended in an error
-- (or whose procedure died); after MAX_ATTEMPTS (services/resolve/engine.py) the failure is TICKETED with the error,
-- for a person to look at, instead of going back to PENDING every cycle.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

ALTER TABLE {{database}}.CORE.FAILURE_EVENT ADD COLUMN IF NOT EXISTS ATTEMPTS NUMBER(4,0) DEFAULT 0;
