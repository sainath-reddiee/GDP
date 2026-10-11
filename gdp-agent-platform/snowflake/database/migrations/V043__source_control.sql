-- V043 Source control: one table of every pull request the app opens for a governed asset.
-- Applied once; never edit after it has been applied. Add a new V### instead.
--
-- GOVERNANCE.GIT_PR  one row per published asset version: a domain model (MODEL), a data quality suite (DQ_SUITE), a
--                    QA pack (QA_PACK) or an STTM mapping (STTM). services/git/roundtrip.py records a row after each
--                    publish and the sync (on demand and from CODEGEN.SYNC_MODEL_PRS_TASK) reads every open pull
--                    request's state from GitHub, updating this table and the asset's own version table. The Source
--                    control page lists these rows. Existing model, suite and pack pull requests are backfilled.
-- Every statement is safe to run again (IF NOT EXISTS, INSERT ... WHERE NOT EXISTS).

CREATE TABLE IF NOT EXISTS {{database}}.GOVERNANCE.GIT_PR (
    KIND          VARCHAR(16)    NOT NULL,   -- MODEL | DQ_SUITE | QA_PACK | STTM
    ASSET_ID      VARCHAR(36)    NOT NULL,   -- MODEL_ID | SUITE_ID | PACK_ID | CATALOG_ID
    VERSION       NUMBER(6,0)    NOT NULL,
    ASSET_NAME    VARCHAR(800),              -- domain, table, pack or target entity, for the list
    DOMAIN_ID     VARCHAR(36),
    REPO_ID       VARCHAR(36),
    BRANCH        VARCHAR(256),
    BASE_BRANCH   VARCHAR(256),
    PR_NUMBER     NUMBER(10,0),
    PR_URL        VARCHAR(1000),
    STATE         VARCHAR(16),               -- OPEN | DRAFT | MERGED | CLOSED | FAILED | NO_CHANGES
    MERGED_SHA    VARCHAR(64),
    TITLE         VARCHAR(400),
    FILES         ARRAY,
    DETAIL        VARCHAR(2000),
    OPENED_BY     VARCHAR(256)   DEFAULT CURRENT_USER(),
    OPENED_AT     TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    SYNCED_AT     TIMESTAMP_LTZ,
    UPDATED_AT    TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_GIT_PR PRIMARY KEY (KIND, ASSET_ID, VERSION)
);

INSERT INTO {{database}}.GOVERNANCE.GIT_PR (KIND, ASSET_ID, VERSION, ASSET_NAME, DOMAIN_ID, REPO_ID, BRANCH, PR_NUMBER, PR_URL,
                                            STATE, MERGED_SHA, TITLE, OPENED_BY, OPENED_AT, SYNCED_AT)
SELECT 'MODEL', D.MODEL_ID, D.VERSION, D.DOMAIN_NAME, D.DOMAIN_ID, D.REPO_ID, D.BRANCH, D.PR_NUMBER, D.PR_URL,
       D.PR_STATE, D.MERGED_SHA, 'Model ' || D.DOMAIN_NAME || ' v' || D.VERSION, COALESCE(D.APPROVED_BY, 'SYSTEM'),
       COALESCE(D.PR_SYNCED_AT, D.UPDATED_AT, CURRENT_TIMESTAMP()), D.PR_SYNCED_AT
  FROM {{database}}.GOVERNANCE.MODEL_DESIGNS D
 WHERE D.MODEL_ID IS NOT NULL AND D.PR_STATE IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM {{database}}.GOVERNANCE.GIT_PR G
                    WHERE G.KIND = 'MODEL' AND G.ASSET_ID = D.MODEL_ID AND G.VERSION = D.VERSION);

INSERT INTO {{database}}.GOVERNANCE.GIT_PR (KIND, ASSET_ID, VERSION, ASSET_NAME, DOMAIN_ID, REPO_ID, BRANCH, PR_NUMBER, PR_URL,
                                            STATE, MERGED_SHA, TITLE, OPENED_BY, OPENED_AT, SYNCED_AT)
SELECT 'DQ_SUITE', V.SUITE_ID, V.VERSION, S.TABLE_NAME, S.DOMAIN_ID, V.REPO_ID, V.BRANCH, V.PR_NUMBER, V.PR_URL,
       V.PR_STATE, V.MERGED_SHA, 'Data quality checks ' || S.TABLE_NAME || ' v' || V.VERSION, COALESCE(V.APPROVED_BY, 'SYSTEM'),
       COALESCE(V.PR_SYNCED_AT, CURRENT_TIMESTAMP()), V.PR_SYNCED_AT
  FROM {{database}}.GOVERNANCE.DQ_SUITE_VERSION V
  JOIN {{database}}.GOVERNANCE.DQ_SUITE S ON S.SUITE_ID = V.SUITE_ID
 WHERE V.PR_STATE IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM {{database}}.GOVERNANCE.GIT_PR G
                    WHERE G.KIND = 'DQ_SUITE' AND G.ASSET_ID = V.SUITE_ID AND G.VERSION = V.VERSION);

INSERT INTO {{database}}.GOVERNANCE.GIT_PR (KIND, ASSET_ID, VERSION, ASSET_NAME, DOMAIN_ID, REPO_ID, BRANCH, PR_NUMBER, PR_URL,
                                            STATE, MERGED_SHA, TITLE, FILES, OPENED_BY, OPENED_AT, SYNCED_AT)
SELECT 'QA_PACK', P.PACK_ID, P.VERSION, P.NAME, P.DOMAIN_ID, P.REPO_ID, P.BRANCH, P.PR_NUMBER, P.PR_URL,
       P.PR_STATE, P.MERGED_SHA, 'QA pack ' || P.NAME || ' v' || P.VERSION,
       IFF(P.REPO_PATH IS NULL, NULL, ARRAY_CONSTRUCT(P.REPO_PATH)), COALESCE(P.CREATED_BY, 'SYSTEM'),
       COALESCE(P.PR_SYNCED_AT, CURRENT_TIMESTAMP()), P.PR_SYNCED_AT
  FROM {{database}}.GOVERNANCE.DOMAIN_QA_PACKS P
 WHERE P.PR_STATE IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM {{database}}.GOVERNANCE.GIT_PR G
                    WHERE G.KIND = 'QA_PACK' AND G.ASSET_ID = P.PACK_ID AND G.VERSION = P.VERSION);

GRANT SELECT ON TABLE {{database}}.GOVERNANCE.GIT_PR TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.GOVERNANCE.GIT_PR TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.GOVERNANCE.GIT_PR TO DATABASE ROLE {{database}}.REVIEWER;
