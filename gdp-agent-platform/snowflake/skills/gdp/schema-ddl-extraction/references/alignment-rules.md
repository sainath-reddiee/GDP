# Layer Alignment Rules (Delegation)

`{PREFIX}` is the project's audit-column / shared-object namespace (e.g. `GDP`, `DW`, `EDW`; may be blank — gathered once per run, see SKILL.md Inputs).

When cloning tables, **delegate** per-table formatting to the appropriate sister skill:

- **Gold layer tables** (databases matching `*_GOLD_*`) → follow [`gold-model`](../../gold-model/SKILL.md)
- **Silver layer tables** (databases matching `*_SILVER_*`) → follow [`silver-model`](../../silver-model/SKILL.md)

This document lists what the clone skill must enforce on top of the source DDL.

## Silver Layer Column Order (pre-MDM) — MANDATORY

For all Silver-layer **pre-MDM** tables (core/fact tables that ingest from upstream sources before Master Data Management consolidation), the column order MUST be:

1. **`<TABLE_NAME>_SKEY`** — own primary key (surrogate)
2. **`SOURCE_UNIQUE_ID`** — natural identifier from the source system
3. **`REF_{PREFIX}_SOURCE_SYSTEM_SKEY`** — FK to `{ENV}_{PREFIX}_SILVER_DB.SHARED.REF_{PREFIX}_SOURCE_SYSTEM`
4. **Foreign Keys** — FKs to other domain/database tables (e.g. `COMPANY_CORE_SKEY`, `CONTACT_CORE_SKEY`, `PROPERTY_USAGE_SKEY`)
5. **Reference Keys** — FKs to in-schema `REF_*` lookup tables (e.g. `REF_OPPORTUNITY_STATUS_SKEY`, `REF_COUNTRY_NAME_SKEY`)
6. **Business / attribute columns** — all descriptive, measure, and date columns
7. **`<TABLE_NAME>_HKEY`** — SCD2 hash key (placed immediately before audit block)
8. **Audit columns** — `{PREFIX}_IS_ACTIVE`, `{PREFIX}_INSERTED_TS`, `{PREFIX}_INSERTED_BY`, `{PREFIX}_UPDATED_TS`, `{PREFIX}_UPDATED_BY` (NOT NULL)
9. **`{PREFIX}_TRANSACTION`** — *history tables only* (placed after audit columns)
10. **Constraints** — PK first, then FKs, all inline at the end of the column list

### Pre-MDM REF / Lookup tables

REF tables are pre-MDM lookups and are simpler — they do NOT include `SOURCE_UNIQUE_ID`, `REF_{PREFIX}_SOURCE_SYSTEM_SKEY`, FK columns, or `HKEY`. Their order is:

1. `<TABLE_NAME>_SKEY`
2. Business columns (e.g. `*_DESC`, `*_CODE`, `*_NAME`)
3. Audit columns (5)
4. PK constraint

### Enforcement

When cloning, the skill MUST reorder columns from the source DDL into the layout above before emitting the runbook. Do NOT trust the source ordering — always re-stage. Flag any column that cannot be classified into one of the buckets with `-- REVIEW`.

## Always Enforce

1. **Data types** — normalise per the layer's `references/data-types.md`:
   - `BIGINT/INT` → `LONG`
   - `VARCHAR(*)` / `CHAR(1)` → `STRING`
   - `FLOAT` / `DECIMAL(38,6)` → `NUMBER(38,5)`
   - `DECIMAL(38,0)` → `NUMBER(38,0)`
   - `TIMESTAMP_NTZ(6)` → `TIMESTAMP_NTZ`

   **CRITICAL — Iceberg precision/scale rule:** `NUMBER` and `DECIMAL` columns in Iceberg tables MUST always declare explicit precision and scale (e.g. `DECIMAL(38,6)`, `DECIMAL(18,6)`, `DECIMAL(38,0)`). Bare `NUMBER` or `DECIMAL` raises:
   `SQL compilation error: data type 'NUMBER/DECIMAL' without precision and scale is not supported for Iceberg tables.`
   When extracting source DDL, query `INFORMATION_SCHEMA.COLUMNS.NUMERIC_PRECISION` and `NUMERIC_SCALE` to recover the actual values; never emit `NUMBER` without `(p,s)`.

   **Convention:** Use `DECIMAL(p,s)` (not `NUMBER(p,s)`) for financial / measure columns to stay consistent with the Silver pre-MDM DDL style already in use across the project's schemas. `LONG` is reserved for surrogate-key columns where the source uses a bigint.
2. **Audit columns** — must appear last and be `NOT NULL`:
   `{PREFIX}_IS_ACTIVE BOOLEAN NOT NULL`,
   `{PREFIX}_INSERTED_TS TIMESTAMP_NTZ NOT NULL`,
   `{PREFIX}_INSERTED_BY STRING NOT NULL`,
   `{PREFIX}_UPDATED_TS TIMESTAMP_NTZ NOT NULL`,
   `{PREFIX}_UPDATED_BY STRING NOT NULL`.
3. **HKEY** — Gold tables only: ensure `{TABLE_NAME}_HKEY STRING NOT NULL` exists immediately above the audit block. Inject if absent.
4. **COMMENTs** — every column and every table requires a COMMENT clause. Preserve existing comments; synthesise sensible defaults if missing and tag with `-- REVIEW`.
5. **Naming fixes**:
   - Spaces in column names → `_`. Add `-- REVIEW`.
   - Trailing `_F` → `_FLAG` (except `ACTIVE_F` and `{PREFIX}_*`).
6. **ICEBERG footer** — preserve `EXTERNAL_VOLUME`, `CATALOG`, `ICEBERG_VERSION`, and update `BASE_LOCATION` to target schema path if embedded.
7. **Constraint names**:
   - PK: `PK_{TABLE}`
   - UK: `UK_{TABLE}_{COL}`
   - FK: `FK_{TABLE}_{FK_COL}`
8. **Sequences for `_SKEY`** — emit `CREATE OR REPLACE SEQUENCE {TARGET_SCHEMA}.SEQ_{TABLE}_SKEY` even if the source table did not have one.

   **Sequence `START` value strategy (MANDATORY user choice):**
   The skill MUST ask the user upfront which strategy to apply for **all tables (fact/core AND REF)**:
   - **(a) MAX-derived:** `START = MAX(<TABLE>_SKEY) + 1` queried from a user-specified source schema. Probe via `SELECT COALESCE(MAX(<SKEY>), 0) FROM <source>.<table>`. If table is empty/missing, fall back to `START 1`. Emit an inline `-- source MAX = N` comment for traceability.
   - **(b) Default:** `START 1` for all sequences.
   - **(c) Custom:** user provides per-table values.

   **Note:** REF tables ARE included in MAX-based derivation when the user chooses (a) — they often hold pre-existing reference data whose IDs must not be reused.

9. **Sequence MUST be attached to the SKEY column as DEFAULT** — every own-PK `_SKEY` column MUST declare `DEFAULT {TARGET_SCHEMA}.SEQ_{TABLE}_SKEY.NEXTVAL` inline. Do NOT leave the sequence orphaned. Pattern:
   ```sql
   {TABLE}_SKEY  LONG  NOT NULL  DEFAULT {TARGET_SCHEMA}.SEQ_{TABLE}_SKEY.NEXTVAL  COMMENT '...'
   ```
   The clone runbook MUST validate that every `CREATE OR REPLACE SEQUENCE` emitted has a matching `DEFAULT ...NEXTVAL` reference on the corresponding table column. Resolve sequence-name mismatches (e.g. `SEQ_REF_X_NAME` vs `SEQ_REF_X`) by matching to the source's actual sequence name; flag with `-- REVIEW` if ambiguous.
   *Exception:* History tables (`*_HIST`) inherit the SKEY value from the parent table and MUST NOT have a DEFAULT NEXTVAL clause.

## Order of Application

Apply the rules in this order so later steps see the column structure produced by earlier ones:

1. Type normalisation
2. Naming fixes
3. Inject `DIM_{PREFIX}_DATA_SOURCE_SKEY` if missing (non-REF tables only — see [./skey-constraint-rules.md](./skey-constraint-rules.md))
4. **Reorder columns into Silver pre-MDM layout** (SKEY → SOURCE_UNIQUE_ID → REF_{PREFIX}_SOURCE_SYSTEM_SKEY → FKs → Reference Keys → business → HKEY → audit → constraints)
5. Move/inject audit columns to end
6. Inject HKEY (Gold + Silver pre-MDM core tables) above audit columns
7. Add `_SKEY` constraints (PK / FK)
8. Re-emit the COMMENTs
9. Re-emit the ICEBERG footer
