# Company MDM → Gold (detail)

Paths relative to `gdp-dbt/gdp-dbt/`. Line refs as of 2026-10-14.

## 1. External Reltio tables — `<ENV>_GDP_SILVER_DB.COMPANY_MDM` (source `company_mdm_source`, `models/silver/company_mdm/company_mdm_source.yml`)

| Table | Grain / keys | Used by dbt |
|---|---|---|
| `COMPANY_CORE` | golden record, versioned. `CBRE_ENTERPRISE_COMPANY_ID`, `MDM_ENTITY_ID`, COMPANY_NAME, ALIAS_NAME, MASTER_ALIAS_NAME, DUNS_ID, COUNTRY_OF_ORIGIN, WEBSITE_URL, RECORD_TYPE (ORG/ImmPar/DomPar/GblPar), PRIMARY_ADDRESS (→ ADDRESS_CORE.CBRE_ENTERPRISE_ADDRESS_ID), PRIMARY_INDUSTRY, C360_CLIENT_NAME, GEOGRAPHIC_COVERAGE_TYPE, MDM_STATUS, COMPANY_STATUS, MDM_*_TS, MDM_POST_MDM_LUD | xref delta, core_mdm, outbox |
| `COMPANY_CROSS_REFERENCE` | source record → golden. `SOURCE_UNIQUE_ID`, `SOURCE_SYSTEM_NAME`, `CBRE_ENTERPRISE_COMPANY_ID`, `MDM_ENTITY_ID`, MDM_STATUS, `MDM_DELETED_DATE`, GDP_IS_ACTIVE, COMPANY_CROSS_REFERENCE_HKEY | everything |
| `COMPANY_HIERARCHY` | `MDM_ENTITY_ID` → `PARENT_ENTITY_ID`, HIERARCHY_TYPE_NAME, LEVEL, LEVEL_NAME, start/end | core_mdm, hierarchy_mdm |
| `COMPANY_INDUSTRY` | CBRE id × INDUSTRY_NAME, PRIMARY_INDUSTRY_FLAG (unused) | industry_mdm |
| `COMPANY_ADDRESS_REL` | COMPANY_CBRE_ENTERPRISE_ID/COMPANY_ENTITY_ID ↔ ADDRESS_ENTITY_ID/ADDRESS_CBRE_ENTERPRISE_ID, ADDRESS_TYPE, REL_END_DATE | alt address |
| `COMPANY_ENTITY_EVENTS_AUDIT` | ENTITY_ID (=MDM_ENTITY_ID), PROCESSED_FLAG, PROCESSED_TIME, LOAD_TIME | xref delta |
| `COMPANY_MERGE` | MDM_SURVIVING_ENTITY_ID, SURVIVING_CBRE_ENTERPRISE_COMPANY_ID, MDM_LOSING_ENTITY_ID, LOSING_CBRE_ENTERPRISE_COMPANY_ID, MERGE_DATE, UNMERGE_DATE (PROD ~748K active) | **not used** — use for merge diagnosis |
| `COMPANY_HIERARCHY_CROSS_REFERENCE` | source-level parent links | not used |
| `COMPANY_ADDRESS_REL_CROSS_REFERENCE`, `COMPANY_RELATION` | | not used |
| `POTENTIAL_MATCH_COMPANY_CROSS_REFERENCE`, `RELTIO_COMPANY_POTENTIAL_MATCHES`, `COMPANY_MATCH_*`, `MDM_STATUS_SUMMARY`, `MATCH_STATISTICS_COMPANY`, `STG_*`, `COMPANY_SQS_RAW_EVENTS` | Reltio ops / match analytics | not used |

Address: `<ENV>_GDP_SILVER_DB.MDM.ADDRESS_CORE` (ESRI-standardized, `CBRE_ENTERPRISE_ADDRESS_ID`, `MDM_ENTITY_ID`, street/city/state/country/postal/lat/long) and `MDM.ADDRESS_CROSS_REFERENCE`.

PROD xref by system: Reltio 27.7M, ReltioGrouping 12.2M (internal), DNB 7.6M, EDP 6.4M, CIQ 4.9M, MTA 0.8M, DIQ 0.7M, GWS_BOE 140K, PEARCE 75K, FPD 45K, TPC 32K, TNT 28K, others small.

## 2. dbt post-MDM models — `models/silver/company_mdm/`

| Model | Mat. | Purpose |
|---|---|---|
| `company_cross_reference_entity_id` | iceberg table, schema STAGE | **Delta** of (CBRE id, SOURCE_UNIQUE_ID, SOURCE_SYSTEM_NAME) changed since wm `RELTIO/COMPANY_CORE_MDM/DIM_COMPANY` (`:9-13`). Branch A: xref⨝core ORG with GREATEST(created,updated,LUD) > wm (`:16-28`); Branch B: processed audit events (`:29-49`). Both always run. |
| `company_core_mdm` | ephemeral | 1 row per CBRE id for delta (`QUALIFY :232-235`). Latest core (`:50-53`), hierarchy pivot (`:58-139`), source_system pick (`:142-156`), active flag (`:213-221`), primary address join. |
| `company_industry_mdm` | ephemeral | delta companies × INDUSTRY_NAME, latest, active=MDM_STATUS ACTIVE |
| `company_hierarchy_mdm` | ephemeral | Reltio group chain org→immediate→domestic→global; consumer disabled |
| `company_event_outbox` | iceberg table STAGE | INSERT/UPDATE/DELETE events, post-hook appends to `STAGE.EVENT_OUTBOX_COMPANY` (status PENDING) |

### Active flag (`company_core_mdm.sql:213-221`)
- latest xref row per (MDM_ENTITY_ID, SOURCE_UNIQUE_ID) deleted if `MDM_DELETED_DATE IS NOT NULL OR MDM_STATUS='INACTIVE'`
- all deleted → `GDP_IS_ACTIVE=FALSE`, `GDP_DELETE_TS=MAX(MDM_DELETED_DATE)`
- ≥1 live → TRUE (even if core MDM_STATUS inactive)
- no xref → `COMPANY_CORE.MDM_STATUS='ACTIVE'`

### Hierarchy embedded in DIM_COMPANY
- `COMPANY_HIERARCHY` types `OrganizationToGlobalParent|DomesticParent|ImmediateParent` pivoted per child entity.
- Parent entity ids looked up in **full** COMPANY_CORE (`parent_lookup :94-114`) → `HIERARCHY_{GLOBAL|DOMESTIC|IMMEDIATE}_PARENT_ENTERPRISE_ID` + `_COMPANY_NAME`.
- Pivot itself is delta-scoped: parent columns only refresh when the **child** is in the delta (historic all-NULL issue; fixed via wm=1900 backfill — see project memory `dim-company-hierarchy-parents-empty`).
- Gold `HIERARCHY_PATH = CONCAT_WS('|', global, domestic, immediate)`; `HIERARCHY_TYPE_NAME/CODE` overwritten by DIM_HIERARCHY_TYPE 'DNB' for every row.

## 3. Gold — `<ENV>_GDP_GOLD_COMPANY_DB.COMPANY` (`models/gold/company/`)

| Table | Unique key | Notes |
|---|---|---|
| `DIM_COMPANY` | CBRE_ENTERPRISE_COMPANY_ID | skey `SEQ_DIM_COMPANY_SKEY` (`dim_company.sql:166`), excluded from merge → stable. SOURCE_UNIQUE_ID = CBRE id. Also MDM_ENTITY_ID, CBRE_ENTERPRISE_ADDRESS_ID, primary address cols, PRIMARY_INDUSTRY_NAME (from core.PRIMARY_INDUSTRY), COMPANY_TYPE_DESC (from RECORD_TYPE: Company/Immediate Parent/Domestic Parent/Global Ultimate), C360_CLIENT_NAME. DIM_DATA_SOURCE_SKEY='RELTIO'. HKEY `m_dim_company_hkey` (`macros/company_gold_utils.sql:13-51`, SHA2). Change filter `:149-158`. Post-hook `_HIST`. |
| `DIM_COMPANY_ALTERNATE_ADDRESS` | (DIM_COMPANY_SKEY, CBRE_ENTERPRISE_ADDRESS_ID, ADDRESS_TYPE_NAME) | ADDRESS_REL minus primary, live rels; INNER join DIM_COMPANY |
| `DIM_COMPANY_INDUSTRY` | (DIM_COMPANY_SKEY, INDUSTRY_NAME) | INNER join `SHARED.DIM_INDUSTRY_SECTOR` on UPPER(TRIM(name)) → unmapped industries dropped |
| `DIM_COMPANY_ALTERNATE_HIERARCHY` | — | `enabled=false` |
| `VW_COMPANY` | view | DIM_COMPANY ⟕ active alt address |
| `VW_COMPANY_CROSS_REFERENCE` | view (gdp-engine migration) | lineage for ES |
| `UPDATE_DIM_COMPANY_SKEY` | marker | post-hooks: advance xref stream → `repoint_all_company_skeys` → `m_repoint_pdl_company_dim_xref` → `repoint_all_pdl_gold_skeys` → advance DIM_COMPANY wm |
| `UPDATE_WATER_MARK_GOLD_COMPANY` | marker | advance wm for DIM_COMPANY, _ALTERNATE_ADDRESS, _INDUSTRY |

Child models read DIM_COMPANY via `source('gold_company', ...)`; order enforced by `-- depends_on` comments.

PROD (2026-10-14): DIM_COMPANY 17.30M active / 1.02M inactive; COMPANY_TYPE_DESC Company 6.08M, Immediate Parent 4.27M, Domestic Parent 4.06M, Global Ultimate 3.91M.

## 4. Event outbox payload
`to_json(object_construct(event_id, schema_version '1.0', domain 'company', type (RECORD_TYPE→Company/Immediate Parent/Global Ultimate/Domestic Parent), operation, flow 'batch', enterprise_id, timestamp, source_lineage[{source_system, source_id}]))`; lineage excludes Reltio/ReltioGrouping; operation DELETE if no ACTIVE core row, INSERT if never emitted before, else UPDATE. Publisher is outside repo.

## 5. OpenSearch `COMPANY_ES_DOC`
`models/gold/opensearch/company/company_es_doc.sql`: only `COMPANY_TYPE_DESC='Company'`; ES wm `GOLD/DIM_COMPANY/COMPANY_ES_DOC`; cascades parent/children when parent attributes or org counts change; `legal_hierarchy.{global,domestic,immediate}_parent` via self-joins on HIERARCHY_*_PARENT_ENTERPRISE_ID; industries, addresses, source_lineage from VW_COMPANY_CROSS_REFERENCE.
