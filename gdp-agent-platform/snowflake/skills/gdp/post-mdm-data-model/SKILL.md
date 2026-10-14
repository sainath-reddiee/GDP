---
name: gdp-post-mdm-data-model
description: "GDP post-MDM (Reltio) data model knowledge for gdp-dbt: how pre-MDM silver (COMPANY_CORE/PROPERTY_CORE) links to post-MDM silver (COMPANY_MDM/PROPERTY_MDM/MDM schemas), how dbt moves MDM data to gold DIM_COMPANY/DIM_PROPERTY (+children, ES docs, event outbox), and how every other domain (opportunity, lease, sale, finance, contact, supplemental) resolves company/property to mastered gold skeys. Use when: writing/reviewing SQL that joins source records to mastered companies/properties, debugging -1 / stale DIM_COMPANY_SKEY or DIM_PROPERTY_SKEY, merges/repoint, MDM watermark/delta issues, adding a new gold consumer, or explaining the MDM data model. Triggers: post mdm, mdm data model, company_mdm, property_mdm, cross reference, CBRE_ENTERPRISE_COMPANY_ID, CBRE_ENTERPRISE_PROPERTY_ID, MDM_ENTITY_ID, reltio, repoint, dim_company_skey -1, dim_property_skey -1, company merge, golden record, pre mdm to post mdm."
---

# GDP Post-MDM Data Model (Company + Property)

Companion to the pre-MDM onboarding skill (`GDP_DBT_ONBOARD`, bronze → silver hub). This skill covers
**silver hub → Reltio MDM → post-MDM silver → gold → consumers**.

Repo root: `gdp-dbt/gdp-dbt/` (nested path). Env prefix `<ENV>` = DEV/QA/UAT/PROD.
Facts below were validated against code + PROD on 2026-10-14; re-verify counts before quoting them.

## Reference files (load on demand)

| File | Load when |
|---|---|
| `references/company-mdm.md` | Company MDM tables, company_core_mdm logic, DIM_COMPANY* derivation, hierarchy, outbox |
| `references/property-mdm.md` | Property MDM tables, property_core_mdm, DIM_PROPERTY*, geography, ES docs |
| `references/consumers-and-repoint.md` | How opportunity/lease/sale/finance/contact/supplemental get DIM_*_SKEY; repoint macros |
| `references/canonical-sql.md` | Copy-paste resolution SQL + validation/diagnostic queries |
| `references/gotchas.md` | Known defects/risks (read before diagnosing any skey/active-flag issue) |

## 1. Layer map (memorize)

```
PRE-MDM SILVER  <ENV>_GDP_SILVER_DB.{COMPANY|PROPERTY}      dbt, 1 row per SOURCE record
  COMPANY_CORE  PK COMPANY_CORE_SKEY   UK (SOURCE_UNIQUE_ID, REF_GDP_SOURCE_SYSTEM_SKEY)
  PROPERTY_CORE PK PROPERTY_CORE_SKEY  UK (SOURCE_UNIQUE_ID, REF_GDP_SOURCE_SYSTEM_SKEY)
  (+ *_ADDRESS, *_INDUSTRY, *_HIERARCHY, *_USAGE children on *_CORE_SKEY)
        │ REF_GDP_SOURCE_SYSTEM_SKEY → SHARED.REF_GDP_SOURCE_SYSTEM.GDP_SOURCE_SYSTEM_NAME
        ▼ Reltio ingests/matches (outside dbt)
POST-MDM SILVER (written by Reltio, read via dbt source())
  <ENV>_GDP_SILVER_DB.COMPANY_MDM.*   <ENV>_GDP_SILVER_DB.PROPERTY_MDM.*   <ENV>_GDP_SILVER_DB.MDM.ADDRESS_*
  *_CROSS_REFERENCE = the BRIDGE: source record → CBRE_ENTERPRISE_*_ID (N:1)
        ▼ dbt, delta-scoped by watermark RELTIO/<X>_CORE_MDM/DIM_<X>
  STAGE.*_CROSS_REFERENCE_ENTITY_ID (DELTA ONLY, rebuilt each run) → *_core_mdm (ephemeral)
  STAGE.*_EVENT_OUTBOX → STAGE.EVENT_OUTBOX_<X> (Kafka contract, PENDING rows)
        ▼
GOLD <ENV>_GDP_GOLD_COMPANY_DB.COMPANY / <ENV>_GDP_GOLD_PROPERTY_DB.PROPERTY
  DIM_COMPANY  PK DIM_COMPANY_SKEY  UK CBRE_ENTERPRISE_COMPANY_ID  (SCD1 + _HIST)
  DIM_PROPERTY PK DIM_PROPERTY_SKEY UK CBRE_ENTERPRISE_PROPERTY_ID (SCD1 + _HIST)
  children: DIM_COMPANY_INDUSTRY, DIM_COMPANY_ALTERNATE_ADDRESS, DIM_PROPERTY_USAGE, DIM_PROPERTY_ALTERNATE_ADDRESS
  update_dim_*_skey (post-hook REPOINT of facts) ; *_ES_DOC → OpenSearch
        ▼
CONSUMER FACTS (FACT_OPPORTUNITY, FACT_REVENUE, FACT_LEASE*, FACT_SALE*, supplemental, contact)
```

## 2. The bridge — key columns that link pre-MDM to post-MDM

| Domain | Pre-MDM key | Xref table | Xref key columns | Mastered id | Gold |
|---|---|---|---|---|---|
| Company | `COMPANY_CORE.SOURCE_UNIQUE_ID` + source-system NAME | `COMPANY_MDM.COMPANY_CROSS_REFERENCE` | `SOURCE_UNIQUE_ID`, `SOURCE_SYSTEM_NAME` | `CBRE_ENTERPRISE_COMPANY_ID`, `MDM_ENTITY_ID` | `DIM_COMPANY` on `CBRE_ENTERPRISE_COMPANY_ID` |
| Property | `PROPERTY_CORE.SOURCE_UNIQUE_ID` + source-system NAME | `PROPERTY_MDM.PROPERTY_CROSS_REFERENCE` | **`SOURCE_SYSTEM_ID`** (not SOURCE_UNIQUE_ID), `SOURCE_SYSTEM_NAME` | `CBRE_ENTERPRISE_PROPERTY_ID`, `MDM_ENTITY_ID` | `DIM_PROPERTY` on `CBRE_ENTERPRISE_PROPERTY_ID` |
| Address | source address id | `MDM.ADDRESS_CROSS_REFERENCE` (`SOURCE_SYSTEM_ID`) | | `CBRE_ENTERPRISE_ADDRESS_ID` | via *_CORE.PRIMARY_ADDRESS |

Rules that make the join accurate:
- Always match **both** id and `UPPER(REF_GDP_SOURCE_SYSTEM.GDP_SOURCE_SYSTEM_NAME) = UPPER(xref.SOURCE_SYSTEM_NAME)` — ids collide across sources.
- Pre-MDM `*_CORE_SKEY` is **never stored** in MDM or gold (exception: `PROPERTY_MDM.PRE_MDM_UPDATED_RECORDS` has PROPERTY_CORE_SKEY → CBRE id, a Reltio-side status table, not used by dbt).
- MDM tables are versioned: dedupe latest with `ORDER BY MDM_POST_MDM_LUD DESC NULLS LAST, MDM_UPDATED_TS DESC NULLS LAST`.
- Xref deleted = `MDM_DELETED_DATE` (company) / **`MDM_DELETE_DATE`** (property) NOT NULL or `MDM_STATUS='INACTIVE'`; or filter `GDP_IS_ACTIVE`.
- Xref `SOURCE_SYSTEM_NAME IN ('Reltio','ReltioGrouping')` are MDM-internal rows (≈40M of ~61M company xref rows) — exclude for lineage.
- Use the **full** `*_CROSS_REFERENCE`, never `STAGE.*_CROSS_REFERENCE_ENTITY_ID` (delta only) for resolution.

PROD match rate pre-MDM active → active xref (2026-10-14): company 99.9–100% for all sources except **PDL 0%** (PDL resolves via name crosswalk `PDL_COMPANY_DIM_XREF`, not MDM); property LIGHTBOX 99.96%, DIQ 98.99%, MTA 97.52%, EDP 97.05%.

## 3. Resolution chain (any source record → mastered gold skey)

```
fact/silver row.COMPANY_CORE_SKEY (pre-MDM, -1 = no pre-MDM match)
 → COMPANY_CORE (active) → REF_GDP_SOURCE_SYSTEM (name)
 → COMPANY_CROSS_REFERENCE (active, latest per suid+system)
 → DIM_COMPANY (active, min skey per CBRE id)  → DIM_COMPANY_SKEY, else -1
```
Property is identical with `PROPERTY_CORE_SKEY`, `SOURCE_SYSTEM_ID`, `DIM_PROPERTY`.
SQL: `references/canonical-sql.md` §1–2. Gold `-1` = no MDM/dim match; it is indistinguishable from silver `-1`.

## 4. MDM → gold movement (summary)

1. Watermark read from `<ENV>_GDP_UTIL_DB.CONFIG.GDP_DBT_WATERMARK_TBL` (`SOURCE_SYSTEM='RELTIO'`, `SOURCE_MODEL_NAME='COMPANY_CORE_MDM'|'PROPERTY_CORE_MDM'`, `TARGET_MODEL_NAME='DIM_COMPANY'|'DIM_PROPERTY'`; missing → 1900 = full).
2. `*_cross_reference_entity_id` = xref rows with `GREATEST(MDM_CREATED_TS, MDM_UPDATED_TS, MDM_POST_MDM_LUD) > wm` ∪ `*_ENTITY_EVENTS_AUDIT` processed events after wm (company: `RECORD_TYPE='ORG'` only).
3. `*_core_mdm` (ephemeral) builds one golden row per CBRE id: latest core + primary address (ADDRESS_CORE via PRIMARY_ADDRESS) + hierarchy parents (company) / primary usage (property) + active flag from xref (active if ANY xref source alive).
4. Gold dims MERGE on CBRE id when HKEY/active/delete_ts changed; skey `COALESCE(existing, SEQ_DIM_*_SKEY.NEXTVAL)` → stable per CBRE id. `DIM_DATA_SOURCE_SKEY` = RELTIO row. `full_refresh=false`.
5. Children MERGE, then `m_child_parent_gold_scd1` bumps parent `GDP_UPDATED_TS` (drives ES delta). `m_insert_history_gold` appends `_HIST` (I/U/D).
6. `update_dim_*_skey` post-hooks: advance xref stream → **repoint** facts' DIM_*_SKEY → advance watermark to `run_started_at`. `update_water_mark_gold_*` advances child watermarks.
7. Event outbox emits INSERT/UPDATE/DELETE JSON per changed CBRE id; ES docs rebuild from gold by `GDP_UPDATED_TS >= ES watermark` (`SOURCE_SYSTEM='GOLD'`).

Detail: `references/company-mdm.md`, `references/property-mdm.md`.

## 5. Workflow when asked a question / task in this area

### Step 1: Classify the request
- **Explain/data-model question** → answer from this file + relevant reference; cite `file:line` from the reference.
- **Write SQL** (resolve source → mastered, recon, coverage) → start from `canonical-sql.md`; use full xref + system-name match.
- **Debug skey -1 / stale / wrong company** → Step 2.
- **Add a new gold consumer** → `consumers-and-repoint.md` §4 (pattern A + register in `company_repoint_targets`/`property_repoint_targets` in `dbt_project.yml`).

### Step 2: Diagnose skey issues (in order — each step isolates one layer)
1. Silver row `*_CORE_SKEY = -1`? → pre-MDM lookup miss (source silver model), not MDM.
2. `*_CORE` row active and present? (`GDP_IS_ACTIVE`, orphan deactivation bug — see project memory).
3. Xref hit on (id, UPPER system name)? Miss → Reltio not processed yet (check `MDM_STATUS_SUMMARY`, `*_ENTITY_EVENTS_AUDIT`).
4. Xref CBRE id present & active in DIM? Miss → gold watermark/delta didn't pick it up (wm=1900 backfill) or merged loser.
5. Consumer uses pattern B / delta xref / no active filter? → see `gotchas.md` G1–G4.
6. Merge happened? → check `COMPANY_MERGE`/`PROPERTY_MERGE` (LOSING→SURVIVING); repoint only covers current delta.
Use diagnostics in `canonical-sql.md` §3.

### Step 3: Validate before concluding
Run queries against Snowflake (PROD role `PROD_GDP_DATA_ENGINEER_ROLE` has no DEV access; use the env the user names). Report counts, not assumptions. Read-only — do not run repoint/UPDATE/DDL unless the user explicitly asks.

## 6. Top gotchas (full list in `references/gotchas.md`)
- `STAGE.*_CROSS_REFERENCE_ENTITY_ID` is a **delta**, yet `fact_lease`/`fact_sale` resolve property through it → spurious -1.
- Many consumers join xref on `SOURCE_UNIQUE_ID` only (no system name) and read DIM_COMPANY via `source()` without active filter.
- Repoint only touches rows whose company/property changed this run; it never reverts to -1 on unmerge, never updates HKEYs.
- dbt does **not** read `COMPANY_MERGE`/`PROPERTY_MERGE`; merges surface only through xref repointing. Loser DIM rows stay until all their xrefs are deleted.
- Active precedence: any live xref ⇒ DIM active even if golden `MDM_STATUS` inactive; outbox uses MDM_STATUS ⇒ can disagree.
- DIM_COMPANY holds parent record types too (Company 6.08M, Immediate Parent 4.27M, Domestic Parent 4.06M, Global Ultimate 3.91M in PROD). Filter `COMPANY_TYPE_DESC='Company'` for org-level counts.
- All PROD DIM_COMPANY rows have `HIERARCHY_TYPE_NAME='DNB'` ⇒ PDL repoint filter `HIERARCHY_TYPE_NAME IS NULL` matches nothing.

## Stopping Points
- ✋ Before running any write (UPDATE repoint, watermark reset, MERGE, DDL) — confirm with user.
- ✋ If a diagnosis needs an env the active role can't reach — ask which connection to use.

## Output
Accurate explanations with `file:line` citations, validated SQL using the canonical resolution pattern, and layered diagnoses (silver → xref → gold → consumer) backed by query results.
