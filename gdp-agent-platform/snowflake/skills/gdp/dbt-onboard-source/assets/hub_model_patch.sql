-- Hub-model patch snippet — add a new source CTE + UNION ALL branch to <entity>.sql.
-- Replace <source_key>, <prefix>, <entity>, <SOURCE_SYSTEM_NAME>.

-- 1) Add AFTER existing source CTEs:
<source_key>_source as (
    select * from {{ ref('<prefix>_<entity>') }}
    {% if is_incremental() %}
    where GREATEST(
        coalesce(gdp_inserted_ts, '1900-01-01'::timestamp_ntz),
        coalesce(gdp_updated_ts,  '1900-01-01'::timestamp_ntz)
    ) > $wm_ts
    {% endif %}
),

-- 2) Inside the `unioned` CTE, append after the last existing branch:
    union all
    -- <SOURCE_SYSTEM_NAME>
    select
        -- *** same business columns (DDL order) as the reference branch ***
    from <source_key>_source where {{ m_is_source_active('<source_key>') }}
