import pytest

from infrastructure.seed_knowledge import PLATFORM_CONFIG
from services.source.catalog_display import (
    catalog_related,
    configure,
    display_domain_name,
    is_hidden_target,
    source_system_name,
    workspace_targets,
)


@pytest.fixture
def seeded_lists():
    """The installation's lists are seeded config (CATALOG_DISPLAY), applied by the API at runtime."""
    configure(next(v for k, v, _ in PLATFORM_CONFIG if k == "CATALOG_DISPLAY"))
    yield
    configure({"hidden_target_tables": [], "hidden_target_databases": [], "hidden_target_schemas": [],
               "hidden_target_ids": []})


def test_nothing_installation_specific_is_hidden_by_code():
    assert not is_hidden_target({"target_table": "COMPLETE_EMPLOYEE_DETAILS", "target_database": "ALATION_POC"})


def test_hides_seed_and_poc_targets(seeded_lists):
    assert is_hidden_target({
        "target_table": "COMPLETE_EMPLOYEE_DETAILS",
        "target_database": "ALATION_POC",
        "target_schema": "ALATION_SCHEMA",
    })
    assert is_hidden_target({
        "target_table": "DIM_CUSTOMER",
        "target_database": "DEV_AI_PLATFORM",
        "target_schema": "GDP_SILVER",
        "target_table_id": "00000000-0000-4000-a000-000000000002",
    })
    assert not is_hidden_target({
        "target_table": "ADDRESSES",
        "target_database": "DEV_GDP_BRONZE_DB",
        "target_schema": "BRONZE_LIGHTBOX",
    })


def test_hides_product_domain_label():
    assert display_domain_name("GDP") is None
    assert display_domain_name("Finance") == "Finance"


def test_workspace_targets_follow_source_catalog():
    rows = [
        {"target_table": "ADDRESSES", "target_database": "DEV_GDP_BRONZE_DB", "target_schema": "BRONZE_LIGHTBOX", "fqn": "DEV_GDP_BRONZE_DB.BRONZE_LIGHTBOX.ADDRESSES"},
        {"target_table": "CAI_CUSTOMER", "target_database": "CUSTOMERAI_DB", "target_schema": "LANDING", "fqn": "CUSTOMERAI_DB.LANDING.CAI_CUSTOMER"},
        {"target_table": "DIM_CUSTOMER", "target_database": "DEV_AI_PLATFORM", "target_schema": "GDP_SILVER", "fqn": "DEV_AI_PLATFORM.GDP_SILVER.DIM_CUSTOMER"},
    ]
    scoped = workspace_targets(rows, "DEV_GDP_BRONZE_DB", "LIGHTBOX")
    assert [r["target_table"] for r in scoped] == ["ADDRESSES"]
    assert catalog_related(rows[0], "DEV_GDP_BRONZE_DB", "LIGHTBOX")
    assert workspace_targets(rows, None, None) == []


def test_source_system_name_from_schema_not_product():
    assert source_system_name("DEV_GDP_BRONZE_DB", "LIGHTBOX") == "LIGHTBOX"
    assert source_system_name("DEV_GDP_BRONZE_DB", None) == "DEV_BRONZE_DB"
    assert source_system_name("DEV_GDP_BRONZE_DB", "LIGHTBOX", "GDP") == "LIGHTBOX"
