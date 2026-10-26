# Unified Plan: Autonomous Happy-Path Pipeline, Default Cortex AI Mapping (with Target Samples), and Store-to-Publish UX

Yes — this unified plan includes **100% of the initial plan** plus **all enhancements from the 4 screenshots**:

---

## Part A: Initial Plan Items (Included)
1. **Fix Next.js Invalid Hook Call (`useState` / `useContext` null dispatcher)**:
   - Pin `react`, `react-dom`, `react/jsx-runtime`, and `react/jsx-dev-runtime` webpack aliases in [apps/web/next.config.mjs](gdp-agent-platform/apps/web/next.config.mjs) and clean `.next` cache.
2. **Default Cortex AI Suggestions in `onboarding_service` & `sttm_engine`**:
   - Upgrade `propose_target_models()` in [services/onboarding_service/service.py](gdp-agent-platform/services/onboarding_service/service.py) to use Cortex AI semantic model recommendation after profiling a Bronze table.
   - Upgrade `generate_sttm()` in [services/sttm_engine/service.py](gdp-agent-platform/services/sttm_engine/service.py) to run `ai_suggest_mapping()` automatically by default (`use_ai: bool = True`), remove the blind `_ID` suffix heuristic mismatch, wrap cross-type expressions in `TRY_CAST(<expr> AS <TARGET_TYPE>)`, and synthesize deterministic surrogate/business key expressions for PKs so the 7-dimension validator scores ≥ 93%.
3. **Autonomous Happy-Path Backend Orchestrator (`happy_path = True`)**:
   - Update `advance_run_stage()` and `start_multi_table_run()` in [services/agent_runtime/service.py](gdp-agent-platform/services/agent_runtime/service.py) and [apps/web/app/api/snowflake/run-action/route.ts](gdp-agent-platform/apps/web/app/api/snowflake/run-action/route.ts) (`run_autonomous_happy_path`) so once Domain and Target Models are locked, the pipeline runs asynchronously in the backend through `PROFILING → MODELING → MAPPING → CODEGEN → DQ_CHECK → VALIDATING → PR_CREATE → PUBLISH → COMPLETED` with zero `AWAITING_HUMAN_REVIEW` stops.
4. **Streamlined `/os/enable` → `/os/onboarding` & `/os/design` Flows**:
   - Auto-profile the Bronze table when navigating from `/os/enable` to `/os/onboarding?source=...&table=...&domain=...`.
   - Provide **"Lock Models & Run Autonomous Pipeline →"** in both [apps/web/app/os/onboarding/page.tsx](gdp-agent-platform/apps/web/app/os/onboarding/page.tsx) and [apps/web/app/os/design/page.tsx](gdp-agent-platform/apps/web/app/os/design/page.tsx) to lock selected models and jump directly to `/os/build`.

---

## Part B: Four-Screenshot Enhancements (Included)
1. **Screenshot 1 — Store Table Selection ([apps/web/app/sources/sources-hub.tsx](gdp-agent-platform/apps/web/app/sources/sources-hub.tsx))**:
   - Rename floating bar button from `Send to modeling` → **`Start build`**.
2. **Screenshot 2 — Fast `Start build` Modal & Silver-Only Target Models ([apps/web/app/sources/model-panel.tsx](gdp-agent-platform/apps/web/app/sources/model-panel.tsx) & [services/schema_profiler/service.py](gdp-agent-platform/services/schema_profiler/service.py))**:
   - Exclude `BRONZE` databases/schemas from `_load_target_candidates()` so Bronze tables (like `DEV_GDP_BRONZE_DB.BRONZE_LIGHTBOX.ADDRESSES`) never show up as target models; only Silver/registered `CORE.TARGET_TABLE_REGISTRY` models appear.
   - Add a **Domain selector** at the top of `ModelPanel` so the user can confirm/switch the domain and check the required target models.
   - Speed up run creation 10x by removing the duplicate catalog scan inside `start_catalog_modeling_run()` and batch-inserting `CORE.COLUMN_PROFILE` rows in a single multi-row SQL `INSERT` instead of 60+ per-column round-trip queries.
   - Rename `Create modeling run` → **`Lock Models & Start Build`**.
3. **Screenshots 3 & 4 — Lock Selected Models Immediately & Skip Confusing `/modeling?tab=mapping` Screen ([apps/web/app/sources/model-panel.tsx](gdp-agent-platform/apps/web/app/sources/model-panel.tsx), [apps/web/app/sources/actions.ts](gdp-agent-platform/apps/web/app/sources/actions.ts), [apps/web/app/runs/[id]/modeling/modeling-workspace.tsx](gdp-agent-platform/apps/web/app/runs/[id]/modeling/modeling-workspace.tsx))**:
   - When **`Lock Models & Start Build`** is clicked, lock `LOCKED_MODEL_ID` on the run immediately, skip `/os/build/runs/[id]/modeling?tab=mapping` completely, redirect straight to **`/os/build`**, and trigger the asynchronous backend happy-path pipeline to `COMPLETED`.
   - If a user ever opens `/os/build/runs/[id]/modeling` directly, keep the `Data model` section locked and collapsed when target models were already selected.
4. **Profile Target Table Sample Values to Improve Cortex AI Mapping ([services/agent_runtime/service.py](gdp-agent-platform/services/agent_runtime/service.py) & [services/sttm_engine/service.py](gdp-agent-platform/services/sttm_engine/service.py))**:
   - Add `_load_target_table_samples()` to sample existing Silver target table rows + `CORE.TARGET_COLUMN_REGISTRY` descriptions and pass both **source column sample values** AND **target column sample values/metadata** into `SNOWFLAKE.CORTEX.COMPLETE` inside `suggest_mapping_cortex()` and `ai_suggest_mapping()`.
   - Make `propose_mapping()` call `suggest_mapping_cortex()` by default so initial mappings are always Cortex AI-generated with source + target sample context.
5. **Snowflake Deployment & Client SQL Sync**:
   - Deploy all updated services and migrations (`V082`) via `python infrastructure/deploy.py --env dev --yes`, regenerate `deploy-logs/client-prod-render.sql`, and verify end-to-end across `/os/design/sources`, `/os/enable`, `/os/onboarding`, `/os/build`, `/os/review` (**Validate**), `/os/release` (**Publish**), and `/os` (**Build Workflows**).
