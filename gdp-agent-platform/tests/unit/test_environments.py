"""Environment databases (services/common/environments.py): people read and write real table names; names in a mapped
database are stored in a logical form and read back as the current environment's; runs and dry runs on another
environment move them to its databases; QA reads any fully qualified table; portable DQ suite files carry real names
and their source mapping; the Admin routes' governance; the deploy's [environment] seed and warnings."""

import json
from pathlib import Path

import pytest

from services.catalog import dq_suite
from services.common import environments as envs
from services.qa import pack_files, packs
from services.qa.guard import check, tables

ROOT = Path(__file__).resolve().parents[2]
DEV = {"silver": "DEV_GDP_SILVER_DB", "landing": "DEV_AI_PLATFORM.LANDING"}
DA = {"silver": "DA_GDP_SILVER_DB", "landing": "DA_AI_PLATFORM.LANDING"}
CONFIG = {"current": "DEV", "environments": [{"name": "DEV", "description": "", "bindings": DEV},
                                             {"name": "DA", "description": "client", "bindings": DA}]}
ORPHANS = ("SELECT a.COMPANY_CORE_SKEY FROM DEV_GDP_SILVER_DB.COMPANY.COMPANY_ADDRESS a "
           "LEFT JOIN DEV_GDP_SILVER_DB.COMPANY.COMPANY_CORE c ON c.COMPANY_CORE_SKEY = a.COMPANY_CORE_SKEY "
           "WHERE c.COMPANY_CORE_SKEY IS NULL")
STORED_ORPHANS = ORPHANS.replace("DEV_GDP_SILVER_DB", "{{silver}}")


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


def braces(value) -> bool:
    return "{{" in json.dumps(value)


# ---------------------------------------------------------------- the setting


def test_validate_normalises_and_refuses_bad_mappings_in_plain_words():
    cleaned, problems = envs.validate({"current": "dev", "environments": [
        {"name": "dev", "bindings": {"Silver": "dev_gdp_silver_db", "mixed": '"Mixed".sch', "gold": ""}}]})
    assert not problems   # an empty cell means the role is not mapped there
    assert cleaned == {"current": "DEV", "environments": [
        {"name": "DEV", "description": "", "bindings": {"mixed": '"Mixed".SCH', "silver": "DEV_GDP_SILVER_DB"}}]}
    _, problems = envs.validate({"current": "QA", "environments": [
        {"name": "DEV", "bindings": {"domain": "X", "three": "A.B.C", "bad name": "A"}},
        {"name": "dev", "bindings": {}}, {"name": "9x"}]})
    text = " | ".join(problems)
    for expected in ("Domain is a reserved role name", "Three must be DATABASE or DATABASE.SCHEMA", "role bad name",
                     "DEV appears twice", "environment 3", "QA is not in the list"):
        assert expected in text, (expected, problems)
    assert not braces(problems)
    assert envs.validate(None) == (envs.DEFAULT, [])
    assert "choose the environment" in envs.validate({"environments": [{"name": "DEV"}]})[1][0]


def test_environment_lookup_labels_and_effective_mapping():
    assert envs.bindings_of(CONFIG) == DEV and envs.bindings_of(CONFIG, "da") == DA
    assert envs.environment(CONFIG, "PROD") is None and envs.bindings_of(None) == {}
    assert envs.is_current(CONFIG, None) and envs.is_current(CONFIG, "dev") and not envs.is_current(CONFIG, "DA")
    assert envs.label("silver") == "Silver" and envs.label("gold_mart") == "Gold mart"
    # with no mapping saved, the domain's registered Silver database plays Silver
    assert envs.effective(None, "dev_gdp_silver_db", "DEV_GDP_SILVER_DB.COMPANY") == {
        "silver": "DEV_GDP_SILVER_DB", "domain": "DEV_GDP_SILVER_DB.COMPANY"}
    assert envs.effective(CONFIG, "OTHER_DB")["silver"] == "DEV_GDP_SILVER_DB"   # a saved mapping wins


# ---------------------------------------------------------------- stored form and real names


def test_store_form_and_resolve_round_trip_outside_strings_and_comments():
    sql = ("SELECT 'DEV_GDP_SILVER_DB.COMPANY.T' AS s FROM DEV_GDP_SILVER_DB.COMPANY.COMPANY_CORE c "
           "JOIN DEV_AI_PLATFORM.LANDING.CRM_ACCOUNTS l ON l.ID = c.ID JOIN OTHER_DB.X.Y o ON o.ID = c.ID "
           "-- DEV_GDP_SILVER_DB.COMPANY.T in a comment")
    stored = envs.store_form(sql, CONFIG, DEV)
    assert envs.references(stored) == ["silver", "landing"] and "OTHER_DB.X.Y" in stored
    assert envs.shown(stored, DEV) == sql   # what people read is exactly what they wrote
    on_da, problems = envs.resolve(stored, DA, "DA")
    assert not problems and "DA_GDP_SILVER_DB.COMPANY.COMPANY_CORE" in on_da and "DA_AI_PLATFORM.LANDING.CRM_ACCOUNTS" in on_da
    assert "'DEV_GDP_SILVER_DB.COMPANY.T'" in on_da and "OTHER_DB.X.Y" in on_da
    # real names pasted from another environment read back as this one's tables
    assert envs.shown(envs.store_form(ORPHANS.replace("DEV_", "DA_"), CONFIG, DEV), DEV) == ORPHANS
    same, problems = envs.resolve("SELECT 1 FROM {{gold}}.S.T, {{gold}}.S.U", DA, "DA")
    assert problems == ["no Gold database is mapped for environment DA (Admin, Environments)"]
    assert envs.resolve("SELECT 'x", DA)[1] == ["unterminated string"]


def test_to_logical_prefers_the_most_specific_mapping_and_leaves_short_names():
    bindings = {"silver": "DEV_GDP_SILVER_DB", "company": "DEV_GDP_SILVER_DB.COMPANY"}
    sql = ("SELECT a.ID, DEV_GDP_SILVER_DB.X FROM DEV_GDP_SILVER_DB.COMPANY.COMPANY_ADDRESS a "
           "JOIN DEV_GDP_SILVER_DB.OTHER.T b ON a.ID = b.ID")
    assert envs.to_logical(sql, bindings) == (
        "SELECT a.ID, DEV_GDP_SILVER_DB.X FROM {{company}}.COMPANY_ADDRESS a JOIN {{silver}}.OTHER.T b ON a.ID = b.ID")
    assert envs.to_logical('SELECT 1 FROM "dev_db".S.T', {"q": '"dev_db"'}) == "SELECT 1 FROM {{q}}.S.T"
    assert envs.to_logical("SELECT 1 FROM {{silver}}.S.T", bindings) == "SELECT 1 FROM {{silver}}.S.T"


def test_rebase_moves_queries_and_locations_between_environments():
    resolved, problems = envs.rebase(ORPHANS, DEV, DA, "DA")
    assert not problems and resolved == ORPHANS.replace("DEV_", "DA_")
    assert envs.rebase_location("DEV_GDP_SILVER_DB.COMPANY", DEV, DA) == "DA_GDP_SILVER_DB.COMPANY"
    assert envs.rebase_location("OTHER_DB.X", DEV, DA) is None and envs.rebase_location(None, DEV, DA) is None


# ---------------------------------------------------------------- the guard: any table, read only


def test_guard_reads_any_fully_qualified_table_but_stays_read_only():
    assert check("SELECT * FROM ANY_DB.ANY_SCHEMA.ANY_TABLE a JOIN OTHER.S.T b ON a.ID = b.ID")[0]
    assert tables("WITH x AS (SELECT * FROM A.B.C) SELECT * FROM x JOIN D.E.F f ON 1 = 1") == ["A.B.C", "D.E.F"]
    for sql, message in (("SELECT * FROM S.T", "fully qualified"), ("DELETE FROM A.B.C", "SELECT or WITH"),
                         ("SELECT 1 FROM A.B.C; DROP TABLE A.B.C", "only one statement"),
                         ("SELECT * FROM A.INFORMATION_SCHEMA.TABLES", "system views")):
        ok, problems, _ = check(sql)
        assert not ok and any(message in p for p in problems), (sql, problems)
    ok, problems, _ = check("SELECT * FROM {{silver}}.COMPANY.COMPANY_CORE")
    assert not ok and problems == ["a database role in the query has no database mapped for this environment "
                                   "(Admin, Environments)"]
    # a caller can still keep a query to a list of tables
    assert not check("SELECT * FROM A.B.C", ["X.Y.Z"])[0]


def test_clean_queries_stores_portable_sql_and_checks_it_as_it_runs_here():
    out = packs.clean_queries([{"query_name": "q1", "sql_expression": ORPHANS + ";"},
                               {"query_name": "q2", "sql_expression": "SELECT 1 FROM SOMEWHERE_ELSE.S.T"}], DEV, CONFIG)
    assert out[0]["sql_expression"] == STORED_ORPHANS and out[1]["sql_expression"] == "SELECT 1 FROM SOMEWHERE_ELSE.S.T"
    with pytest.raises(AssertionError, match="q1: no Gold database is mapped"):
        packs.clean_queries([{"query_name": "q1", "sql_expression": "SELECT 1 FROM {{gold}}.S.T"}], DEV, CONFIG)
    with pytest.raises(AssertionError, match="fully qualified"):
        packs.clean_queries([{"query_name": "q1", "sql_expression": "SELECT 1 FROM S.T"}], DEV, CONFIG)


# ---------------------------------------------------------------- reading and running packs


@pytest.fixture
def domain(monkeypatch):
    monkeypatch.setattr(envs, "load", lambda s: json.loads(json.dumps(CONFIG)))
    monkeypatch.setattr(envs, "domain_location", lambda s, d: "DEV_GDP_SILVER_DB.COMPANY")
    monkeypatch.setattr(envs, "domain_silver", lambda s, d: "DEV_GDP_SILVER_DB")
    version = {"pack_id": "p", "version": 1, "status": "APPROVED", "domain_id": "d1", "name": "Integrity"}
    queries = [{"query_id": "q1", "query_name": "orphans", "severity": "ERROR", "expectation": "0 rows",
                "sql_expression": STORED_ORPHANS},
               {"query_id": "q2", "query_name": "domain_rows", "severity": "WARN", "expectation": "0 rows",
                "sql_expression": "SELECT ID FROM {{domain}}.COMPANY_CORE WHERE ID IS NULL"},
               {"query_id": "q3", "query_name": "shared", "severity": "WARN", "expectation": "0 rows",
                "sql_expression": "SELECT ID FROM REFERENCE_DB.X.COUNTRY"}]
    monkeypatch.setattr(packs, "_versions", lambda s, pid: [version])
    monkeypatch.setattr(packs, "_query_rows", lambda s, pid, v: [dict(q) for q in queries])
    pii = []
    monkeypatch.setattr(packs, "_pii", lambda s, t: pii.append(list(t)) or (set(), False))
    return {"queries": queries, "pii": pii}


def test_get_pack_shows_real_names_never_the_stored_form(domain):
    detail = packs.get_pack(Session(), "p")
    assert not braces(detail["queries"])
    assert detail["queries"][0]["sql_expression"] == ORPHANS
    assert detail["queries"][1]["sql_expression"] == "SELECT ID FROM DEV_GDP_SILVER_DB.COMPANY.COMPANY_CORE WHERE ID IS NULL"


def test_target_for_the_current_and_another_environment(domain):
    here = packs.target(Session(), "d1")
    assert here["current"] and here["name"] == "DEV" and here["bindings"]["domain"] == "DEV_GDP_SILVER_DB.COMPANY"
    there = packs.target(Session(), "d1", "da")
    assert not there["current"] and there["name"] == "DA" and there["bindings"]["domain"] == "DA_GDP_SILVER_DB.COMPANY"
    with pytest.raises(AssertionError, match="PROD is not configured"):
        packs.target(Session(), "d1", "PROD")


def test_execute_on_another_environment_moves_tables_and_records_it(domain, monkeypatch):
    import services.qa.run as run

    ran, stored = [], []
    monkeypatch.setattr(run, "execute", lambda s, t, a, p, k, c: ran.append((t["sql"], list(a))) or {**t, "outcome": "PASS"})
    monkeypatch.setattr(run, "_store", lambda s, qa_run, results: stored.append(qa_run))
    out = packs.execute(Session(), "d1", "p", environment="DA")
    assert out["environment"] == "DA" and out["current_environment"] is False
    assert ran[0] == (ORPHANS.replace("DEV_", "DA_"), [])   # no allowlist
    assert ran[1][0] == "SELECT ID FROM DA_GDP_SILVER_DB.COMPANY.COMPANY_CORE WHERE ID IS NULL"
    assert ran[2][0] == "SELECT ID FROM REFERENCE_DB.X.COUNTRY"   # an unmapped database is read where it is
    assert stored[0]["trigger_detail"]["environment"] == "DA" and stored[0]["target"] == "domain pack Integrity on DA"
    # PII comes from the profiles of the tables the queries read here
    assert "DEV_GDP_SILVER_DB.COMPANY.COMPANY_ADDRESS" in domain["pii"][0] and "REFERENCE_DB.X.COUNTRY" in domain["pii"][0]
    ran.clear()
    packs.execute(Session(), "d1", "p")
    assert ran[0][0] == ORPHANS and not any(braces(r) for r in ran)
    adhoc = packs.execute(Session(), "d1", sql="SELECT 1 FROM ANY_DB.S.T")
    assert adhoc["results"][0]["sql"] == "SELECT 1 FROM ANY_DB.S.T"


def test_dry_run_shows_real_names_guards_and_explains_without_storing(domain):
    session = Session(missing={"DA_GDP_SILVER_DB.COMPANY.COMPANY_ADDRESS"})
    out = packs.dry_run(session, "d1", "p", environment="DA")
    by_name = {q["query_name"]: q for q in out["queries"]}
    assert not braces(out) and out["environment"] == "DA" and out["ok"] is False
    assert by_name["orphans"]["written"] == ORPHANS and by_name["orphans"]["sql"] == ORPHANS.replace("DEV_", "DA_")
    assert by_name["orphans"]["rewritten"] and "does not exist" in by_name["orphans"]["explain"]["error"]
    assert by_name["domain_rows"]["ok"]
    assert by_name["shared"]["kept"] == ["REFERENCE_DB.X.COUNTRY"] and by_name["shared"]["ok"]
    assert all(c.startswith("EXPLAIN USING TEXT SELECT") for c in session.calls)   # nothing ran, nothing stored
    proposed = {"current": "DEV", "environments": [{"name": "DEV", "bindings": DEV},
                                                    {"name": "DA", "bindings": {"silver": "DA2_DB"}}]}
    out = packs.dry_run(Session(), "d1", "p", environment="DA", config_json=json.dumps(proposed))
    assert out["queries"][0]["sql"].count("DA2_DB.COMPANY.") == 2 and out["queries"][0]["ok"]
    unmapped = {"current": "DEV", "environments": [{"name": "DEV", "bindings": DEV}, {"name": "QA", "bindings": {}}]}
    out = packs.dry_run(Session(), "d1", "p", environment="QA", config_json=json.dumps(unmapped))
    assert out["queries"][0]["problems"] == ["no Silver database is mapped for environment QA (Admin, Environments)"]
    with pytest.raises(AssertionError, match="must be DATABASE"):
        packs.dry_run(Session(), "d1", "p", config_json=json.dumps({"current": "DEV", "environments": [
            {"name": "DEV", "bindings": {"silver": "A.B.C"}}]}))


def test_saving_a_pack_stores_the_portable_form(domain, monkeypatch):
    inserted = []
    monkeypatch.setattr(packs, "_insert_version", lambda s, pid, v, d, meta, queries, *a, **k: inserted.append(queries))
    monkeypatch.setattr(packs, "scalar", lambda s, sql, p=None: 1)
    monkeypatch.setattr(packs, "rows", lambda s, sql, p=None: [])
    packs.create_pack(Session(), "d1", json.dumps({"name": "New", "queries": [
        {"query_name": "q1", "sql_expression": ORPHANS.replace("DEV_", "DA_")}]}))
    assert inserted[0][0]["sql_expression"] == STORED_ORPHANS


# ---------------------------------------------------------------- files


def test_pack_file_round_trip_keeps_real_names():
    pack = {"name": "Integrity", "description": "Orphans", "tags": ["reconciliation"],
            "queries": [{"query_name": "orphans", "severity": "ERROR", "expectation": "0 rows", "tags": [],
                         "description": "Addresses without a hub row", "sql_expression": ORPHANS}]}
    text = pack_files.render(pack)
    assert "{{" not in text and pack_files.parse(text)["queries"][0]["sql_expression"] == ORPHANS
    assert not hasattr(pack_files, "portable")


def test_repository_packs_are_portable_and_read_with_real_names():
    """Repository pack files may use the logical form so one file seeds every environment; resolved for DEV and for
    another environment they read each environment's own tables, and storing the DEV text gives the file back."""
    company = json.loads((ROOT / "domain/company/domain_pack.json").read_text())
    dev = {"silver": company["domain"]["silver_database"]}
    for path in sorted((ROOT / "domain").glob("*/qa/*.sql")):
        for q in pack_files.parse(path.read_text())["queries"]:
            on_dev, _ = envs.resolve(q["sql_expression"], dev)
            on_da, _ = envs.resolve(q["sql_expression"], DA)
            assert dev["silver"] + ".COMPANY." in on_dev and "DA_GDP_SILVER_DB.COMPANY." in on_da
            assert envs.store_form(on_dev, None, dev) == q["sql_expression"]


def test_dq_suite_portable_file_has_real_names_and_moves_on_import():
    checks = [
        {"check_id": "c1", "target_column": "EMAIL", "severity": "WARN", "status": "ACTIVE",
         "definition": {"kind": "not_null"}, "soda_dsl": "x", "headline": "x", "origin": "USER"},
        {"check_id": "c2", "target_column": "COUNTRY", "severity": "FAIL", "status": "PAUSED",
         "definition": {"kind": "reference", "reference_table": "DEV_GDP_SILVER_DB.REF.COUNTRY", "reference_column": "CODE"}},
        {"check_id": "c3", "target_column": None, "severity": "FAIL", "status": "ACTIVE",
         "definition": {"kind": "failed_rows", "name": "orphans",
                        "query": "SELECT * FROM DEV_GDP_SILVER_DB.COMPANY.COMPANY_ADDRESS WHERE ID IS NULL"}},
    ]
    name, media, text = dq_suite.portable("DEV_GDP_SILVER_DB.COMPANY.COMPANY_CORE", "COMPANY", checks,
                                          {**DEV, "domain": "DEV_GDP_SILVER_DB.COMPANY"}, "DEV", 3)
    assert name == "dev_gdp_silver_db_company_company_core.dq.json" and media == "application/json"
    assert "{{" not in text and "check_id" not in text and "DEV_GDP_SILVER_DB.COMPANY.COMPANY_CORE" in text
    doc = dq_suite.parse_portable(text)
    assert doc["source"] == {"environment": "DEV", "suite_version": 3, "databases": DEV}
    table, raws, problems = dq_suite.localize(doc, DA, None, "DA")   # the file's own mapping is enough
    assert not problems and table == "DA_GDP_SILVER_DB.COMPANY.COMPANY_CORE"
    assert raws[1]["definition"]["reference_table"] == "DA_GDP_SILVER_DB.REF.COUNTRY" and raws[1]["status"] == "PAUSED"
    assert raws[2]["definition"]["query"] == "SELECT * FROM DA_GDP_SILVER_DB.COMPANY.COMPANY_ADDRESS WHERE ID IS NULL"
    for raw in raws:
        assert not dq_suite.check_record(raw)[1], raw
    same, _, problems = dq_suite.localize(doc, DEV, CONFIG, "DEV")
    assert not problems and same == "DEV_GDP_SILVER_DB.COMPANY.COMPANY_CORE"
    _, _, problems = dq_suite.localize(doc, {}, None, "QA")
    assert problems == ["no Silver database is mapped for environment QA (Admin, Environments)"]
    legacy = json.loads(text)
    legacy["table"], legacy["source"] = "{{silver}}.COMPANY.COMPANY_CORE", {}
    assert dq_suite.localize(dq_suite.parse_portable(json.dumps(legacy)), DA)[0] == "DA_GDP_SILVER_DB.COMPANY.COMPANY_CORE"
    for bad, message in (("{", "not JSON"), ('{"format": "x"}', "not a gdp-dq-suite"),
                         ('{"format": "gdp-dq-suite", "format_version": 2, "table": "T"}', "newer"),
                         ('{"format": "gdp-dq-suite", "table": "T", "checks": [1]}', "list of objects")):
        with pytest.raises(ValueError, match=message):
            dq_suite.parse_portable(bad)


def test_import_suite_adds_only_new_checks_and_opens_no_empty_draft(monkeypatch):
    _, _, text = dq_suite.portable("DEV_GDP_SILVER_DB.COMPANY.COMPANY_CORE", "COMPANY", [
        {"target_column": "EMAIL", "severity": "WARN", "definition": {"kind": "not_null"}},
        {"target_column": "ID", "severity": "FAIL", "definition": {"kind": "unique"}}], DEV, "DEV")
    monkeypatch.setattr(envs, "load", lambda s: {"current": "DA", "environments": [{"name": "DA", "bindings": DA}]})
    monkeypatch.setattr(envs, "domain_location", lambda s, d: None)
    monkeypatch.setattr(envs, "domain_silver", lambda s, d: None)
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

    assert privilege_for("PUT", "/api/config/environments")[:2] == ("CONFIG.EDIT", "Change environment databases")
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
    with pytest.raises(deploy.ConfigError, match=r"\[environment\] PROD: Silver must be"):
        deploy.validate_env({**raw, "environment": {"name": "prod", "bindings": {"silver": "a.b.c"}}}, "client")
    assert deploy.validate_env({k: v for k, v in raw.items() if k != "environment"}, "client")["environment"] is None


class SeedCursor:
    def __init__(self, value=None, count=0):
        self.value, self.count, self.sql = value, count, []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))

    def fetchall(self):
        last = self.sql[-1][0]
        if "COUNT(*)" in last:
            return [(self.count,)]
        return [(json.dumps(self.value),)] if self.value is not None else []


def test_seed_environment_writes_once_and_keeps_saved_mappings():
    from infrastructure.seed_knowledge import seed_environment

    cur = SeedCursor(count=0)
    log = seed_environment(cur, "AIP", {"name": "PROD", "bindings": {"silver": "prod_db"}})
    assert log == ["environment bindings: PROD (1 bindings)"]
    insert = cur.sql[-1]
    assert "INSERT INTO AIP.CORE.PLATFORM_CONFIG" in insert[0]
    assert json.loads(insert[1][1]) == {"current": "PROD", "environments": [
        {"name": "PROD", "description": "", "bindings": {"silver": "PROD_DB"}}]}
    cur = SeedCursor(count=2)
    assert seed_environment(cur, "AIP", {"name": "PROD", "bindings": {}}) == ["environment bindings: kept the saved value"]
    assert len(cur.sql) == 1 and seed_environment(cur, "AIP", None) == []


def test_deploy_reads_repository_packs_with_the_domain_silver_database(monkeypatch):
    """With no mapping saved, Silver falls back to the domain pack's silver database: no warning, and the seeded
    knowledge reads with real names. A role mapped nowhere is warned about."""
    import infrastructure.seed_knowledge as seed

    assert seed.check_pack_bindings(SeedCursor(None), "AIP") == []
    mapping = seed.pack_mappings(SeedCursor(None), "AIP")("COMPANY")
    assert mapping["silver"] == "DEV_GDP_SILVER_DB" and mapping["domain"] == "DEV_GDP_SILVER_DB.COMPANY"
    monkeypatch.setattr(seed, "load_qa_packs", lambda: [("COMPANY", {"name": "Gold", "queries": [
        {"query_name": "q", "sql_expression": "SELECT 1 FROM {{gold}}.S.T"}]}, None)])
    warned = seed.check_pack_bindings(SeedCursor(None), "AIP")
    assert warned and warned[0].startswith("WARNING qa packs: no Gold database is mapped") and not braces(warned)


def test_repository_suite_files_parse():
    from infrastructure.seed_knowledge import load_dq_suite_files

    for _, text, path in load_dq_suite_files():
        dq_suite.parse_portable(text)
