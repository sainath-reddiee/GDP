# Plan: Confidence-Threshold Mapping Human Gate + Post-Mapping Autonomous Pipeline & Airflow Operations Knowledge / DAG Skill

## 1. Confidence-Threshold Human Gate at Mapping → Autonomous Execution After Mapping
1. **Confidence Threshold & Hard Human Gate at `MAPPING_REVIEW`**:
   - Add `MAPPING_CONFIDENCE_THRESHOLD` (`0.85` / `85%`) and `MAPPING_HARD_GATE` (`true`) to `CORE.PLATFORM_CONFIG` (in `infrastructure/seed_knowledge.py` and migration `V083`).
   - In `services/workflow/autopilot.py` (`next_action`) and `apps/api/app/autopilot_api.py` (`_facts`, `drive`):
     - When a run reaches `MAPPING_REVIEW`, pre-populate draft `CORE.MAPPING_DECISION` rows for high-confidence rank-1 candidates (`FINAL_SCORE >= MAPPING_CONFIDENCE_THRESHOLD`) so the reviewer does not start from a blank slate, and compute the confidence breakdown (`confident_count`, `below_threshold_count`, `min_score`, `threshold`).
     - Stop at a **Hard Human Gate at `MAPPING_REVIEW`** (`kind: "GATE"`, `where: "mapping"`) whenever `MAPPING_HARD_GATE` is enabled or any column's best candidate score is below `MAPPING_CONFIDENCE_THRESHOLD`, surfacing a clear message (e.g., `"Review and approve the AI field mapping (X of Y columns meet the 85% confidence threshold; Z need human verification)"`).
2. **100% Autonomous Pipeline After Human Mapping Approval (`MAPPING_APPROVED → COMPLETED`)**:
   - Once the human approves the mapping (`MAPPING_REVIEW → MAPPING_APPROVED` via `POST /api/runs/{run_id}/review` or `POST /api/runs/{run_id}/autopilot`), automatically launch `start_autopilot(run_id, db)` in the background (for both single-table runs and multi-table child runs).
   - From `MAPPING_APPROVED`, Autopilot executes `generate_sttm → approve_sttm → generate_soda → approve_soda → generate_dbt → validate → publish_pr → signoff_qa → approve_code → COMPLETED` (`Published`) autonomously without further manual gates, stopping only if `validate` fails (`VALIDATION_FAILED` for Scenario 2 HITL skill recovery).
   - When starting from **Store (`/os/design/sources` → `Lock Models & Start Build`)**, open `/os/design?view=selected&run={run_id}` (or Home `/os` with a direct link to the Mapping Gate) once the AI mapping is ready for human confirmation, and once approved in Mapping, automatically complete through `Publish`.

---

## 2. Airflow Run Operations Known Issues (`airflow_error_pack.json`)
1. **Repository Integration**:
   - Add the attached `airflow_error_pack.json` (12 learned GDP Airflow failure runbooks: `ops.runbook.gdp.snaplogic-gate-hard-stop`, `ops.runbook.gdp.post-mdm-upstream-engine-failed`, `ops.runbook.gdp.pipeline-done-marked-failed`, `ops.runbook.gdp.k8s-container-exit-sync-es`, `ops.runbook.gdp.snaplogic-bearer-token`, `ops.runbook.gdp.snowflake-failed-to-copy-previous-results`, `ops.runbook.gdp.k8s-pod-not-found`, `ops.runbook.gdp.snaplogic-exit-threshold`, `ops.runbook.gdp.dbt-job-duplicate-row-gold`, `ops.runbook.gdp.event-outbox-not-authorized`, `ops.runbook.gdp.k8s-pod-unschedulable`, `ops.runbook.gdp.snowflake-table-lock-queue`) at `gdp-agent-platform/domain/general/airflow_error_pack.json` and merge them into `gdp-agent-platform/domain/general/airflow_runbook_pack.json`.
   - Update `load_runbooks()` in `infrastructure/seed_knowledge.py` so both `airflow_runbook_pack.json` and `airflow_error_pack.json` are loaded (deduplicated by `key`) and seeded into `CORE.DOMAIN_KNOWLEDGE` (`KNOWLEDGE_TYPE = 'AIRFLOW_RUNBOOK'`).
2. **Operations UI & RCA Matching**:
   - Ensure all 12 `ops.runbook.gdp.*` entries are active in `DEV_AI_PLATFORM.CORE.DOMAIN_KNOWLEDGE` so they immediately power:
     - `services/ops/pipeline.py` (`match_runbooks`) during RCA incident diagnosis,
     - **Run & Quality (`/os/run`) → Learning from operations / Known fixes (`/os/run/ops/fixes`)**, and
     - **Knowledge (`/os/knowledge?tab=operational`)**.

---

## 3. GDP Airflow DAGs Skill (`SKILL.md` → `GDP-AIRFLOW-DAGS`)
1. **Add Skill to `snowflake/skills/gdp/airflow-dags/SKILL.md`**:
   - Save the attached `SKILL.md` under `gdp-agent-platform/snowflake/skills/gdp/airflow-dags/SKILL.md` with normalized frontmatter (`name: GDP-AIRFLOW-DAGS`, `type: OPERATIONS`, `version: 1.0.0`, `domain: GDP`, `category: domain-standards`).
   - Bind `"OPS": ["GDP-AIRFLOW-DAGS"]` in `STAGE_SKILLS` (`services/knowledge/usage.py`) and register `"gdp/airflow-dags"` in `services/knowledge/skills.py`.
2. **RCA & Operations Context Integration**:
   - In `services/ops/context.py` (`pipeline_part`), include an excerpt of the bound `OPS` skill (`GDP-AIRFLOW-DAGS`) when diagnosing `gdp_*` Airflow DAGs so the RCA Assistant understands the GDP DAG topology (`init_batch → validate_source → snaplogic_start → trigger_snaplogic → snaplogic_end → soda_bronze / dbt_silver_* → pipeline_done`, `gdp_gold_<env>`, `gdp_post_mdm_to_gold_*`, `gdp_engine_*`, `gdp_master_<env>`).
   - Also seed a corresponding `AIRFLOW_DAG` knowledge entry (`ops.dag.gdp-airflow-dags-architecture`) in `CORE.DOMAIN_KNOWLEDGE` so the GDP DAG architecture is browsable in Knowledge (`/os/knowledge`).

---

## 4. Versioned Snowflake Migration (`V083`) & Client SQL Bundle Sync
1. Create `gdp-agent-platform/snowflake/database/migrations/V083__mapping_confidence_gate_and_airflow_ops_knowledge.sql` capturing:
   - `MAPPING_CONFIDENCE_THRESHOLD` (`0.85`) and `MAPPING_HARD_GATE` (`true`) in `{{database}}.CORE.PLATFORM_CONFIG`,
   - `GDP-AIRFLOW-DAGS` (`1.0.0`) in `{{database}}.CORE.SKILL_REGISTRY`, `SKILL_LABEL` (`production`), and `SKILL_STAGE_BINDING` (`STAGE = 'OPS'`),
   - All 12 `ops.runbook.gdp.*` `AIRFLOW_RUNBOOK` items + the `AIRFLOW_DAG` architecture item in `{{database}}.CORE.DOMAIN_KNOWLEDGE`.
2. Apply `V083` and updated procedures/skills to `DEV_AI_PLATFORM.CORE`, re-render `deploy-logs/dev-render.sql` and `deploy-logs/client-prod-render.sql`, and run unit tests to verify.
