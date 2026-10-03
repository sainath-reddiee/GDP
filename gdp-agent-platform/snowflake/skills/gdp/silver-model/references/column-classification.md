# Silver — Column Classification Rules

When given any ER diagram, table spec, or column list, classify EVERY column into one of four zones using the rules below **in priority order**. Apply Zone 1 rules first, then Zone 4, then Zone 3, then Zone 2 last for anything that remains.

---

## Zone 1 — Key Columns (always first in DDL)

These columns MUST always appear first, in this fixed sub-order:

| Sub-order | Column pattern | Notes |
|---|---|---|
| 1.1 | `{ENTITY_NAME}_SKEY` where entity = the table being created | Table's own surrogate PK |
| 1.2 | `SOURCE_UNIQUE_ID` | Always present, exact name |
| 1.3 | `REF_{PREFIX}_SOURCE_SYSTEM_SKEY` | Always present, exact name |
| 1.4 | `{PARENT_ENTITY}_SKEY` (one or more) | FK to a parent Silver entity — child tables only |

**Classification rules:**
- Column name exactly equals `SOURCE_UNIQUE_ID` → Zone 1.2
- Column name exactly equals `REF_{PREFIX}_SOURCE_SYSTEM_SKEY` → Zone 1.3
- Column name ends with `_SKEY` AND it is the PK of this very table → Zone 1.1
- Column name ends with `_SKEY` AND it is described as "FK to {another entity}" AND that entity is a core Silver entity (not a reference/lookup table) → Zone 1.4
- If multiple parent SKEYs exist, order them in order of logical proximity (direct parent first)

---

## Zone 2 — Business Columns (middle of DDL)

Everything that is not a key, hash key, or audit column lives here. Sub-order within Zone 2 is:
1. Any remaining `{ENTITY}_SKEY` FKs to **reference/lookup tables** (`REF_` prefix) — place these first in Zone 2
2. Core domain attribute columns (IDs, names, descriptions, URLs, codes, numeric measures)
3. `IS_*` boolean flag columns
4. Date/time business columns (`VALID_FROM`, `VALID_TO`, `EFFECTIVE_DATE`, `EXPIRY_DATE`, etc.)

**Classification rules:**
- Column name starts with `REF_` AND ends with `_SKEY` (NOT `REF_{PREFIX}_SOURCE_SYSTEM_SKEY`) → Zone 2 (reference FK)
- **Naming enforcement:** If the ER diagram has a column starting with `REF_` but ending with `_KEY`, `_ID`, or any suffix other than `_SKEY`, **rename it to end with `_SKEY`** before emitting the DDL. Always standardise to the `REF_{TABLE}_SKEY` pattern. Note the rename in the column COMMENT.
- Column name starts with `IS_` → Zone 2 (flag)
- Column name is `VALID_FROM` or `VALID_TO` → Zone 2 (business date)
- Any other domain attribute (NAME, CODE, URL, DESCRIPTION, STATUS, TYPE, LEVEL, PATH, SCORE, PROVIDER, LATITUDE, LONGITUDE, etc.) → Zone 2
- `SOURCE_*` prefix columns (raw/unmodified source values) → Zone 2, group at end of Zone 2 block

**Default:** If a column from the ER does not match any zone rule, place it in Zone 2 and add a comment: `-- REVIEW: zone placement inferred`.

---

## Zone 3 — Hash Key Column (always second-to-last block)

| Column pattern | Notes |
|---|---|
| `{ENTITY_NAME}_HKEY` | MD5/SHA2 hash of all Zone 2 business columns for change detection |

**Rules:**
- Always exactly ONE HKEY per table
- Named `{ENTITY_NAME}_HKEY` (same prefix as the table's SKEY)
- Always `STRING NOT NULL`
- If the ER diagram does NOT include an HKEY column, **add it anyway** — it is mandatory for all Silver models
- The comment should list which Zone 2 columns feed the hash

---

## Zone 4 — Audit Columns (always last, always fixed)

These 5 columns are **always injected in this exact order**, regardless of whether the ER diagram includes them. Never take audit columns from the ER diagram order. `{PREFIX}` is the project-configurable audit-column namespace gathered in Step 1 of SKILL.md (e.g. `GDP`, `DW`; may be blank).

| Column | Type | Constraint |
|---|---|---|
| `{PREFIX}_IS_ACTIVE` | `BOOLEAN` | `NOT NULL` |
| `{PREFIX}_INSERTED_TS` | `TIMESTAMP_NTZ(6)` | `NOT NULL` |
| `{PREFIX}_INSERTED_BY` | `STRING` | `NOT NULL` |
| `{PREFIX}_UPDATED_TS` | `TIMESTAMP_NTZ(6)` | `NOT NULL` |
| `{PREFIX}_UPDATED_BY` | `STRING` | `NOT NULL` |

**Rules:**
- These 5 columns are ALWAYS present in every Silver table — add them if missing from ER
- Order is fixed: IS_ACTIVE → INSERTED_TS → INSERTED_BY → UPDATED_TS → UPDATED_BY
- No other columns may be placed after Zone 4

---

## Complete Example — COMPANY_CORE

ER diagram might list columns in any order. Correct classified output (using `{PREFIX}` = `GDP` as an example):

```
-- ZONE 1: Keys
COMPANY_CORE_SKEY            (1.1 — own PK)
SOURCE_UNIQUE_ID             (1.2 — always)
REF_GDP_SOURCE_SYSTEM_SKEY   (1.3 — always)

-- ZONE 2: Business columns
REF_COMPANY_TYPE_SKEY        (2 — reference FK, REF_ prefix)
REF_COUNTRY_OF_REGISTRATION_SKEY (2 — reference FK)
REF_ADDRESS_SKEY             (2 — reference FK)
DUNS_NUMBER                  (2 — domain attribute)
COMPANY_NAME                 (2 — domain attribute)
ALIAS_NAME                   (2 — domain attribute)
WEBSITE_URL                  (2 — domain attribute)
COMPANY_STATUS               (2 — domain attribute)
IS_INTERNAL_ENTITY           (2 — IS_ flag)

-- ZONE 3: Hash key
COMPANY_CORE_HKEY            (3 — hash of Zone 2 business cols)

-- ZONE 4: Audit (injected, fixed order)
GDP_IS_ACTIVE
GDP_INSERTED_TS
GDP_INSERTED_BY
GDP_UPDATED_TS
GDP_UPDATED_BY
```

---

## Complete Example — COMPANY_SEGMENT (child table)

```
-- ZONE 1: Keys
COMPANY_SEGMENT_SKEY         (1.1 — own PK)
SOURCE_UNIQUE_ID             (1.2)
REF_GDP_SOURCE_SYSTEM_SKEY   (1.3)
COMPANY_CORE_SKEY            (1.4 — parent entity FK)

-- ZONE 2: Business columns
REF_SEGMENT_SKEY             (2 — reference FK)
REF_SERVICE_LINE_SKEY        (2 — reference FK)
VALID_FROM                   (2 — business date)
VALID_TO                     (2 — business date)

-- ZONE 3: Hash key
COMPANY_SEGMENT_HKEY

-- ZONE 4: Audit
GDP_IS_ACTIVE
GDP_INSERTED_TS
GDP_INSERTED_BY
GDP_UPDATED_TS
GDP_UPDATED_BY
```

---

## Decision Checklist for Unknown Columns

When a column from the ER doesn't obviously fit:

1. Does it end with `_SKEY`?
   - Yes, it IS this table's PK → Zone 1.1
   - Yes, FK to a core Silver entity → Zone 1.4
   - Yes, FK to a reference/lookup table (name starts with `REF_`) → Zone 2
2. Does it start with `{PREFIX}_` → Zone 4
3. Does it end with `_HKEY` → Zone 3
4. Does it start with `IS_` → Zone 2 (flag)
5. Everything else → Zone 2 (domain attribute)

---

## Naming Fixes (applied silently with REVIEW comment)

| Issue | Fix |
|---|---|
| Column name contains a space | Replace space with `_` — e.g. `CLIENT CLASSIFICATION` → `CLIENT_CLASSIFICATION` |
| Leading/trailing whitespace | Trim silently |
| Column name ends with `_F` (excluding `ACTIVE_F` and any `{PREFIX}_*` column) | Rename suffix `_F` to `_FLAG` (e.g. `SENT_F` → `SENT_FLAG`) |
| All other names | Preserve exactly as in spec |
