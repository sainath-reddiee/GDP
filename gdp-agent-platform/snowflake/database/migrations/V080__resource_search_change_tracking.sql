-- V080: change tracking on the tables CORE.RESOURCE_SEARCH reads (snowflake/cortex_search/resource_search.sql: the
-- Home search by meaning). A Cortex Search service refreshes from its base tables' change streams; without change
-- tracking switched on by their owner, creating the service fails and search stays keyword-only. The deploy role
-- that ran the migrations owns these tables, so it switches tracking on here, before the service is created.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

ALTER TABLE {{database}}.CORE.ONBOARDING_REQUEST SET CHANGE_TRACKING = TRUE;
ALTER TABLE {{database}}.CORE.DOMAIN_REGISTRY SET CHANGE_TRACKING = TRUE;
ALTER TABLE {{database}}.CORE.SOURCE_REGISTRY SET CHANGE_TRACKING = TRUE;
ALTER TABLE {{database}}.CORE.WORKFLOW_RUN SET CHANGE_TRACKING = TRUE;
ALTER TABLE {{database}}.CORE.TARGET_TABLE_REGISTRY SET CHANGE_TRACKING = TRUE;
