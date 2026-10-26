-- V083: Add MAPPING_CONFIDENCE_THRESHOLD (0.85) and MAPPING_HARD_GATE (true) in PLATFORM_CONFIG,
-- register GDP-AIRFLOW-DAGS v1.0.0 skill in SKILL_REGISTRY/SKILL_LABEL/SKILL_STAGE_BINDING,
-- and seed the 12 GDP Airflow known-issue runbooks (airflow_error_pack.json) + GDP DAG architecture into DOMAIN_KNOWLEDGE.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

INSERT INTO {{database}}.CORE.PLATFORM_CONFIG (CONFIG_KEY, CONFIG_VALUE, DESCRIPTION, VERSION, IS_CURRENT, CREATED_BY) SELECT 'MAPPING_CONFIDENCE_THRESHOLD', PARSE_JSON('0.85'), 'Minimum AI candidate score (0.85 = 85%) for pre-selecting field mappings at the Mapping Human Gate', 1, TRUE, CURRENT_USER() WHERE NOT EXISTS (SELECT 1 FROM {{database}}.CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = 'MAPPING_CONFIDENCE_THRESHOLD' AND IS_CURRENT);
INSERT INTO {{database}}.CORE.PLATFORM_CONFIG (CONFIG_KEY, CONFIG_VALUE, DESCRIPTION, VERSION, IS_CURRENT, CREATED_BY) SELECT 'MAPPING_HARD_GATE', PARSE_JSON('true'), 'Require human confirmation at MAPPING_REVIEW before running the post-mapping autonomous pipeline', 1, TRUE, CURRENT_USER() WHERE NOT EXISTS (SELECT 1 FROM {{database}}.CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = 'MAPPING_HARD_GATE' AND IS_CURRENT);

INSERT INTO {{database}}.CORE.SKILL_REGISTRY (SKILL_ID, SKILL_NAME, SKILL_TYPE, DOMAIN_ID, VERSION, STAGE_PATH, CHECKSUM, STATUS, DESCRIPTION, CONTENT, CONFIG, IS_CURRENT, CREATED_BY, REVISION, ORIGIN, CATEGORY_ID, PARENT_SKILL, CHANGE_NOTE) SELECT 'ebf136a7-05a6-5980-d2a0-26fb367449a2', 'GDP-AIRFLOW-DAGS', 'OPERATIONS', NULL, '1.0.0', 'gdp/airflow-dags/1.0.0-4e9fb684/SKILL.md', '4e9fb684eaac29eb420cca30d068e8d50f7fba012a414ef28d8ec538b6b57524', 'ACTIVE', 'Knowledge base for the GDP (Global Data Platform) Airflow 3 / MWAA DAG codebase in gdp-airflow-dags/dags/gdp-dags. Use when building, changing, debugging or explaining GDP DAGs: DAG factory/discovery (gdp_dags.py), multi-env resolution (dev/qa/uat/prod), source DAGs (SnapLogic -> bronze -> Soda -> dbt silver), gold DAG, MDM property coordinator, master status board, post-MDM gold + Kafka publish, OpenSearch engine pods (incremental/bulk/reindex), Snowflake GDP_BATCH_MASTER audit, Akeyless secrets, Soda scans, dbt execution, Asset/Dataset chaining, failure/recovery email threading, tests.', '# GDP Airflow DAGs — Knowledge Base

Code root: `gdp-airflow-dags/dags/gdp-dags/` (git root is `gdp-airflow-dags/`).
Runtime: Airflow 3.x on AWS MWAA (Task SDK; no ORM access in tasks/callbacks), Snowflake, SnapLogic, Soda, dbt, Rafay Kubernetes (OpenSearch engine), Confluent Kafka REST, Akeyless secrets, SMTP/SES email.

> Line numbers drift. Always locate code by **function name** (grep `^def <name>`), not by line.

---

## 1. Module map

| Module | Role | Registers DAGs? |
|---|---|---|
| `gdp_dags.py` | **Single DAG entrypoint.** Resolves env list, calls every builder, registers results in `globals()`, canary DAGs | Yes |
| `gdp_post_mdm.py` | Self-contained post-MDM gold + Kafka DAGs (does NOT import `gdp_utils`) | Yes |
| `gdp_dataset_reset.py` | Admin DAG `admin_reset_dataset_dependencies` (clear queued Asset events) | Yes |
| `gdp_utils.py` (~9k lines) | Core library: env, Snowflake, Akeyless, batch master, task factories, `build_source_dag`, `build_gold_dag`, `build_bronze_only_dag`, `build_mdm_property_trigger_dag`, callbacks | No (`.airflowignore`) |
| `gdp_pipelines.py` | `DOMAIN_CONFIGS` registry per env + bronze-only / legacy DAG builder | No |
| `gdp_source_pipelines.py` | Builds source DAGs, gold DAG, MDM property coordinator; DNB gate; isolated sources | No |
| `gdp_master.py` | `gdp_master_<env>` status-board DAG (ExternalTaskSensors) | No |
| `gdp_opensearch.py` | `gdp_engine_<domain>_<mode>_<env>` DAGs, `GdpEnginePodOperator`, Snowflake mutex | No |
| `gdp_dbt.py` | `dbt_run_task` (dbt-ol subprocess, git-repo cache, transient retry, model logging) | No |
| `gdp_soda.py` | Soda contract verify / consolidated scan tasks, digest emails, Datadog events | No |
| `gdp_snaplogic.py` | SnapLogic trigger + recursive status polling (`build_snaplogic_trigger_task`) | No |
| `gdp_notify.py` | Failure/recovery emails, threading, dedupe (no `gdp_utils` import) | No |
| `gdp_alerts.py` | Alert hub: re-exports + SnapLogic summary email, success/cycle emails, CBRE HTML shell | No |

`.airflowignore` lists every library module so the DAG processor only parses `gdp_dags.py`, `gdp_post_mdm.py`, `gdp_dataset_reset.py`. Library modules are still importable via `sys.path`.

**Rule:** a new library module must (a) register nothing on import and (b) be added to `.airflowignore`. A new DAG family is wired as a `build_dags_for_env(env) -> {dag_id: DAG}` function called from `gdp_dags.py`.

---

## 2. How DAGs are built (parse-time flow)

```mermaid
flowchart TD
  A[gdp_dags.py parsed] --> B[_envs_to_build]
  B --> C[reset_registered_dag_ids]
  C --> D{for env in ENVS}
  D --> E[gdp_pipelines.build_dags_for_env: bronze-only + legacy]
  D --> F[gdp_source_pipelines.build_dags_for_env: source + gold + mdm_property]
  D --> G[gdp_master.build_dags_for_env: status board]
  D --> H[gdp_opensearch.build_dags_for_env: engine DAGs, fail-soft]
  E & F & G & H --> I[_built dict]
  I --> J{env produced 0 DAGs?}
  J -- yes --> K[gdp_PARSE_FAILURE_env canary]
  I --> L[globals dag_id = dag]
```

### 2.1 Env resolution (`_envs_to_build` in `gdp_dags.py`)
Priority:
1. Literal `/dev/`, `/qa/`, `/uat/`, `/prod/` segment in the file path (per-env S3 folder).
2. `GDP_ENVS` (comma list).
3. `GDP_ENV` (single).
4. `gdp_utils._resolve_env()` auto-detect (`AIRFLOW_ENV_NAME`, webserver base URL, remote log folder, file path; word-boundary match; **prod-guard** raises if "prod" seen but not strictly matched).
5. Default `dev,qa,uat` (never prod by default).

If the instance looks like prod but `prod` is not in the build list → `gdp_ENV_MISMATCH` canary DAG.

### 2.2 Per-env global switching
`gdp_utils._set_env(env)` mutates module globals (`GDP_ENV`, `UTIL_DB`, `BRONZE_DB`, `SILVER_DB`, `BATCH_MASTER_FQN`, `SNAPLOGIC_MASTER_FQN`, `DBT_PROJECT_FQN`, Akeyless paths) and clears env-dependent caches. Called:
- at parse time before building each env;
- at task runtime via `_set_env_from_context(context)` which reads the DAG tag `env=<env>`.

**Every DAG must carry the tag `env=<env>`** — tasks use it to target the right env.

### 2.3 Failure semantics at parse time (important design rule)
- Config reads (Snowflake/Akeyless) **raise** → `gdp_dags.py` re-raises → Airflow records Import Error and **keeps last-good DAGs** (no flicker / disappearing DAGs).
- A single bad source inside the loop is caught and logged; siblings continue.
- Gold DAG and MDM coordinator builds are fail-closed (raise).
- `reset_registered_dag_ids()` runs **outside** the try so a failed parse can''t leave stale "already registered" ids.
- Env with genuinely 0 DAGs (empty config) → `gdp_PARSE_FAILURE_<env>` canary.
- OpenSearch builder is fail-soft (returns `{}`).

### 2.4 DAG ID catalogue (all env-suffixed)

| DAG ID | Builder | Trigger |
|---|---|---|
| `gdp_<source>_<env>` | `gdp_utils.build_source_dag` | Cron (`_no_catchup`), Dataset gate (DNB_API), or `None` (manual/isolated) |
| `gdp_<source>_bronze_only_<env>` | `gdp_utils.build_bronze_only_dag` | Cron / manual |
| `gdp_gold_<env>` | `gdp_utils.build_gold_dag` | Dataset AND of gold-feeding sources'' `silver_done` |
| `gdp_mdm_property_<env>` | `gdp_utils.build_mdm_property_trigger_dag` | Dataset AND of property `silver_done` of gating sources |
| `gdp_master_<env>` | `gdp_master.build_dags_for_env` | `GDP_MASTER_SCHEDULE` (default manual) |
| `gdp_engine_<domain>_incremental_<env>` | `gdp_opensearch` | Asset `post_mdm_gold_done` |
| `gdp_engine_<domain>_bulk_<env>` | `gdp_opensearch` | `GDP_ENGINE_BULK_CRON` or manual |
| `gdp_engine_<domain>_reindex_<env>` | `gdp_opensearch` | Manual, paused on creation |
| `gdp_post_mdm_to_gold_<domain>_<env>` | `gdp_post_mdm` (module level loop) | Cron `*/15 * * * *` |
| `admin_reset_dataset_dependencies` | `gdp_dataset_reset` | Manual |
| `gdp_PARSE_FAILURE_<env>`, `gdp_ENV_MISMATCH` | `gdp_dags._build_parse_failure_canary` | Canary only |

Domains for engine/post-MDM: `property`, `company`.

---

## 3. Configuration sources

### 3.1 Snowflake config tables (`<ENV>_GDP_UTIL_DB.CONFIG`)
| Table | Purpose |
|---|---|
| `GDP_SNAPLOGIC_CONFIG_MASTER` | **Source registry**: SOURCE_SYSTEM, DOMAIN, SCHEDULE_CRON, SCHEDULE_FREQUENCY, AIRFLOW_SOURCE_SYSTEM, ACTIVE_FLAG. Drives which source DAGs exist and their domains (`_get_source_domain_map()` → `{SOURCE: [domains]}`) |
| `GDP_BATCH_MASTER` | Write-only audit per run/stage: ORCHESTRATION_ID, BATCH_ID, SNAPLOGIC_*, DBT_*, SODA_*, DBT_GOLD_* status/dates, SNAPLOGIC_RUID |
| `GDP_DAG_DEPENDENCY` | Cross-DAG activation flags |
| `GDP_DAG_MUTEX` | OpenSearch per-domain advisory lock rows |
| `GDP_DBT_<ENV>` | Snowflake DBT PROJECT object |

DB naming: `<ENV>_GDP_UTIL_DB`, `<ENV>_GDP_BRONZE_DB` (schemas `BRONZE_<SOURCE>`), `<ENV>_GDP_SILVER_DB` (schema per domain), `<ENV>_GDP_GOLD_<DOMAIN>_DB`. Role `<ENV>_GDP_DATA_ENGINEER_ROLE`, warehouse `<ENV>_GDP_DATA_ENGINEER_WHS`.

### 3.2 `DOMAIN_CONFIGS` (`gdp_pipelines.py`)
`_BASE_DOMAIN_CONFIGS` is deep-copied into `DOMAIN_CONFIGS_BY_ENV[env]`; read via `get_domain_configs(env)`. Edit one env''s list to diverge it.

Keys per entry:
- `domain` (required), `dbt_command` (default `run`), `use_datasets` (must be True for source/gold topology)
- `soda_datasets` — silver tables to scan
- `silver_select` — source-agnostic silver selector (e.g. geography `tag:geography_silver`)
- `gold_select` — gold selector (e.g. `tag:gold_opportunity`); absent ⇒ silver-only domain
- `soda_bronze_datasets_by_source` — `{SOURCE: {"schema": ..., "datasets": [...]} }`
- `soda_datasets_by_source` — supplemental silver scans per source
- `mdm_trigger_by_source` — Reltio MDM DAG ids with `{env}` placeholder

Domains: company, property (silver-only, MDM-fed), contact, opportunity, finance, lease, sale (gold), geography (inline gold in ATLAS source DAG).

### 3.3 Knobs
`gdp_utils._cfg_get(name, default)`: **env var first**, then Airflow Variable (avoids secrets-backend noise at parse time). `airflow_variable()` bridges Airflow 2/3 `Variable.get` signatures and never raises.

Key env vars / Variables:
- Env: `GDP_ENV`, `GDP_ENVS`
- Scheduling: `GDP_SOURCE_DAG_SCHEDULE_ENABLED`, `GDP_MANUAL_ONLY_SOURCES` (default includes LIGHTBOX, PDL, ATLAS, INTROHIVE, DIGITAL_MARKETING, DNB_FILE), `GDP_DNB_SOURCES`, `GDP_DNB_GATE_EXCLUDE`, `GDP_MDM_PROPERTY_GATE_*`, `GDP_MAX_RUN_AGE_SEC`, `GDP_SCHEDULE_GRACE_SEC`, `GDP_SUPPRESS_UNPAUSE_RUN`
- dbt: `GDP_DBT_OL_BIN`, `GDP_DBT_PROJECT_DIR`, `GDP_DBT_RUN_TIMEOUT_SEC` (6h), `GDP_DBT_TRANSIENT_RETRIES`, `GDP_DBT_GIT_*`, `GDP_DBT_CACHE_*`
- Soda: `GDP_SODA_TIMEOUT_SEC` (600), `GDP_SODA_RUNNER_FLAG`, `GDP_SODA_EMAIL_MODE` (`off`|`failed_only`|`all`)
- SnapLogic: `GDP_SNAPLOGIC_TASK_TIMEOUT_HOURS` (24), `GDP_SNAPLOGIC_POLL_INTERVAL_SEC`, `GDP_SNAPLOGIC_INFLIGHT_MODE` (`resume`), `GDP_SNAPLOGIC_MAX_ERROR_COUNT`, `GDP_SNAPLOGIC_EMAIL_MODE`
- Master: `GDP_MASTER_SCHEDULE`, `GDP_MASTER_POKE_SEC`, `GDP_MASTER_TIMEOUT_SEC`, `GDP_MASTER_INCLUDE_BRONZE_ONLY`, `GDP_MASTER_REQUIRE_ALL_SUCCESS`
- Post-MDM: `GDP_POST_MDM_ES_SYNC_ENVS` (default `dev,qa,uat,prod`), `GDP_POST_MDM_ES_SYNC_WAIT_MIN` (180), `GDP_POST_MDM_PUBLISH_MAX` (20000), `GDP_POST_MDM_BATCH_SIZE` (10000), `GDP_POST_MDM_STALE_RUN_MAX_MIN` (30), `GDP_KAFKA_MAX_RETRIES`
- Engine: `GDP_ENGINE_VERSION` (required, `:latest` refused), `GDP_ENGINE_REGISTRY`, `GDP_ENGINE_DAGS_ENABLED`, `GDP_ENGINE_ENABLED_ENVS`, `GDP_ENGINE_DOMAINS`, `GDP_ENGINE_BULK_CRON`, `GDP_ENGINE_MUTEX_TTL_SEC` (8h), `GDP_ENGINE_DBT_SELECT*`
- Alerts: `GDP_ALERT_EMAILS`, `GDP_SUCCESS_ALERTS_ENABLED`

### 3.4 Secrets (Akeyless)
Path: `/{teams-nonprod|teams}/global-data-platform-gdp/<env>/airflow/<leaf>` with leaves `snowflake-secrets`, `soda-secrets`, `snaplogic`, `snaplogic-polling` (UAT polling reuses QA), `kafka-secrets`. Auth = AWS IAM cloud-id; access id per env (`GDP_AKEYLESS_ACCESS_ID` override). Token cached ~50 min per access id (thread-safe). Snowflake uses key-pair auth (`_get_sf_connection`, keep-alive, 2h network/statement timeout); `_sf_run` uses cached connections — **only retry idempotent reads, never DML**.

---

## 4. Source DAG (`gdp_<source>_<env>`) — `build_source_dag`

```mermaid
flowchart LR
  init_batch --> validate_source --> snaplogic_start --> trigger_snaplogic --> snaplogic_end
  snaplogic_end --> soda_bronze
  snaplogic_end --> dbt_silver_start --> S[dbt_silver_company -> property -> contact -> opportunity+finance -> lease -> sale]
  S --> M[dbt_&lt;domain&gt;_silver_end outlets gdp://env/source/domain/silver_done]
  S --> silver_end[silver_end outlets gdp://env/source/silver_done]
  silver_end --> G[inline gold: gold_start -> dbt_gold_geography -> gold_end]
  silver_end & soda_bronze & G --> pipeline_done
```

- `init_batch` (`init_batch_task`): inserts `GDP_BATCH_MASTER` row, pushes `batch_id`/`orchestration_id` XCom, **skips stale scheduled runs** (`skip_if_stale_run`, `GDP_MAX_RUN_AGE_SEC`).
- `validate_source`: source must be ACTIVE in `GDP_SNAPLOGIC_CONFIG_MASTER`.
- `snaplogic_start` / `snaplogic_end` / `dbt_silver_start`: `update_stage_task(stage, status)` markers; `snaplogic_end` also sends SnapLogic summary email and is the hard stop on failure.
- `trigger_snaplogic`: `gdp_snaplogic.build_snaplogic_trigger_task` — `retries=0` (side-effecting), timeout 24h + 15 min buffer.
- `soda_bronze`: one consolidated scan of `BRONZE_<SOURCE>` curated tables, `ALL_DONE`, warn-and-continue.
- Silver dbt chain in strict FK order: company → property → contact → [opportunity, finance] → lease → sale; only domains the source feeds get tasks; `ALL_SUCCESS`.
- Signal domains (`_SILVER_SIGNAL_DOMAINS`: company, property) get per-domain marker tasks emitting `gdp://<env>/<source>/<domain>/silver_done`.
- `pipeline_done` (`pipeline_done_task`, `ALL_DONE`): final status to batch master + notification. **The master DAG sensors watch this task id** — never rename it.

Scheduling rules (`gdp_source_pipelines.build_dags_for_env`):
- Isolated sources (`_ISOLATED_SOURCES`, e.g. DNB_FILE): `schedule=None`, `isolated=True` → no outlets, no silver markers, no MDM trigger, never gate gold.
- DNB targets (`_DNB_TARGET_SOURCES`, DNB_API): Dataset-gated on every other company source''s `company/silver_done` (`_dnb_gate_schedule`, excludes `GDP_DNB_GATE_EXCLUDE`).
- Manual-only (`_MANUAL_ONLY_SOURCES`): `schedule=None`.
- On-demand (`_ON_DEMAND_SOURCES`): built paused.
- Otherwise `_resolve_source_schedule(src)` (cron + frequency) wrapped by `_no_catchup` (CronTriggerTimetable, no catch-up; `_no_catchup_start_date` suppresses missed ticks).
- Gold gate only includes sources feeding a `gold_select` domain that is not inline-gold (so quarterly LIGHTBOX / monthly ATLAS don''t starve daily gold).

## 5. Gold DAG (`gdp_gold_<env>`) — `build_gold_dag`
Schedule = list of `_source_silver_done_dataset(src, env)` (Dataset AND). Only built sources are listed (a Dataset with no producer would hang it forever).

`init_batch → soda_silver (consolidated, once for platform) → soda_post_mdm (COMPANY_MDM/PROPERTY_MDM) → gold_start → dbt_gold_<domain> (parallel: opportunity, finance, contact, lease, sale, supplemental) → gold_end → soda_gold → notify_* (optional, gdp_alerts) → pipeline_done`.
Batch row uses sentinel `source_system="gold"`, `domain="all_domains"`. Gold-DAG Soda never decides pass/fail.

## 6. MDM property coordinator (`gdp_mdm_property_<env>`)
Waits on `property/silver_done` of every gating source (default excludes LIGHTBOX, isolated, unbuilt) and fires the single Reltio MDM property DAG via `TriggerDagRunOperator`. Replaced four legacy per-source `trigger_mdm_property` tasks. Not registered if gate resolves empty.

## 7. Master status board (`gdp_master_<env>`)
Observes, never launches. `begin → status_gdp_<src>` (TaskGroup) / `status_gdp_<src>_bronze_only`; company sources → `status_gdp_dnb_api`; all sources → `status_gdp_gold` → `all_pipelines_complete` (`ALL_DONE`, or `ALL_SUCCESS` if `GDP_MASTER_REQUIRE_ALL_SUCCESS=1`).
Sensors: `ExternalTaskSensor(external_task_id="pipeline_done", execution_date_fn=_latest_logical_date_fn(dag_id), mode="reschedule", check_existence=True, failed_states=[failed, upstream_failed, skipped])` — watches each DAG''s **latest** run (cron-independent). Layout via `_resolve_layout(env)`; fails hard.

## 8. Post-MDM → OpenSearch → Kafka (Asset chain)

```mermaid
sequenceDiagram
  participant PM as gdp_post_mdm_to_gold_<d>_<env> (*/15)
  participant EN as gdp_engine_<d>_incremental_<env>
  PM->>PM: check_incremental_data (ShortCircuit on VW_GOLD_<D>_INC_CHECK, stale gate 30m)
  PM->>PM: dbt_gold_<d> (EXECUTE DBT PROJECT tag:gold_<d> --exclude tag:es_sync)
  PM-->>EN: Asset gdp://env/d/post_mdm_gold_done
  EN->>EN: mutex_gate -> dbt_<d>_es_doc -> gdp_engine pod -> finalize (NONE_FAILED)
  EN-->>PM: Asset gdp://env/d/es_sync_done extra{post_mdm_run_ids,status}
  PM->>PM: wait_es_sync (PythonSensor reschedule, 180 min) 
  PM->>PM: dbt_kafka_outbox_<d> (tag:kafka_<d>) -> publish_kafka
```

### 8.1 `gdp_post_mdm.py` (self-contained)
- Module-level loop over `DOMAIN_CONFIG` (company, property): `inc_check_view`, `gold_table`, `dbt_select`, `kafka_dbt_select`, `kafka_outbox_table` (`<UTIL_DB>.AUDIT.KAFKA_OUTBOX_<D>`), `kafka_bulk_view`.
- `max_active_runs=1`, `catchup=False`, DAG-level `notify_dag_failure`; task-level `notify_failure` on dbt gold; `notify_recovery` on `publish_kafka` success.
- `wait_es_sync` only if `ENV in ES_SYNC_ENVS`; extra.status `failed` → fail, `skipped` → skip. Kafka tasks are `ALL_SUCCESS` so Kafka never runs ahead of search; nothing is lost (watermark + outbox).
- `publish_kafka`: claim PENDING (+ ERROR under retry limit) rows; > `PUBLISH_MAX_RECORDS` ⇒ BULK mode (park + alert, succeed); else STREAM in chunks of `BATCH_SIZE` via Confluent REST v3 (`/kafka/v3/clusters/{cluster}/topics/{topic}/records`, Azure AD OAuth, urllib3 Retry on 429/5xx), commit per chunk (crash-safe), audit `KAFKA_PUBLISH_LOG`. Topic: nonprod `{account}-gdp-{env}-{domain}-batch`, prod from secret.
- Timeouts: Snowflake `STATEMENT_TIMEOUT_IN_SECONDS` fires first (6h); Airflow `execution_timeout` = publish timeout + buffer is the backstop.
- Emits OpenLineage START/COMPLETE/FAIL for Datadog; parses dbt node lines from `SYSTEM$GET_DBT_LOG`.

### 8.2 `gdp_opensearch.py`
- Modes `incremental | bulk | reindex`; domains `property | company`; `ENABLED_ENVS`, `ENABLED_DOMAINS`.
- Task graph: `mutex_gate → dbt_<d>_es_doc → gdp_engine_<mode> → finalize`; incremental adds `signal_es_sync_failed` (`ONE_FAILED`) emitting es_sync_done status=failed so post-MDM fails fast.
- Mutex: row in `<UTIL_DB>.CONFIG.GDP_DAG_MUTEX`, lock `<domain>_es_sync`, TTL reaper + `INSERT ... WHERE NOT EXISTS`; loser raises `AirflowSkipException`. `finalize` (`NONE_FAILED`) releases only if this run acquired. Optional secondary pool `GDP_ENGINE_MUTEX_POOL` (must exist or tasks queue forever).
- es-doc dbt: `EXECUTE DBT PROJECT ... --select <domain>_es_doc`; bulk/reindex batched with `--vars {full_load, batch_mod, total_batches}`; pool `gdp_heavy`.
- `GdpEnginePodOperator` (KubernetesPodOperator subclass): image `<registry>/gdp-engine/<env>:<version>` (Jenkins tokens `{CONTAINER_REGISTRY_HOST}`/`{RECENT_PUSHED_VERSION}`, per-env fallbacks), cmd `python main.py run --pipeline sync_<d>_es --mode <mode>`, namespace `gdp-nonprod-<env>` / `gdp-prod-ns`, kubeconfig materialized from Akeyless to a temp file, non-root (uid/gid 1000), node selector `memorymax`, sizing **only** via `ENGINE_POD[domain][mode]` constants (namespace quota: req 40 CPU/80Gi, lim 80 CPU/320Gi, 20 pods). `cleanup` reads base-container logs, parses engine JSON lines → XCom `engine_failure_details` → failure email.
- Engine exit codes: 0 ok, 1 systemic (no progress kept), 2 partial (progress kept), 130 interrupted.

---

## 9. Building blocks (task factories)

### dbt — `gdp_dbt.dbt_run_task(model=None, task_id=None, dbt_command="run", pool=None, domain=None, models=None, entity_ids=False, model_batches=None, silver_selector=False, is_gold=False, silver_select=None)`
1. Resolve batches (model_batches → models → model).
2. Silver selector `tag:<src_alias>_<domain>_silver tag:<domain>_merged_pipeline` or `silver_select`.
3. Fetch dbt project from Snowflake GIT REPOSITORY into a per-(branch, sha) cache (`/tmp/.gdp_dbt_project_cache`, file lock, atomic rename, `dir` GET mode to avoid rate limit 093563, prune with grace > longest run). Partial-parse msgpack reused.
4. Materialize key-pair PEM, patch `profiles.yml`.
5. Run `dbt-ol run --select execute_via_snowflake` (wrapper model → `EXECUTE DBT PROJECT` server-side); extra vars via `dbt_vars` → `model_vars`.
6. Transient errors (e.g. "Operations rate limit exceeded") retried in place with linear backoff (`GDP_DBT_TRANSIENT_RETRIES`, 45s base). Wall-clock deadline on reader thread.
7. Per-model results logged from `SYSTEM$GET_DBT_LOG(job_id)`; OpenLineage to Datadog.
Pools: `_dbt_pool_for_domain()` → `gdp_heavy` for all dbt; `_gdp_pool()` for Soda.

### Soda — `gdp_soda`
- `soda contract verify --dataset <ds> --use-runner --soda-cloud <cfg> --publish` subprocess (`start_new_session=True`, timeout 600s), Soda Cloud creds from Akeyless (redacted logging).
- Outcome via `_classify_soda_outcome` → PASSED/FAILED/SKIPPED/ERROR; bronze FAILED downgraded to WARN.
- **Warn-and-continue**: Soda tasks never fail the DAG.
- Consolidated tasks: `soda_consolidated_scan_task` (bronze per source), `soda_silver_consolidated_scan_task`, `soda_post_mdm_scan_task`, `soda_gold_all_scan_task`. Curated allow-lists only (`BRONZE_/SILVER_/GOLD_/MDM_SODA_DATASETS` in `gdp_utils`), env-aware Soda data-source prefixes (`_SODA_DATA_SOURCE_BY_ENV`).
- Digest email inline (`_send_soda_scan_digest`) gated by `GDP_SODA_EMAIL_MODE` (default off). Structured Datadog JSON event per dataset.

### SnapLogic — `gdp_snaplogic._trigger_and_wait`
- Secrets: `snaplogic` (url, trigger_path, bearer_token) and `snaplogic-polling` (status_url, status_token).
- Trigger POST with query params SOURCE_SYSTEM, DOMAIN, BATCH_ID, airflow_dag_name, airflow_dag_run_id; retry **only** on 429/safe connect errors (never on timeout/5xx — may have triggered).
- **Idempotency:** in-flight RUID in batch master with SNAPLOGIC_STATUS=RUNNING is resumed (`GDP_SNAPLOGIC_INFLIGHT_MODE=resume`) instead of re-triggering after worker loss.
- Poll until terminal; success requires 2 consecutive `completed` observations; children polled recursively under a shared deadline; failure states include failed/stopped/aborted/cancelled/skipped (override `GDP_SNAPLOGIC_TREAT_SKIPPED_AS_SUCCESS`); `error_count > GDP_SNAPLOGIC_MAX_ERROR_COUNT` fails.
- Readable failure report (`_log_snaplogic_failure_report`), secrets redacted in response dumps.

### Batch master / stage tasks (`gdp_utils`)
`init_batch_task`, `validate_source_task`, `update_stage_task(stage, status)`, `pipeline_done_task`, `reconcile_batch_status_task`. Batch master is **write-only audit** — never read back to drive control flow (except SnapLogic in-flight resume).

### Defaults
`START_DATE = pendulum.datetime(2024,1,1, tz="America/Chicago")`; `DEFAULT_ARGS`: owner `gdp-team`, `retries=0`, `execution_timeout=2h`, `post_execute=_post_task_sleep` (5s CloudWatch log flush). `max_active_runs=1` typical.

---

## 10. Alerting

- **Task-level callbacks send failure emails** (`pipeline_failure_callback`, `gdp_notify.notify_failure`). On Airflow 3 the DAG-level `on_failure_callback` runs in the DAG processor; its `ti` is NOT the failed task and SDK DagRun lacks `get_task_instances`. `notify_dag_failure` uses `ti.get_task_states` and dedupes via XCom `failure_alert_sent`.
- Recipients: Variable `GDP_ALERT_EMAILS` → env `GDP_ALERT_EMAILS` → `AIRFLOW_VAR_GDP_ALERT_EMAILS` → last-good → default. `parse_email_list` accepts comma/semicolon/JSON/`Name <addr>`.
- Threading: one open thread per DAG in Variable `gdp_alert_thread__<dag_id>`; replies `RE: <subject>` with Thread-Topic/Thread-Index (MS-OXOMSG); Message-IDs unique per email (SES rewrites; Exchange drops duplicates). Recovery (`notify_recovery`, `pipeline_done`) replies "RESOLVED" and closes thread.
- Dedupe keys `dag::run::task::try`; intermediate retries skipped; stale-skipped runs suppressed.
- `gdp_alerts`: SnapLogic summary email (`GDP_SNAPLOGIC_EMAIL_MODE`), success/cycle emails (`GDP_SUCCESS_ALERTS_ENABLED`, `GDP_CYCLE_LOOKBACK_HOURS`), CBRE shell (brand `#003F2D`), `gdp_subject(env, headline)`.
- Alerts must never raise into tasks (wrap in try/except).

---

## 11. Recipes

### Add a new source
1. Insert ACTIVE rows (one per domain) in `<ENV>_GDP_UTIL_DB.CONFIG.GDP_SNAPLOGIC_CONFIG_MASTER` with SCHEDULE_CRON + AIRFLOW_SOURCE_SYSTEM.
2. Add bronze Soda tables to `BRONZE_SODA_DATASETS["BRONZE_<SRC>"]` or `soda_bronze_datasets_by_source` in the domain config.
3. If it feeds property/company MDM, add `mdm_trigger_by_source` / check MDM gate exclusions.
4. Manual-only? add to `_MANUAL_ONLY_SOURCES`; isolated? `_ISOLATED_SOURCES`; on-demand? `_ON_DEMAND_SOURCES`.
5. Add to `_FALLBACK_ALLOWED_SOURCES` if needed. Next parse builds `gdp_<src>_<env>`; master/gold gates update automatically.

### Add a new domain
Add entry to `_BASE_DOMAIN_CONFIGS` (domain, `use_datasets=True`, `soda_datasets`, optional `gold_select`), place it in the silver FK order, add `SILVER_/GOLD_SODA_DATASETS`, ensure dbt tags `<alias>_<domain>_silver`, `<domain>_merged_pipeline`, `gold_<domain>` exist.

### Add a new DAG family
Library module with `build_dags_for_env(env) -> dict`, no import-time registration, tag `env=<env>`, add to `.airflowignore`, call it in the env loop in `gdp_dags.py`. Config failures must raise (keep last-good), per-item failures may be swallowed.

### Change engine pod size
Edit `ENGINE_POD[domain][mode]` in `gdp_opensearch.py` only; stay within namespace quota; run `tests/verify_engine_autosize.py`.

### Clear stuck Asset queue
Trigger `admin_reset_dataset_dependencies` with `target_dag_id` (or `clear_all=true`), `dry_run=true` first (uses MWAA `invoke_rest_api`).

---

## 12. Gotchas
- Never rename `pipeline_done` or the `env=<env>` tag.
- Never gate a Dataset schedule on a source whose DAG wasn''t built (hangs forever) — builders intersect with `built_sources`.
- SnapLogic trigger and Kafka publish are side-effecting: `retries=0`, idempotency via batch master / outbox status.
- `_sf_run(retries=...)` only for reads.
- Missing Airflow pool ⇒ task queued forever (`gdp_heavy`, `GDP_ENGINE_MUTEX_POOL`).
- `_cfg_get` reads env vars before Variables on purpose (parse-time secrets backend noise).
- DNB_FILE is isolated + manual; DNB_API alone is Dataset-gated.
- Default env list never includes prod; prod needs folder `/prod/`, `GDP_ENVS=prod` or `GDP_ENV=prod`.
- Engine `:latest` image tag is refused.

## 13. Tests (`gdp-airflow-dags/tests/`)
Mostly AST-extract scripts — no Airflow needed: `python tests/verify_<name>.py`.
- `verify_env_resolution.py`, `validate_env_isolation.py` — env detection & isolation
- `verify_cross_module_imports.py` — import coupling
- `verify_dbt_model_logging.py`, `verify_dbt_ol_changes.py`, `verify_dbt_transient_retry.py`, `verify_es_doc_dbt_sql.py` — dbt
- `verify_dnb_source_parity.py` — DNB isolation/gating
- `verify_email_list_parsing.py`, `verify_failure_logs.py`, `verify_engine_failure_email.py` — alerts
- `verify_engine_autosize.py` — pod sizing vs quota
- `verify_es_sync_paused_skip.py` — wait_es_sync when engine paused (4 known pre-existing FAILs)
- `test_email_notification.py` (one known pre-existing failure), `test_soda.py`, `test_snowflake_connection.py`, `test_snaplogic_connection.py` — DAG-style smoke tests run in Airflow
- `preview_gdp_cycle_email.py` — render cycle email HTML

Deps: `env-conf/requirements.txt` (+ `constraints.txt`), providers snowflake, amazon, smtp, http, openlineage, cncf-kubernetes; dbt-ol in a separate venv set up by `env-conf/startup.sh`.', NULL, TRUE, 'SEED', 1, 'REPOSITORY', 'domain-standards', NULL, 'Repository version' WHERE NOT EXISTS (SELECT 1 FROM {{database}}.CORE.SKILL_REGISTRY WHERE SKILL_ID = 'ebf136a7-05a6-5980-d2a0-26fb367449a2');
MERGE INTO {{database}}.CORE.SKILL_LABEL T USING (SELECT 'GDP-AIRFLOW-DAGS' AS SKILL_NAME, 'production' AS LABEL, 'ebf136a7-05a6-5980-d2a0-26fb367449a2' AS SKILL_ID) S ON T.SKILL_NAME = S.SKILL_NAME AND T.LABEL = S.LABEL WHEN MATCHED THEN UPDATE SET SKILL_ID = S.SKILL_ID, MOVED_BY = 'SEED', MOVED_AT = CURRENT_TIMESTAMP(), NOTE = 'Repository deploy' WHEN NOT MATCHED THEN INSERT (SKILL_NAME, LABEL, SKILL_ID, MOVED_BY, NOTE) VALUES (S.SKILL_NAME, S.LABEL, S.SKILL_ID, 'SEED', 'Repository deploy');
MERGE INTO {{database}}.CORE.SKILL_STAGE_BINDING T USING (SELECT 'OPS' AS STAGE, 'GDP-AIRFLOW-DAGS' AS SKILL_NAME) S ON T.STAGE = S.STAGE AND T.SKILL_NAME = S.SKILL_NAME WHEN NOT MATCHED THEN INSERT (STAGE, SKILL_NAME, ENABLED, POSITION, STANDARD, PIPELINE, UPDATED_BY) VALUES (S.STAGE, S.SKILL_NAME, TRUE, 10, 'ANY', 'ANY', 'SEED');

INSERT INTO {{database}}.CORE.DOMAIN_KNOWLEDGE (KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, TITLE, CONTENT, CONTENT_JSON, TAGS, SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY, LINEAGE_ID, ORIGIN, CHANGE_NOTE) SELECT '2c929b31-a826-78bc-8e77-9f829e1952cc', (SELECT DOMAIN_ID FROM {{database}}.CORE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = 'GENERAL' LIMIT 1), 'AIRFLOW_RUNBOOK', 'SnapLogic gate stopped the DAG (snaplogic_end: trigger_snaplogic did not succeed)', 'Signals: ''AirflowException: trigger_snaplogic did not succeed -- hard-stopping dbt via snaplogic_end''. Meaning: snaplogic_end is a guard task. It fails on purpose when trigger_snaplogic did not succeed (or did not push its snaplogic_details XCom), so the dbt tasks do not run on a partial load. It is never the root cause. Usual causes: the SnapLogic pipeline itself failed: a rejected bearer token (401 ''Mismatched bearer token''), an Exit snap threshold exceeded by error documents, a Snaplex outage, or trigger_snaplogic killed before it wrote its XCom. Repair: open trigger_snaplogic in the same run and read its error (it names the SnapLogic runtime id, ruuid); repair that with its own runbook; then clear trigger_snaplogic with downstream so snaplogic_end and dbt run again. Rerunning snaplogic_end alone fails again.', PARSE_JSON('{"signals": ["trigger_snaplogic did not succeed", "hard-stopping dbt via snaplogic_end"], "meaning": "A guard task stopped the DAG because the SnapLogic trigger task did not succeed; the cause is in trigger_snaplogic.", "usual_cause": "SnapLogic pipeline failure: rejected bearer token, Exit snap threshold exceeded, Snaplex outage, or the trigger task killed before writing its XCom.", "action": null, "human_steps": ["Open trigger_snaplogic in the same DAG run and read its error and the SnapLogic runtime id (ruuid).", "Fix the SnapLogic failure with its own runbook (bearer token, Exit threshold, Snaplex).", "Clear trigger_snaplogic with downstream so snaplogic_end and the dbt tasks rerun; do not rerun snaplogic_end alone."], "related": ["ops.runbook.snaplogic-pipeline-error"], "evidence": {"runs": 25, "dag_count": 20, "dags": ["gdp_cornerstone_dev", "gdp_edp_qa", "gdp_tpc_qa", "gdp_news_to_leads_dev", "gdp_zoominfo_qa", "gdp_cornerstone_qa", "gdp_spoc_qa", "gdp_zoominfo_5cubes_qa", "gdp_dealiq_qa", "gdp_tcc_qa"], "tasks": ["gdp_cornerstone_dev.snaplogic_end", "gdp_edp_qa.snaplogic_end", "gdp_tpc_qa.snaplogic_end", "gdp_news_to_leads_dev.snaplogic_end", "gdp_zoominfo_qa.snaplogic_end", "gdp_cornerstone_qa.snaplogic_end", "gdp_spoc_qa.snaplogic_end", "gdp_zoominfo_5cubes_qa.snaplogic_end", "gdp_dealiq_qa.snaplogic_end", "gdp_tcc_qa.snaplogic_end"], "sources": ["INCIDENT", "TASK_RUN"], "first_seen": "2026-10-21T19:23:42+00:00", "last_seen": "2026-10-24T20:12:29+00:00", "incidents": 22} }'), PARSE_JSON('["AIRFLOW", "RUNBOOK", "LEARNED", "SNAPLOGIC", "DEPENDENCY"]'), 'ops.runbook.gdp.snaplogic-gate-hard-stop', 'ACTIVE', 1, TRUE, 'SEED', '99946bd253fa604ce56c2f99db1d256f', 'OPS', 'Airflow error pack (learned runtime runbooks)' WHERE NOT EXISTS (SELECT 1 FROM {{database}}.CORE.DOMAIN_KNOWLEDGE WHERE SOURCE_REFERENCE = 'ops.runbook.gdp.snaplogic-gate-hard-stop' AND IS_CURRENT);
INSERT INTO {{database}}.CORE.DOMAIN_KNOWLEDGE (KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, TITLE, CONTENT, CONTENT_JSON, TAGS, SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY, LINEAGE_ID, ORIGIN, CHANGE_NOTE) SELECT 'aad4ce72-beb7-2192-e5f0-23f75ce28b8a', (SELECT DOMAIN_ID FROM {{database}}.CORE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = 'GENERAL' LIMIT 1), 'AIRFLOW_RUNBOOK', 'Post-MDM to gold: upstream engine DAG failed, not publishing to Kafka', 'Signals: ''AirflowFailException: [post-mdm-property] (or [post-mdm-company]) gdp_engine_<entity>_incremental_<env> FAILED for run ...; not publishing to Kafka''. Meaning: the gdp_post_mdm_to_gold_<entity> DAG waits (wait_es_sync) for the engine DAG''s es_sync_done event for the same run. The engine DAG failed, so the wait task fails fast and nothing is published to Kafka. Usual causes: the engine DAG''s Kubernetes task (sync to OpenSearch) failed: container exit, pod not found during cleanup, pod unschedulable, or a dbt failure in the gold OpenSearch models. Repair: open the engine DAG run named in the message and fix it first (see the Kubernetes and dbt runbooks). Once that run succeeds, clear wait_es_sync with downstream in the post-MDM DAG. Do not mark wait_es_sync success by hand: Kafka would get events for data that was not synced.', PARSE_JSON('{"signals": ["not publishing to Kafka", "[post-mdm-"], "meaning": "The post-MDM DAG stopped because the upstream engine DAG failed for the same run.", "usual_cause": "A failure in gdp_engine_<entity>_incremental_<env>: Kubernetes sync task or gold dbt models.", "action": null, "human_steps": ["Open the engine DAG run named in the message and find its failed task.", "Fix and rerun the engine DAG until it succeeds.", "Clear wait_es_sync with downstream in the post-MDM DAG; never mark it success by hand."], "evidence": {"runs": 17, "dag_count": 3, "dags": ["gdp_post_mdm_to_gold_property_qa", "gdp_post_mdm_to_gold_company_qa", "gdp_post_mdm_to_gold_property_dev"], "tasks": ["gdp_post_mdm_to_gold_property_qa.wait_es_sync", "gdp_post_mdm_to_gold_company_qa.wait_es_sync", "gdp_post_mdm_to_gold_property_dev.wait_es_sync"], "sources": ["INCIDENT", "TASK_RUN"], "first_seen": "2026-10-21T14:23:33+00:00", "last_seen": "2026-10-24T20:06:38+00:00", "incidents": 3} }'), PARSE_JSON('["AIRFLOW", "RUNBOOK", "LEARNED", "DEPENDENCY", "KAFKA"]'), 'ops.runbook.gdp.post-mdm-upstream-engine-failed', 'ACTIVE', 1, TRUE, 'SEED', '621f7a6db4efd2d30f0c49a4341dbde2', 'OPS', 'Airflow error pack (learned runtime runbooks)' WHERE NOT EXISTS (SELECT 1 FROM {{database}}.CORE.DOMAIN_KNOWLEDGE WHERE SOURCE_REFERENCE = 'ops.runbook.gdp.post-mdm-upstream-engine-failed' AND IS_CURRENT);
INSERT INTO {{database}}.CORE.DOMAIN_KNOWLEDGE (KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, TITLE, CONTENT, CONTENT_JSON, TAGS, SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY, LINEAGE_ID, ORIGIN, CHANGE_NOTE) SELECT '4a8edd59-3921-8bca-03ef-7ff9a7a91250', (SELECT DOMAIN_ID FROM {{database}}.CORE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = 'GENERAL' LIMIT 1), 'AIRFLOW_RUNBOOK', 'pipeline_done: pipeline marked FAILED in GDP_BATCH_MASTER (a stage failed earlier)', 'Signals: ''ValueError: [pipeline-done] Pipeline marked FAILED in GDP_BATCH_MASTER (overall_status=FAILED; stage failures: SnapLogic=FAILED | dbt (silver)=FAILED)'', ''Failing this task to ensure the DAG run state is FAILED in the Airflow UI''. Meaning: pipeline_done is the summary task of the GDP ingestion DAGs. It reads the batch''s stage results from GDP_BATCH_MASTER and fails on purpose so the DAG run shows FAILED. The ValueError is not a code bug. Usual causes: an earlier stage failed; the message names it (SnapLogic=FAILED: see the SnapLogic gate and SnapLogic runbooks; dbt (silver)=FAILED: see the dbt task''s log, often a transient Snowflake error or a lock on a shared silver table). Repair: find the failed stage task in the same run, fix it with its own runbook, then clear that stage task with downstream; pipeline_done reruns and passes once GDP_BATCH_MASTER shows the stage succeeded.', PARSE_JSON('{"signals": ["[pipeline-done] Pipeline marked FAILED", "Failing this task to ensure the DAG run state is FAILED"], "meaning": "The DAG''s summary task failed on purpose because an earlier stage (SnapLogic or dbt) failed for this batch.", "usual_cause": "A failed SnapLogic or dbt (silver) stage, named in ''stage failures:''.", "action": null, "human_steps": ["Read ''stage failures:'' in the message to find the failed stage.", "Open that stage''s task in the same run and fix it with its own runbook.", "Clear the failed stage task with downstream; pipeline_done reruns after it."], "related": ["ops.runbook.python-task-error", "ops.runbook.snaplogic-pipeline-error"], "evidence": {"runs": 14, "dag_count": 11, "dags": ["gdp_cornerstone_dev", "gdp_news_to_leads_dev", "gdp_spoc_qa", "gdp_news_to_leads_qa", "gdp_zoominfo_qa", "gdp_edp_qa", "gdp_mta_qa", "gdp_rcoe_qa", "gdp_tcc_qa", "gdp_lightbox_dev"], "tasks": ["gdp_cornerstone_dev.pipeline_done", "gdp_news_to_leads_dev.pipeline_done", "gdp_spoc_qa.pipeline_done", "gdp_news_to_leads_qa.pipeline_done", "gdp_zoominfo_qa.pipeline_done", "gdp_edp_qa.pipeline_done", "gdp_mta_qa.pipeline_done", "gdp_rcoe_qa.pipeline_done", "gdp_tcc_qa.pipeline_done", "gdp_lightbox_dev.pipeline_done"], "sources": ["INCIDENT", "TASK_RUN"], "first_seen": "2026-10-21T20:00:49+00:00", "last_seen": "2026-10-24T20:54:47+00:00", "incidents": 11} }'), PARSE_JSON('["AIRFLOW", "RUNBOOK", "LEARNED", "DEPENDENCY"]'), 'ops.runbook.gdp.pipeline-done-marked-failed', 'ACTIVE', 1, TRUE, 'SEED', '3ad727574544c33e23368b279c209523', 'OPS', 'Airflow error pack (learned runtime runbooks)' WHERE NOT EXISTS (SELECT 1 FROM {{database}}.CORE.DOMAIN_KNOWLEDGE WHERE SOURCE_REFERENCE = 'ops.runbook.gdp.pipeline-done-marked-failed' AND IS_CURRENT);
INSERT INTO {{database}}.CORE.DOMAIN_KNOWLEDGE (KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, TITLE, CONTENT, CONTENT_JSON, TAGS, SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY, LINEAGE_ID, ORIGIN, CHANGE_NOTE) SELECT 'b6c9a176-4dd0-b0f9-706a-bd882781481b', (SELECT DOMAIN_ID FROM {{database}}.CORE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = 'GENERAL' LIMIT 1), 'AIRFLOW_RUNBOOK', 'Kubernetes pod container failed (sync-property-es exit code 1)', 'Signals: "''message'': ''sync-property-es: failed''", "''terminated'': {''container_id''", "''exit_code'': 1", pod phase Failed. Meaning: the container that syncs property documents to OpenSearch (gdp_engine_property_incremental_<env>) exited with an error. Airflow only sees the pod status. Usual causes: the sync job failed on OpenSearch (cluster unavailable, index mapping rejected a document, bulk request rejected, credentials), on its Snowflake read, or ran much longer than usual and was stopped. Repair: read the container''s own log (the pod log in the task log, or Datadog for the pod name), fix what it reports, then rerun the task. A rerun alone is reasonable when the log shows an OpenSearch or network timeout.', PARSE_JSON('{"signals": ["sync-property-es", "''terminated'': {''container_id''"], "meaning": "The OpenSearch sync container in the Kubernetes pod exited with an error.", "usual_cause": "OpenSearch unavailable or rejecting documents, a Snowflake read failure, or a run far longer than usual.", "action": null, "human_steps": ["Read the container log of the pod (task log or Datadog by pod name).", "Check OpenSearch cluster health and index mapping errors.", "Rerun the task after the fix; one rerun is fine for a timeout."], "evidence": {"runs": 10, "dag_count": 1, "dags": ["gdp_engine_property_incremental_qa"], "tasks": ["gdp_engine_property_incremental_qa.gdp_engine_incremental"], "sources": ["INCIDENT", "TASK_RUN"], "first_seen": "2026-10-22T18:12:27+00:00", "last_seen": "2026-10-23T06:52:32+00:00", "incidents": 1} }'), PARSE_JSON('["AIRFLOW", "RUNBOOK", "LEARNED", "KUBERNETES", "OPENSEARCH"]'), 'ops.runbook.gdp.k8s-container-exit-sync-es', 'ACTIVE', 1, TRUE, 'SEED', '9945edc061495d3338d8c9355e5035dd', 'OPS', 'Airflow error pack (learned runtime runbooks)' WHERE NOT EXISTS (SELECT 1 FROM {{database}}.CORE.DOMAIN_KNOWLEDGE WHERE SOURCE_REFERENCE = 'ops.runbook.gdp.k8s-container-exit-sync-es' AND IS_CURRENT);
INSERT INTO {{database}}.CORE.DOMAIN_KNOWLEDGE (KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, TITLE, CONTENT, CONTENT_JSON, TAGS, SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY, LINEAGE_ID, ORIGIN, CHANGE_NOTE) SELECT '7d61cc5b-1b0a-7639-1b7c-2d57428a0996', (SELECT DOMAIN_ID FROM {{database}}.CORE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = 'GENERAL' LIMIT 1), 'AIRFLOW_RUNBOOK', 'SnapLogic trigger rejected: 401 Mismatched bearer token', 'Signals: ''Exception: SnapLogic trigger HTTPError 401 UNAUTHORIZED'', ''"message": "Mismatched bearer token"''. Meaning: the SnapLogic triggered task refused the bearer token the DAG sent. Usual causes: the triggered task was recreated or its token regenerated in SnapLogic, while the Airflow connection or the Akeyless secret (under the environment''s AKEYLESS_BASE_PATH) still holds the old one; or the DAG points at another environment''s task URL. Repair: a human step. In SnapLogic Manager open the triggered task, copy its current bearer token, update the Airflow connection or Akeyless secret for this environment, then clear trigger_snaplogic with downstream. Retries do not help: every try fails the same way.', PARSE_JSON('{"signals": ["Mismatched bearer token", "SnapLogic trigger HTTPError"], "meaning": "SnapLogic rejected the triggered task''s bearer token.", "usual_cause": "Token regenerated or task recreated in SnapLogic without updating the Airflow connection / Akeyless secret, or a URL for another environment.", "action": null, "human_steps": ["Open the triggered task in SnapLogic Manager and copy its bearer token.", "Update the Airflow connection or the Akeyless secret under this environment''s AKEYLESS_BASE_PATH.", "Clear trigger_snaplogic with downstream."], "related": ["ops.runbook.auth-expired"], "evidence": {"runs": 5, "dag_count": 2, "dags": ["gdp_cornerstone_dev", "gdp_news_to_leads_dev"], "tasks": ["gdp_cornerstone_dev.trigger_snaplogic", "gdp_news_to_leads_dev.trigger_snaplogic"], "sources": ["INCIDENT", "TASK_RUN"], "first_seen": "2026-10-21T20:00:37+00:00", "last_seen": "2026-10-24T20:09:32+00:00", "incidents": 2} }'), PARSE_JSON('["AIRFLOW", "RUNBOOK", "LEARNED", "SNAPLOGIC", "AUTH"]'), 'ops.runbook.gdp.snaplogic-bearer-token', 'ACTIVE', 1, TRUE, 'SEED', '706c92318d169d78bdb1d7e13ddcd4de', 'OPS', 'Airflow error pack (learned runtime runbooks)' WHERE NOT EXISTS (SELECT 1 FROM {{database}}.CORE.DOMAIN_KNOWLEDGE WHERE SOURCE_REFERENCE = 'ops.runbook.gdp.snaplogic-bearer-token' AND IS_CURRENT);
INSERT INTO {{database}}.CORE.DOMAIN_KNOWLEDGE (KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, TITLE, CONTENT, CONTENT_JSON, TAGS, SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY, LINEAGE_ID, ORIGIN, CHANGE_NOTE) SELECT 'c788a710-b086-45db-2e9e-6d7a630d9ff5', (SELECT DOMAIN_ID FROM {{database}}.CORE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = 'GENERAL' LIMIT 1), 'AIRFLOW_RUNBOOK', 'Snowflake 210012: Failed to copy previous results (dbt execute_via_snowflake)', 'Signals: ''210012 (P0000): Failed to copy previous results'', ''Database Error in model execute_via_snowflake'', ''dbt-ol run --select tag:<source>_company_silver ... failed''. Meaning: Snowflake failed internally while reusing a previous query result during the dbt run. The SQL and the data are fine. Usual causes: a transient Snowflake service error, most often when many silver dbt runs (company_merged_pipeline) start at the same time. Repair: one rerun (RERUN_ONLY) of the dbt task normally passes. When it recurs, add retries to the dbt profile (retries: 2) or the task (retries with retry_exponential_backoff), disable result reuse in execute_via_snowflake with a pre_hook ''ALTER SESSION SET USE_CACHED_RESULT = FALSE'', stagger the source DAGs, and open a Snowflake support case with the query id.', PARSE_JSON('{"signals": ["210012", "Failed to copy previous results"], "meaning": "A transient Snowflake internal error while reusing a previous query result.", "usual_cause": "Snowflake service error, often when many silver dbt runs start together.", "action": "RERUN_ONLY", "human_steps": ["Rerun the failed dbt task.", "When it recurs, add dbt or Airflow retries with backoff and USE_CACHED_RESULT = FALSE in execute_via_snowflake.", "Open a Snowflake support case with the query id."], "evidence": {"runs": 5, "dag_count": 5, "dags": ["gdp_zoominfo_qa", "gdp_mta_qa", "gdp_rcoe_qa", "gdp_tcc_qa", "gdp_spoc_qa"], "tasks": ["gdp_zoominfo_qa.dbt_silver_company", "gdp_mta_qa.dbt_silver_company", "gdp_rcoe_qa.dbt_silver_company", "gdp_tcc_qa.dbt_silver_company", "gdp_spoc_qa.dbt_silver_company"], "sources": ["INCIDENT", "TASK_RUN"], "first_seen": "2026-10-24T10:21:31+00:00", "last_seen": "2026-10-24T20:41:47+00:00", "incidents": 5} }'), PARSE_JSON('["AIRFLOW", "RUNBOOK", "LEARNED", "SNOWFLAKE", "DBT"]'), 'ops.runbook.gdp.snowflake-failed-to-copy-previous-results', 'ACTIVE', 1, TRUE, 'SEED', '86df7df0c40587803301d9956a106a09', 'OPS', 'Airflow error pack (learned runtime runbooks)' WHERE NOT EXISTS (SELECT 1 FROM {{database}}.CORE.DOMAIN_KNOWLEDGE WHERE SOURCE_REFERENCE = 'ops.runbook.gdp.snowflake-failed-to-copy-previous-results' AND IS_CURRENT);
INSERT INTO {{database}}.CORE.DOMAIN_KNOWLEDGE (KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, TITLE, CONTENT, CONTENT_JSON, TAGS, SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY, LINEAGE_ID, ORIGIN, CHANGE_NOTE) SELECT '46667536-e58c-cb88-a452-cc42c5da59d3', (SELECT DOMAIN_ID FROM {{database}}.CORE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = 'GENERAL' LIMIT 1), 'AIRFLOW_RUNBOOK', 'Kubernetes pod not found (ApiException 404) while Airflow reads or cleans up the pod', 'Signals: ''ApiException: (404)'', ''"reason":"NotFound"'', ''"kind":"pods"''. Meaning: the KubernetesPodOperator asked for its pod''s status or logs and the pod was already gone. Usual causes: the node was scaled in or the pod evicted, a retry of the same task deleted the earlier pod, or the pod was cleaned up before Airflow read it. Repair: one rerun (RERUN_ONLY) usually passes. When it recurs, check the cluster events and the autoscaler around the failure time, use on_finish_action=''delete_succeeded_pod'' so failed pods stay for inspection, and give the task retries with backoff.', PARSE_JSON('{"signals": ["\"reason\":\"NotFound\"", "\"kind\":\"pods\""], "meaning": "Airflow could not find the task''s Kubernetes pod.", "usual_cause": "Node scale-in or eviction, a retry deleting the earlier pod, or early cleanup.", "action": "RERUN_ONLY", "human_steps": ["Rerun the task.", "Check cluster events and the autoscaler at the failure time.", "Set on_finish_action=''delete_succeeded_pod'' and retries with backoff."], "related": ["ops.runbook.zombie-or-killed"], "evidence": {"runs": 4, "dag_count": 1, "dags": ["gdp_engine_property_incremental_qa"], "tasks": ["gdp_engine_property_incremental_qa.gdp_engine_incremental"], "sources": ["INCIDENT", "TASK_RUN"], "first_seen": "2026-10-23T13:50:41+00:00", "last_seen": "2026-10-24T20:03:08+00:00", "incidents": 1} }'), PARSE_JSON('["AIRFLOW", "RUNBOOK", "LEARNED", "KUBERNETES", "INFRASTRUCTURE"]'), 'ops.runbook.gdp.k8s-pod-not-found', 'ACTIVE', 1, TRUE, 'SEED', '27fcafcdc54ef66c883ad39fb1bea540', 'OPS', 'Airflow error pack (learned runtime runbooks)' WHERE NOT EXISTS (SELECT 1 FROM {{database}}.CORE.DOMAIN_KNOWLEDGE WHERE SOURCE_REFERENCE = 'ops.runbook.gdp.k8s-pod-not-found' AND IS_CURRENT);
INSERT INTO {{database}}.CORE.DOMAIN_KNOWLEDGE (KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, TITLE, CONTENT, CONTENT_JSON, TAGS, SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY, LINEAGE_ID, ORIGIN, CHANGE_NOTE) SELECT 'bada2a07-5724-a25a-57f6-00af21094831', (SELECT DOMAIN_ID FROM {{database}}.CORE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = 'GENERAL' LIMIT 1), 'AIRFLOW_RUNBOOK', 'SnapLogic Exit snap: exceeded threshold limit (error documents in the pipeline)', 'Signals: "SnapLogic run for <SOURCE> failed: Snap ''Exit'': Exceeded threshold limit -- Number of input documents received is more than 0". Meaning: the pipeline''s Exit snap is a data check. It stops the pipeline when any document reaches the error path. Records failed validation or mapping inside SnapLogic. Usual causes: new or malformed values from the source (a new code, a null in a required field, a changed file layout), or a mapping change in the pipeline. Repair: a human step. Open the runtime by its ruuid in SnapLogic Manager and read the documents in the error view; fix the source data or the pipeline mapping; then clear trigger_snaplogic with downstream. Rerunning without a fix fails the same way.', PARSE_JSON('{"signals": ["Exceeded threshold limit", "Number of input documents received is more than"], "meaning": "Documents reached the SnapLogic pipeline''s error path and its Exit snap stopped it.", "usual_cause": "New or malformed source values or a pipeline mapping change.", "action": null, "human_steps": ["Open the runtime (ruuid in the message) in SnapLogic Manager and read the error view documents.", "Fix the source data or the pipeline mapping; tell the source owner when the source changed.", "Clear trigger_snaplogic with downstream."], "related": ["ops.runbook.snaplogic-pipeline-error", "ops.runbook.schema-drift"], "evidence": {"runs": 2, "dag_count": 2, "dags": ["gdp_edp_qa", "gdp_tpc_qa"], "tasks": ["gdp_edp_qa.trigger_snaplogic", "gdp_tpc_qa.trigger_snaplogic"], "sources": ["INCIDENT", "TASK_RUN"], "first_seen": "2026-10-24T07:53:11+00:00", "last_seen": "2026-10-24T09:07:34+00:00", "incidents": 2} }'), PARSE_JSON('["AIRFLOW", "RUNBOOK", "LEARNED", "SNAPLOGIC", "DATA_QUALITY"]'), 'ops.runbook.gdp.snaplogic-exit-threshold', 'ACTIVE', 1, TRUE, 'SEED', '002d02e6b57a3be64dbd2b0414ec1e7d', 'OPS', 'Airflow error pack (learned runtime runbooks)' WHERE NOT EXISTS (SELECT 1 FROM {{database}}.CORE.DOMAIN_KNOWLEDGE WHERE SOURCE_REFERENCE = 'ops.runbook.gdp.snaplogic-exit-threshold' AND IS_CURRENT);
INSERT INTO {{database}}.CORE.DOMAIN_KNOWLEDGE (KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, TITLE, CONTENT, CONTENT_JSON, TAGS, SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY, LINEAGE_ID, ORIGIN, CHANGE_NOTE) SELECT '345b2d49-cc0e-fbf0-6912-e93a8e4afbfa', (SELECT DOMAIN_ID FROM {{database}}.CORE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = 'GENERAL' LIMIT 1), 'AIRFLOW_RUNBOOK', 'DBT job failed: duplicate row detected during DML (100090) in a gold model', 'Signals: ''DBT job failed: N model(s) failed out of N total'', ''Duplicate row detected during DML action'' (100090), first error in a gold model such as property_es_doc. Meaning: the model''s MERGE matched one target row with several source rows. Usual causes: a join in the model fans out (several MDM matches or history rows per key), or an upstream silver table received the same record twice. Repair: find the duplicate keys in the model''s source query (GROUP BY key HAVING COUNT(*) > 1). A fan-out is fixed in the model (QUALIFY ROW_NUMBER() OVER (PARTITION BY key ...) = 1 or a tighter join), a code change; duplicates in an upstream table follow the duplicate-keys runbook. Rerun the engine DAG after.', PARSE_JSON('{"signals": ["Duplicate row detected during DML action", "100090", "DBT job failed", "during DML action"], "meaning": "A gold dbt model''s MERGE found several source rows for one key.", "usual_cause": "A fan-out join in the model or duplicates in an upstream silver table.", "action": null, "human_steps": ["Find the duplicate keys in the model''s source query.", "Deduplicate in the model (QUALIFY ROW_NUMBER() = 1) or fix the join.", "Rerun the engine DAG."], "related": ["ops.runbook.duplicate-keys"], "evidence": {"runs": 1, "dag_count": 1, "dags": ["gdp_engine_property_incremental_dev"], "tasks": ["gdp_engine_property_incremental_dev.dbt_property_es_doc"], "sources": ["INCIDENT", "TASK_RUN"], "first_seen": "2026-10-21T14:22:41+00:00", "last_seen": "2026-10-21T14:22:41+00:00", "incidents": 1} }'), PARSE_JSON('["AIRFLOW", "RUNBOOK", "LEARNED", "DBT", "DATA_QUALITY"]'), 'ops.runbook.gdp.dbt-job-duplicate-row-gold', 'ACTIVE', 1, TRUE, 'SEED', '8da053b82822fb03eac314743c297c36', 'OPS', 'Airflow error pack (learned runtime runbooks)' WHERE NOT EXISTS (SELECT 1 FROM {{database}}.CORE.DOMAIN_KNOWLEDGE WHERE SOURCE_REFERENCE = 'ops.runbook.gdp.dbt-job-duplicate-row-gold' AND IS_CURRENT);
INSERT INTO {{database}}.CORE.DOMAIN_KNOWLEDGE (KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, TITLE, CONTENT, CONTENT_JSON, TAGS, SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY, LINEAGE_ID, ORIGIN, CHANGE_NOTE) SELECT '992a8b95-69ee-3cc4-2c24-0617a16f27c7', (SELECT DOMAIN_ID FROM {{database}}.CORE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = 'GENERAL' LIMIT 1), 'AIRFLOW_RUNBOOK', 'dbt property_event_outbox: STAGE.EVENT_OUTBOX_PROPERTY does not exist or not authorized (002003)', 'Signals: ''002003 (42S02): SQL compilation error'', "Object ''<ENV>_GDP_SILVER_DB.STAGE.EVENT_OUTBOX_PROPERTY'' does not exist or not authorized", ''Database Error in model property_event_outbox''. Meaning: the post-MDM dbt model that writes the Kafka event outbox cannot see its STAGE table. Usual causes: the table was not deployed in this environment yet, or the environment''s data engineer role has no privilege on the STAGE schema (new table without future grants). Repair: a human step. Check the table exists (SHOW TABLES LIKE ''EVENT_OUTBOX_PROPERTY'' IN SCHEMA <ENV>_GDP_SILVER_DB.STAGE); deploy it when missing, or grant the role SELECT/INSERT on it and future grants on the schema. Rerun the post-MDM DAG after.', PARSE_JSON('{"signals": ["EVENT_OUTBOX_PROPERTY", "does not exist or not authorized", "002003", "property_event_outbox"], "meaning": "The event outbox model cannot see its STAGE table.", "usual_cause": "Table not deployed in this environment or missing grants for the environment role.", "action": null, "human_steps": ["Check the table exists in <ENV>_GDP_SILVER_DB.STAGE.", "Deploy it or grant the environment role privileges and future grants.", "Rerun the post-MDM DAG."], "related": ["ops.runbook.object-does-not-exist", "ops.runbook.insufficient-privileges"], "evidence": {"runs": 1, "dag_count": 1, "dags": ["gdp_post_mdm_to_gold_property_qa"], "tasks": ["gdp_post_mdm_to_gold_property_qa.dbt_gold_property"], "sources": ["INCIDENT", "TASK_RUN"], "first_seen": "2026-10-22T07:16:51+00:00", "last_seen": "2026-10-22T07:16:51+00:00", "incidents": 1} }'), PARSE_JSON('["AIRFLOW", "RUNBOOK", "LEARNED", "SNOWFLAKE", "ACCESS", "DBT"]'), 'ops.runbook.gdp.event-outbox-not-authorized', 'ACTIVE', 1, TRUE, 'SEED', '611e32f6898cfd61044c5fc4e2814e7d', 'OPS', 'Airflow error pack (learned runtime runbooks)' WHERE NOT EXISTS (SELECT 1 FROM {{database}}.CORE.DOMAIN_KNOWLEDGE WHERE SOURCE_REFERENCE = 'ops.runbook.gdp.event-outbox-not-authorized' AND IS_CURRENT);
INSERT INTO {{database}}.CORE.DOMAIN_KNOWLEDGE (KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, TITLE, CONTENT, CONTENT_JSON, TAGS, SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY, LINEAGE_ID, ORIGIN, CHANGE_NOTE) SELECT 'a9c0f9d8-8892-2f46-bf75-d9127774cac5', (SELECT DOMAIN_ID FROM {{database}}.CORE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = 'GENERAL' LIMIT 1), 'AIRFLOW_RUNBOOK', 'Kubernetes pod could not start (nodes available: Insufficient memory, image pull)', 'Signals: ''0/N nodes are available'', ''Insufficient memory'', ''Insufficient cpu'', ''ErrImagePull'', ''ImagePullBackOff''. Meaning: the task''s pod never started: no node had the CPU or memory it requests, or the image could not be pulled. Usual causes: the cluster was full or the autoscaler at its maximum, pod requests larger than any node, a wrong image tag after a release, or registry credentials that expired. Repair: a human step for the image (fix the tag or the pull secret). For capacity, rerun when the cluster has room (RERUN_ONLY), and if it recurs lower the pod''s requests or raise the node group''s capacity.', PARSE_JSON('{"signals": ["nodes are available", "Insufficient memory", "ErrImagePull", "ImagePullBackOff"], "meaning": "The task''s pod could not be scheduled or its image pulled.", "usual_cause": "Cluster capacity, oversized requests, wrong image tag or expired pull secret.", "action": "RERUN_ONLY", "human_steps": ["Check whether the error is capacity (Insufficient) or image (ErrImagePull).", "Fix the image tag or pull secret for an image error.", "For capacity, rerun later and review requests and node group limits."], "related": ["ops.runbook.zombie-or-killed"], "evidence": {"runs": 1, "dag_count": 1, "dags": ["gdp_engine_property_incremental_qa"], "tasks": ["gdp_engine_property_incremental_qa.gdp_engine_incremental"], "sources": ["INCIDENT", "TASK_RUN"], "first_seen": "2026-10-24T19:13:38+00:00", "last_seen": "2026-10-24T19:13:38+00:00", "incidents": 1} }'), PARSE_JSON('["AIRFLOW", "RUNBOOK", "LEARNED", "KUBERNETES", "INFRASTRUCTURE"]'), 'ops.runbook.gdp.k8s-pod-unschedulable', 'ACTIVE', 1, TRUE, 'SEED', '58091219398fa7d2643239ad0b080420', 'OPS', 'Airflow error pack (learned runtime runbooks)' WHERE NOT EXISTS (SELECT 1 FROM {{database}}.CORE.DOMAIN_KNOWLEDGE WHERE SOURCE_REFERENCE = 'ops.runbook.gdp.k8s-pod-unschedulable' AND IS_CURRENT);
INSERT INTO {{database}}.CORE.DOMAIN_KNOWLEDGE (KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, TITLE, CONTENT, CONTENT_JSON, TAGS, SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY, LINEAGE_ID, ORIGIN, CHANGE_NOTE) SELECT '36ce57ef-316f-9b9f-9809-0cb977da2d9b', (SELECT DOMAIN_ID FROM {{database}}.CORE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = 'GENERAL' LIMIT 1), 'AIRFLOW_RUNBOOK', 'Snowflake lock queue on a shared silver table (has locked table ... not yet released)', 'Signals: "Statement ''<id>'' has locked table ''<TABLE>'' in transaction <n> and this lock has not yet been released", the dbt model aborted (for example company_core). Meaning: too many statements were already waiting for the lock on the same table, so Snowflake aborted this one. Usual causes: several source DAGs (gdp_<source>_qa with tag company_merged_pipeline) write to the same shared silver table (COMPANY_CORE) at the same time. Repair: one rerun (RERUN_ONLY) once the other runs finish usually passes. When it recurs, run the writers one at a time: an Airflow pool with one slot for the company_merged_pipeline dbt tasks, staggered schedules, or one merge step that the sources feed instead of each writing the table.', PARSE_JSON('{"signals": ["has locked table", "lock has not yet been released"], "meaning": "The statement was aborted while waiting for a lock other runs held on a shared table.", "usual_cause": "Concurrent source DAGs writing the same silver table.", "action": "RERUN_ONLY", "human_steps": ["Check which DAGs were writing the table at the time.", "Rerun after they finish.", "Serialize the writers with a one-slot Airflow pool or staggered schedules."], "related": ["ops.runbook.statement-timeout"], "evidence": {"runs": 1, "dag_count": 1, "dags": ["gdp_news_to_leads_qa"], "tasks": ["gdp_news_to_leads_qa.dbt_silver_company"], "sources": ["INCIDENT", "TASK_RUN"], "first_seen": "2026-10-24T11:04:34+00:00", "last_seen": "2026-10-24T11:04:34+00:00", "incidents": 1} }'), PARSE_JSON('["AIRFLOW", "RUNBOOK", "LEARNED", "SNOWFLAKE", "DBT", "PERFORMANCE"]'), 'ops.runbook.gdp.snowflake-table-lock-queue', 'ACTIVE', 1, TRUE, 'SEED', '5e62e6c7af1050e334ea80b81545b495', 'OPS', 'Airflow error pack (learned runtime runbooks)' WHERE NOT EXISTS (SELECT 1 FROM {{database}}.CORE.DOMAIN_KNOWLEDGE WHERE SOURCE_REFERENCE = 'ops.runbook.gdp.snowflake-table-lock-queue' AND IS_CURRENT);
INSERT INTO {{database}}.CORE.DOMAIN_KNOWLEDGE (KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, TITLE, CONTENT, CONTENT_JSON, TAGS, SOURCE_REFERENCE, STATUS, VERSION, IS_CURRENT, CREATED_BY, LINEAGE_ID, ORIGIN, CHANGE_NOTE) SELECT '3737a859-f412-aacf-dd9c-934a2a2f273e', (SELECT DOMAIN_ID FROM {{database}}.CORE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = 'GENERAL' LIMIT 1), 'AIRFLOW_DAG', 'GDP Airflow DAGs Knowledge Base (Airflow 3 / MWAA Architecture & Recipes)', '# GDP Airflow DAGs — Knowledge Base

Code root: `gdp-airflow-dags/dags/gdp-dags/` (git root is `gdp-airflow-dags/`).
Runtime: Airflow 3.x on AWS MWAA (Task SDK; no ORM access in tasks/callbacks), Snowflake, SnapLogic, Soda, dbt, Rafay Kubernetes (OpenSearch engine), Confluent Kafka REST, Akeyless secrets, SMTP/SES email.

> Line numbers drift. Always locate code by **function name** (grep `^def <name>`), not by line.

---

## 1. Module map

| Module | Role | Registers DAGs? |
|---|---|---|
| `gdp_dags.py` | **Single DAG entrypoint.** Resolves env list, calls every builder, registers results in `globals()`, canary DAGs | Yes |
| `gdp_post_mdm.py` | Self-contained post-MDM gold + Kafka DAGs (does NOT import `gdp_utils`) | Yes |
| `gdp_dataset_reset.py` | Admin DAG `admin_reset_dataset_dependencies` (clear queued Asset events) | Yes |
| `gdp_utils.py` (~9k lines) | Core library: env, Snowflake, Akeyless, batch master, task factories, `build_source_dag`, `build_gold_dag`, `build_bronze_only_dag`, `build_mdm_property_trigger_dag`, callbacks | No (`.airflowignore`) |
| `gdp_pipelines.py` | `DOMAIN_CONFIGS` registry per env + bronze-only / legacy DAG builder | No |
| `gdp_source_pipelines.py` | Builds source DAGs, gold DAG, MDM property coordinator; DNB gate; isolated sources | No |
| `gdp_master.py` | `gdp_master_<env>` status-board DAG (ExternalTaskSensors) | No |
| `gdp_opensearch.py` | `gdp_engine_<domain>_<mode>_<env>` DAGs, `GdpEnginePodOperator`, Snowflake mutex | No |
| `gdp_dbt.py` | `dbt_run_task` (dbt-ol subprocess, git-repo cache, transient retry, model logging) | No |
| `gdp_soda.py` | Soda contract verify / consolidated scan tasks, digest emails, Datadog events | No |
| `gdp_snaplogic.py` | SnapLogic trigger + recursive status polling (`build_snaplogic_trigger_task`) | No |
| `gdp_notify.py` | Failure/recovery emails, threading, dedupe (no `gdp_utils` import) | No |
| `gdp_alerts.py` | Alert hub: re-exports + SnapLogic summary email, success/cycle emails, CBRE HTML shell | No |

`.airflowignore` lists every library module so the DAG processor only parses `gdp_dags.py`, `gdp_post_mdm.py`, `gdp_dataset_reset.py`. Library modules are still importable via `sys.path`.

**Rule:** a new library module must (a) register nothing on import and (b) be added to `.airflowignore`. A new DAG family is wired as a `build_dags_for_env(env) -> {dag_id: DAG}` function called from `gdp_dags.py`.

---

## 2. How DAGs are built (parse-time flow)

```mermaid
flowchart TD
  A[gdp_dags.py parsed] --> B[_envs_to_build]
  B --> C[reset_registered_dag_ids]
  C --> D{for env in ENVS}
  D --> E[gdp_pipelines.build_dags_for_env: bronze-only + legacy]
  D --> F[gdp_source_pipelines.build_dags_for_env: source + gold + mdm_property]
  D --> G[gdp_master.build_dags_for_env: status board]
  D --> H[gdp_opensearch.build_dags_for_env: engine DAGs, fail-soft]
  E & F & G & H --> I[_built dict]
  I --> J{env produced 0 DAGs?}
  J -- yes --> K[gdp_PARSE_FAILURE_env canary]
  I --> L[globals dag_id = dag]
```

### 2.1 Env resolution (`_envs_to_build` in `gdp_dags.py`)
Priority:
1. Literal `/dev/`, `/qa/`, `/uat/`, `/prod/` segment in the file path (per-env S3 folder).
2. `GDP_ENVS` (comma list).
3. `GDP_ENV` (single).
4. `gdp_utils._resolve_env()` auto-detect (`AIRFLOW_ENV_NAME`, webserver base URL, remote log folder, file path; word-boundary match; **prod-guard** raises if "prod" seen but not strictly matched).
5. Default `dev,qa,uat` (never prod by default).

If the instance looks like prod but `prod` is not in the build list → `gdp_ENV_MISMATCH` canary DAG.

### 2.2 Per-env global switching
`gdp_utils._set_env(env)` mutates module globals (`GDP_ENV`, `UTIL_DB`, `BRONZE_DB`, `SILVER_DB`, `BATCH_MASTER_FQN`, `SNAPLOGIC_MASTER_FQN`, `DBT_PROJECT_FQN`, Akeyless paths) and clears env-dependent caches. Called:
- at parse time before building each env;
- at task runtime via `_set_env_from_context(context)` which reads the DAG tag `env=<env>`.

**Every DAG must carry the tag `env=<env>`** — tasks use it to target the right env.

### 2.3 Failure semantics at parse time (important design rule)
- Config reads (Snowflake/Akeyless) **raise** → `gdp_dags.py` re-raises → Airflow records Import Error and **keeps last-good DAGs** (no flicker / disappearing DAGs).
- A single bad source inside the loop is caught and logged; siblings continue.
- Gold DAG and MDM coordinator builds are fail-closed (raise).
- `reset_registered_dag_ids()` runs **outside** the try so a failed parse can''t leave stale "already registered" ids.
- Env with genuinely 0 DAGs (empty config) → `gdp_PARSE_FAILURE_<env>` canary.
- OpenSearch builder is fail-soft (returns `{}`).

### 2.4 DAG ID catalogue (all env-suffixed)

| DAG ID | Builder | Trigger |
|---|---|---|
| `gdp_<source>_<env>` | `gdp_utils.build_source_dag` | Cron (`_no_catchup`), Dataset gate (DNB_API), or `None` (manual/isolated) |
| `gdp_<source>_bronze_only_<env>` | `gdp_utils.build_bronze_only_dag` | Cron / manual |
| `gdp_gold_<env>` | `gdp_utils.build_gold_dag` | Dataset AND of gold-feeding sources'' `silver_done` |
| `gdp_mdm_property_<env>` | `gdp_utils.build_mdm_property_trigger_dag` | Dataset AND of property `silver_done` of gating sources |
| `gdp_master_<env>` | `gdp_master.build_dags_for_env` | `GDP_MASTER_SCHEDULE` (default manual) |
| `gdp_engine_<domain>_incremental_<env>` | `gdp_opensearch` | Asset `post_mdm_gold_done` |
| `gdp_engine_<domain>_bulk_<env>` | `gdp_opensearch` | `GDP_ENGINE_BULK_CRON` or manual |
| `gdp_engine_<domain>_reindex_<env>` | `gdp_opensearch` | Manual, paused on creation |
| `gdp_post_mdm_to_gold_<domain>_<env>` | `gdp_post_mdm` (module level loop) | Cron `*/15 * * * *` |
| `admin_reset_dataset_dependencies` | `gdp_dataset_reset` | Manual |
| `gdp_PARSE_FAILURE_<env>`, `gdp_ENV_MISMATCH` | `gdp_dags._build_parse_failure_canary` | Canary only |

Domains for engine/post-MDM: `property`, `company`.

---

## 3. Configuration sources

### 3.1 Snowflake config tables (`<ENV>_GDP_UTIL_DB.CONFIG`)
| Table | Purpose |
|---|---|
| `GDP_SNAPLOGIC_CONFIG_MASTER` | **Source registry**: SOURCE_SYSTEM, DOMAIN, SCHEDULE_CRON, SCHEDULE_FREQUENCY, AIRFLOW_SOURCE_SYSTEM, ACTIVE_FLAG. Drives which source DAGs exist and their domains (`_get_source_domain_map()` → `{SOURCE: [domains]}`) |
| `GDP_BATCH_MASTER` | Write-only audit per run/stage: ORCHESTRATION_ID, BATCH_ID, SNAPLOGIC_*, DBT_*, SODA_*, DBT_GOLD_* status/dates, SNAPLOGIC_RUID |
| `GDP_DAG_DEPENDENCY` | Cross-DAG activation flags |
| `GDP_DAG_MUTEX` | OpenSearch per-domain advisory lock rows |
| `GDP_DBT_<ENV>` | Snowflake DBT PROJECT object |

DB naming: `<ENV>_GDP_UTIL_DB`, `<ENV>_GDP_BRONZE_DB` (schemas `BRONZE_<SOURCE>`), `<ENV>_GDP_SILVER_DB` (schema per domain), `<ENV>_GDP_GOLD_<DOMAIN>_DB`. Role `<ENV>_GDP_DATA_ENGINEER_ROLE`, warehouse `<ENV>_GDP_DATA_ENGINEER_WHS`.

### 3.2 `DOMAIN_CONFIGS` (`gdp_pipelines.py`)
`_BASE_DOMAIN_CONFIGS` is deep-copied into `DOMAIN_CONFIGS_BY_ENV[env]`; read via `get_domain_configs(env)`. Edit one env''s list to diverge it.

Keys per entry:
- `domain` (required), `dbt_command` (default `run`), `use_datasets` (must be True for source/gold topology)
- `soda_datasets` — silver tables to scan
- `silver_select` — source-agnostic silver selector (e.g. geography `tag:geography_silver`)
- `gold_select` — gold selector (e.g. `tag:gold_opportunity`); absent ⇒ silver-only domain
- `soda_bronze_datasets_by_source` — `{SOURCE: {"schema": ..., "datasets": [...]} }`
- `soda_datasets_by_source` — supplemental silver scans per source
- `mdm_trigger_by_source` — Reltio MDM DAG ids with `{env}` placeholder

Domains: company, property (silver-only, MDM-fed), contact, opportunity, finance, lease, sale (gold), geography (inline gold in ATLAS source DAG).

### 3.3 Knobs
`gdp_utils._cfg_get(name, default)`: **env var first**, then Airflow Variable (avoids secrets-backend noise at parse time). `airflow_variable()` bridges Airflow 2/3 `Variable.get` signatures and never raises.

Key env vars / Variables:
- Env: `GDP_ENV`, `GDP_ENVS`
- Scheduling: `GDP_SOURCE_DAG_SCHEDULE_ENABLED`, `GDP_MANUAL_ONLY_SOURCES` (default includes LIGHTBOX, PDL, ATLAS, INTROHIVE, DIGITAL_MARKETING, DNB_FILE), `GDP_DNB_SOURCES`, `GDP_DNB_GATE_EXCLUDE`, `GDP_MDM_PROPERTY_GATE_*`, `GDP_MAX_RUN_AGE_SEC`, `GDP_SCHEDULE_GRACE_SEC`, `GDP_SUPPRESS_UNPAUSE_RUN`
- dbt: `GDP_DBT_OL_BIN`, `GDP_DBT_PROJECT_DIR`, `GDP_DBT_RUN_TIMEOUT_SEC` (6h), `GDP_DBT_TRANSIENT_RETRIES`, `GDP_DBT_GIT_*`, `GDP_DBT_CACHE_*`
- Soda: `GDP_SODA_TIMEOUT_SEC` (600), `GDP_SODA_RUNNER_FLAG`, `GDP_SODA_EMAIL_MODE` (`off`|`failed_only`|`all`)
- SnapLogic: `GDP_SNAPLOGIC_TASK_TIMEOUT_HOURS` (24), `GDP_SNAPLOGIC_POLL_INTERVAL_SEC`, `GDP_SNAPLOGIC_INFLIGHT_MODE` (`resume`), `GDP_SNAPLOGIC_MAX_ERROR_COUNT`, `GDP_SNAPLOGIC_EMAIL_MODE`
- Master: `GDP_MASTER_SCHEDULE`, `GDP_MASTER_POKE_SEC`, `GDP_MASTER_TIMEOUT_SEC`, `GDP_MASTER_INCLUDE_BRONZE_ONLY`, `GDP_MASTER_REQUIRE_ALL_SUCCESS`
- Post-MDM: `GDP_POST_MDM_ES_SYNC_ENVS` (default `dev,qa,uat,prod`), `GDP_POST_MDM_ES_SYNC_WAIT_MIN` (180), `GDP_POST_MDM_PUBLISH_MAX` (20000), `GDP_POST_MDM_BATCH_SIZE` (10000), `GDP_POST_MDM_STALE_RUN_MAX_MIN` (30), `GDP_KAFKA_MAX_RETRIES`
- Engine: `GDP_ENGINE_VERSION` (required, `:latest` refused), `GDP_ENGINE_REGISTRY`, `GDP_ENGINE_DAGS_ENABLED`, `GDP_ENGINE_ENABLED_ENVS`, `GDP_ENGINE_DOMAINS`, `GDP_ENGINE_BULK_CRON`, `GDP_ENGINE_MUTEX_TTL_SEC` (8h), `GDP_ENGINE_DBT_SELECT*`
- Alerts: `GDP_ALERT_EMAILS`, `GDP_SUCCESS_ALERTS_ENABLED`

### 3.4 Secrets (Akeyless)
Path: `/{teams-nonprod|teams}/global-data-platform-gdp/<env>/airflow/<leaf>` with leaves `snowflake-secrets`, `soda-secrets`, `snaplogic`, `snaplogic-polling` (UAT polling reuses QA), `kafka-secrets`. Auth = AWS IAM cloud-id; access id per env (`GDP_AKEYLESS_ACCESS_ID` override). Token cached ~50 min per access id (thread-safe). Snowflake uses key-pair auth (`_get_sf_connection`, keep-alive, 2h network/statement timeout); `_sf_run` uses cached connections — **only retry idempotent reads, never DML**.

---

## 4. Source DAG (`gdp_<source>_<env>`) — `build_source_dag`

```mermaid
flowchart LR
  init_batch --> validate_source --> snaplogic_start --> trigger_snaplogic --> snaplogic_end
  snaplogic_end --> soda_bronze
  snaplogic_end --> dbt_silver_start --> S[dbt_silver_company -> property -> contact -> opportunity+finance -> lease -> sale]
  S --> M[dbt_&lt;domain&gt;_silver_end outlets gdp://env/source/domain/silver_done]
  S --> silver_end[silver_end outlets gdp://env/source/silver_done]
  silver_end --> G[inline gold: gold_start -> dbt_gold_geography -> gold_end]
  silver_end & soda_bronze & G --> pipeline_done
```

- `init_batch` (`init_batch_task`): inserts `GDP_BATCH_MASTER` row, pushes `batch_id`/`orchestration_id` XCom, **skips stale scheduled runs** (`skip_if_stale_run`, `GDP_MAX_RUN_AGE_SEC`).
- `validate_source`: source must be ACTIVE in `GDP_SNAPLOGIC_CONFIG_MASTER`.
- `snaplogic_start` / `snaplogic_end` / `dbt_silver_start`: `update_stage_task(stage, status)` markers; `snaplogic_end` also sends SnapLogic summary email and is the hard stop on failure.
- `trigger_snaplogic`: `gdp_snaplogic.build_snaplogic_trigger_task` — `retries=0` (side-effecting), timeout 24h + 15 min buffer.
- `soda_bronze`: one consolidated scan of `BRONZE_<SOURCE>` curated tables, `ALL_DONE`, warn-and-continue.
- Silver dbt chain in strict FK order: company → property → contact → [opportunity, finance] → lease → sale; only domains the source feeds get tasks; `ALL_SUCCESS`.
- Signal domains (`_SILVER_SIGNAL_DOMAINS`: company, property) get per-domain marker tasks emitting `gdp://<env>/<source>/<domain>/silver_done`.
- `pipeline_done` (`pipeline_done_task`, `ALL_DONE`): final status to batch master + notification. **The master DAG sensors watch this task id** — never rename it.

Scheduling rules (`gdp_source_pipelines.build_dags_for_env`):
- Isolated sources (`_ISOLATED_SOURCES`, e.g. DNB_FILE): `schedule=None`, `isolated=True` → no outlets, no silver markers, no MDM trigger, never gate gold.
- DNB targets (`_DNB_TARGET_SOURCES`, DNB_API): Dataset-gated on every other company source''s `company/silver_done` (`_dnb_gate_schedule`, excludes `GDP_DNB_GATE_EXCLUDE`).
- Manual-only (`_MANUAL_ONLY_SOURCES`): `schedule=None`.
- On-demand (`_ON_DEMAND_SOURCES`): built paused.
- Otherwise `_resolve_source_schedule(src)` (cron + frequency) wrapped by `_no_catchup` (CronTriggerTimetable, no catch-up; `_no_catchup_start_date` suppresses missed ticks).
- Gold gate only includes sources feeding a `gold_select` domain that is not inline-gold (so quarterly LIGHTBOX / monthly ATLAS don''t starve daily gold).

## 5. Gold DAG (`gdp_gold_<env>`) — `build_gold_dag`
Schedule = list of `_source_silver_done_dataset(src, env)` (Dataset AND). Only built sources are listed (a Dataset with no producer would hang it forever).

`init_batch → soda_silver (consolidated, once for platform) → soda_post_mdm (COMPANY_MDM/PROPERTY_MDM) → gold_start → dbt_gold_<domain> (parallel: opportunity, finance, contact, lease, sale, supplemental) → gold_end → soda_gold → notify_* (optional, gdp_alerts) → pipeline_done`.
Batch row uses sentinel `source_system="gold"`, `domain="all_domains"`. Gold-DAG Soda never decides pass/fail.

## 6. MDM property coordinator (`gdp_mdm_property_<env>`)
Waits on `property/silver_done` of every gating source (default excludes LIGHTBOX, isolated, unbuilt) and fires the single Reltio MDM property DAG via `TriggerDagRunOperator`. Replaced four legacy per-source `trigger_mdm_property` tasks. Not registered if gate resolves empty.

## 7. Master status board (`gdp_master_<env>`)
Observes, never launches. `begin → status_gdp_<src>` (TaskGroup) / `status_gdp_<src>_bronze_only`; company sources → `status_gdp_dnb_api`; all sources → `status_gdp_gold` → `all_pipelines_complete` (`ALL_DONE`, or `ALL_SUCCESS` if `GDP_MASTER_REQUIRE_ALL_SUCCESS=1`).
Sensors: `ExternalTaskSensor(external_task_id="pipeline_done", execution_date_fn=_latest_logical_date_fn(dag_id), mode="reschedule", check_existence=True, failed_states=[failed, upstream_failed, skipped])` — watches each DAG''s **latest** run (cron-independent). Layout via `_resolve_layout(env)`; fails hard.

## 8. Post-MDM → OpenSearch → Kafka (Asset chain)

```mermaid
sequenceDiagram
  participant PM as gdp_post_mdm_to_gold_<d>_<env> (*/15)
  participant EN as gdp_engine_<d>_incremental_<env>
  PM->>PM: check_incremental_data (ShortCircuit on VW_GOLD_<D>_INC_CHECK, stale gate 30m)
  PM->>PM: dbt_gold_<d> (EXECUTE DBT PROJECT tag:gold_<d> --exclude tag:es_sync)
  PM-->>EN: Asset gdp://env/d/post_mdm_gold_done
  EN->>EN: mutex_gate -> dbt_<d>_es_doc -> gdp_engine pod -> finalize (NONE_FAILED)
  EN-->>PM: Asset gdp://env/d/es_sync_done extra{post_mdm_run_ids,status}
  PM->>PM: wait_es_sync (PythonSensor reschedule, 180 min) 
  PM->>PM: dbt_kafka_outbox_<d> (tag:kafka_<d>) -> publish_kafka
```

### 8.1 `gdp_post_mdm.py` (self-contained)
- Module-level loop over `DOMAIN_CONFIG` (company, property): `inc_check_view`, `gold_table`, `dbt_select`, `kafka_dbt_select`, `kafka_outbox_table` (`<UTIL_DB>.AUDIT.KAFKA_OUTBOX_<D>`), `kafka_bulk_view`.
- `max_active_runs=1`, `catchup=False`, DAG-level `notify_dag_failure`; task-level `notify_failure` on dbt gold; `notify_recovery` on `publish_kafka` success.
- `wait_es_sync` only if `ENV in ES_SYNC_ENVS`; extra.status `failed` → fail, `skipped` → skip. Kafka tasks are `ALL_SUCCESS` so Kafka never runs ahead of search; nothing is lost (watermark + outbox).
- `publish_kafka`: claim PENDING (+ ERROR under retry limit) rows; > `PUBLISH_MAX_RECORDS` ⇒ BULK mode (park + alert, succeed); else STREAM in chunks of `BATCH_SIZE` via Confluent REST v3 (`/kafka/v3/clusters/{cluster}/topics/{topic}/records`, Azure AD OAuth, urllib3 Retry on 429/5xx), commit per chunk (crash-safe), audit `KAFKA_PUBLISH_LOG`. Topic: nonprod `{account}-gdp-{env}-{domain}-batch`, prod from secret.
- Timeouts: Snowflake `STATEMENT_TIMEOUT_IN_SECONDS` fires first (6h); Airflow `execution_timeout` = publish timeout + buffer is the backstop.
- Emits OpenLineage START/COMPLETE/FAIL for Datadog; parses dbt node lines from `SYSTEM$GET_DBT_LOG`.

### 8.2 `gdp_opensearch.py`
- Modes `incremental | bulk | reindex`; domains `property | company`; `ENABLED_ENVS`, `ENABLED_DOMAINS`.
- Task graph: `mutex_gate → dbt_<d>_es_doc → gdp_engine_<mode> → finalize`; incremental adds `signal_es_sync_failed` (`ONE_FAILED`) emitting es_sync_done status=failed so post-MDM fails fast.
- Mutex: row in `<UTIL_DB>.CONFIG.GDP_DAG_MUTEX`, lock `<domain>_es_sync`, TTL reaper + `INSERT ... WHERE NOT EXISTS`; loser raises `AirflowSkipException`. `finalize` (`NONE_FAILED`) releases only if this run acquired. Optional secondary pool `GDP_ENGINE_MUTEX_POOL` (must exist or tasks queue forever).
- es-doc dbt: `EXECUTE DBT PROJECT ... --select <domain>_es_doc`; bulk/reindex batched with `--vars {full_load, batch_mod, total_batches}`; pool `gdp_heavy`.
- `GdpEnginePodOperator` (KubernetesPodOperator subclass): image `<registry>/gdp-engine/<env>:<version>` (Jenkins tokens `{CONTAINER_REGISTRY_HOST}`/`{RECENT_PUSHED_VERSION}`, per-env fallbacks), cmd `python main.py run --pipeline sync_<d>_es --mode <mode>`, namespace `gdp-nonprod-<env>` / `gdp-prod-ns`, kubeconfig materialized from Akeyless to a temp file, non-root (uid/gid 1000), node selector `memorymax`, sizing **only** via `ENGINE_POD[domain][mode]` constants (namespace quota: req 40 CPU/80Gi, lim 80 CPU/320Gi, 20 pods). `cleanup` reads base-container logs, parses engine JSON lines → XCom `engine_failure_details` → failure email.
- Engine exit codes: 0 ok, 1 systemic (no progress kept), 2 partial (progress kept), 130 interrupted.

---

## 9. Building blocks (task factories)

### dbt — `gdp_dbt.dbt_run_task(model=None, task_id=None, dbt_command="run", pool=None, domain=None, models=None, entity_ids=False, model_batches=None, silver_selector=False, is_gold=False, silver_select=None)`
1. Resolve batches (model_batches → models → model).
2. Silver selector `tag:<src_alias>_<domain>_silver tag:<domain>_merged_pipeline` or `silver_select`.
3. Fetch dbt project from Snowflake GIT REPOSITORY into a per-(branch, sha) cache (`/tmp/.gdp_dbt_project_cache`, file lock, atomic rename, `dir` GET mode to avoid rate limit 093563, prune with grace > longest run). Partial-parse msgpack reused.
4. Materialize key-pair PEM, patch `profiles.yml`.
5. Run `dbt-ol run --select execute_via_snowflake` (wrapper model → `EXECUTE DBT PROJECT` server-side); extra vars via `dbt_vars` → `model_vars`.
6. Transient errors (e.g. "Operations rate limit exceeded") retried in place with linear backoff (`GDP_DBT_TRANSIENT_RETRIES`, 45s base). Wall-clock deadline on reader thread.
7. Per-model results logged from `SYSTEM$GET_DBT_LOG(job_id)`; OpenLineage to Datadog.
Pools: `_dbt_pool_for_domain()` → `gdp_heavy` for all dbt; `_gdp_pool()` for Soda.

### Soda — `gdp_soda`
- `soda contract verify --dataset <ds> --use-runner --soda-cloud <cfg> --publish` subprocess (`start_new_session=True`, timeout 600s), Soda Cloud creds from Akeyless (redacted logging).
- Outcome via `_classify_soda_outcome` → PASSED/FAILED/SKIPPED/ERROR; bronze FAILED downgraded to WARN.
- **Warn-and-continue**: Soda tasks never fail the DAG.
- Consolidated tasks: `soda_consolidated_scan_task` (bronze per source), `soda_silver_consolidated_scan_task`, `soda_post_mdm_scan_task`, `soda_gold_all_scan_task`. Curated allow-lists only (`BRONZE_/SILVER_/GOLD_/MDM_SODA_DATASETS` in `gdp_utils`), env-aware Soda data-source prefixes (`_SODA_DATA_SOURCE_BY_ENV`).
- Digest email inline (`_send_soda_scan_digest`) gated by `GDP_SODA_EMAIL_MODE` (default off). Structured Datadog JSON event per dataset.

### SnapLogic — `gdp_snaplogic._trigger_and_wait`
- Secrets: `snaplogic` (url, trigger_path, bearer_token) and `snaplogic-polling` (status_url, status_token).
- Trigger POST with query params SOURCE_SYSTEM, DOMAIN, BATCH_ID, airflow_dag_name, airflow_dag_run_id; retry **only** on 429/safe connect errors (never on timeout/5xx — may have triggered).
- **Idempotency:** in-flight RUID in batch master with SNAPLOGIC_STATUS=RUNNING is resumed (`GDP_SNAPLOGIC_INFLIGHT_MODE=resume`) instead of re-triggering after worker loss.
- Poll until terminal; success requires 2 consecutive `completed` observations; children polled recursively under a shared deadline; failure states include failed/stopped/aborted/cancelled/skipped (override `GDP_SNAPLOGIC_TREAT_SKIPPED_AS_SUCCESS`); `error_count > GDP_SNAPLOGIC_MAX_ERROR_COUNT` fails.
- Readable failure report (`_log_snaplogic_failure_report`), secrets redacted in response dumps.

### Batch master / stage tasks (`gdp_utils`)
`init_batch_task`, `validate_source_task`, `update_stage_task(stage, status)`, `pipeline_done_task`, `reconcile_batch_status_task`. Batch master is **write-only audit** — never read back to drive control flow (except SnapLogic in-flight resume).

### Defaults
`START_DATE = pendulum.datetime(2024,1,1, tz="America/Chicago")`; `DEFAULT_ARGS`: owner `gdp-team`, `retries=0`, `execution_timeout=2h`, `post_execute=_post_task_sleep` (5s CloudWatch log flush). `max_active_runs=1` typical.

---

## 10. Alerting

- **Task-level callbacks send failure emails** (`pipeline_failure_callback`, `gdp_notify.notify_failure`). On Airflow 3 the DAG-level `on_failure_callback` runs in the DAG processor; its `ti` is NOT the failed task and SDK DagRun lacks `get_task_instances`. `notify_dag_failure` uses `ti.get_task_states` and dedupes via XCom `failure_alert_sent`.
- Recipients: Variable `GDP_ALERT_EMAILS` → env `GDP_ALERT_EMAILS` → `AIRFLOW_VAR_GDP_ALERT_EMAILS` → last-good → default. `parse_email_list` accepts comma/semicolon/JSON/`Name <addr>`.
- Threading: one open thread per DAG in Variable `gdp_alert_thread__<dag_id>`; replies `RE: <subject>` with Thread-Topic/Thread-Index (MS-OXOMSG); Message-IDs unique per email (SES rewrites; Exchange drops duplicates). Recovery (`notify_recovery`, `pipeline_done`) replies "RESOLVED" and closes thread.
- Dedupe keys `dag::run::task::try`; intermediate retries skipped; stale-skipped runs suppressed.
- `gdp_alerts`: SnapLogic summary email (`GDP_SNAPLOGIC_EMAIL_MODE`), success/cycle emails (`GDP_SUCCESS_ALERTS_ENABLED`, `GDP_CYCLE_LOOKBACK_HOURS`), CBRE shell (brand `#003F2D`), `gdp_subject(env, headline)`.
- Alerts must never raise into tasks (wrap in try/except).

---

## 11. Recipes

### Add a new source
1. Insert ACTIVE rows (one per domain) in `<ENV>_GDP_UTIL_DB.CONFIG.GDP_SNAPLOGIC_CONFIG_MASTER` with SCHEDULE_CRON + AIRFLOW_SOURCE_SYSTEM.
2. Add bronze Soda tables to `BRONZE_SODA_DATASETS["BRONZE_<SRC>"]` or `soda_bronze_datasets_by_source` in the domain config.
3. If it feeds property/company MDM, add `mdm_trigger_by_source` / check MDM gate exclusions.
4. Manual-only? add to `_MANUAL_ONLY_SOURCES`; isolated? `_ISOLATED_SOURCES`; on-demand? `_ON_DEMAND_SOURCES`.
5. Add to `_FALLBACK_ALLOWED_SOURCES` if needed. Next parse builds `gdp_<src>_<env>`; master/gold gates update automatically.

### Add a new domain
Add entry to `_BASE_DOMAIN_CONFIGS` (domain, `use_datasets=True`, `soda_datasets`, optional `gold_select`), place it in the silver FK order, add `SILVER_/GOLD_SODA_DATASETS`, ensure dbt tags `<alias>_<domain>_silver`, `<domain>_merged_pipeline`, `gold_<domain>` exist.

### Add a new DAG family
Library module with `build_dags_for_env(env) -> dict`, no import-time registration, tag `env=<env>`, add to `.airflowignore`, call it in the env loop in `gdp_dags.py`. Config failures must raise (keep last-good), per-item failures may be swallowed.

### Change engine pod size
Edit `ENGINE_POD[domain][mode]` in `gdp_opensearch.py` only; stay within namespace quota; run `tests/verify_engine_autosize.py`.

### Clear stuck Asset queue
Trigger `admin_reset_dataset_dependencies` with `target_dag_id` (or `clear_all=true`), `dry_run=true` first (uses MWAA `invoke_rest_api`).

---

## 12. Gotchas
- Never rename `pipeline_done` or the `env=<env>` tag.
- Never gate a Dataset schedule on a source whose DAG wasn''t built (hangs forever) — builders intersect with `built_sources`.
- SnapLogic trigger and Kafka publish are side-effecting: `retries=0`, idempotency via batch master / outbox status.
- `_sf_run(retries=...)` only for reads.
- Missing Airflow pool ⇒ task queued forever (`gdp_heavy`, `GDP_ENGINE_MUTEX_POOL`).
- `_cfg_get` reads env vars before Variables on purpose (parse-time secrets backend noise).
- DNB_FILE is isolated + manual; DNB_API alone is Dataset-gated.
- Default env list never includes prod; prod needs folder `/prod/`, `GDP_ENVS=prod` or `GDP_ENV=prod`.
- Engine `:latest` image tag is refused.

## 13. Tests (`gdp-airflow-dags/tests/`)
Mostly AST-extract scripts — no Airflow needed: `python tests/verify_<name>.py`.
- `verify_env_resolution.py`, `validate_env_isolation.py` — env detection & isolation
- `verify_cross_module_imports.py` — import coupling
- `verify_dbt_model_logging.py`, `verify_dbt_ol_changes.py`, `verify_dbt_transient_retry.py`, `verify_es_doc_dbt_sql.py` — dbt
- `verify_dnb_source_parity.py` — DNB isolation/gating
- `verify_email_list_parsing.py`, `verify_failure_logs.py`, `verify_engine_failure_email.py` — alerts
- `verify_engine_autosize.py` — pod sizing vs quota
- `verify_es_sync_paused_skip.py` — wait_es_sync when engine paused (4 known pre-existing FAILs)
- `test_email_notification.py` (one known pre-existing failure), `test_soda.py`, `test_snowflake_connection.py`, `test_snaplogic_connection.py` — DAG-style smoke tests run in Airflow
- `preview_gdp_cycle_email.py` — render cycle email HTML

Deps: `env-conf/requirements.txt` (+ `constraints.txt`), providers snowflake, amazon, smtp, http, openlineage, cncf-kubernetes; dbt-ol in a separate venv set up by `env-conf/startup.sh`.', PARSE_JSON('{"skill_name": "GDP-AIRFLOW-DAGS", "version": "1.0.0", "dags": ["gdp_<source>_<env>", "gdp_gold_<env>", "gdp_mdm_property_<env>", "gdp_master_<env>", "gdp_post_mdm_to_gold_<domain>_<env>", "gdp_engine_<domain>_<mode>_<env>"]}'), PARSE_JSON('["AIRFLOW", "DAG", "GDP", "MWAA", "ARCHITECTURE"]'), 'ops.dag.gdp-airflow-dags-architecture', 'ACTIVE', 1, TRUE, 'SEED', 'cacb0462fa39c7bfac9c0e905ace07ee', 'OPS', 'GDP Airflow DAGs knowledge skill (SKILL.md)' WHERE NOT EXISTS (SELECT 1 FROM {{database}}.CORE.DOMAIN_KNOWLEDGE WHERE SOURCE_REFERENCE = 'ops.dag.gdp-airflow-dags-architecture' AND IS_CURRENT);
