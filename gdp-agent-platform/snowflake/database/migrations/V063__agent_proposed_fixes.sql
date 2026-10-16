-- V063: agent-proposed fixes. On a miss the RCA agent may propose a fix built only from the safe-action catalogue
-- (services/resolve/actions.py: CORE.FIX_ADD_COLUMN, FIX_CREATE_TABLE_FROM, FIX_CREATE_STAGE, FIX_DEDUP_ON_KEY,
-- FIX_RERUN_ONLY), on objects inside AGENT_FIX_SCOPE only. The proposal is a DRAFT known fix written by the agent and an
-- APPROVAL_REQUEST of KIND 'PROPOSAL': one person's approval publishes it (four eyes: the agent wrote it) and runs it,
-- and from then on the same failure is fixed automatically.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

ALTER TABLE {{database}}.CORE.CONTROL_PREFERENCE ADD COLUMN IF NOT EXISTS AGENT_PROPOSALS_ENABLED BOOLEAN DEFAULT FALSE;
ALTER TABLE {{database}}.CORE.CONTROL_PREFERENCE ADD COLUMN IF NOT EXISTS AGENT_FIX_SCOPE VARCHAR(2000);   -- comma list of DB.SCHEMA.NAME patterns (* wildcards)

ALTER TABLE {{database}}.CORE.KNOWN_ISSUE ADD COLUMN IF NOT EXISTS PROPOSED_BY_AGENT BOOLEAN DEFAULT FALSE;
ALTER TABLE {{database}}.CORE.KNOWN_ISSUE ADD COLUMN IF NOT EXISTS RATIONALE VARCHAR(4000);                -- why the agent proposed it

ALTER TABLE {{database}}.CORE.APPROVAL_REQUEST ADD COLUMN IF NOT EXISTS KIND VARCHAR(16) DEFAULT 'RUN';    -- RUN | PROPOSAL
