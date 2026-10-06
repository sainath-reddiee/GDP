-- ====================================================================
-- Generic <domain> macros — created/extended by the onboarding skill.
-- One m_<target>_hkey macro per silver target in the domain.
-- ====================================================================

-- Source-system SKEY lookup (one per domain)
{% macro m_get_source_system_skey_<domain>(source_system_name) -%}
    select distinct
        GDP_SOURCE_SYSTEM_SKEY as gdp_source_system_skey
    from {{ source('<domain>_shared_reference', 'ref_gdp_source_system') }}
    where upper(GDP_SOURCE_SYSTEM_NAME) = '{{ source_system_name | upper }}'
{%- endmacro %}


-- Hub HKEY (typically <domain>_core)
{% macro m_<hub_target>_hkey() -%}
{{ generate_sha2_hash_key([
    -- *** Paste HKEY BLOCK from contract section: <hub_target> ***
]) }}
{%- endmacro %}


-- Spoke HKEYs — one per spoke target. Add as needed.
-- {% macro m_<spoke_target_1>_hkey() -%}
-- {{ generate_sha2_hash_key([
--     -- *** Paste HKEY BLOCK from contract section: <spoke_target_1> ***
-- ]) }}
-- {%- endmacro %}
--
-- {% macro m_<spoke_target_2>_hkey() -%}
-- {{ generate_sha2_hash_key([
--     -- *** Paste HKEY BLOCK from contract section: <spoke_target_2> ***
-- ]) }}
-- {%- endmacro %}
