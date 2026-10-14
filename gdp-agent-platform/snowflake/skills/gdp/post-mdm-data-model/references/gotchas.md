# Known defects / risks (validate before quoting — code as of 2026-10-14)

| # | Issue | Where | Effect |
|---|---|---|---|
| G1 | Property resolution uses **delta** xref `ref('property_cross_reference_entity_id')` | `gold/lease/fact_lease.sql:49`, `gold/sale/fact_sale.sql:36` | Lease/sale rows whose property didn't change this run → DIM_PROPERTY_SKEY -1 (overwrites good value); repoint can't fix (also delta) |
| G2 | Pattern B joins xref on SOURCE_UNIQUE_ID only (no system name) | lease/sale company_role, contact, supplemental (except parent_crossref) | Cross-source id collision → wrong company; repoint (strict) may later flip it |
| G3 | Inconsistent dedup: GDP_UPDATED_TS vs MDM_UPDATED_TS vs lowest CBRE id vs lowest DIM skey | various | Multi-mapped source rows disagree across consumers |
| G4 | DIM lookup via `source()` without `gdp_is_active`/dedup | contact, supplemental | Can attach to inactive/merged-away dim rows; no DAG edge |
| G5 | Delta filters RECORD_TYPE='ORG'; full xref doesn't | `company_cross_reference_entity_id.sql:23,38` | DIM_COMPANY still holds ~12M parent-type rows from older loads; filter COMPANY_TYPE_DESC='Company' for org counts |
| G6 | Repoint scope = current delta only; requires active core; inner join xref | `macros/company_repoint.sql:27-48`, `property_repoint.sql:20-29` | Facts loaded with -1 before the company existed stay -1 until it re-enters delta; unmerge never reverts |
| G7 | Repoint UPDATE skips HKEY/_HIST | repoint macros | Next incremental sees hkey diff → extra history rows |
| G8 | Not in repoint lists: DIM_CONTACT_COMPANY_ROLE, FACT_CONTACT_ACTIVITY_DETAIL, FACT_COMPANY_CONTACT_REL | `dbt_project.yml:238-426` | Stay stale after merges |
| G9 | Pre-MDM skey written into DIM column | `fact_contact_activity_detail.sql:47`, `fact_company_contact_rel.sql:47`; funding `SOURCE_INVESTOR_COMPANY_CORE_SKEY` | Won't join to DIM_COMPANY |
| G10 | `-1` means both "no pre-MDM match" (silver) and "no MDM/dim match" (gold) | | Diagnose layer by layer |
| G11 | `COMPANY_MERGE`/`PROPERTY_MERGE` never read by dbt | | Merge handled only by xref repointing; loser DIM rows persist active until all xrefs deleted |
| G12 | Active precedence: any live xref ⇒ DIM active even if MDM_STATUS inactive; outbox DELETE uses MDM_STATUS | `company_core_mdm.sql:213-217`, `property_core_mdm.sql:146-150`, outboxes | Kafka vs gold/ES disagree |
| G13 | PDL crosswalk filter `HIERARCHY_TYPE_NAME IS NULL` | `company_repoint.sql:131` | PROD: every DIM_COMPANY row = 'DNB' → crosswalk rematch finds nothing |
| G14 | Hierarchy parents only refresh when child is in delta | `company_core_mdm.sql:58-139` | Parent rename/move not propagated; wm=1900 backfill workaround |
| G15 | Outbox: two different uuids (column vs payload), append w/o dedupe, depends on unmanaged table | `*_event_outbox.sql` | Duplicate events on rerun |
| G16 | `DIM_PROPERTY_ALTERNATE_ADDRESS` merge key contains nullable CBRE_ENTERPRISE_ADDRESS_ID | `dim_property_alternate_address.sql:1-14` | Re-insert with new skey every change |
| G17 | Vanished children (ended address rel, dropped usage) never deactivated in gold | alt address / usage dims | ES keeps showing them |
| G18 | `m_advance_*_xref_stream` CTAS `WHERE 1=2` on stream — offset advance unverified | update_dim_*_skey | Stream may grow unbounded (nothing else consumes it) |
| G19 | `full_refresh=false` on gold; watermark missing ⇒ 1900 full delta; ES wm missing ⇒ incremental no-op | | Backfill = reset wm row, not `--full-refresh` |
| G20 | Doc drift in `dim_company.yml` (HKEY "MD5" is SHA2, HIERARCHY_PATH format, PRIMARY_INDUSTRY source, DIM_DATA_SOURCE lineage) | | Trust code over yml |
| G21 | Lease/sale gold EDP-only; undefined vars `silver_sale_schema`/`silver_sales_schema` fall back to SALES | `fact_lease*.sql`, `dbt_project.yml:254,292` | |
| G22 | `get_env_prefix()` only dev/qa/uat/prod | gold + silver MDM models | personal targets fail |

Related project memories: `company-core-orphan-deactivation-bug`, `dim-company-hierarchy-parents-empty`, `uat-company-mdm-state`, `company-domain-join-integrity`, `gdp-sector-source-gaps`, `fact-opportunity-gold-hkey`.
