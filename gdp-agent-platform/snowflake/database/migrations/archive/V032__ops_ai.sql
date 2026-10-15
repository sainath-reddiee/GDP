-- Ops AI (PR O3): what the worker's service identity (database role OPS_SERVICE) needs for the AI diagnosis, the
-- resolution knowledge and the weekly digest. No new tables: the diagnosis is stored on OPS.INCIDENT.AI (V031), DAG
-- dependencies in OPS.DAG_DEPENDENCY (KIND is now DATASET | SENSOR | MARKER | TRIGGER | CODE_GRAPH | MANUAL), and retry
-- preview tokens are signed by the API host, never stored.
-- AI_COMPLETE also needs SNOWFLAKE.CORTEX_USER on the service user's account role (granted to PUBLIC by default).
-- Every statement is safe to run again.

-- read the context: code index, knowledge (and its Cortex Search service), STTM, QA and data quality results
GRANT DATABASE ROLE {{database}}.VIEWER TO DATABASE ROLE {{database}}.OPS_SERVICE;

-- audit and cost rows for every AI call the worker makes
GRANT USAGE ON SCHEMA {{database}}.AUDIT TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT INSERT ON TABLE {{database}}.AUDIT.AGENT_TOOL_CALL TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT INSERT ON TABLE {{database}}.AUDIT.COST_USAGE TO DATABASE ROLE {{database}}.OPS_SERVICE;

-- INCIDENT_RESOLUTION knowledge written when a person resolves an incident with a note (and the GENERAL domain)
GRANT USAGE ON SCHEMA {{database}}.KNOWLEDGE TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.KNOWLEDGE.DOMAIN_KNOWLEDGE TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT SELECT, INSERT ON TABLE {{database}}.KNOWLEDGE.DOMAIN_REGISTRY TO DATABASE ROLE {{database}}.OPS_SERVICE;
