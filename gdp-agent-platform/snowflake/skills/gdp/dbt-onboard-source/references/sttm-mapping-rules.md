# STTM CSV → dbt Code Mapping Rules

How the skill translates each STTM transformation note into standard SQL for this project. This is the deterministic rulebook that backs Step 0 in `SKILL.md`. `{PREFIX}` is the project's audit-column / shared-object namespace (may be blank).

---

## Header Block Parsing

The STTM CSV begins with a header block (typically rows 1–22). Field names are case-insensitive.

| Header Field | Maps To | Notes |
|---|---|---|
| `Domain` | `domain` | Lowercase |
| `Target DB.SCHEMA` | `target_db`, `target_schema` | Validates `DEV_{PREFIX}_SILVER_DB.<DOMAIN>` |
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
- **Action**: Skip — handled by ephemeral template footer (`{prefix_lower}_inserted_ts` etc.).

### 2. SEQUENCE
- **Triggers**: `transformation = "DB Auto incremental ID"`
- **Action**: Skip — handled by hub model SCD1 merge (sequence default in DDL).

### 3. SOURCE_SYSTEM_REF
- **Triggers**: `transformation` matches `{PREFIX}_SOURCE_SYSTEM_NAME = '<X>'`
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

### 7. STANDARDIZATION_STEP
- **Triggers**: `transformation` references a downstream standardisation/enrichment step not available at onboarding time (e.g. address or geocode normalization)
- **Action**: Emit `null as <target_col>` + comment `-- TODO: populated by downstream standardization step`.

### 8. STANDARDIZATION_OUTPUT
- **Triggers**: `transformation` references the *output* of a downstream standardisation step
- **Action**: Same as STANDARDIZATION_STEP — leave `null` with TODO comment.

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
   - `where_filter`: any filter inside the branch
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
- A subset of targets requires a filter not applicable to others (e.g., a role-type filter specific to one target).

**Code pattern**: each ephemeral model has its own `sp_<target>` extraction CTE with its own bronze JOIN graph; the contract documents Pattern A / Pattern B / etc. per target.

**Reference example**: see `examples/property-contract.md` — PROPERTY_CORE/USAGE/ADDRESS share one join pattern; PROPERTY_OWNER uses a distinct pattern with different bronze tables.

---

## Empty-Target Drop Rule

A target is considered empty when its STTM body rows contain ZERO of: PASSTHROUGH, FK_LOOKUP, or COMPOUND_PK rows (i.e., only AUDIT/SEQUENCE/STANDARDIZATION/UNMAPPED).

**Action**: AUTO-mode drops the target entirely — no ephemeral model, no hub patch, no watermark. Note in summary.

**Reference example**: see `examples/company-contract.md` — a source STTM lists 6 targets but only 2 have business mappings; the rest are dropped.

---

## Source-System Reconciliation

When STTM transformation row says `{PREFIX}_SOURCE_SYSTEM_NAME = '<X>'` but `<X>` is not in `REF_{PREFIX}_SOURCE_SYSTEM`:

1. Try fuzzy match (substring, abbreviation) against known source-system names/aliases.
2. Try STTM file path parent folder as the source-system short name.
3. If both miss — STOP (true blocker #1).

Reconciliation outcome is logged in summary, not silently swapped.

---

## Bronze Table Discovery

When STTM `Source Tables` references a table not in the expected bronze schema:

1. Try `DEV_{PREFIX}_BRONZE_DB.BRONZE_<SOURCE_KEY>.<TABLE>` (registry path, if the project documents one).
2. If miss, run `INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME ILIKE '<TABLE>'` across the database. Prefer:
   - Non-`*_HIST`, non-`*_BKP*`, non-`*_TEST`, non-`*_SYNTHETIC*` variants
   - Highest row count among candidates
3. If STTM names a `*_SYNTHETIC` variant, prefer the live equivalent table with the same base name.
4. Add the resolved schema to the project's source registry update list (note in summary).
5. If no candidate found — STOP (true blocker #2).

---

## Bronze Schema Lookup

If the project maintains a source registry (mapping source-system name/SKEY to its bronze schema), consult it first. **This list is never assumed to be exhaustive** — fall through to `INFORMATION_SCHEMA.TABLES` discovery (see Bronze Table Discovery rule above) whenever a source isn't listed, or when no registry exists yet.

Example registry shape (values below are illustrative, not real project data):

| SKEY | Source | Bronze Schema |
|---|---|---|
| 100 | EXAMPLE_CRM | BRONZE_EXAMPLE_CRM |
| 101 | EXAMPLE_ERP | BRONZE_EXAMPLE_ERP |

The skill validates the source-system name against `REF_{PREFIX}_SOURCE_SYSTEM` (with fuzzy match per Source-System Reconciliation rule).

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
  - <auto-resolved choice>
  - <auto-resolved choice>
TODOS           :
  - <e.g., LOV mapping pending for ref_property_kind_skey>
  - <e.g., standardization step downstream>
UNMAPPED COLS   : [...]
```

The summary is the final user-facing output. The user can then re-invoke the skill with `manual mode` flag to revise.
