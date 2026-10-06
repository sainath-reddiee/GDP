{{ config(materialized = 'ephemeral') }}

-- ====================================================================
-- Generic ephemeral staging template
-- Pick ONE of the three extraction patterns below per target.
-- ====================================================================

-- ----- PATTERN 1: Single source table (FPD-style) ---------------------
-- with sp_<entity_plural> as (
--     select distinct
--         <source_unique_id_expr> as source_unique_id,
--         -- TEXT  : NULLIF(TRIM("col"), '') as alias
--         -- NUMBER: "col" as alias
--         -- DATE  : "col" as alias
--         GDP_INSERTED_TS, GDP_UPDATED_TS, GDP_IS_ACTIVE
--     from {{ source('<entity>_<source_key>_source', '<BRONZE_TABLE>') }}
--     where <source_unique_id_expr> is not null
--     qualify row_number() over (partition by <source_unique_id_expr>
--             order by gdp_updated_ts desc) = 1
-- ),

-- ----- PATTERN 2: Multi-source UNION ALL (MTA-style) ------------------
-- with sp_<entity_plural> as (
--     -- Branch 1
--     select distinct
--         upper(trim(c."col_a")) || '||' || coalesce(upper(trim(c."col_b")), '') as source_unique_id,
--         -- ... aliases (must match branch 2 EXACTLY)
--         c.GDP_INSERTED_TS, c.GDP_UPDATED_TS, c.GDP_IS_ACTIVE
--     from {{ source('<entity>_<source_key>_source', '<TABLE_1>') }} c
--     left join {{ source('<entity>_<source_key>_source', '<TABLE_1_ADDR>') }} ca on ...
--     where ...
--     union all
--     -- Branch 2
--     select distinct
--         upper(trim(v."col_a")) || '||' || coalesce(upper(trim(v."col_b")), '') as source_unique_id,
--         -- ... same aliases as branch 1
--         v.GDP_INSERTED_TS, v.GDP_UPDATED_TS, v.GDP_IS_ACTIVE
--     from {{ source('<entity>_<source_key>_source', '<TABLE_2>') }} v
--     left join {{ source('<entity>_<source_key>_source', '<TABLE_2_ADDR>') }} va on ...
--     where ...
--     qualify row_number() over (partition by source_unique_id
--             order by greatest(coalesce(gdp_updated_ts,'1900-01-01'::timestamp_ntz),
--                                coalesce(gdp_inserted_ts,'1900-01-01'::timestamp_ntz)) desc) = 1
-- ),

-- ----- PATTERN 3: Per-target join graph (EDP-style) -------------------
-- with sp_<target> as (
--     -- This target's specific bronze JOIN graph (different from sibling targets)
--     select distinct ...
--     from {{ source('<entity>_<source_key>_source', '<TABLE>') }} a
--     join {{ source('<entity>_<source_key>_source', '<JOIN_TBL>') }} b on ...
--     where <target-specific filter>
--     qualify row_number() over (...) = 1
-- ),

-- ====================================================================
-- Common downstream blocks (apply to all patterns)
-- ====================================================================

ref_source_system as (
    {{ m_get_source_system_skey_<domain>('<SOURCE_SYSTEM_NAME>') }}
),

-- Reference CTEs (FK lookups) — pull from references/<domain>-contract.md
-- ref_<short> as (select <skey>, <desc> from {{ source(...) }}),

-- Hub lookup (only for SPOKE targets, not the *_CORE hub itself)
-- hub as (
--     select source_unique_id, <hub>_core_skey
--     from {{ ref('<hub_entity>') }}
--     where gdp_is_active = TRUE
-- ),

base as (
    select distinct
        o.source_unique_id,
        r.gdp_source_system_skey as ref_gdp_source_system_skey,
        -- h.<hub>_core_skey as <hub>_core_skey,   -- (spokes only)
        -- *** FK SKEYs + business columns in DDL order from contract ***
        -- *** Type cast in base CTE ONLY where source type != target type ***

        o.GDP_IS_ACTIVE as gdp_is_active,
        o.GDP_INSERTED_TS as gdp_inserted_ts,
        current_user as gdp_inserted_by,
        o.GDP_UPDATED_TS as gdp_updated_ts,
        current_user as gdp_updated_by
    from sp_<entity_plural> as o
    cross join ref_source_system as r
    -- left join hub h on upper(trim(o.source_unique_id)) = upper(trim(h.source_unique_id))   -- (spokes only)
    -- *** LEFT JOINs to ref_<short> tables from contract ***
)

select
    -- *** DDL-order final SELECT from contract ***
    {{ m_<target>_hkey() }} as <target>_hkey,
    gdp_is_active,
    gdp_inserted_ts,
    gdp_inserted_by,
    gdp_updated_ts,
    gdp_updated_by
from base
