"""The consolidation into one platform schema (V061): the mapping, the generated SQL, the codemod and the deploy gate."""

import re
from pathlib import Path

import pytest

from infrastructure.consolidation import codemod, generate
from infrastructure.deploy_snowflake import agent_statements, consolidation_gate, list_migrations, render
from services.common.platform_schema import (KEPT_KINDS, LEGACY_SCHEMAS, MOVED, PLATFORM_SCHEMA, RENAME, fqn, obj,
                                             resolve, stage)

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "snowflake" / "database" / "migrations"
TEMPLATE_DIRS = [ROOT / "snowflake" / d for d in ("procedures", "views", "cortex_search", "agents")]
CREATE = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:SECURE\s+)?(TABLE|VIEW|STAGE|FILE\s+FORMAT|SEQUENCE|ROW\s+ACCESS\s+POLICY)"
                    r"\s+(?:IF\s+NOT\s+EXISTS\s+)?\{\{database\}\}\.([A-Z_]+)\.([A-Z_][A-Z0-9_]*)", re.I)


def _migration_objects(upto="059"):
    found = {}
    for version, _, path in list_migrations():
        if version > upto:
            continue
        for kind, schema, name in CREATE.findall(path.read_text(encoding="utf-8")):
            found[(schema.upper(), name.upper())] = re.sub(r"\s+", " ", kind.upper())
    return found


def test_every_object_of_the_old_schemas_is_mapped():
    declared = {k: v for k, v in _migration_objects().items() if k[0] in LEGACY_SCHEMAS}
    missing = sorted(f"{t} {s}.{n}" for (s, n), t in declared.items() if (s, n) not in MOVED)
    assert not missing, missing
    wrong_type = sorted(f"{s}.{n}" for (s, n), t in declared.items() if MOVED[(s, n)][0] != t)
    assert not wrong_type, wrong_type


def test_no_two_objects_collide_in_the_platform_schema():
    core = {n for (s, n), t in _migration_objects().items() if s == PLATFORM_SCHEMA}
    targets = {}
    for (schema, name), (kind, new, _) in MOVED.items():
        namespace = "PROC" if kind == "PROCEDURE" else kind if kind in ("STAGE", "FILE FORMAT", "SEQUENCE") else "REL"
        key = (namespace, new)
        assert key not in targets, f"{schema}.{name} and {targets.get(key)} both become {PLATFORM_SCHEMA}.{new}"
        targets[key] = f"{schema}.{name}"
        assert not (namespace == "REL" and new in core), f"{schema}.{name} collides with {PLATFORM_SCHEMA}.{new}"


def test_generated_files_match_the_mapping():
    assert generate.V061.read_text(encoding="utf-8") == generate.v061_sql()
    assert generate.COMPAT.read_text(encoding="utf-8") == generate.compat_sql()


def test_v061_only_renames_in_place_and_is_rerunnable():
    sql = re.sub(r"--[^\n]*", "", generate.V061.read_text(encoding="utf-8"))
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    for s in statements:
        assert re.match(r"^(ALTER|GRANT|REVOKE|CREATE OR REPLACE VIEW)\b", s), s
        assert not re.search(r"\b(DROP|TRUNCATE|DELETE|INSERT|UPDATE|MERGE|CLONE)\b", s), s
        if "RENAME TO" in s:
            assert re.match(r"^ALTER [A-Z ]+ IF EXISTS \{\{database\}\}\.[A-Z_]+\.[A-Z_0-9]+ RENAME TO "
                            r"\{\{database\}\}\.CORE\.[A-Z_0-9]+$", s), s
    renames = sum("RENAME TO" in s for s in statements)
    assert renames == sum(1 for (_, _, m) in MOVED.values() if m == RENAME)


def test_compatibility_views_never_expose_credentials():
    text = re.sub(r"--[^\n]*", "", generate.COMPAT.read_text(encoding="utf-8"))
    assert "JIRA.USER_TOKEN " not in text and "JIRA.OAUTH_STATE " not in text
    assert text.count("CREATE OR REPLACE VIEW") == text.count("COPY GRANTS")


def test_no_code_or_template_names_an_old_schema():
    stale = []
    for f, rel in codemod.files(codemod.DEFAULT_PATHS):
        text = f.read_bytes().decode("utf-8")
        if codemod.rewrite(text, template=rel.startswith(codemod.TEMPLATE_DIRS)) != text:
            stale.append(rel)
    assert not stale, f"run python -m infrastructure.consolidation.codemod: {stale}"


def test_templates_use_the_schema_placeholder():
    for d in TEMPLATE_DIRS:
        for path in d.glob("*.sql"):
            text = path.read_text(encoding="utf-8")
            assert "{{database}}.CORE." not in text, path.name
            if path.name != "compat_legacy.sql":
                for schema in LEGACY_SCHEMAS:
                    assert f"{{{{database}}}}.{schema}." not in text, (path.name, schema)


def test_privilege_codes_that_look_qualified_are_untouched():
    text = (ROOT / "services" / "governance" / "policy.py").read_text(encoding="utf-8") if \
        (ROOT / "services" / "governance" / "policy.py").exists() else ""
    sample = "OPS.VIEW JIRA.READ KNOWLEDGE.EDIT MAPPING.APPROVE SOURCE.CONNECT PROFILE.RUN AUDIT.VIEW CODE.VIEW"
    assert codemod.rewrite(sample, template=False) == sample
    assert codemod.rewrite(text, template=False) == text


def test_codemod_rewrites_only_mapped_objects():
    assert codemod.rewrite("SELECT * FROM KNOWLEDGE.DOMAIN_REGISTRY", False) == "SELECT * FROM CORE.DOMAIN_REGISTRY"
    assert codemod.rewrite("FROM {database}.CONFLUENCE.SOURCE s", False) == "FROM {database}.CORE.CONFLUENCE_SOURCE s"
    assert codemod.rewrite("{{database}}.CODEGEN.DBT_STAGE", True) == "{{database}}.{{schema}}.DBT_STAGE"
    assert codemod.rewrite("@CODEGEN.DBT_STAGE/run/v1", False) == "@CORE.DBT_STAGE/run/v1"
    assert codemod.rewrite("CLIENT_DB.SALES.ORDERS and KNOWLEDGE.SOMETHING_ELSE", False) == \
        "CLIENT_DB.SALES.ORDERS and KNOWLEDGE.SOMETHING_ELSE"
    kept = "x = 'CASES.CASE_SEQ'  # names before V061"
    assert codemod.rewrite(kept + "\nKNOWLEDGE.SKILL_STAGE\n", False) == kept + "\nCORE.SKILL_STAGE\n"


def test_resolve_translates_stored_names_and_leaves_everything_else():
    assert resolve("CODEGEN.DBT_STAGE/run/v1", "stage") == "CORE.DBT_STAGE/run/v1"
    assert resolve("@AIP.CODEGEN.DBT_STAGE/run/v1/models/x.sql", "stage") == "@AIP.CORE.DBT_STAGE/run/v1/models/x.sql"
    assert resolve("AIP.PROFILE.PROF_ABC", "task") == "AIP.CORE.PROF_ABC"
    assert resolve("AIP.QUALITY.DQ_1234", "task") == "AIP.CORE.DQ_1234"
    assert resolve("AIP.CONFLUENCE.SOURCE") == "AIP.CORE.CONFLUENCE_SOURCE"
    for kind in KEPT_KINDS:
        assert resolve("AIP.CODE.ACME_GIT_TOKEN", kind) == "AIP.CODE.ACME_GIT_TOKEN"
    for unchanged in ("AIP.CORE.DBT_STAGE/x", "CLIENT.SALES.ORDERS", "LANDING.CRM__CUST", "", None, "not a name"):
        assert resolve(unchanged, "stage") == unchanged
    assert obj("REPO") == "CORE.REPO" and fqn("AIP", "REPO") == "AIP.CORE.REPO" and stage("AIP", "DBT_STAGE") == "@AIP.CORE.DBT_STAGE"


def test_later_migrations_create_only_in_the_platform_schema_with_explicit_grants():
    for version, _, path in list_migrations():
        if version <= "061":
            continue
        sql = path.read_text(encoding="utf-8")
        for kind, schema, name in CREATE.findall(sql):
            assert schema.upper() in (PLATFORM_SCHEMA, "LANDING", "GDP_SILVER"), f"{path.name}: {schema}.{name}"
            assert re.search(rf"GRANT .+ ON {kind.upper()} \{{\{{database\}}\}}\.{schema}\.{name}\b", sql), \
                f"{path.name}: {name} has no explicit grant (no future grants in {PLATFORM_SCHEMA} since V061)"


def test_all_templates_render_with_the_schema_variable():
    variables = {"database": "AIP", "schema": PLATFORM_SCHEMA, "services_import": "@AIP.CORE.CODE_STAGE/s.zip",
                 "warehouse": "WH", "agent_skills": "skills: []"}
    for d in TEMPLATE_DIRS:
        for path in d.glob("*.sql"):
            assert "AIP.CORE." in render(path.read_text(encoding="utf-8"), variables) or path.stat().st_size < 400


class Cur:
    def __init__(self, answers):
        self.answers, self.sql = answers, []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self._last = next((v for k, v in self.answers.items() if k in sql), [])

    def fetchone(self):
        return self._last[0] if self._last else None

    def fetchall(self):
        return self._last


def test_gate_lets_a_new_environment_through():
    assert "no baseline needed" in consolidation_gate(Cur({"INFORMATION_SCHEMA.TABLES": [(0,)]}), "AIP", False)


def test_gate_refuses_a_database_with_data_unless_baselined_and_consolidating():
    with pytest.raises(AssertionError, match="Nothing was moved"):
        consolidation_gate(Cur({"INFORMATION_SCHEMA.TABLES": [(5,)]}), "AIP", False)
    with pytest.raises(AssertionError, match="needs a completed baseline"):
        consolidation_gate(Cur({"INFORMATION_SCHEMA.TABLES": [(5,)], "CONSOLIDATION_RUN": [(0,)]}), "AIP", True)
    ok = consolidation_gate(Cur({"INFORMATION_SCHEMA.TABLES": [(5,)], "CONSOLIDATION_RUN": [(1,)]}), "AIP", True)
    assert "completed baseline found" in ok


AGENT = """CREATE OR REPLACE AGENT AIP.CORE.SUP
  COMMENT = 'c'
  PROFILE = '{"display_name": "S"}'
  FROM SPECIFICATION
  $$
models: {orchestration: x}
  $$;

GRANT USAGE ON AGENT AIP.CORE.SUP TO DATABASE ROLE AIP.VIEWER;"""


def test_existing_agent_is_altered_in_place_and_keeps_its_versions():
    out = agent_statements(Cur({"SHOW AGENTS": [("SUP",)]}), AGENT)
    assert "CREATE OR REPLACE AGENT" not in out
    assert "ALTER AGENT AIP.CORE.SUP SET COMMENT = 'c' PROFILE = '{\"display_name\": \"S\"}';" in out
    assert "ALTER AGENT AIP.CORE.SUP MODIFY LIVE VERSION SET SPECIFICATION = $$" in out
    assert out.rstrip().endswith("GRANT USAGE ON AGENT AIP.CORE.SUP TO DATABASE ROLE AIP.VIEWER;")


def test_new_agent_is_created():
    assert agent_statements(Cur({"SHOW AGENTS": []}), AGENT) == AGENT
