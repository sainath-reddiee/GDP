"""Environment bindings (services/common/environments.py): logical names in QA pack SQL and Data Quality suite files,
resolved per environment; the read-only guard on resolved SQL; runs and dry runs on another environment; portable
export and import of packs and suites; the Admin routes' governance; the deploy's [environment] seed."""

import json
from pathlib import Path

import pytest

from services.catalog import dq_suite
from services.common import environments as envs
from services.qa import pack_files, packs
from services.qa.guard import check

ROOT = Path(__file__).resolve().parents[2]
DEV = {"silver": "DEV_GDP_SILVER_DB", "landing": "DEV_AI_PLATFORM.LANDING"}
DA = {"silver": "DA_GDP_SILVER_DB", "landing": "DA_AI_PLATFORM.LANDING"}
CONFIG = {"current": "DEV", "environments": [{"name": "DEV", "description": "", "bindings": DEV},
                                             {"name": "DA", "description": "client", "bindings": DA}]}
ALLOWED = ["DEV_GDP_SILVER_DB.COMPANY.COMPANY_CORE", "DEV_GDP_SILVER_DB.COMPANY.COMPANY_ADDRESS",
           "DEV_AI_PLATFORM.LANDING.CRM_ACCOUNTS", "OTHER_DB.X.UNBOUND"]
ORPHANS = ("SELECT a.COMPANY_CORE_SKEY FROM DEV_GDP_SILVER_DB.COMPANY.COMPANY_ADDRESS a "
           "LEFT JOIN DEV_GDP_SILVER_DB.COMPANY.COMPANY_CORE c ON c.COMPANY_CORE_SKEY = a.COMPANY_CORE_SKEY "
           "WHERE c.COMPANY_CORE_SKEY IS NULL")


class Session:
    """Snowpark-shaped: records SQL; EXPLAIN fails for statements naming a table in `missing`."""

    def __init__(self, missing=()):
        self.calls, self.missing = [], set(missing)
        self._last = ""

    def sql(self, sql, params=None):
        self.calls.append(" ".join(sql.split()))
        self._last = sql
        return self

    def collect(self):
        if self._last.startswith("EXPLAIN") and any(m in self._last for m in self.missing):
            raise RuntimeError("SQL compilation error: Object does not exist or not authorized.")
        return []


# ---------------------------------------------------------------- the setting


def test_validate_normalises_and_refuses_bad_bindings():
    cleaned, problems = envs.validate({"current": "dev", "environments": [
        {"name": "dev", "bindings": {"Silver": "dev_gdp_silver_db", "mixed": '"Mixed".sch'}}]})
    assert not problems
    assert cleaned == {"current": "DEV", "environments": [
        {"name": "DEV", "description": "", "bindings": {"mixed": '"Mixed".SCH', "silver": "DEV_GDP_SILVER_DB"}}]}
    _, problems = envs.validate({"current": "QA", "environments": [
        {"name": "DEV", "bindings": {"domain": "X", "three": "A.B.C", "bad name": "A", "empty": ""}},
        {"name": "dev", "bindings": {}}, {"name": "9x"}]})
    text = " | ".join(problems)
    for expected in ("{{domain}} is reserved", "three must be DATABASE or DATABASE.SCHEMA", "bad name",
                     "empty must be", "DEV appears twice", "environment 3", "QA is not in the list"):
        assert expected in text, (expected, problems)
    assert envs.validate(None) == (envs.DEFAULT, [])
    assert "choose the environment" in envs.validate({"environments": [{"name": "DEV"}]})[1][0]


def test_environment_lookup_and_current():
    assert envs.bindings_of(CONFIG) == DEV and envs.bindings_of(CONFIG, "da") == DA
    assert envs.environment(CONFIG, "PROD") is None and envs.bindings_of(None) == {}
    assert envs.is_current(CONFIG, None) and envs.is_current(CONFIG, "dev") and not envs.is_current(CONFIG, "DA")


# ---------------------------------------------------------------- resolution


def test_resolve_replaces_references_outside_strings_and_comments():
    sql = ("SELECT '{{silver}}' AS s FROM {{ silver }}.COMPANY.COMPANY_CORE c "
           "JOIN {{landing}}.CRM_ACCOUNTS l ON l.ID = c.ID -- {{silver}} in a comment")
    assert envs.references(sql) == ["silver", "landing"]
    resolved, problems = envs.resolve(sql, DA, "DA")
    assert not problems
    assert resolved == ("SELECT '{{silver}}' AS s FROM DA_GDP_SILVER_DB.COMPANY.COMPANY_CORE c "
                        "JOIN DA_AI_PLATFORM.LANDING.CRM_ACCOUNTS l ON l.ID = c.ID -- {{silver}} in a comment")
    same, problems = envs.resolve("SELECT 1 FROM {{gold}}.S.T, {{gold}}.S.U", DA, "DA")
    assert same.count("{{gold}}") == 2 and problems == ["{{gold}} is not bound in environment DA (add it in Admin, Environments)"]
    assert envs.resolve(ORPHANS, DA) == (ORPHANS, [])  # nothing logical: unchanged
    assert envs.resolve("SELECT 'x", DA)[1] == ["unterminated string"]


def test_to_logical_prefers_the_most_specific_binding_and_leaves_short_names():
    bindings = {"silver": "DEV_GDP_SILVER_DB", "company": "DEV_GDP_SILVER_DB.COMPANY"}
    sql = ("SELECT a.ID, DEV_GDP_SILVER_DB.X FROM DEV_GDP_SILVER_DB.COMPANY.COMPANY_ADDRESS a "
           "JOIN DEV_GDP_SILVER_DB.OTHER.T b ON a.ID = b.ID WHERE a.N = 'DEV_GDP_SILVER_DB.COMPANY.T'")
    assert envs.to_logical(sql, bindings) == (
        "SELECT a.ID, DEV_GDP_SILVER_DB.X FROM {{company}}.COMPANY_ADDRESS a "
        "JOIN {{silver}}.OTHER.T b ON a.ID = b.ID WHERE a.N = 'DEV_GDP_SILVER_DB.COMPANY.T'")
    assert envs.to_logical('SELECT 1 FROM "dev_db".S.T', {"q": '"dev_db"'}) == "SELECT 1 FROM {{q}}.S.T"
    assert envs.to_logical("SELECT 1 FROM dev_gdp_silver_db.s.t", bindings) == "SELECT 1 FROM {{silver}}.s.t"
    assert envs.to_logical("SELECT 1 FROM {{silver}}.S.T", bindings) == "SELECT 1 FROM {{silver}}.S.T"


def test_rebase_moves_queries_tables_and_locations_between_environments():
    resolved, problems = envs.rebase(ORPHANS, DEV, DA, "DA")
    assert not problems and "DEV_" not in resolved and "DA_GDP_SILVER_DB.COMPANY.COMPANY_CORE" in resolved
    # a table no binding covers has no name in the target, so a run elsewhere never reads this environment
    assert envs.rebase_tables(ALLOWED, DEV, DA) == ["DA_GDP_SILVER_DB.COMPANY.COMPANY_CORE",
                                                    "DA_GDP_SILVER_DB.COMPANY.COMPANY_ADDRESS",
                                                    "DA_AI_PLATFORM.LANDING.CRM_ACCOUNTS"]
    assert envs.rebase_location("DEV_GDP_SILVER_DB.COMPANY", DEV, DA) == "DA_GDP_SILVER_DB.COMPANY"
    assert envs.rebase_location("OTHER_DB.X", DEV, DA) is None and envs.rebase_location(None, DEV, DA) is None


# ---------------------------------------------------------------- the guard


def test_guard_refuses_unresolved_names_and_passes_resolved_ones():
    logical = "SELECT * FROM {{silver}}.COMPANY.COMPANY_CORE"
    ok, problems, _ = check(logical, ALLOWED)
    assert not ok and problems == ["logical names ({{name}}) must be resolved for an environment before the query runs"]
    ok, problems, _ = check(envs.resolve(logical, DEV)[0], ALLOWED)
    assert ok, problems
    # resolved for another environment, the current environment's tables are not allowed
    ok, problems, _ = check(envs.resolve(logical, DA)[0], ALLOWED)
    assert not ok and "is not one of this run's source or target tables" in problems[0]


def test_clean_queries_checks_resolved_sql_and_keeps_it_logical():
    logical = "SELECT * FROM {{silver}}.COMPANY.COMPANY_CORE WHERE GDP_IS_ACTIVE;"
    out = packs.clean_queries([{"query_name": "q1", "sql_expression": logical}], ALLOWED, DEV)
    assert out[0]["sql_expression"] == "SELECT * FROM {{silver}}.COMPANY.COMPANY_CORE WHERE GDP_IS_ACTIVE"
    with pytest.raises(AssertionError, match=r"q1: \{\{silver\}\} is not bound"):
        packs.clean_queries([{"query_name": "q1", "sql_expression": logical}], ALLOWED, {})
    with pytest.raises(AssertionError, match="not one of this domain's tables"):
        packs.clean_queries([{"query_name": "q1", "sql_expression": "SELECT 1 FROM {{silver}}.S.NOPE"}], ALLOWED, DEV)
    fqn = packs.clean_queries([{"query_name": "q2", "sql_expression": ORPHANS}], ALLOWED)  # fully qualified still works
    assert fqn[0]["sql_expression"] == ORPHANS


# ---------------------------------------------------------------- running on an environment


@pytest.fixture
def domain(monkeypatch):
    monkeypatch.setattr(packs, "allowed_tables", lambda s, d: list(ALLOWED))
    monkeypatch.setattr(envs, "load", lambda s: json.loads(json.dumps(CONFIG)))
    monkeypatch.setattr(envs, "domain_location", lambda s, d: "DEV_GDP_SILVER_DB.COMPANY")
    pack = {"pack_id": "p", "version": 1, "status": "APPROVED", "domain_id": "d1", "name": "Integrity"}
    queries = [{"query_id": "q1", "query_name": "orphans", "severity": "ERROR", "expectation": "0 rows",
                "sql_expression": ORPHANS},
               {"query_id": "q2", "query_name": "domain_rows", "severity": "WARN", "expectation": "0 rows",
                "sql_expression": "SELECT ID FROM {{domain}}.COMPANY_CORE WHERE ID IS NULL"},
               {"query_id": "q3", "query_name": "unbound", "severity": "WARN", "expectation": "0 rows",
                "sql_expression": "SELECT ID FROM OTHER_DB.X.UNBOUND"}]
    monkeypatch.setattr(packs, "get_pack", lambda s, pid, v=None: {"pack": pack, "queries": queries,
                                                                    "approved_version": 1})
    monkeypatch.setattr(packs, "_pii", lambda s, a: (set(), False))
    return queries


def test_target_for_the_current_and_another_environment(domain):
    here = packs.target(Session(), "d1")
    assert here["current"] and here["name"] == "DEV" and here["allowed"] == ALLOWED
    assert here["bindings"]["domain"] == "DEV_GDP_SILVER_DB.COMPANY"
    there = packs.target(Session(), "d1", "da")
    assert not there["current"] and there["name"] == "DA" and "OTHER_DB.X.UNBOUND" not in there["allowed"]
    assert there["bindings"]["domain"] == "DA_GDP_SILVER_DB.COMPANY" and there["source"] == DEV
    with pytest.raises(AssertionError, match="PROD is not configured"):
        packs.target(Session(), "d1", "PROD")
    lonely = {"current": "DEV", "environments": [{"name": "DEV", "bindings": {}}, {"name": "DA", "bindings": DA}]}
    with pytest.raises(AssertionError, match="no binding of environment DA"):
        packs.target(Session(), "d1", "DA", lonely)


def test_execute_on_another_environment_rewrites_sql_and_records_it(domain, monkeypatch):
    import services.qa.run as run

    ran, stored = [], []
    monkeypatch.setattr(run, "execute", lambda s, t, a, p, k, c: ran.append((t["sql"], list(a))) or {**t, "outcome": "PASS"})
    monkeypatch.setattr(run, "_store", lambda s, qa_run, results: stored.append(qa_run))
    out = packs.execute(Session(), "d1", "p", environment="DA")
    assert out["environment"] == "DA" and out["current_environment"] is False
    assert ran[0][0] == ORPHANS.replace("DEV_GDP_SILVER_DB", "DA_GDP_SILVER_DB")
    assert ran[1][0] == "SELECT ID FROM DA_GDP_SILVER_DB.COMPANY.COMPANY_CORE WHERE ID IS NULL"
    assert all(not t.startswith("DEV_") and not t.startswith("OTHER_") for t in ran[0][1])
    assert stored[0]["trigger_detail"]["environment"] == "DA" and stored[0]["target"] == "domain pack Integrity on DA"
    ran.clear()
    packs.execute(Session(), "d1", "p")  # current environment: fully qualified SQL is unchanged
    assert ran[0][0] == ORPHANS and ran[1][0].startswith("SELECT ID FROM DEV_GDP_SILVER_DB.COMPANY.COMPANY_CORE")


def test_dry_run_resolves_guards_and_explains_without_storing(domain):
    session = Session(missing={"DA_GDP_SILVER_DB.COMPANY.COMPANY_ADDRESS"})
    out = packs.dry_run(session, "d1", "p", environment="DA")
    by_name = {q["query_name"]: q for q in out["queries"]}
    assert out["environment"] == "DA" and out["ok"] is False
    assert by_name["orphans"]["rewritten"] and by_name["orphans"]["explain"]["ok"] is False
    assert "does not exist" in by_name["orphans"]["explain"]["error"]
    assert by_name["domain_rows"]["ok"] and by_name["domain_rows"]["sql"].startswith("SELECT ID FROM DA_GDP_SILVER_DB.COMPANY")
    assert by_name["unbound"]["explain"] is None
    assert by_name["unbound"]["problems"] == ["OTHER_DB.X.UNBOUND is not one of this domain's tables in environment DA"]
    assert all(c.startswith("EXPLAIN USING TEXT SELECT") for c in session.calls)  # nothing ran, nothing stored
    # unsaved bindings (the Admin preview) replace the saved ones
    proposed = {"current": "DEV", "environments": [{"name": "DEV", "bindings": DEV},
                                                    {"name": "DA", "bindings": {"silver": "DA2_DB", "landing": "DA2_DB.L"}}]}
    out = packs.dry_run(Session(), "d1", "p", environment="DA", config_json=json.dumps(proposed))
    assert out["queries"][0]["sql"].count("DA2_DB.COMPANY.") == 2 and out["queries"][0]["ok"]
    with pytest.raises(AssertionError, match="must be DATABASE"):
        packs.dry_run(Session(), "d1", "p", config_json=json.dumps({"current": "DEV", "environments": [
            {"name": "DEV", "bindings": {"silver": "A.B.C"}}]}))


# ---------------------------------------------------------------- export and import


def test_pack_file_round_trip_with_logical_names():
    pack = {"name": "Integrity", "description": "Orphans", "tags": ["reconciliation"],
            "queries": [{"query_name": "orphans", "severity": "ERROR", "expectation": "0 rows", "tags": [],
                         "description": "Addresses without a hub row", "sql_expression": ORPHANS}]}
    text = pack_files.render(pack_files.portable(pack, DEV))
    assert "DEV_GDP_SILVER_DB" not in text and "{{silver}}.COMPANY.COMPANY_ADDRESS" in text
    parsed = pack_files.parse(text)
    assert parsed["name"] == "Integrity" and parsed["queries"][0]["query_name"] == "orphans"
    assert envs.resolve(parsed["queries"][0]["sql_expression"], DEV) == (ORPHANS, [])
    assert envs.resolve(parsed["queries"][0]["sql_expression"], DA)[0] == ORPHANS.replace("DEV_", "DA_")
    # the seed stores the logical SQL as written; it resolves when the pack runs
    seeded = packs.seed_rows("d1", parsed)
    assert seeded["queries"][0]["sql_expression"] == parsed["queries"][0]["sql_expression"]


def test_repository_packs_are_logical_and_survive_export():
    """The repository packs are written with {{silver}}; exporting them again changes nothing, and resolving them
    for DEV and for another environment gives each environment's own tables."""
    company = json.loads((ROOT / "domain/company/domain_pack.json").read_text())
    dev = {"silver": company["domain"]["silver_database"]}
    for path in sorted((ROOT / "domain").glob("*/qa/*.sql")):
        parsed = pack_files.parse(path.read_text())
        again = pack_files.parse(pack_files.render(pack_files.portable(parsed, dev)))
        assert again["queries"] == parsed["queries"], path.name
        for q in parsed["queries"]:
            assert envs.is_logical(q["sql_expression"]), (path.name, q["query_name"])
            on_dev, _ = envs.resolve(q["sql_expression"], dev)
            on_da, _ = envs.resolve(q["sql_expression"], DA)
            assert dev["silver"] + ".COMPANY." in on_dev and "DA_GDP_SILVER_DB.COMPANY." in on_da
            assert envs.to_logical(on_dev, dev) == q["sql_expression"]


def test_dq_suite_portable_round_trip():
    checks = [
        {"check_id": "c1", "target_column": "EMAIL", "severity": "WARN", "status": "ACTIVE",
         "definition": {"kind": "not_null"}, "soda_dsl": "x", "headline": "x", "origin": "USER"},
        {"check_id": "c2", "target_column": "COUNTRY", "severity": "FAIL", "status": "PAUSED",
         "definition": {"kind": "reference", "reference_table": "DEV_GDP_SILVER_DB.REF.COUNTRY", "reference_column": "CODE"}},
        {"check_id": "c3", "target_column": None, "severity": "FAIL", "status": "ACTIVE",
         "definition": {"kind": "failed_rows", "name": "orphans",
                        "query": "SELECT * FROM DEV_GDP_SILVER_DB.COMPANY.COMPANY_ADDRESS WHERE ID IS NULL"}},
    ]
    name, media, text = dq_suite.portable("DEV_GDP_SILVER_DB.COMPANY.COMPANY_CORE", "COMPANY", checks, DEV, "DEV", 3)
    assert name == "dev_gdp_silver_db_company_company_core.dq.json" and media == "application/json"
    assert "DEV_GDP_SILVER_DB" not in text and "check_id" not in text
    doc = dq_suite.parse_portable(text)
    assert doc["table"] == "{{silver}}.COMPANY.COMPANY_CORE" and doc["source"] == {"environment": "DEV", "suite_version": 3}
    table, raws, problems = dq_suite.localize(doc, DA, "DA")
    assert not problems and table == "DA_GDP_SILVER_DB.COMPANY.COMPANY_CORE"
    assert raws[1]["definition"]["reference_table"] == "DA_GDP_SILVER_DB.REF.COUNTRY" and raws[1]["status"] == "PAUSED"
    assert raws[2]["definition"]["query"] == "SELECT * FROM DA_GDP_SILVER_DB.COMPANY.COMPANY_ADDRESS WHERE ID IS NULL"
    for raw in raws:
        assert not dq_suite.check_record(raw)[1], raw
    _, _, problems = dq_suite.localize(doc, {}, "QA")
    assert problems == ["{{silver}} is not bound in environment QA (add it in Admin, Environments)"]
    for bad, message in (("{", "not JSON"), ('{"format": "x"}', "not a gdp-dq-suite"),
                         ('{"format": "gdp-dq-suite", "format_version": 2, "table": "T"}', "newer"),
                         ('{"format": "gdp-dq-suite", "table": "T", "checks": [1]}', "list of objects")):
        with pytest.raises(ValueError, match=message):
            dq_suite.parse_portable(bad)


def test_import_suite_adds_only_new_checks_and_opens_no_empty_draft(monkeypatch):
    _, _, text = dq_suite.portable("DEV_GDP_SILVER_DB.COMPANY.COMPANY_CORE", "COMPANY", [
        {"target_column": "EMAIL", "severity": "WARN", "definition": {"kind": "not_null"}},
        {"target_column": "ID", "severity": "FAIL", "definition": {"kind": "unique"}}], DEV)
    monkeypatch.setattr(envs, "load", lambda s: {"current": "DA", "environments": [{"name": "DA", "bindings": DA}]})
    monkeypatch.setattr(envs, "domain_location", lambda s, d: None)
    monkeypatch.setattr(dq_suite, "scalar", lambda s, sql, p=None: "d1")
    monkeypatch.setattr(dq_suite, "_header", lambda s, t: {"suite_id": "s1", "approved_version": 1})
    monkeypatch.setattr(dq_suite, "_versions", lambda s, sid: [])
    approved = [dq_suite.check_record({"target_column": "EMAIL", "severity": "WARN", "definition": {"kind": "not_null"}})[0]]
    monkeypatch.setattr(dq_suite, "_checks", lambda s, sid, v: list(approved))
    staged = []
    monkeypatch.setattr(dq_suite, "stage_many", lambda s, table, raws, origin, user, domain_id=None:
                        staged.append((table, raws, origin, user, domain_id)) or {"added": len(raws), "skipped": 0})
    out = dq_suite.import_suite(Session(), text, "ANA")
    assert out["table"] == "DA_GDP_SILVER_DB.COMPANY.COMPANY_CORE" and out["environment"] == "DA"
    assert out["added"] == 1 and out["skipped"] == 1
    assert staged[0][0] == "DA_GDP_SILVER_DB.COMPANY.COMPANY_CORE" and staged[0][1][0]["target_column"] == "ID"
    assert staged[0][2:] == ("USER", "ANA", "d1")
    approved.append(dq_suite.check_record({"target_column": "ID", "severity": "FAIL", "definition": {"kind": "unique"}})[0])
    again = dq_suite.import_suite(Session(), text, "ANA")
    assert again["staged"] is False and again["added"] == 0 and len(staged) == 1


# ---------------------------------------------------------------- governance and deploy


def test_environment_routes_are_governed():
    from services.governance.policy import no_queue, privilege_for

    assert privilege_for("PUT", "/api/config/environments")[0] == "CONFIG.EDIT"
    assert privilege_for("POST", "/api/config/environments/preview")[0] == "ADMIN.VIEW"
    assert privilege_for("GET", "/api/config/environments")[0] == "ADMIN.VIEW"
    assert privilege_for("GET", "/api/qa/environments")[0] is None
    assert privilege_for("POST", "/api/dq/suites/import")[0] == "SODA.EDIT" and no_queue("POST", "/api/dq/suites/import")
    assert privilege_for("POST", "/api/qa/execute")[0] == "QA.EDIT"


def test_deploy_reads_the_environment_section():
    from infrastructure import deploy

    raw = {"snowflake": {"connection": "c", "role": "r", "warehouse": "w", "database": "d"},
           "access": {"model": "account_roles"},
           "environment": {"name": "prod", "bindings": {"silver": "prod_gdp_silver_db"}}}
    env = deploy.validate_env(raw, "client")
    assert env["environment"] == {"name": "PROD", "description": "", "bindings": {"silver": "prod_gdp_silver_db"}}
    with pytest.raises(deploy.ConfigError, match=r"\[environment\] PROD: silver must be"):
        deploy.validate_env({**raw, "environment": {"name": "prod", "bindings": {"silver": "a.b.c"}}}, "client")
    assert deploy.validate_env({k: v for k, v in raw.items() if k != "environment"}, "client")["environment"] is None


def test_seed_environment_writes_once_and_keeps_saved_bindings():
    from infrastructure.seed_knowledge import seed_environment

    class Cursor:
        def __init__(self, count):
            self.count, self.sql = count, []

        def execute(self, sql, params=None):
            self.sql.append((sql, params))

        def fetchall(self):
            return [(self.count,)]

    cur = Cursor(0)
    log = seed_environment(cur, "AIP", {"name": "PROD", "bindings": {"silver": "prod_db"}})
    assert log == ["environment bindings: PROD (1 bindings)"]
    insert = cur.sql[-1]
    assert "INSERT INTO AIP.CORE.PLATFORM_CONFIG" in insert[0]
    assert json.loads(insert[1][1]) == {"current": "PROD", "environments": [
        {"name": "PROD", "description": "", "bindings": {"silver": "PROD_DB"}}]}
    cur = Cursor(2)
    assert seed_environment(cur, "AIP", {"name": "PROD", "bindings": {}}) == ["environment bindings: kept the saved value"]
    assert len(cur.sql) == 1 and seed_environment(cur, "AIP", None) == []


def test_repository_suite_files_parse():
    from infrastructure.seed_knowledge import load_dq_suite_files

    for _, text, path in load_dq_suite_files():
        doc = dq_suite.parse_portable(text)
        assert envs.is_logical(doc["table"]), path.name


def test_deploy_warns_about_unbound_repository_pack_names():
    from infrastructure.seed_knowledge import check_pack_bindings

    class Cursor:
        def __init__(self, value):
            self.value = value

        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return [(json.dumps(self.value),)] if self.value is not None else []

    unbound = check_pack_bindings(Cursor(None), "AIP")
    assert unbound and unbound[0].startswith("WARNING qa pack bindings: {{silver}} is not bound in this deployment")
    dev = {"current": "DEV", "environments": [{"name": "DEV", "bindings": {"silver": "DEV_GDP_SILVER_DB"}}]}
    assert check_pack_bindings(Cursor(dev), "AIP") == ["qa pack bindings: silver bound in DEV"]
