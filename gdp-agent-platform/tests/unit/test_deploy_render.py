import zipfile
import io

import pytest

from infrastructure.deploy_snowflake import (
    build_services_zip,
    list_migrations,
    merge_statement,
    render,
    seed_rows,
    AGENTS_DIR,
    DEMO_DIR,
    PROCEDURES_DIR,
    SEARCH_DIR,
    VIEWS_DIR,
)


def test_render_substitutes_and_rejects_unknown():
    assert render("USE {{database}};", {"database": "AI_PLATFORM"}) == "USE AI_PLATFORM;"
    with pytest.raises(AssertionError, match="unknown placeholder"):
        render("{{nope}}", {"database": "X"})


def test_all_sql_renders_without_leftover_placeholders():
    variables = {"database": "AI_PLATFORM", "services_import": "@AI_PLATFORM.CORE.CODE_STAGE/services-x.zip",
                 "warehouse": "DBT_WH", "agent_skills": "skills:\n  - name: example\n"}
    for _, _, path in list_migrations():
        render(path.read_text(encoding="utf-8"), variables)
    for folder in (PROCEDURES_DIR, VIEWS_DIR, SEARCH_DIR, AGENTS_DIR):
        for path in folder.glob("*.sql"):
            render(path.read_text(encoding="utf-8"), variables)
    for path in DEMO_DIR.glob("*.sql"):
        render(path.read_text(encoding="utf-8"), {**variables, "demo_database": "DEMO_SOURCE"})


def test_procedure_handlers_exist():
    import importlib
    import re

    for path in PROCEDURES_DIR.glob("*.sql"):
        for handler in re.findall(r"HANDLER = '([\w.]+)'", path.read_text(encoding="utf-8")):
            module, func = handler.rsplit(".", 1)
            assert callable(getattr(importlib.import_module(module), func)), handler


def test_migrations_are_sequential():
    versions = [int(v) for v, _, _ in list_migrations()]
    assert versions == list(range(1, len(versions) + 1))


def test_services_zip_is_deterministic_and_importable_layout():
    name1, data1 = build_services_zip()
    name2, data2 = build_services_zip()
    assert name1 == name2 and data1 == data2
    names = zipfile.ZipFile(io.BytesIO(data1)).namelist()
    assert "services/__init__.py" in names
    assert "services/workflow/procedures.py" in names
    assert "services/source/procedures.py" in names
    assert "services/dbt/procedures.py" in names
    assert "services/validation/procedures.py" in names
    assert not [n for n in names if "__pycache__" in n]


def test_seed_rows_shape():
    version, states, transitions = seed_rows("AI_PLATFORM")
    assert version and len(states) == 34
    assert all(len(r) == 8 for r in states) and all(len(r) == 6 for r in transitions)


def test_merge_statement_placeholder_count():
    sql = merge_statement("DB.CORE.T", ["A", "B", "C"], ["A"], 2)
    assert sql.count("%s") == 6
    assert "ON t.A = s.A" in sql and "B = s.B" in sql
