{{ config(materialized = 'ephemeral') }}

-- ====================================================================
-- Generic ephemeral staging template
-- Pick ONE of the three extraction patterns below per target.
-- {prefix_lower} = project audit-column prefix (may be blank).
-- ====================================================================

-- ----- PATTERN 1: Single source table ---------------------------------
-- with sp_<entity_plural> as (
--     select distinct
--         <source_unique_id_expr> as source_unique_id,
--         -- TEXT  : NULLIF(TRIM("col"), '') as alias
--         -- NUMBER: "col" as alias
--         -- DATE  : "col" as alias
--         {PREFIX}_INSERTED_TS, {PREFIX}_UPDATED_TS, {PREFIX}_IS_ACTIVE
--     from {{ source('<entity>_<source_key>_source', '<BRONZE_TABLE>') }}
--     where <source_unique_id_expr> is not null
--     qualify row_number() over(partition by <source_unique_id_expr>
--             order by {prefix_lower}_updated_ts desc) = 1
-- ),

-- ----- PATTERN 2: Multi-source UNION ALL -------------------------------
-- with sp_<entity_plural> as (
--     -- Branch 1
--     select distinct
--         upper(trim(c."col_a")) || '||' || coalesce(upper(trim(c."col_b")), '') as source_unique_id,
--         -- ... aliases (must match branch 2 EXACTLY)
--         c.{PREFIX}_INSERTED_TS, c.{PREFIX}_UPDATED_TS, c.{PREFIX}_IS_ACTIVE
--     from {{ source('<entity>_<source_key>_source', '<TABLE_1>') }} c
--     left join {{ source('<entity>_<source_key>_source', '<TABLE_1_ADDR>') }} ca on ...
--     where ...
--     union all
--     -- Branch 2
--     select distinct
--         upper(trim(v."col_a")) || '||' || coalesce(upper(trim(v."col_b")), '') as source_unique_id,
--         -- ... same aliases as branch 1
--         v.{PREFIX}_INSERTED_TS, v.{PREFIX}_UPDATED_TS, v.{PREFIX}_IS_ACTIVE
--     from {{ source('<entity>_<source_key>_source', '<TABLE_2>') }} v
--     left join {{ source('<entity>_<source_key>_source', '<TABLE_2_ADDR>') }} va on ...
--     where ...
--     qualify row_number() over(partition by source_unique_id
--             order by greatest(coalesce({prefix_lower}_updated_ts,'1900-01-01'::timestamp_ntz),
--                                coalesce({prefix_lower}_inserted_ts,'1900-01-01'::timestamp_ntz)) desc) = 1
-- ),

-- ----- PATTERN 3: Per-target join graph ---------------------------------
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
--     where {prefix_lower}_is_active = TRUE
-- ),

base as (
    select distinct
        o.source_unique_id,
        r.{prefix_lower}_source_system_skey as ref_{prefix_lower}_source_system_skey,
        -- h.<hub>_core_skey as <hub>_core_skey,   -- (spokes only)
        -- *** FK SKEYs + business columns in DDL order from contract ***
        -- *** Type cast in base CTE ONLY where source type != target type ***

        o.{PREFIX}_IS_ACTIVE as {prefix_lower}_is_active,
        o.{PREFIX}_INSERTED_TS as {prefix_lower}_inserted_ts,
        current_user as {prefix_lower}_inserted_by,
        o.{PREFIX}_UPDATED_TS as {prefix_lower}_updated_ts,
        current_user as {prefix_lower}_updated_by
    from sp_<entity_plural> as o
    cross join ref_source_system as r
    -- left join hub h on upper(trim(o.source_unique_id)) = upper(trim(h.source_unique_id))   -- (spokes only)
    -- *** LEFT JOINs to ref_<short> tables from contract ***
)

select
    -- *** DDL-order final SELECT from contract ***
    {{ m_<target>_hkey() }} as <target>_hkey,
    {prefix_lower}_is_active,
    {prefix_lower}_inserted_ts,
    {prefix_lower}_inserted_by,
    {prefix_lower}_updated_ts,
    {prefix_lower}_updated_by
from base
