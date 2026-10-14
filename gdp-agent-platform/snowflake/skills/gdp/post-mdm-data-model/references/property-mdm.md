# Property MDM → Gold (detail)

Paths relative to `gdp-dbt/gdp-dbt/`.

## 1. Pre-MDM silver — `<ENV>_GDP_SILVER_DB.PROPERTY`
- `PROPERTY_CORE` PK `PROPERTY_CORE_SKEY`, UK (SOURCE_UNIQUE_ID, REF_GDP_SOURCE_SYSTEM_SKEY) (`models/silver/property/property_core.sql:7,596`); branches edp/diq/lightbox/mta/service_layer gated by `m_is_source_active`. LIGHTBOX SOURCE_UNIQUE_ID = `BUILDING_LID`.
- Children `PROPERTY_ADDRESS`, `PROPERTY_USAGE`, `PROPERTY_OWNER` on PROPERTY_CORE_SKEY.
- Hard-delete branch deactivates rows missing from source (`:367-593`, no service_layer).

## 2. External Reltio tables — `<ENV>_GDP_SILVER_DB.PROPERTY_MDM` (source `property_mdm_source`, `models/silver/property_mdm/source.yml`)

| Table | Keys / notable cols | dbt use |
|---|---|---|
| `PROPERTY_CROSS_REFERENCE` | `SOURCE_SYSTEM_ID` (= pre-MDM SOURCE_UNIQUE_ID), `SOURCE_SYSTEM_NAME`, `CBRE_ENTERPRISE_PROPERTY_ID`, `MDM_ENTITY_ID`, MDM_STATUS, **`MDM_DELETE_DATE`**, GDP_IS_ACTIVE | delta, core_mdm, outbox |
| `PROPERTY_CORE` | `CBRE_ENTERPRISE_PROPERTY_ID`, `MDM_ENTITY_ID`, PROPERTY_KIND/NAME, PARK_NAME, PARCEL_NUMBER, areas, YEAR_BUILT, floors, BUILDING_IDENTIFIER, GEOMETRY, PRIMARY_USAGE_TYPE, TENANCY_TYPE, PRIMARY_ADDRESS, PRIMARY_CBRE_ENTERPRISE_ADDRESS_ID, PROPERTY_CLASS, MDM_STATUS | core_mdm, outbox |
| `PROPERTY_TYPE` | MDM_ENTITY_ID / CBRE id × USAGE_TYPE × PROPERTY_SUBTYPE, NRA, units, class | core_mdm (primary usage), DIM_PROPERTY_USAGE |
| `PROPERTY_ADDRESS_REL` | PROPERTY_MDM_ENTITY_ID/CBRE property id ↔ ADDRESS_MDM_ENTITY_ID/CBRE address id, ADDRESS_TYPE, REL_END_DATE | alt address |
| `PROPERTY_ENTITY_EVENTS_AUDIT` | ENTITY_ID, PROCESSED_FLAG, PROCESSED_TIME, LOAD_TIME | delta |
| `PROPERTY_MERGE` | SURVIVING/LOSING CBRE_ENTERPRISE_PROPERTY_ID + entity ids, MERGE_DATE, UNMERGE_DATE (PROD ~254K active) | **not used** |
| `PRE_MDM_UPDATED_RECORDS` | SOURCE_SYSTEM_ID, SOURCE_SYSTEM_NAME, GDP_SOURCE_SYSTEM_SKEY, **PROPERTY_CORE_SKEY**, PRE_MDM_RECORD_STATUS, DQ_STATUS, POST_MDM_STATUS, CBRE_ENTERPRISE_PROPERTY_ID, MDM_ENTITY_ID | not used; useful to see why a pre-MDM row has no xref (DQ-filtered / not processed) |
| `MDM_STATUS_SUMMARY`, `PRE_MDM_STATUS_SUMMARY` | per run/source counts: new, updated, DQ filtered, processed, Lightbox auto/manual/not matched | ops monitoring |
| `PROPERTY_EDP_LIGHTBOX_MAPPING`, `MATCH_*`, `PROPERTYMATCHRESULTS_*`, `STG_*`, `*_08_04` | match analytics / backups | not used |

PROD xref by system: LIGHTBOX 21.2M, EDP 4.27M, MTA 2.0M, DIQ 1.72M, Reltio 37. Many-to-one: EDP 3.1M active rows → 880K CBRE ids; LIGHTBOX ≈1:1 (Lightbox is the property spine; EDP/MTA/DIQ merge onto Lightbox buildings).

## 3. dbt post-MDM models — `models/silver/property_mdm/`
| Model | Notes |
|---|---|
| `property_cross_reference_entity_id` (STAGE, iceberg table) | delta since wm `RELTIO/PROPERTY_CORE_MDM/DIM_PROPERTY`; xref ts branch (`:14-24`) ∪ audit branch (`:25-42`, missing NULLS LAST at `:34`). No RECORD_TYPE filter. |
| `property_core_mdm` (ephemeral) | 1 row / CBRE id. latest core (`:50-53`); active flag from xref (`:56-76,146-154`) same rule as company; primary PROPERTY_TYPE = row matching PRIMARY_USAGE_TYPE first (`:80-95`); address via PRIMARY_ADDRESS → MDM.ADDRESS_CORE (`:159-166`). |
| `property_event_outbox` (STAGE) | → `STAGE.EVENT_OUTBOX_PROPERTY`, event_type always NULL |

## 4. Gold — `<ENV>_GDP_GOLD_PROPERTY_DB.PROPERTY` (`models/gold/property/`)
| Table | Unique key | Notes |
|---|---|---|
| `DIM_PROPERTY` | CBRE_ENTERPRISE_PROPERTY_ID | skey `SEQ_DIM_PROPERTY_SKEY` (`dim_property.sql:123`); PROPERTY_MDM_ENTITY_ID, CBRE_ENTERPRISE_ADDRESS_ID; zero/neg numerics → NULL; hardcoded NULL STATE_PROVINCE_CODE/COUNTRY_CODE/COUNTY; `YEAR_BUILT_BEFORE_1950 = 10` (constant); HKEY 47 cols (`macros/property_gold_utils.sql:16-66`); change filter `:111-115` |
| `DIM_PROPERTY_ALTERNATE_ADDRESS` | (DIM_PROPERTY_SKEY, CBRE_ENTERPRISE_ADDRESS_ID, ADDRESS_TYPE_DESC='ALTERNATE') | skey column is `DIM_ADDRESS_SKEY`; NULL address id breaks merge key |
| `DIM_PROPERTY_USAGE` | SOURCE_UNIQUE_ID = `cbre_id~usage_type~subtype` | IS_PRIMARY_USAGE; `DIM_GEOGRAPHY_HIERARCHY_SKEY` -1 on insert, set by `USP_GOLD_GEOGRAPHY_DERIVATION` post-hook (`macros/geography_derivation.sql:23-33`) |
| `VW_PROPERTY_CROSS_REFERENCE` | view | SOURCE_SYSTEM_NAME, SOURCE_SYSTEM_ID, MDM_ENTITY_ID, CBRE_ENTERPRISE_PROPERTY_ID, **DIM_PROPERTY_SKEY** — ready-made source-record → gold skey map (verify active semantics before relying) |
| `UPDATE_DIM_PROPERTY_SKEY` | marker | advance stream → `repoint_all_property_skeys` (FACT_LEASE, FACT_SALE) → advance wm |
| `UPDATE_WATER_MARK_GOLD_PROPERTY` | marker | advance wm DIM_PROPERTY/_ALTERNATE_ADDRESS/_USAGE |

PROD: DIM_PROPERTY 18.42M active / 263K inactive.

## 5. OpenSearch `PROPERTY_ES_DOC`
Incremental on DIM_PROPERTY.GDP_UPDATED_TS >= ES wm (`GOLD/DIM_PROPERTY/PROPERTY_ES_DOC`; missing row ⇒ incremental no-op). Doc = core attrs + `usages[]` (with geography) + primary/alternate addresses + `suggest[]` + `metadata{id=DIM_PROPERTY_SKEY, mdm_entity_id, gdp_is_active, source_lineage}`. `_DELETED = NOT GDP_IS_ACTIVE`; soft deletes still emitted.
