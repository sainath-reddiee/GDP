-- V059: Microsoft Teams incident notifications are retired. Support teams keep their ownership, Jira routing and
-- escalation timing; only the stored Teams webhook secrets go. Outbox rows left on channel TEAMS are marked SKIPPED (the
-- worker now delivers channel JIRA only and skips anything else), so nothing is retried.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.
ALTER TABLE {{database}}.OPS.TEAM DROP COLUMN IF EXISTS TEAMS_WEBHOOK_SECRET;
ALTER TABLE {{database}}.OPS.TEAM DROP COLUMN IF EXISTS ESCALATION_WEBHOOK_SECRET;

UPDATE {{database}}.OPS.NOTIFICATION
   SET STATUS = 'SKIPPED', ERROR = 'Teams notifications are retired'
 WHERE CHANNEL = 'TEAMS' AND STATUS IN ('PENDING', 'FAILED', 'SENDING', 'SUPPRESSED');
