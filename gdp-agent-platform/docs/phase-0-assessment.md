# Phase 0 — Inspection, Assessment and Implementation Plan

Status: complete. No existing code was modified in Phase 0.

Scope: `gdp-silver-can-model` (Streamlit-in-Snowflake modeler, ~10k LOC Python) and
`gdp-dbt-code-generator` (React/Vite + FastAPI dbt generator, ~5k LOC). Repo has one commit
(`8c28308`), clean tree. Local toolchain: Python 3.11.9, Node 24.13, npm 11.6.

---

## 1. Current-state assessment

| Capability (master prompt) | Exists today? | Where | Quality |
|---|---|---|---|
| Source registration / access validation | No | — | — |
| Landing / Bronze ingestion | No (assumes Bronze already exists) | — | — |
| Profiling (deterministic SQL) | Yes | `extract/profile_real_data.py::profile_table` | Good core: one wide aggregate query per table. Missing: pattern stats, duplicates, avg, date range, key/FK detection, PII, sample masking. Uses `os.environ` for context. |
| Semantic enrichment (AI) | Yes | `iterative_mapper.generate_column_descriptions`, `ai/data_quality_companion.py` | Works; prompt built by SQL string concatenation; no structured output; nothing persisted. |
| Embeddings | Yes | `iterative_mapper.compute_embedding_similarity`, `ai/semantic_clustering.py` | Per-row `AI_EMBED` via `UNION ALL`, cosine in numpy, recomputed every run, never stored. Embed model hard-coded (`iterative_mapper.py:276`). |
| Hybrid mapping | Partial | `iterative_mapper.filter_compatible_sources` (type/name/embedding heuristics) + LLM batches | Final score is LLM self-reported; the appended JSON shape omits `mapping_score` (`iterative_mapper.py:803-820`) so it is frequently 0. O(target_batches x source_chunks) LLM calls. |
| Business rules | Yes | `ai/business_rules_engine.py`, `data/business_rules/rules_config.json` | Useful BOOST/FILTER/PENALTY model. JSON file storage. |
| Human approval | Yes (thin) | `ai/approval_manager.py`, Streamlit buttons | `reviewer="user"`, no business justification, JSON / `/tmp` storage — lost per SiS session. |
| Learning / patterns | Yes | `ai/learning_engine.py`, `ai/pattern_library.py` | Logic usable; JSON storage; auto-applies learned patterns to output (conflicts with "no automatic change from feedback"). |
| Mapping validation | Yes | `ai/mapping_validator.py` | Type compatibility + naive SQL checks (paren/quote counting). |
| STTM | No (CSV export only) | Streamlit export | Two incompatible CSV schemas (mapping mode vs generation mode). |
| Soda | No | — | — |
| dbt generation | Yes | `gdp-dbt-code-generator/backend/app/routers/dbt_offline_router.py` | Whole project in ONE `AI_COMPLETE` response (truncation risk, non-deterministic); joins guessed from FACT/DIM names; tests guessed from names; LLM writes `profiles.yml`; no parse/compile. Good, reusable pieces: `_prepare_mapping_data_for_ai`, `_group_multi_source_mappings`, macro library, multi-source COALESCE/UNION semantics, null-population rule. |
| dbt validation | No | — | — |
| Code review UI | Partial | `frontend/src/components/DBTCodeViewer.tsx`, `CodeViewerPage.tsx` | Viewer only, no diff, no versioning (regeneration deletes old files: `dbt_offline_router.py:690`). |
| Agent / orchestration | No | — | — |
| Audit / cost | No | — | — |
| Auth | Partial | backend: password / key-pair service user | No per-user identity. |
| Tests | Minimal | `ai/tests/test_iterative_mapper_merge.py` (unit, pytest); backend `test_*.py` are live-Snowflake scripts, not tests | — |
| Domain knowledge / skills | None formal | `rules_config.json`, `patterns.json`, prompt text | — |

Cross-cutting defects to fix while porting (not patched in place — Streamlit is being retired):

1. `run_iterative_mapping` closes the caller's connection in `finally` (`iterative_mapper.py:1105`) — in SiS that is the shared session connection.
2. Unmapped sentinel mismatch: mapper writes `SourceColumn="N/A"`, UI filters on `'UNMAPPED'` → unmapped targets counted as mapped with score 0.
3. All LLM/embedding calls interpolate prompts into SQL (`replace("'", "''")`) instead of bind parameters.
4. Context passed by mutating `os.environ` (`streamlit_app.py:1512-1513`).
5. dbt setup script drops tables unconditionally (`snowflake_complete_setup.sql:37-46`).
6. `cortex_client.cortex_complete` creates a new Snowpark session per call and switches `USE DATABASE SNOWFLAKE`; `max_tokens` argument unused.

---

## 2. Reuse / Refactor / Replace / Deprecate matrix

| Component | Decision | Reason | New home |
|---|---|---|---|
| `extract/profile_real_data.py::profile_table` (wide aggregate query, robust-null logic, special-type handling) | **REFACTOR** | Correct deterministic core; extend stats, parameterize context, persist to `PROFILE_REGISTRY` | `services/profiling` |
| `extract/profile_real_data.py::list_tables / get_column_info / list_schemas` | **REFACTOR** | Becomes `SourceAdapter.discover_objects / get_metadata` | `services/source/adapters` |
| `iterative_mapper.generate_column_descriptions` | **REFACTOR** | Keep prompt intent; switch to `AI_COMPLETE` structured output + bind params; persist `GENERATED_DESCRIPTION` | `services/profiling/enrichment.py` |
| `iterative_mapper.compute_embedding_similarity` | **REFACTOR** (reuse required by prompt) | Keep text construction ("Table T: Column C represents D"); move to stored `VECTOR` + `VECTOR_COSINE_SIMILARITY` in SQL | `services/mapping/features/semantic.py` |
| `iterative_mapper.check_type_compatibility`, `mapping_validator.check_type_compatibility` | **REUSE** (merge duplicates) | Deterministic and correct | `services/mapping/features/datatype.py` |
| `iterative_mapper.filter_compatible_sources` scoring heuristics | **REFACTOR** | Becomes separate `KEYWORD_SCORE` / `DATATYPE_SCORE` / `SEMANTIC_SCORE` features | `services/mapping/features/*` |
| `iterative_mapper.generate_mapping_prompt` + `run_iterative_mapping` batching | **REPLACE** | LLM now adjudicates only top-K ambiguous candidates per target; score computed in code | `services/mapping/adjudicate.py` |
| `iterative_mapper.merge_mapping_states` + its tests | **DEPRECATE** | Only needed for chunked LLM state; keep tests until removal | — |
| `ai/business_rules_engine.py` (rule model, `_rule_matches`) | **REFACTOR** | Rule semantics reused as `DOMAIN_SCORE`; rules stored in `DOMAIN_KNOWLEDGE` (type `BUSINESS_RULE`) | `services/mapping/features/domain.py` |
| `ai/approval_manager.py` | **REPLACE** | Storage + identity model wrong; replaced by `MAPPING_DECISION` + caller's-rights procedure | `snowflake/procedures` + API |
| `ai/learning_engine.py`, `ai/pattern_library.py` | **REFACTOR** | Pattern extraction logic → `HISTORICAL_SCORE` feature reading approved decisions; no auto-apply | `services/mapping/features/historical.py` |
| `ai/mapping_validator.py::validate_mapping` | **REFACTOR** | Keep nullability/type checks; replace naive SQL check with `EXPLAIN`-based validation | `services/validation` |
| `ai/data_quality_companion.py` | **REFACTOR (partial)** | Reuse semantic-type / quality inference prompts; drop histogram code generation and in-process code execution | `services/profiling/enrichment.py` |
| `ai/semantic_clustering.py`, `silver_model_generator.py`, `generate_silver_snowflake_ddl.py` | **DEPRECATE (Phase 1)** | "Generation mode" (invent a new Silver model) is out of Phase 1 scope — Phase 1 maps to an existing GDP target model | Archive |
| `ai/nl_mapping_interface.py`, `ai/refinement_engine.py`, `ai/profiler_personas.py`, `ai/code_validator.py` | **DEPRECATE** | Superseded by the Cortex Agent + approval UI | Archive |
| `ui/streamlit_app.py`, `utils/stage_exporter.py`, `utils/csv_sanitizer.py` | **DEPRECATE** | Streamlit retired; `sanitize_text` logic may be reused for exports | Archive |
| `validation/*` (DDL compare, metrics) | **DEPRECATE** | Tied to generation mode | Archive |
| `dbt_offline_router._prepare_mapping_data_for_ai`, `_group_multi_source_mappings` | **REFACTOR** | Logic becomes STTM → dbt model-plan builder (deterministic) | `services/dbt/plan.py` |
| `dbt_offline_router._generate_complete_dbt_project_ai` | **REPLACE** | Single-response generation → Jinja templates per file + LLM only for complex expressions, then compile | `services/dbt/render.py` |
| Prompt rules in that function (COALESCE/UNION/null population/aggregation phases) | **REUSE** as written policy | Move into `GDP_DOMAIN_SKILL` / dbt skill text and template logic | `snowflake/skills/dbt` |
| `MACRO_LIBRARY` table + `seed_macros.py` | **REFACTOR** | Macros become versioned knowledge (`DBT_PATTERN`) | `domain/gdp/macros` |
| `mapping_router.py` (CSV upload, projects) | **DEPRECATE** | Replaced by run/STTM APIs; CSV import retained only for client Excel/CSV expectation import | — |
| `backend/app/utils/snowflake_connection.py`, `config/settings.py`, `error_handler.py`, `logger.py` | **REFACTOR** | Good FastAPI scaffolding; add per-user OAuth connections | `apps/api` |
| `cortex_client.py` | **REPLACE** | Per-call session + `USE DATABASE` switching; replaced by pooled connection + bind params | `apps/api` / `services/common` |
| Frontend `DBTCodeViewer.tsx`, `LineageGraph.tsx`, `Stepper.tsx`, `Pagination.tsx` | **REFACTOR** (port to Next.js) | Useful UI logic; stack changes from Vite to Next.js per prompt | `apps/web` |
| Frontend remaining pages/hooks (`useMappingsApi.ts`, CSV parser, `storageClient.ts`) | **DEPRECATE** | Bound to the CSV-project model | — |
| `snowflake_complete_setup.sql` | **REPLACE** | Destructive (`DROP TABLE`), wrong data model | `snowflake/database` |
| `Dockerfile`, `docker-compose.yml`, `nginx.conf` | **REFACTOR** | Container pattern reusable for `apps/web` + `apps/api` | `infrastructure` |

---

## 3. Snowflake feature verification (official docs, checked 2026-10-03)

| Feature | Result | Consequence for design |
|---|---|---|
| `CREATE AGENT ... FROM SPECIFICATION $$yaml$$` with `orchestration.budget`, `instructions`, `tools`, `tool_resources` | Verified | Agent defined as code in `snowflake/agents/` |
| Custom tools = stored procedure / UDF on a warehouse | Verified | All tools are procedures |
| Custom tool parameters of type `OBJECT` | **Not supported** | Tools take `VARCHAR` ids / JSON strings; schemas validated inside the procedure |
| Procedures used as tools keep owner's / caller's rights | Verified | Read tools: owner's rights; decision tools: caller's rights (reviewer identity) |
| Agent REST API `agents/{name}:run`, SSE events (`response.status`, `response.text.delta`, `response.tool_use`, thinking deltas), threads | Verified | BFF proxies SSE; UI shows tool/status events only, never `response.thinking.*` |
| Agent skills from stage / Git; `CORTEX_EXTENSION` on stateless run needs `experimental.ReasoningAgentToolConfig` | Verified (partly experimental) | Skills loaded via agent spec; stateless/inline skill use treated as experimental |
| Built-in code execution tool (sandboxed Python) | Verified | Not used for code generation in Phase 1 (deterministic templates instead) |
| `CREATE CORTEX SEARCH SERVICE` multi-index (`TEXT INDEXES` + `VECTOR INDEXES`), `ATTRIBUTES` filters, `TARGET_LAG`, `PRIMARY KEY` | Verified | Hybrid keyword+semantic retrieval with domain/status/version filters; refresh is service-managed (not a Task) |
| Imported (share) databases | Read-only; **no clone, no Time Travel**; `CREATE DATABASE ... FROM SHARE` needs `IMPORT SHARE` + `CREATE DATABASE` | Landing = `CREATE TABLE ... AS SELECT` (or `INSERT ... SELECT`). Mounting a share is an **admin prerequisite**, never an agent action |
| `COPY INTO` for share sources | Not applicable (COPY loads staged files) | COPY reserved for the future `FILE`/`S3` adapters |
| `CREATE DBT PROJECT ... FROM '@stage/path'` with `AUTO_COMPILE`, `EXECUTE DBT PROJECT ARGS='compile'|'parse'|'test'`, `WRITEBACK = FALSE` | Verified | Validation deploys each generation to a versioned stage path and compiles it in Snowflake; `dbt deps` needs an external access integration → Phase 1 dbt output avoids packages or pre-vendors them |
| Some dbt-project features require the mutable `live` version (2026_06 bundle) | Verified | Check bundle status on the account before Phase 11 |

Not yet verified (must be checked before the phase that needs it): Snowflake External OAuth with Entra ID for REST API calls on behalf of a user; `AI_COMPLETE` `response_format` structured-output limits for the selected models; Snowflake Anaconda availability of `jinja2`, `sqlglot` (query `INFORMATION_SCHEMA.PACKAGES`); Cortex Code / `code_toolset_all` availability on this account.

---

## 4. Key architecture decisions

1. **One supervisor agent** (`DATA_ENGINEERING_SUPERVISOR`) with ~15 custom tools, one Cortex Search tool and per-capability skills. Specialist "agents" are skills + tools, not separate Cortex Agents (simplest mechanism that satisfies the requirement; fewer tokens, deterministic tool behavior).
2. **The state machine lives in Snowflake.** `WORKFLOW_TRANSITION` (allowed `from_state → to_state`, required role, required artifact status) + procedure `TRANSITION_RUN`. Both the agent tools and the FastAPI backend call it; neither can bypass it. Phase 2 states (`DEPLOYMENT_PENDING`, `DEPLOYED`, `PIPELINE_ENABLED`) exist in the state table with `ENABLED = FALSE`.
3. **Two paths to the same procedures.** Chat → Agent → tools (proposals, generation). Review screens → FastAPI → caller's-rights decision procedures (approvals). Approvals are never mediated by the LLM.
4. **One Python package, two runtimes.** `services/` is pure Python (no Streamlit, no FastAPI imports), unit-tested locally, deployed to a stage and imported by stored procedures. The backend calls procedures rather than duplicating logic.
5. **Facts in SQL, interpretation in Cortex.** Profiling stats, feature scores, STTM assembly, Soda YAML, dbt files are deterministic. LLM calls: column descriptions/semantic type, domain recommendation, mapping adjudication for ambiguous candidates, transformation expressions that cannot be templated.
6. **Everything versioned, append-only.** Registries carry `*_VERSION` and `IS_CURRENT`; regeneration inserts a new version; nothing is overwritten.
7. **Sample values gated by classification.** Samples stored only after PII classification; masked via masking policy for roles below `DATA_STEWARD`; never sent to the LLM when classified sensitive.

Repository layout (inside `gdp-agent-platform/`, following section 57 of the master prompt): `apps/web`, `apps/api`, `snowflake/{database,schemas,tables,views,procedures,functions,cortex_search,agents,skills}`, `services/{source,profiling,mapping,sttm,soda,dbt,validation,git}`, `domain/gdp`, `tests/{unit,integration,e2e,evaluation}`, `docs`, `infrastructure`. The `agents/` top-level folder from the prompt is folded into `snowflake/agents` + `snowflake/skills` because Snowflake owns those definitions.

---

## 5. Dependency analysis

External prerequisites (owner: Snowflake admin unless stated):

| # | Prerequisite | Needed by phase |
|---|---|---|
| D1 | Dev Snowflake account access for integration tests (key-pair or PAT in local env, never committed) | 1 |
| D2 | Roles `PLATFORM_ADMIN`, `SERVICE_AGENT`, `DATA_ENGINEER`, `REVIEWER`, `DATA_STEWARD`, `DOMAIN_OWNER`, `VIEWER`; warehouse for platform + one for Cortex Search | 1 |
| D3 | `SNOWFLAKE.CORTEX_USER` for `SERVICE_AGENT`; model access (Claude / GPT / Llama confirmed available) | 4 |
| D4 | An inbound share mounted as a database + `IMPORTED PRIVILEGES` granted to `SERVICE_AGENT`. A real inbound share requires a **second (provider) account** | 3 |
| D5 | External OAuth security integration (Entra ID) for per-user identity | 2 |
| D6 | `CREATE DBT PROJECT` on a sandbox schema; 2026_06 bundle status | 11 |
| D7 | (Optional) ADO service principal + external access integration for branch/PR | 12 |
| D8 | (Later) Soda Cloud API key; Phase 1 only generates and validates Soda YAML, no execution | 9 |

Internal build order (each depends on the previous): metadata DDL + state machine → services/common (connection, Cortex wrapper, audit) → source adapter + landing → profiling → knowledge + Cortex Search → mapping features + candidates → decisions → STTM → Soda → dbt render → validation → review/versioning. The frontend is built against the API contract in parallel from Phase 2 onward.

---

## 6. Implementation plan

Each phase ends with: unit tests passing locally, integration test against the dev account, and a short note appended to `docs/`.

| Phase | Deliverables | Verification |
|---|---|---|
| 1 Foundation | `AI_PLATFORM` DDL (all registries in the prompt + `WORKFLOW_TRANSITION`, `AGENT_RUN`, `AGENT_TOOL_CALL`, `COST_USAGE`, `MAPPING_SCORING_CONFIG`), roles/grants, `TRANSITION_RUN` procedure, idempotent deploy script | Deploy twice (idempotent); transition tests: legal moves pass, illegal moves and Phase-2 states rejected |
| 2 Frontend + API skeleton | Next.js + Tailwind + shadcn shell with the required routes, stage rail driven by `GET /api/runs/{id}`; FastAPI with auth middleware, run CRUD, SSE proxy stub | Playwright smoke: login → create run → stage rail reflects backend state |
| 3 Source onboarding | `SourceAdapter` + `SnowflakeShareAdapter` + `SnowflakeDatabaseAdapter` (share adapter = database adapter + share checks); `validate_source_access`, `discover_source_objects`, `register_source`, `generate_landing_objects`, `execute_landing` (CTAS, row-count reconciliation) | Integration: land demo tables, row counts equal, `LANDING_*_REGISTRY` populated |
| 4 Profiling | Ported `profile_table` + extended stats, PII classification, masked samples, enrichment via structured `AI_COMPLETE` | Unit tests on SQL builders; integration on demo source |
| 5 Knowledge | `DOMAIN_REGISTRY`, `DOMAIN_KNOWLEDGE` seed for GDP, Cortex Search service, skills (`SNOWFLAKE_SHARE_ONBOARDING`, `GDP_DOMAIN`, `MAPPING`, `STTM`, `SODA`, `DBT`), supervisor agent spec | Retrieval returns filtered top-K; agent can call two tools end-to-end |
| 6 Hybrid mapping | Feature calculators (semantic via stored embeddings, keyword, datatype, statistical, domain, historical, context), configurable weights, top-K candidates, LLM adjudication for the ambiguous band | Demo `CRM_CUSTOMER → DIM_CUSTOMER` produces expected top-1 for all 7 columns; evaluation script logs precision |
| 7 Approval | Decision procedures (caller's rights), mapping review UI with justification | Reviewer identity = Snowflake user; no STTM transition until all required mappings decided |
| 8 STTM | Deterministic STTM assembly (incl. table-level grain/keys/join/SCD section), review UI, export | Contract schema validation tests |
| 9 Soda | Expectation registry, client Excel/CSV import, Soda YAML render, approval | YAML schema validation; golden-file tests |
| 10 dbt | STTM → model plan → Jinja templates (staging/intermediate/mart, `schema.yml`, `sources.yml`, tests, docs), macro library reuse, versioned `DBT_GENERATION_REGISTRY` | Golden-file tests for demo |
| 11 Validation | Deploy generation to stage path → `CREATE/ALTER DBT PROJECT` → `EXECUTE ... ARGS='compile'`; naming / required-column / datatype / STTM-consistency / Soda-config checks → `VALIDATION_RUN` | Failing validation blocks `DBT_APPROVED` |
| 12 Review | Diff vs previous generation (and existing project if provided), approve / request changes / regenerate; optional ADO branch + PR (no merge) | Regenerate creates new version; diff renders |
| 13 E2E | Full demo script + Playwright e2e | Section 60 steps 1–28 pass |

Phase 2 extension points kept open: `WORKFLOW_STATE.ENABLED` flag, `ENVIRONMENT` and `CONFIG_VERSION` on `WORKFLOW_RUN`, `DBT_GENERATION_REGISTRY.GENERATION_ID` as the hand-off key for a future `PIPELINE_METADATA`, and the dbt project object naming convention. No `TASK` objects are created in Phase 1.

---

## 7. Risks and open items

- **Demo share needs a provider account (D4).** Fallback for the first demo: run the identical flow through `SnowflakeDatabaseAdapter` on a normal database, then switch to the share adapter when a provider account is available.
- **Per-user OAuth (D5) is an admin dependency.** Until configured, the dev build can use per-user PATs; production must use External OAuth.
- **STTM needs table-level design** (grain, business keys, join paths, SCD, incremental strategy). Without it dbt generation reverts to guessing. Added as an explicit STTM section with its own approval.
- **No ground-truth set yet.** Mapping precision in Phase 6 is measured on the demo dataset only; real calibration needs accumulated `MAPPING_DECISION` history.
- **Cortex Coding Agent.** The prompt asks to use it where appropriate. Phase 1 uses deterministic templates for generation; coding-agent use is limited to optional "request changes" edits, after availability is verified (not yet verified on this account).
