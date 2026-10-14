# How other domains reach mastered company / property

## 1. Silver hubs carry PRE-MDM skeys
Each source silver model looks up its own source's core row by SOURCE_UNIQUE_ID and outputs `nvl(core_skey,-1)`:
opportunity e.g. `models/silver/opportunity/tnt/tnt_opportunity_core.sql:199-218`; lease `silver/lease/edp/edp_lease_core.sql:102-106`; sale `silver/sale/diq/diq_sale_core.sql:75-89`; finance `silver/finance/fpd/fpd_finance_core.sql:104-133`; contact `silver/contact/zoominfo/zoominfo_contact_company_role.sql:43-75`.
Silver `-1` = no pre-MDM match.

## 2. Gold resolution patterns
- **A (strict, preferred)**: company_core → REF_GDP_SOURCE_SYSTEM → full xref on SOURCE_UNIQUE_ID **and** UPPER(system name) → DIM_COMPANY (active, min skey per CBRE id).
- **B (loose)**: xref on SOURCE_UNIQUE_ID only; dim often via `source()` with no active filter.
- **P (property)**: property_core → rss → xref on SOURCE_SYSTEM_ID + name → DIM_PROPERTY. Today uses the **delta** STAGE xref (bug).
- **X (PDL)**: name/alias crosswalk `SILVER.COMPANY.PDL_COMPANY_DIM_XREF`, no MDM.

| Consumer (gold) | Column | Pattern | Key refs |
|---|---|---|---|
| `opportunity/fact_opportunity.sql` | DIM_COMPANY_SKEY | A | `company_resolution :72-101`, join `:358-359`, `nvl -1 :125` |
| `finance/fpd/fpd_fact_revenue.sql` → FACT_REVENUE | DIM_COMPANY_SKEY | A | `:59-88`, `:187-188`; finance_core active filter commented `:38` |
| `lease/fact_lease_company_role.sql` | DIM_COMPANY_SKEY | B | `:30-40`, EDP only `:75-79` |
| `lease/fact_lease.sql` | DIM_PROPERTY_SKEY | P (delta xref) | `:40-64`, xref `:49` |
| `sale/fact_sale_company_role.sql` | DIM_COMPANY_SKEY | B | `:34-44`, EDP only |
| `sale/fact_sale.sql` | DIM_PROPERTY_SKEY | P (delta xref) | `:27-50`, xref `:36` |
| `contact/dim_contact_company_role.sql` | DIM_COMPANY_SKEY | B, dedup MDM_UPDATED_TS, dim via source() | `:39-54`; not in repoint list |
| `supplemental/{dim_company_contact, fact_company_competitor, earning, funding, intent, spoc, fact_news, fact_survey_score}` | DIM_COMPANY_SKEY | B (spoc driven by REF_COMPANY_CORE_SKEY) | CTE ~`:33-50` each |
| `supplemental/fact_company_parent_crossref.sql` | DIM_COMPANY_SKEY | A, truncate+reload | `:32-58` |
| `supplemental/fact_company_workforce / executive_hires / lead_employer` | DIM_*COMPANY_SKEY | X | |
| `supplemental/fact_contact_activity_detail`, `fact_company_contact_rel` | DIM_RELATED_TO_COMPANY_SKEY | **none** — pre-MDM skey stored in DIM column (`:47`) | |

Lease/sale gold is **EDP-only**; MTA/DIQ silver never reaches gold.

## 3. Repoint (post-merge reconciliation)
- Triggered by `models/gold/company/update_dim_company_skey.sql:6-12` and `models/gold/property/update_dim_property_skey.sql:6-10`.
- `macros/company_repoint.sql` `m_company_skey_src` (`:27-48`): target's silver core (active) → COMPANY_CORE (active) → rss → **STAGE delta xref** (id + name) → ⟕ DIM_COMPANY; `QUALIFY ... ORDER BY DIM_COMPANY_SKEY NULLS LAST`. `m_repoint_company_skey` (`:52-69`) UPDATEs where `NVL(old,-1) != NVL(new,-1)`.
- Targets: `dbt_project.yml` vars `company_repoint_targets` (`:260-395`: FACT_OPPORTUNITY [suid+data source], FACT_REVENUE, FACT_LEASE_COMPANY_ROLE, FACT_SALE_COMPANY_ROLE, DIM_COMPANY_CONTACT, supplemental facts, FACT_COMPANY_PARENT_CROSSREF), `pdl` targets (`:399-426`), `property_repoint_targets` (`:238-258`: FACT_LEASE, FACT_SALE).
- PDL: `m_repoint_pdl_company_dim_xref` (`company_repoint.sql:109-139`) rematches names against active DIM_COMPANY where `HIERARCHY_TYPE_NAME IS NULL` (matches 0 rows in PROD — all are 'DNB').
- Repoint does not touch HKEY or write _HIST; next incremental re-merge may then create history rows.

## 4. Adding a new gold consumer (checklist)
1. Silver: carry `COMPANY_CORE_SKEY`/`PROPERTY_CORE_SKEY` (source-specific lookup, `nvl(...,-1)`).
2. Gold: use pattern A from `canonical-sql.md` with `ref('dim_company')` (gives a DAG edge) and full xref source.
3. Include `dim_company_skey` in the model's HKEY macro (see project memory `fact-opportunity-gold-hkey`).
4. Register in `company_repoint_targets` / `property_repoint_targets` (`core_table`, `core_schema_var`, `core_company_skey_col` if not COMPANY_CORE_SKEY, `match_cols`, `include_data_source`/`translate_data_source`).
5. Ensure orchestration runs it after `gold_company` / `gold_property` tags.

## 5. Run order (inferred; orchestration is external Airflow by tag)
bronze → silver sources → silver COMPANY_CORE/PROPERTY_CORE + domain hubs → [Reltio] → `silver_company_mdm`/`silver_property_mdm` → STAGE xref delta → DIM_COMPANY/DIM_PROPERTY → children → `update_dim_*_skey` (repoint, wm) → domain gold facts → ES docs (`es_company`/`es_property` tags, separate DAG).
