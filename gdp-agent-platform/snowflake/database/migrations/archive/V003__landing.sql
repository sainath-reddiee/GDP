-- V003 landing: as-is copies of onboarded source objects.
-- Landed data can contain unclassified PII, so VIEWER does not get SELECT here; profiling and
-- later stages read it through owner's-rights procedures. DATA_STEWARD (and PLATFORM_ADMIN) can query it.

CREATE SCHEMA IF NOT EXISTS {{database}}.LANDING
  COMMENT = 'As-is landing (bronze) of onboarded source objects; one table per source object';

GRANT USAGE ON SCHEMA {{database}}.LANDING TO DATABASE ROLE {{database}}.DATA_STEWARD;
GRANT SELECT ON FUTURE TABLES IN SCHEMA {{database}}.LANDING TO DATABASE ROLE {{database}}.DATA_STEWARD;
