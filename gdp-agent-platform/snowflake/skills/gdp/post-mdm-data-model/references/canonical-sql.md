# Canonical SQL (replace <ENV> with DEV/QA/UAT/PROD)

## 1. Company: source record → mastered DIM_COMPANY_SKEY (pattern A)
```sql
with dim_company as (
  select dim_company_skey, cbre_enterprise_company_id
  from <ENV>_GDP_GOLD_COMPANY_DB.COMPANY.DIM_COMPANY
  where gdp_is_active
  qualify row_number() over (partition by cbre_enterprise_company_id order by dim_company_skey) = 1
), xref as (   -- FULL xref, never STAGE.*_ENTITY_ID
  select source_unique_id, upper(source_system_name) ssn, cbre_enterprise_company_id, mdm_entity_id
  from <ENV>_GDP_SILVER_DB.COMPANY_MDM.COMPANY_CROSS_REFERENCE
  where gdp_is_active and source_system_name not in ('Reltio','ReltioGrouping')
  qualify row_number() over (partition by source_unique_id, source_system_name
          order by gdp_updated_ts desc nulls last, cbre_enterprise_company_id) = 1
)
select cc.company_core_skey, rss.gdp_source_system_name, cc.source_unique_id,
       x.cbre_enterprise_company_id, nvl(dc.dim_company_skey, -1) dim_company_skey
from <ENV>_GDP_SILVER_DB.COMPANY.COMPANY_CORE cc
join <ENV>_GDP_SILVER_DB.SHARED.REF_GDP_SOURCE_SYSTEM rss
  on cc.ref_gdp_source_system_skey = rss.gdp_source_system_skey
left join xref x on cc.source_unique_id = x.source_unique_id
                and upper(rss.gdp_source_system_name) = x.ssn
left join dim_company dc on x.cbre_enterprise_company_id = dc.cbre_enterprise_company_id
where cc.gdp_is_active
qualify row_number() over (partition by cc.company_core_skey order by dc.dim_company_skey nulls last) = 1;
```
dbt form: `source('company_mdm_source','company_cross_reference')`, `ref('company_core')`, `ref('dim_company')`, `source('silver_shared','ref_gdp_source_system')`.

## 2. Property: source record → DIM_PROPERTY_SKEY
```sql
with dim_property as (
  select dim_property_skey, cbre_enterprise_property_id
  from <ENV>_GDP_GOLD_PROPERTY_DB.PROPERTY.DIM_PROPERTY where gdp_is_active
  qualify row_number() over (partition by cbre_enterprise_property_id order by dim_property_skey) = 1
), xref as (
  select source_system_id, upper(source_system_name) ssn, cbre_enterprise_property_id
  from <ENV>_GDP_SILVER_DB.PROPERTY_MDM.PROPERTY_CROSS_REFERENCE
  where gdp_is_active and mdm_delete_date is null
  qualify row_number() over (partition by source_system_id, source_system_name
          order by mdm_post_mdm_lud desc nulls last, mdm_updated_ts desc nulls last) = 1
)
select pc.property_core_skey, x.cbre_enterprise_property_id, nvl(dp.dim_property_skey,-1) dim_property_skey
from <ENV>_GDP_SILVER_DB.PROPERTY.PROPERTY_CORE pc
join <ENV>_GDP_SILVER_DB.SHARED.REF_GDP_SOURCE_SYSTEM rss on pc.ref_gdp_source_system_skey = rss.gdp_source_system_skey
left join xref x on pc.source_unique_id = x.source_system_id and upper(rss.gdp_source_system_name) = x.ssn
left join dim_property dp on x.cbre_enterprise_property_id = dp.cbre_enterprise_property_id
where pc.gdp_is_active
qualify row_number() over (partition by pc.property_core_skey order by dp.dim_property_skey nulls last) = 1;
```
Shortcut for read-only analysis: `<ENV>_GDP_GOLD_PROPERTY_DB.PROPERTY.VW_PROPERTY_CROSS_REFERENCE` (SOURCE_SYSTEM_NAME, SOURCE_SYSTEM_ID, CBRE id, DIM_PROPERTY_SKEY).

## 3. Diagnostics
```sql
-- 3a. Coverage pre-MDM → xref by source (company; swap for property)
with x as (select source_unique_id, upper(source_system_name) ssn, cbre_enterprise_company_id
           from <ENV>_GDP_SILVER_DB.COMPANY_MDM.COMPANY_CROSS_REFERENCE where gdp_is_active)
select upper(r.gdp_source_system_name) ssn, count(*) rows_, count(x.cbre_enterprise_company_id) matched,
       round(100*matched/rows_,2) pct
from <ENV>_GDP_SILVER_DB.COMPANY.COMPANY_CORE cc
join <ENV>_GDP_SILVER_DB.SHARED.REF_GDP_SOURCE_SYSTEM r on cc.ref_gdp_source_system_skey=r.gdp_source_system_skey
left join x on cc.source_unique_id=x.source_unique_id and upper(r.gdp_source_system_name)=x.ssn
where cc.gdp_is_active group by 1 order by 2 desc;

-- 3b. Trace one source record end to end
select cc.company_core_skey, cc.gdp_is_active core_active, x.cbre_enterprise_company_id, x.gdp_is_active xref_active,
       x.mdm_status, x.mdm_deleted_date, dc.dim_company_skey, dc.gdp_is_active dim_active, dc.company_type_desc
from <ENV>_GDP_SILVER_DB.COMPANY.COMPANY_CORE cc
join <ENV>_GDP_SILVER_DB.SHARED.REF_GDP_SOURCE_SYSTEM r on cc.ref_gdp_source_system_skey=r.gdp_source_system_skey
left join <ENV>_GDP_SILVER_DB.COMPANY_MDM.COMPANY_CROSS_REFERENCE x
  on cc.source_unique_id=x.source_unique_id and upper(r.gdp_source_system_name)=upper(x.source_system_name)
left join <ENV>_GDP_GOLD_COMPANY_DB.COMPANY.DIM_COMPANY dc on x.cbre_enterprise_company_id=dc.cbre_enterprise_company_id
where cc.source_unique_id = '<SUID>' and r.gdp_source_system_name = '<SYSTEM>';

-- 3c. Was the CBRE id merged away?
select * from <ENV>_GDP_SILVER_DB.COMPANY_MDM.COMPANY_MERGE
where losing_cbre_enterprise_company_id = '<ID>' or surviving_cbre_enterprise_company_id = '<ID>';

-- 3d. Stale fact skeys: fact points to a different skey than fresh resolution (use §1 as cr)
-- select count(*) from FACT_X f join cr on f.<core_skey>=cr.company_core_skey where f.dim_company_skey <> cr.dim_company_skey;

-- 3e. Watermarks
select * from <ENV>_GDP_UTIL_DB.CONFIG.GDP_DBT_WATERMARK_TBL
where source_system in ('RELTIO','GOLD') and target_model_name ilike any ('DIM_COMPANY%','DIM_PROPERTY%','%ES_DOC');

-- 3f. MDM ingestion status for property (why no xref)
select post_mdm_status, dq_status, count(*) from <ENV>_GDP_SILVER_DB.PROPERTY_MDM.PRE_MDM_UPDATED_RECORDS group by 1,2;
```
