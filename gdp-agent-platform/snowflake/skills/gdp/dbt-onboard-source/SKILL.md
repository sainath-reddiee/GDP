---
name: gdp-dbt-onboard-source
description: "Domain-agnostic skill to onboard a new bronze source into a GDP silver iceberg SCD1 + HIST model. Accepts an STTM CSV as input — auto-derives source tables, target tables, joins, source_unique_id, and column maps. Loads per-domain canonical contract from references/<domain>-contract.md. Generates ephemeral staging model(s), extends hub model(s), creates macros if missing, and registers watermark(s). Supports multi-target + multi-source-table UNION patterns. Triggers: 'onboard <domain> source', 'STTM to dbt', 'generate dbt from STTM', 'add source to <domain>', 'gdp onboard'."
---

# GDP dbt — Onboard Source Skill (Domain-Agnostic)

Reusable scaffold for adding a new bronze source to ANY GDP silver iceberg SCD1 + HIST hub model in `gdp-dbt`.

The domain-specific canonical column contract, reference-table joins, type-cast rules, and HKEY column list live in `references/<domain>-contract.md`. The workflow below is identical across domains.

---

## Autonomy Mode (default: AUTO)

The skill runs end-to-end without stopping for confirmation EXCEPT for the following true blockers:

**ALWAYS stop and ask** when:
1. Source system not found in `REF_GDP_SOURCE_SYSTEM` AND no clear inferable replacement
2. Bronze table referenced in STTM does not exist anywhere in `DEV_GDP_BRONZE_DB` (including under different schemas)
3. STTM target column doesn't exist on the silver table AND no obvious rename mapping
4. Compound `SOURCE_UNIQUE_ID` cannot be deterministically derived from STTM
5. Hub model file path conflict (would overwrite existing source's branch)

**NEVER stop for these — apply default and continue (note in summary):**
- Domain contract is a stub → populate inline from DESCRIBE TABLE on the fly
- LOV Mapping references → emit `null` with `-- TODO: LOV mapping pending` comment
- STTM column not in silver contract (drop, e.g. LEGAL_ENTITY_NAME) → skip with note
- Contract column not mapped in STTM → emit `null`
- Source-table name case mismatch → resolve via DESCRIBE
- `<ESRI ADDRESS Standardisation>` annotations → emit `null` with TODO comment
- Multiple bronze candidates with similar names → prefer non-DIM/non-HIST/non-BKP variant with most rows
- Bronze schema not in AGENTS.md registry → use INFORMATION_SCHEMA lookup, note for AGENTS.md update
- Per-target join graph differences (e.g., EDP Pattern A vs Pattern B) → generate distinct ephemeral models per target
- Files-to-create plan → execute directly, summarize at end

The user can override by saying "ask before X" or "manual mode".

---

## Input Modes

The skill accepts inputs in two modes:

### Mode A — STTM-driven (preferred)

User provides a path to an STTM CSV (e.g., `GDP/projects/gdp-<domain>/mappings/<source>/...STTM.csv`). The skill parses the CSV and derives ALL required inputs automatically. **No manual column-map needed.**

### Mode B — Manual inputs

User provides each input directly. Use only when no STTM exists.

---

## Step 0 — STTM Parsing (Mode A only)

When invoked with an STTM path, parse it deterministically using the GDP STTM CSV layout:

### STTM CSV Schema

| Header Row | Format | Extracts |
|---|---|---|
| Row 1 | `Domain ,<value>` | `domain` (lowercase) |
| Row 2 | `Target DB.SCHEMA,<value>` | Validates `DEV_GDP_SILVER_DB.<DOMAIN>` |
| Row 3+ | `Target Table,"<multi-line list>"` | `targets[]` (one per line, lowercase entity name) |
| Next | `Source Tables,"<multi-line list>"` | `source_tables[]` (one per line) |
| Next | `Join,"<multi-line SQL>"` | `join_sql` (raw FROM/JOIN clause + UNION ALL pattern) |
| Next | `Filters,...` | `where_clauses[]` |
| Body | `<Target Table>,<Target Column>,,<Source Table.Column>,<Transformation>,<Notes>` | Column map per target |

### Extraction Algorithm

See [`references/sttm-mapping-rules.md`](references/sttm-mapping-rules.md) for the deterministic rulebook (header parsing, body classification, multi-source UNION detection, per-target join graph detection, empty-target drop, source-system reconciliation, bronze-table discovery, anomaly handling, output plan format).

High-level (AUTO mode — no stops unless true blocker):

```
1. Read CSV, strip blank rows + trailing-comma padding (Excel artifact)
2. Parse header block until first body row
3. Infer:
   - domain                   = Row 1.Domain (lowercase). If absent, infer from STTM file path (`gdp-<domain>/`).
   - target_db, target_schema = Row 2 (split on '.'). Default DEV_GDP_SILVER_DB.<DOMAIN>.
   - targets[]                = unique list from "Target Table" header (lowercase, strip schema prefix)
   - source_tables[]          = list from "Source Tables" header
   - source_fqns[]            = each source_table resolved by:
       (a) AGENTS.md source registry lookup, then
       (b) INFORMATION_SCHEMA.TABLES scan in DEV_GDP_BRONZE_DB if (a) misses, then
       (c) STOP if not found anywhere.
     If STTM names a *_SYNTHETIC / *_BACKUP / *_TEST variant: prefer the live equivalent (most rows, no _BKP/_HIST/_TEST/_SYNTHETIC suffix).
   - source_system_name       = resolved by:
       (a) STTM transformation `GDP_SOURCE_SYSTEM_NAME = '<X>'` if present,
       (b) STTM file path parent folder UPPER if (a) absent,
       (c) Look up in REF_GDP_SOURCE_SYSTEM. If exact match misses, fuzzy match (e.g. STTM 'FINANCIAL_RCOE' → registry 'FPD' SKEY 202). If no fuzzy match, STOP.
   - source_unique_id_expr    = COMPOUND_PK rule (see rulebook). Compound keys MUST use `||` delimiter + `coalesce(...,'')`.
   - join_blocks[]            = parse "Join" cell. Detect:
       • single-source pattern (one FROM clause)
       • multi-source UNION ALL pattern (split on UNION ALL)
       • per-target join graph (target name markers like `------ <TARGET>` or distinct join shapes per target)
4. Body rows → per-target column maps. Apply transformation classification (rulebook).
5. Drop empty targets: any target with 0 PASSTHROUGH/FK_LOOKUP/COMPOUND_PK rows is auto-dropped (note in summary).
6. Cross-validate against `references/<domain>-contract.md`:
   - If contract is a stub: populate inline from DESCRIBE TABLE on each target.
   - STTM target_col not in contract: drop with note (do NOT stop unless `--strict` flag).
7. Run DESCRIBE TABLE on each source_fqn. Identify type-mismatch casts.
8. Proceed to file generation per Steps 2–7. Stop only on the 5 true blockers (Autonomy Mode).
```

### STTM Anomalies (AUTO-mode handling)

The skill DOES NOT stop on these — it applies a default and notes in summary:

- Target column in STTM but not in contract → DROP with note (e.g., MTA STTM has `LEGAL_ENTITY_NAME`/`TRADE_NAME` not in COMPANY_CORE → drop).
- Contract column not mapped in STTM → emit `null`, list in unmapped summary.
- Source column reference not in `DESCRIBE TABLE` output → case-insensitive retry; if still missing, emit `null` with TODO comment.
- `<ESRI ADDRESS Standardisation>` / `<ESRI OUTPUT>` → emit `null` with `-- TODO: ESRI step` comment.
- STTM Source Tables outside `BRONZE_<SOURCE>` → INFORMATION_SCHEMA scan, prefer non-HIST/non-BKP.
- STTM source-system name doesn't match registry → fuzzy match (e.g., FINANCIAL_RCOE → FPD).
- LOV mapping references → emit `null` with `-- TODO: LOV mapping pending`.
- Empty target (0 mapped business cols) → DROP target, note in summary.

Only the 5 true blockers in Autonomy Mode require stopping.

---

## Required Inputs (Mode B — manual)

Collect ALL of these before generating any files. Ask the user for any not provided.

| Input | Example | Notes |
|---|---|---|
| `domain` | `opportunity` | Lowercase domain folder name. Drives `references/<domain>-contract.md` lookup. |
| `entity` | `opportunity_core` | Hub model file name (without `.sql`). UPPER form used for watermark + tags. |
| `source_fqns` | `['DEV_GDP_BRONZE_DB.BRONZE_MTA.PS_CUSTOMER', 'DEV_GDP_BRONZE_DB.BRONZE_MTA.PS_VENDOR']` | One or more bronze tables. Multiple tables → UNION ALL extraction (see Multi-Source Pattern). |
| `source_system_name` | `MTA` | Must exist in `REF_GDP_SOURCE_SYSTEM.GDP_SOURCE_SYSTEM_NAME` |
| `source_prefix` | `mta` | Short name for ephemeral file. Produces `<prefix>_<entity>.sql` |
| `source_key` | `mta` | Value passed to `m_is_source_active('<source_key>')` in hub model |
| `source_unique_id_expr` | `c."Name1" \|\| '\|\|' \|\| coalesce(ca."COUNTRY",'')` | Source column(s) used as `SOURCE_UNIQUE_ID`. Compound keys MUST use `\|\|` delimiter + `coalesce(...,'')` sentinel. |
| `targets` | `['company_core', 'company_address', 'company_industry']` | One or more silver tables this source onboards. Single-target = traditional hub. Multi-target = generates one ephemeral model per target. |
| `column_map` | See `column_map` schema below | Per-target map: bronze columns to canonical contract aliases. Columns not mapped get `null`. |

### Column Map Entry Structure

Each mapped column needs:
- `source_col`: Bronze column name (quoted if case-sensitive, e.g., `"Id__c"`)
- `target_col`: Canonical target alias (must appear in the domain contract, e.g., `opportunity_name`)
- `source_type`: Bronze data type (`TEXT`, `NUMBER`, `DATE`, `TIMESTAMP_NTZ`, `BOOLEAN`)
- `target_type`: Silver target type (look up in `references/<domain>-contract.md`)
- `is_text`: `true` if column should get `NULLIF(TRIM(...), '')` treatment

---

## Architecture Overview

### Single-target, single-source (simple)
```
Bronze Source Table              →   Ephemeral Staging Model               →   Hub Model (<entity>.sql)
(<ENTITY_UPPER>_<SRC>_DEV)            (<prefix>_<entity>.sql)                   UNION ALL branch + SCD1 merge
```

### Multi-source, multi-target (MTA-style)
```
N bronze tables → UNION ALL extraction CTE → M ephemeral staging models (one per target)
                                              → M hub-model patches
                                              → M watermark rows
```

### File Layout After Onboarding

```
models/
├── bronze/
│   └── <entity>_<source_key>_source.yml         ← NEW: bronze source declaration (lists ALL bronze tables)
└── silver/
    └── <domain>/
        ├── <source_key>/
        │   ├── <prefix>_<target_1>.sql          ← NEW: ephemeral staging for target 1
        │   ├── <prefix>_<target_2>.sql          ← NEW: (only if multi-target)
        │   └── ...
        ├── <target_1>.sql                       ← MODIFIED: add UNION branch
        ├── <target_2>.sql                       ← MODIFIED: (only if multi-target)
        └── <entity>_reference.yml               ← MODIFIED (if new shared refs needed)

macros/
└── <domain>_utils.sql                           ← CREATED if missing (HKEY macros for ALL targets)
```

---

## GDP Standard Rules (MUST follow — domain-independent)

### Rule 1: Extraction CTE (`sp_<entity_plural>`)
- **TEXT columns**: `NULLIF(TRIM("Column_Name"), '') as target_alias`
- **NUMBER/FLOAT columns**: `"Column_Name" as target_alias` (plain passthrough)
- **DATE/TIMESTAMP columns**: `"Column_Name" as target_alias` (plain passthrough)
- **Derived columns**: CASE/IFF expressions
- **Dedup**: `qualify row_number() over(partition by <PK> order by gdp_updated_ts desc) = 1`
- **Filter**: `where <PK> is not null and TRIM(<PK>) <> ''`
- **Multi-source UNION**: When `source_fqns` has > 1 entry, build one branch per source pair (e.g., `PS_CUSTOMER` + `PS_CUST_ADDRESS`) with **identical aliases and order** in each branch, then `UNION ALL`, then dedup with `qualify` over `source_unique_id`. See `references/<domain>-contract.md` Multi-Source-Table Extraction Pattern.

### Rule 2: Base CTE (column conformance)
- Type cast ONLY where bronze source type differs from silver target type:
  - `DATE` → `TIMESTAMP_NTZ`: `o.col::timestamp_ntz as target_col`
  - `NUMBER` → `FLOAT`: `o.col::float as target_col`
  - `NUMBER(p1,s1)` → `NUMBER(p2,s2)`: cast only if precision/scale differ
- Unmapped columns: plain `null` (NO type suffix like `null::varchar`)
- FK resolution: LEFT JOIN to reference tables using `upper(trim(...))` matching

### Rule 3: HKEY Macro (`m_<entity>_hkey`)
- Include ONLY pure business/descriptive columns in DDL position order
- EXCLUDE: `*_SKEY`, `SOURCE_UNIQUE_ID`, `<ENTITY_UPPER>_HKEY`, all `GDP_*` audit columns
- The exact column list lives in `references/<domain>-contract.md` under the HKEY section

### Rule 4: Defensive Coding (per AGENTS.md)
- Parent SKEY lookups: ALWAYS add `QUALIFY ROW_NUMBER() OVER (PARTITION BY SOURCE_UNIQUE_ID ORDER BY GDP_INSERTED_TS DESC) = 1`
- SOURCE_UNIQUE_ID: Use safe CONCAT with `||` delimiter and blank `''` sentinel for NULLs
- Joins to ref tables: Always LEFT JOIN (never INNER) to prevent silent data loss
- Bronze dedup: Add optional QUALIFY on PK when source has known duplicates
- Watermark: Param 2 = UPPERCASE ref model name (e.g., `'<ENTITY_UPPER>'`)
- Column names with spaces: Must be double-quoted (`"Company Name"`)

---

## Workflow

### Step 1 — Collect & Validate Inputs

**Mode A (STTM)**:
1. Run Step 0 (STTM Parsing) end-to-end.
2. Load `references/<domain>-contract.md`. If file is a stub: populate inline from `DESCRIBE TABLE` on each target silver table.
3. Cross-validate STTM targets/columns against the contract; build anomaly list (handled per AUTO rules).
4. `DESCRIBE TABLE` each `source_fqns[]` entry. Capture column names + types.
5. Verify source system in `REF_GDP_SOURCE_SYSTEM`. If exact miss, fuzzy match. If still miss, STOP.
6. Identify type-mismatch casts.
7. **AUTO mode**: proceed to file generation. Stop only on the 5 true blockers.

**Mode B (manual)**: skip step 1.1; user provides inputs directly. Steps 2–7 still apply.

### Step 2 — Generate Bronze Source YAML

Path: `models/bronze/<entity>_<source_key>_source.yml`

Template: [`assets/bronze_source_template.yml`](assets/bronze_source_template.yml). Copy and replace the placeholders; expand `columns:` from `DESCRIBE TABLE <source_fqn>`.

### Step 3 — Generate Ephemeral Staging Model(s)

For EACH target in `targets`:

Path: `models/silver/<domain>/<source_key>/<prefix>_<target>.sql`

Skeleton: [`assets/ephemeral_model_template.sql`](assets/ephemeral_model_template.sql).

Fill the four marked blocks from the target's section in `references/<domain>-contract.md`:
1. REFERENCE CTE BLOCK (per-target)
2. FK SKEYs + business columns (in DDL order, with type casts only where needed)
3. REFERENCE JOIN BLOCK (per-target)
4. FINAL SELECT BLOCK (per-target)

**Spoke models** (target != hub): JOIN to `{{ ref('<hub_entity>') }}` to resolve `<HUB>_CORE_SKEY` from `SOURCE_UNIQUE_ID` instead of cross-join only.

**Multi-source extraction**: When `source_fqns` > 1, replace the single `from {{ source(...) }}` with the UNION ALL block from the contract's Multi-Source-Table Extraction Pattern.

### Step 4 — Create Macros (if `macros/<domain>_utils.sql` does not exist or is missing macros)

Path: `macros/<domain>_utils.sql`

Template: [`assets/domain_utils_macro_template.sql`](assets/domain_utils_macro_template.sql).

For EACH target in `targets`, add an `m_<target>_hkey` macro using the per-target HKEY BLOCK from the contract. The `m_get_source_system_skey_<domain>` macro is shared across all targets in the domain (create once).

### Step 5 — Extend Target Hub Model(s)

For EACH target in `targets`: patch `models/silver/<domain>/<target>.sql` using [`assets/hub_model_patch.sql`](assets/hub_model_patch.sql). Insert the source CTE after existing source CTEs and append the UNION branch inside the `unioned` CTE.

**If hub model file does not exist**: AUTO mode creates a minimal hub-model skeleton (SCD1 merge + UNION ALL across source branches). Note in summary; user can replace with their preferred pattern later.

### Step 6 — Insert Watermark Row(s)

For EACH target in `targets`: emit one INSERT using [`assets/watermark_insert.sql`](assets/watermark_insert.sql). Each watermark row is keyed on `(SOURCE_SYSTEM, SOURCE_MODEL_NAME='<SOURCE_KEY_UPPER>_<TARGET_UPPER>', TARGET_MODEL_NAME='<TARGET_UPPER>')`. Idempotent — guarded by `WHERE NOT EXISTS`.

### Step 7 — Update `dbt_project.yml` (if needed)

Ensure the bronze schema var exists:
```yaml
vars:
  bronze_schema_<source_key>: 'BRONZE_<SOURCE_KEY_UPPER>'
```

### Step 8 — Verify

1. `dbt compile --select <prefix>_<target1> <prefix>_<target2> ... <target1> <target2> ...` — compile ALL new ephemeral models AND all modified hub models. Must succeed.
2. Spawn a dbt-verify pass on the listed file changes.
3. Spawn a sql-verify pass on the compiled SQL of each target.
4. `dbt build --select +<target1> +<target2> ... --vars 'active_source: <source_key>'` — only after user approves. Hub-order matters: build `company_core` first, then spokes, since spokes resolve `COMPANY_CORE_SKEY` via `ref('company_core')`.

---

## Stopping Points (require user confirmation)

In AUTO mode (default), only the 5 true blockers in Autonomy Mode require stopping. Otherwise the skill executes end-to-end and presents a final summary of decisions taken, files created, and remaining TODOs.

---

## Domain Contract Files

| Domain | Reference File | Status |
|---|---|---|
| opportunity | `references/opportunity-contract.md` | Complete (single-hub, 147-col contract). |
| company | `references/company-contract.md` | Complete (multi-target: COMPANY_CORE + 5 spokes; multi-source UNION pattern). |
| property | `references/property-contract.md` | Complete (4 targets: CORE/USAGE/ADDRESS/OWNER; per-target join graph pattern). |

To onboard a brand-new domain (e.g., `contact`):
1. Skill auto-creates `references/<domain>-contract.md` by running `DESCRIBE TABLE` against the domain's silver targets (declared in the STTM `Target Table` cell).
2. The skill populates the contract sections (column list, HKEY block, type-cast table) on the fly from DDL.
3. User can refine the contract after first onboarding for downstream sources to consume.
