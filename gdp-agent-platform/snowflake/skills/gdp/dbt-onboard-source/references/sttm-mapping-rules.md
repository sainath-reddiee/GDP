# STTM CSV → dbt Code Mapping Rules

How the skill translates each STTM transformation note into GDP-standard SQL. This is the deterministic rulebook that backs Step 0 in `SKILL.md`.

---

## Header Block Parsing

The STTM CSV begins with a header block (typically rows 1–22). Field names are case-insensitive.

| Header Field | Maps To | Notes |
|---|---|---|
| `Domain` | `domain` | Lowercase |
| `Target DB.SCHEMA` | `target_db`, `target_schema` | Validates `DEV_GDP_SILVER_DB.<DOMAIN>` |
| `Target Table` | `targets[]` | Multi-line cell. Each line = one target. Strip schema prefix. |
| `Source Tables` | `source_tables[]` | Multi-line cell. Each non-empty line = one bronze table. |
| `Join` | `join_blocks[]` | Free-form SQL. Split on `UNION ALL` to identify multi-source pattern. |
| `Filters` | `where_clauses[]` | Append to extraction CTE WHERE clause. |

---

## Body Row Layout

Body rows after the header block follow:
```
<Target Table>,<Target Column>,,<Source Table.Column>,<Transformation>,<Notes>
```

Empty rows separate target groups. Group by `Target Table` to build per-target column maps.

---

## Transformation Classification

Each body row's `Transformation` cell is classified into one of these categories. Each category has a deterministic code template.

### 1. AUDIT
- **Triggers**: `transformation = "ETL AUDIT"`
- **Action**: Skip — handled by ephemeral template footer (`gdp_inserted_ts` etc.).

### 2. SEQUENCE
- **Triggers**: `transformation = "DB Auto incremental ID"`
- **Action**: Skip — handled by hub model SCD1 merge (sequence default in DDL).

### 3. SOURCE_SYSTEM_REF
- **Triggers**: `transformation` matches `GDP_SOURCE_SYSTEM_NAME = '<X>'`
- **Action**: Capture `<X>` as `source_system_name`. Skip from column map (handled by `cross join ref_source_system`).

### 4. COMPOUND_PK
- **Triggers**: `target_col = SOURCE_UNIQUE_ID` AND source contains `+` or `UNION` annotation
- **Code template**:
  ```sql
  upper(trim(<col_a>)) || '||' || coalesce(upper(trim(<col_b>)), '') as source_unique_id
  ```
- **Notes**: Always `||` delimiter, always `coalesce(...,'')` sentinel for nullable parts.

### 5. FK_LOOKUP
- **Triggers**: `target_col` ends in `_SKEY` AND source_col present AND target_col != `*_CORE_SKEY` (those are FKs to hub)
- **Code template**: emit two artifacts
  - **Reference CTE** (in `with` clause):
    ```sql
    ref_<short> as (
        select <target_col_lc>, <desc_col>
        from {{ source('<domain>_reference', 'ref_<short>') }}
    )
    ```
  - **LEFT JOIN** (in base CTE FROM):
    ```sql
    left join ref_<short> as <alias>
        on upper(trim(o.<source_col>)) = upper(trim(<alias>.<desc_col>))
    ```

### 6. HUB_FK
- **Triggers**: `target_col = COMPANY_CORE_SKEY` (or `<HUB>_CORE_SKEY`) on a SPOKE target
- **Code template**:
  ```sql
  left join {{ ref('<hub_entity>') }} as hub
      on upper(trim(o.source_unique_id)) = upper(trim(hub.source_unique_id))
  ```
  Then `hub.<hub>_core_skey as <hub>_core_skey` in base SELECT.

### 7. ESRI_STD
- **Triggers**: `transformation = "<ESRI ADDRESS Standardisation>"`
- **Action**: Emit `null as <target_col>` + comment `-- TODO: populated by ESRI standardization step (downstream)`.

### 8. ESRI_OUTPUT
- **Triggers**: `transformation = "ESRI OUTPUT"`
- **Action**: Same as ESRI_STD — leave `null` with TODO comment.

### 9. PASSTHROUGH (default)
- **Triggers**: None of the above, source_col is present.
- **Code template (in extraction CTE)**:
  - TEXT target: `NULLIF(TRIM("<source_col>"), '') as <target_col>`
  - NUMBER/FLOAT target: `"<source_col>" as <target_col>`
  - DATE/TIMESTAMP target: `"<source_col>" as <target_col>`
- **Code template (in base CTE)** — type cast ONLY where `bronze_type != contract.target_type`:
  - DATE → TIMESTAMP_NTZ: `o.<target_col>::timestamp_ntz as <target_col>`
  - NUMBER → FLOAT: `o.<target_col>::float as <target_col>`
  - TEXT → NUMBER: `try_to_number(o.<target_col>) as <target_col>`

### 10. UNMAPPED
- **Triggers**: source_col is blank/empty (placeholder STTM row).
- **Code template (in base CTE)**: `null as <target_col>` (no type suffix).
- **Action**: Add to "unmapped columns" summary in user output.

---

## Multi-Source UNION Detection

If the `Join` cell contains `UNION ALL`, treat the source as multi-source:

1. Split `Join` cell on `UNION ALL` → N branches.
2. Each branch has:
   - `primary_table`: the table after `FROM`
   - `joined_tables[]`: tables introduced by `JOIN` clauses
   - `join_predicates[]`: `ON ...` clauses
   - `where_filter`: any filter inside the branch (e.g., `c.cust_status = 'A'`)
3. Generate a UNION ALL extraction CTE — each branch outputs **identical aliases and order** (copy the column-map output across branches).
4. Apply final `qualify row_number() over (partition by source_unique_id order by ...) = 1` AFTER the union.

---

## Multi-Target Detection

If `Target Table` header cell contains > 1 line, treat as multi-target:

1. Group body rows by target table name.
2. For EACH target, run the classification rules above on its column map.
3. Generate one ephemeral staging model per target (`<prefix>_<target>.sql`).
4. Determine extraction shape per target (see Per-Target Join Graph below).
5. Spoke targets (target != hub `*_CORE`) automatically get a HUB_FK lookup via `{{ ref('<hub_entity>') }}`.

---

## Per-Target Join Graph (third pattern)

When targets within a single STTM share most bronze tables but require distinct join shapes, generate per-target extraction CTEs (NOT a shared CTE).

**Detection signals** (any one):
- STTM `Join` cell contains target-name section markers (e.g., `------ PROPERTY_OWNER`).
- Different targets reference disjoint sets of bronze tables.
- A subset of targets requires a filter not applicable to others (e.g., `party_role_type_desc IN ('Legal Owner','True Owner')` for PROPERTY_OWNER only).

**Code pattern**: each ephemeral model has its own `sp_<target>` extraction CTE with its own bronze JOIN graph; the contract documents Pattern A / Pattern B / etc. per target.

**Reference example**: EDP property domain — PROPERTY_CORE/USAGE/ADDRESS use Pattern A (`dim_property_building` + `dim_property_usage` + `country` + `fact_aar`); PROPERTY_OWNER uses Pattern B (`dim_property_building` + `fact_property_role` + `dim_party_role` + `dim_organization`).

---

## Empty-Target Drop Rule

A target is considered empty when its STTM body rows contain ZERO of: PASSTHROUGH, FK_LOOKUP, or COMPOUND_PK rows (i.e., only AUDIT/SEQUENCE/ESRI/UNMAPPED).

**Action**: AUTO-mode drops the target entirely — no ephemeral model, no hub patch, no watermark. Note in summary.

**Reference example**: FPD STTM lists 6 targets but only `company_core` and `company_segment` have business mappings; `company_address`, `company_industry`, `company_relationship`, `company_hierarchy` are dropped.

---

## Source-System Reconciliation

When STTM transformation row says `GDP_SOURCE_SYSTEM_NAME = '<X>'` but `<X>` is not in `REF_GDP_SOURCE_SYSTEM`:

1. Try fuzzy match (substring, abbreviation): `FINANCIAL_RCOE` → `FPD` if STTM folder is `FPD/`.
2. Try STTM file path parent folder: `gdp-company/mappings/FPD/...` → `FPD`.
3. If both miss — STOP (true blocker #1).

Reconciliation outcome is logged in summary, not silently swapped.

---

## Bronze Table Discovery

When STTM `Source Tables` references a table not in the registry's expected schema:

1. Try `DEV_GDP_BRONZE_DB.BRONZE_<SOURCE_KEY>.<TABLE>` (registry path).
2. If miss, run `INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME ILIKE '<TABLE>'` across the database. Prefer:
   - Non-`*_HIST`, non-`*_BKP*`, non-`*_TEST`, non-`*_SYNTHETIC*` variants
   - Highest row count among candidates
3. If STTM names a `*_SYNTHETIC` variant: prefer the live equivalent (FPD example: `C360_CLIENTPROFITABILITYREPORT_SYNTHETIC` → `C360_CLIENTPROFITABILITYREPORT` in `BRONZE_FINANCE`).
4. Add the resolved schema to AGENTS.md update list (note in summary).
5. If no candidate found — STOP (true blocker #2).

---

## Bronze Schema Lookup

Use the AGENTS.md source registry to resolve bronze schema. **This list is NOT exhaustive** — fall through to `INFORMATION_SCHEMA.TABLES` discovery (see Bronze Table Discovery rule above) if a source isn't listed.

| SKEY | Source | Bronze Schema |
|---|---|---|
| 100 | EDP | BRONZE_EDP |
| 102 | LIGHTBOX | BRONZE_LIGHTBOX |
| 107 | SPOC | BRONZE_SPOC |
| 110 | INTROHIVE | BRONZE_INTROHIVE |
| 112 | BUSINESS_SMARTSHEET | BRONZE_BUSINESS_SMARTSHEET |
| 115 | CLIENT_SENTIMENT | BRONZE_CLIENT_SENTIMENT |
| 120 | NEWS_TO_LEADS | BRONZE_NEWS_TO_LEADS |
| 123 | TAT | BRONZE_TAT |
| 200 | DIQ | BRONZE_DIQ |
| 201 | MTA | BRONZE_MTA |
| 202 | FPD | BRONZE_FINANCE *(registry SKEY=FPD; physical schema=BRONZE_FINANCE — documentation drift, AGENTS.md needs update)* |

The skill validates the source-system name against `REF_GDP_SOURCE_SYSTEM` (with fuzzy match per Source-System Reconciliation rule).

---

## Output Plan Format (presented to user AFTER code generation in AUTO mode)

In AUTO mode the skill executes Steps 0–7 then emits a summary like:

```
DOMAIN          : <domain>
SOURCE_SYSTEM   : <name> (registry SKEY=<n>)  [reconciled from STTM '<orig>' if different]
SOURCE_FQNS     : [...]
EXTRACTION      : single-source | multi-source UNION (N branches) | per-target join graph
SOURCE_UNIQUE_ID: <expr>
TARGETS         : [...]                       (dropped: [...] — reason: empty)
FILES_CREATED   : [...]
FILES_MODIFIED  : [...]
WATERMARKS      : N rows inserted
DECISIONS       :
  - <auto-resolved choice> (e.g., "DIM_COUNTRY skipped — only 2 rows; used COUNTRY")
  - <auto-resolved choice>
TODOS           :
  - <e.g., LOV mapping pending for ref_property_kind_skey>
  - <e.g., ESRI standardization downstream>
UNMAPPED COLS   : [...]
```

The summary is the final user-facing output. The user can then re-invoke the skill with `manual mode` flag to revise.
