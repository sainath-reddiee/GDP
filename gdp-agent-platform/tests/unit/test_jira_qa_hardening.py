"""QA and Jira hardening: the read-only guard's tokenizer, sign-off on tested work only, PII on the run path and in
result text, Jira token caching and 401 recovery, bug claims, link visibility, bulk idempotency, grants and V033."""

import importlib
import json
import re
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

import test_jira as tj  # noqa: E402
import test_jira_inbox as ji  # noqa: E402
import test_qa_scope as qs  # noqa: E402
from services.jira import triage  # noqa: E402
from services.jira.client import JiraError  # noqa: E402
from services.qa import run as qa_run  # noqa: E402
from services.qa.guard import check, tokenize  # noqa: E402
from services.qa.tests import build_suite  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = ["SILVER.CRM.DIM_CUSTOMER", "LAND.CRM.CUSTOMERS", "LAND.CRM.ORDERS"]


def ok(sql, allowed=ALLOWED):
    good, problems, _ = check(sql, allowed)
    assert good, (sql, problems)


def refused(sql, reason, allowed=ALLOWED):
    good, problems, _ = check(sql, allowed)
    assert not good and any(reason in p for p in problems), (sql, problems)


# ---------------------------------------------------------------- 1. strings and comments are read together

@pytest.mark.parametrize("sql", [
    "SELECT '--', p.* FROM SECRET.HR.PAY p",
    "SELECT '/*', p.* FROM SECRET.HR.PAY p --*/",
    "SELECT 'a''--', p.* FROM SECRET.HR.PAY p",
    "SELECT 'it\\'s --', p.* FROM SECRET.HR.PAY p",
    "SELECT $$ -- $$, p.* FROM SECRET.HR.PAY p",
    "SELECT \"col--x\" FROM SECRET.HR.PAY",
])
def test_comment_markers_inside_strings_do_not_hide_tables(sql):
    refused(sql, "SECRET.HR.PAY is not one of this run's")


@pytest.mark.parametrize("sql, reason", [
    ("SELECT '--' FROM SILVER.CRM.DIM_CUSTOMER; DROP TABLE X", "one statement"),
    ("SELECT '/*' FROM SILVER.CRM.DIM_CUSTOMER; DELETE FROM LAND.CRM.CUSTOMERS --*/", "one statement"),
    ("SELECT '/*' AS a, 1 FROM SILVER.CRM.DIM_CUSTOMER WHERE 1 = 1 /* x */ ; CALL SYSTEM$WAIT(1)", "one statement"),
    ("SELECT 'x' FROM SILVER.CRM.DIM_CUSTOMER WHERE 'a' = '--' AND SYSTEM$CANCEL_ALL_QUERIES(1) IS NULL", "not allowed"),
])
def test_statements_and_forbidden_calls_after_a_string_are_seen(sql, reason):
    refused(sql, reason)


def test_keywords_and_markers_inside_strings_are_only_text():
    ok("SELECT 'drop table; -- /* delete' AS note FROM SILVER.CRM.DIM_CUSTOMER")
    ok("SELECT $$ insert into x; FROM SECRET.HR.PAY $$ AS t FROM SILVER.CRM.DIM_CUSTOMER")
    ok("SELECT 'it\\'s fine; FROM SECRET.A.B' AS t FROM SILVER.CRM.DIM_CUSTOMER")
    ok("SELECT 'O''Brien' AS n FROM SILVER.CRM.DIM_CUSTOMER WHERE NAME <> 'FROM SECRET.A.B'")


def test_comments_in_odd_places():
    ok("SELECT /* FROM SECRET.HR.PAY */ ID FROM /* c */ SILVER.CRM.DIM_CUSTOMER -- trailing")
    ok("SELECT ID FROM SILVER . CRM . DIM_CUSTOMER // a Snowflake line comment")
    ok("SELECT ID FROM SILVER/**/.CRM.DIM_CUSTOMER")
    refused("SELECT ID FROM SILVER.CRM.DIM_CUSTOMER /* , SECRET.HR.PAY */ , SECRET.HR.PAY", "SECRET.HR.PAY")
    refused("SELECT ID FROM SILVER.CRM.DIM_CUSTOMER // , x\n, SECRET.HR.PAY", "SECRET.HR.PAY")
    refused("SELECT 1 FROM SILVER.CRM.DIM_CUSTOMER /* never closed", "unterminated")
    refused("SELECT 'never closed FROM SILVER.CRM.DIM_CUSTOMER", "unterminated")


def test_trailing_semicolons_and_comments_are_dropped_from_the_cleaned_sql():
    good, _, cleaned = check("SELECT ID FROM SILVER.CRM.DIM_CUSTOMER;  -- done\n", ALLOWED)
    assert good and cleaned == "SELECT ID FROM SILVER.CRM.DIM_CUSTOMER"
    good, _, cleaned = check("SELECT ';' AS s FROM SILVER.CRM.DIM_CUSTOMER;;", ALLOWED)
    assert good and cleaned == "SELECT ';' AS s FROM SILVER.CRM.DIM_CUSTOMER"


def test_unbalanced_parentheses_cannot_escape_the_wrapper():
    # run.wrap() puts the test inside SELECT * FROM ( ... ): a stray ')' would close it early
    refused("SELECT 1 FROM SILVER.CRM.DIM_CUSTOMER) , (SELECT 1", "unbalanced")
    refused("SELECT (1 FROM SILVER.CRM.DIM_CUSTOMER", "unbalanced")


# ---------------------------------------------------------------- 2. every FROM list item is checked

@pytest.mark.parametrize("sql", [
    "SELECT * FROM SILVER.CRM.DIM_CUSTOMER a, SECRET.HR.PAY b",
    "SELECT * FROM SILVER.CRM.DIM_CUSTOMER a, LAND.CRM.ORDERS o, SECRET.HR.PAY b WHERE a.ID = o.ID",
    "SELECT * FROM SILVER.CRM.DIM_CUSTOMER a JOIN LAND.CRM.ORDERS o ON COALESCE(a.ID, 0) = o.ID, SECRET.HR.PAY b",
    "SELECT * FROM (SELECT * FROM SILVER.CRM.DIM_CUSTOMER) a, SECRET.HR.PAY b",
    "SELECT * FROM (SILVER.CRM.DIM_CUSTOMER a JOIN SECRET.HR.PAY b ON a.ID = b.ID)",
    "SELECT * FROM ((SILVER.CRM.DIM_CUSTOMER a), SECRET.HR.PAY b)",
    "SELECT (SELECT MAX(X) FROM SECRET.HR.PAY) AS m FROM SILVER.CRM.DIM_CUSTOMER",
    "SELECT * FROM SILVER.CRM.DIM_CUSTOMER WHERE ID IN (SELECT ID FROM LAND.CRM.ORDERS, SECRET.HR.PAY)",
    "SELECT ID FROM SILVER.CRM.DIM_CUSTOMER UNION ALL SELECT ID FROM SECRET.HR.PAY",
    "SELECT ID FROM SILVER.CRM.DIM_CUSTOMER MINUS SELECT ID FROM LAND.CRM.ORDERS, SECRET.HR.PAY",
    "SELECT * FROM SILVER.CRM.DIM_CUSTOMER WHERE EXISTS (SELECT 1 FROM (SELECT 1 FROM SECRET.HR.PAY) z)",
    "WITH a AS (SELECT * FROM SECRET.HR.PAY) SELECT * FROM a",
    "WITH a AS (SELECT * FROM SILVER.CRM.DIM_CUSTOMER), b AS (SELECT * FROM a, SECRET.HR.PAY) SELECT * FROM b",
    "SELECT * FROM SILVER.CRM.DIM_CUSTOMER WHERE ID = (SELECT EXTRACT(YEAR FROM (SELECT MAX(D) FROM SECRET.HR.PAY)))",
    "SELECT * FROM ((SELECT 1 AS X) UNION SELECT X FROM SECRET.HR.PAY)",
])
def test_every_table_in_every_from_list_is_checked(sql):
    refused(sql, "SECRET.HR.PAY")


def test_reads_of_allowed_tables_in_every_shape():
    ok("SELECT * FROM SILVER.CRM.DIM_CUSTOMER a, LAND.CRM.ORDERS o WHERE a.ID = o.ID")
    ok("WITH x AS (SELECT * FROM SILVER.CRM.DIM_CUSTOMER), y (id) AS (SELECT ID FROM x) "
       "SELECT * FROM x, y WHERE x.ID = y.id")
    ok("WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r WHERE n < 3) SELECT * FROM r")
    ok("SELECT * FROM (SELECT * FROM (SELECT ID FROM LAND.CRM.CUSTOMERS) a) b")
    ok("SELECT EXTRACT(YEAR FROM d), TRIM(' ' FROM n), SUBSTRING(n FROM 2) FROM SILVER.CRM.DIM_CUSTOMER")
    ok("SELECT * FROM SILVER.CRM.DIM_CUSTOMER a FULL OUTER JOIN LAND.CRM.CUSTOMERS c USING (ID) WHERE a.X IS DISTINCT FROM c.X")
    ok("SELECT NTH_VALUE(X, 2) FROM FIRST OVER (ORDER BY ID) FROM SILVER.CRM.DIM_CUSTOMER")
    ok("SELECT * FROM (VALUES (1, 'a'), (2, 'b')) AS v (n, s)")
    ok("SELECT ID FROM SILVER.CRM.DIM_CUSTOMER GROUP BY ID, NAME ORDER BY ID, NAME")
    ok("SELECT ID FROM SILVER.CRM.DIM_CUSTOMER AT (OFFSET => -60) LIMIT 10")


def test_quoted_identifiers_keep_their_case():
    ok('SELECT * FROM "SILVER"."CRM"."DIM_CUSTOMER"')
    ok('SELECT * FROM silver.crm."DIM_CUSTOMER"')
    refused('SELECT * FROM "SILVER"."CRM"."dim_customer"', "is not one of this run's")   # another table in Snowflake
    ok('SELECT * FROM "SILVER"."CRM"."MyTable"', ["SILVER.CRM.MyTable"])                 # registry spelling
    ok('SELECT * FROM "SILVER"."CRM"."Odd Name"', ['"SILVER"."CRM"."Odd Name"'])
    refused('SELECT * FROM "SILVER.CRM.DIM_CUSTOMER"', "fully qualified")
    ok('SELECT "FROM", "SELECT" FROM SILVER.CRM.DIM_CUSTOMER')


@pytest.mark.parametrize("sql, reason", [
    ("SELECT * FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))", "table functions"),
    ("SELECT * FROM SILVER.CRM.DIM_CUSTOMER c, LATERAL FLATTEN(input => c.TAGS) f", "table functions"),
    ("SELECT * FROM SILVER.CRM.DIM_CUSTOMER c, TABLE(SPLIT_TO_TABLE(c.X, ','))", "table functions"),
    ("SELECT * FROM SILVER.CRM.DIM_CUSTOMER WHERE ID IN (SELECT $1 FROM RESULT_SCAN('01ab'))", "RESULT_SCAN"),
    ("SELECT * FROM IDENTIFIER('SECRET.HR.PAY')", "IDENTIFIER"),
    ("SELECT * FROM IDENTIFIER($t)", "IDENTIFIER"),
    ("SELECT $1 FROM @SECRET.HR.STAGE/file.csv", "stage"),
    ("SELECT $1 FROM '@~/file.csv'", "stage"),
    ("SELECT * FROM SILVER.INFORMATION_SCHEMA.TABLES", "system views"),
    ("SELECT * FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY", "system views"),
    ('SELECT * FROM SILVER."INFORMATION_SCHEMA".COLUMNS', "system views"),
    ("SELECT * FROM DIM_CUSTOMER", "fully qualified"),
    ("SELECT * FROM CRM.DIM_CUSTOMER", "fully qualified"),
    ("SELECT * FROM SILVER..DIM_CUSTOMER", "fully qualified"),
    ("SELECT * FROM SILVER.CRM.DIM_CUSTOMER a, DIM_CUSTOMER b", "fully qualified"),
    ("SELECT * FROM SILVER.CRM.DIM_CUSTOMER JOIN", "missing its table"),
])
def test_refused_sources(sql, reason):
    refused(sql, reason)


def test_cte_names_are_references_only_as_single_names():
    ok("WITH PAY AS (SELECT * FROM SILVER.CRM.DIM_CUSTOMER) SELECT * FROM PAY")
    refused("WITH PAY AS (SELECT * FROM SILVER.CRM.DIM_CUSTOMER) SELECT * FROM SECRET.HR.PAY", "SECRET.HR.PAY")
    refused('WITH "pay" AS (SELECT * FROM SILVER.CRM.DIM_CUSTOMER) SELECT * FROM pay', "fully qualified")


def test_tokenizer_shapes():
    tokens, error = tokenize("SELECT 'a''b', $$x$$, \"Q\"\"n\", 1.5e-3, $v -- c\n/* d */ x::INT")
    assert error is None
    assert [(t.kind, t.text) for t in tokens] == [
        ("id", "SELECT"), ("str", "a''b"), ("punct", ","), ("str", "x"), ("punct", ","), ("qid", 'Q"n'), ("punct", ","),
        ("num", "1.5e-3"), ("punct", ","), ("var", "$v"), ("id", "x"), ("punct", "::"), ("id", "INT")]


def test_every_generated_suite_passes_the_guard():
    lines = [{"target_column": t, "source_table": "CUSTOMERS", "source_column": c, "mapping_type": kind,
              "transformation": rule, "nullable_rule": False, "accepted_values": values, "default_value": None}
             for t, c, kind, rule, values in [("CUSTOMER_ID", "CUST_ID", "DIRECT", None, []),
                                               ("NAME", "NAME", "TRANSFORM", "INITCAP(TRIM(o.NAME))", []),
                                               ("STATUS", "STATUS", "DIRECT", None, ["A", "B"]),
                                               ("LAST_ORDER", "ORDER_DATE", "TRANSFORM", "MAX(ORDER_DATE)", [])]]
    graph = {"driving_table": "CUSTOMERS", "joins": [{"left_table": "CUSTOMERS", "right_table": "ORDERS", "join_type": "LEFT",
                                                      "keys": ["CUST_ID=CUSTOMER_ID"], "cardinality": "1:N"}]}
    tests = build_suite({"fqn": "SILVER.CRM.DIM_CUSTOMER", "name": "DIM_CUSTOMER"},
                        {"CUSTOMERS": "LAND.CRM.CUSTOMERS", "ORDERS": "LAND.CRM.ORDERS"}, lines, ["CUSTOMER_ID"], graph, {})
    assert tests
    for t in tests:
        ok(t["sql"])


# ---------------------------------------------------------------- 3. sign-off needs a QA run on the current STTM

class SignoffDb:
    user, role = "ANA", "QA"

    def __init__(self, sttm="s2"):
        self.sttm, self.executed = sttm, []

    def query(self, sql, params=()):
        if "FROM CORE.WORKFLOW_RUN" in sql:
            return [{"current_state": "VALIDATION_PASSED"}]
        if "FROM CONTRACT.STTM_REGISTRY" in sql and "QA_SIGNOFF" not in sql:
            return [{"sttm_id": self.sttm}]
        return []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))


def _signoff(monkeypatch, last, results=(), **body):
    import app.main as main

    monkeypatch.setattr(qa_run, "latest", lambda query, run_id: (last, list(results)))
    monkeypatch.setattr(main, "_drop_run", lambda run_id: None)
    db = SignoffDb()
    out = main.qa_signoff("r1", main.QaSignoff(**{"decision": "APPROVED", **body}), db=db)
    return out, db


@pytest.mark.parametrize("last", [None, {"qa_run_id": "q1", "sttm_id": "s1"}, {"qa_run_id": "q1", "sttm_id": None}])
def test_approval_without_a_run_on_the_current_sttm_is_refused(monkeypatch, last):
    with pytest.raises(HTTPException) as err:
        _signoff(monkeypatch, last, override=True, note="a long enough override reason")
    assert err.value.status_code == 409 and "Run QA on the current STTM version" in err.value.detail


def test_approval_on_the_current_sttm_keeps_the_override_rules(monkeypatch):
    failing = [{"outcome": "FAIL", "severity": "HIGH"}]
    with pytest.raises(HTTPException) as err:
        _signoff(monkeypatch, {"sttm_id": "s2"}, failing)
    assert err.value.status_code == 409 and "failing critical or high" in err.value.detail
    _, db = _signoff(monkeypatch, {"sttm_id": "s2"}, failing, override=True, note="known issue, ticket QA-12 open")
    (sql, params), = db.executed
    assert "INSERT INTO CONTRACT.QA_SIGNOFF" in sql and params[2] == "s2" and params[4].startswith("[override: 1")
    _, db = _signoff(monkeypatch, {"sttm_id": "s2"}, [{"outcome": "PASS", "severity": "HIGH"}])
    assert db.executed[0][1][3] == "APPROVED"


def test_rejection_needs_no_qa_run(monkeypatch):
    _, db = _signoff(monkeypatch, None, decision="REJECTED", note="totals are wrong")
    assert db.executed and db.executed[0][1][3] == "REJECTED"


# ---------------------------------------------------------------- 4. run-path PII is conservative without a profile

def _run_ctx(session):
    from services.qa.procedures import context

    return context(session, "r1")


def test_run_path_without_profile_rows_is_conservative():
    session = qs.Session(qs._handlers())
    pii, basis = qa_run.run_pii(session, _run_ctx(session))
    assert basis == "conservative" and "CONTACT" not in pii


def test_run_path_with_an_unreadable_profile_is_conservative():
    session = qs.Session(qs._handlers(extra=[("FROM PROFILE.PROFILE_REGISTRY", RuntimeError("insufficient privileges"))]))
    assert qa_run.run_pii(session, _run_ctx(session))[1] == "conservative"
    # the table path too: a profile it cannot read is never replaced by curation
    curated = [dict(c, IS_PII=c["COLUMN_NAME"] == "SEGMENT") for c in qs.COLUMNS]
    table = qs.Session(qs._handlers(columns=curated, extra=[("FROM PROFILE.PROFILE_REGISTRY", RuntimeError("denied"))]))
    assert qs.table_context(table, "t1")["pii_basis"] == "conservative"


def test_run_path_with_a_profile_maps_sources_and_adds_registry_flags():
    profile = [{"TABLE_NAME": "CUSTOMERS", "COLUMN_NAME": "CONTACT_INFO", "PII_CLASSIFICATION": "EMAIL"},
               {"TABLE_NAME": "CUSTOMERS", "COLUMN_NAME": "SEG", "PII_CLASSIFICATION": "NONE"}]
    curated = [dict(c, IS_PII=c["COLUMN_NAME"] == "SEGMENT") for c in qs.COLUMNS]
    session = qs.Session(qs._handlers(profile=profile, columns=curated))
    pii, basis = qa_run.run_pii(session, _run_ctx(session))
    assert basis == "profile" and {"CONTACT_INFO", "CONTACT", "SEGMENT"} <= pii


def test_run_scope_passes_conservative_on_the_run_path(monkeypatch):
    calls = []
    monkeypatch.setattr(qa_run, "execute", qs._fake_execute(calls))
    monkeypatch.setattr(qa_run, "_target_built", lambda session, fqn: True)
    session = qs.Session(qs._handlers(extra=[("WHERE SCOPE = 'TABLE' AND TARGET_TABLE_ID", [qs.SAVED_TABLE]),
                                             ("WHERE RUN_ID = ? AND NOT IS_DELETED", [])]))
    out = qa_run.run_tests(session, "r1", ["tt1"], "UI")
    assert calls[0]["conservative"] is True and out["pii_basis"] == "conservative"


# ---------------------------------------------------------------- 5 and 6. counts only, whatever the alias

class _Field:
    def __init__(self, name):
        self.name = name


class _Frame:
    def __init__(self, columns, data, error=None):
        self.schema = type("S", (), {"fields": [_Field(c) for c in columns]})()
        self.data, self.error = data, error

    def collect(self):
        if self.error:
            raise self.error
        return [tuple(r) for r in self.data]


class FrameSession:
    def __init__(self, columns, data, error=None):
        self.frame = _Frame(columns, data, error)

    def sql(self, text, params=None):
        return self.frame


SIDE_ROWS = [("expected", "ana@example.com", 2), ("actual", "ana@example.com", 1), ("expected", "bo@example.com", 1)]
TABLE = "SILVER.CRM.DIM_CUSTOMER"


def _test(sql, expected="matching counts per value on both sides", target_column=None):
    return {"test_id": "x1", "sql": sql, "expected": expected, "target_column": target_column, "severity": "HIGH"}


def test_values_are_listed_only_when_nothing_is_sensitive():
    out = qa_run.execute(FrameSession(["SIDE", "VALUE", "N"], [("expected", "A", 2), ("actual", "A", 1)]),
                         _test(f"SELECT 'expected' AS side, STATUS AS value, COUNT(*) AS n FROM {TABLE} GROUP BY 2"),
                         [TABLE], set())
    assert out["detail"] == "1 values differ: A"


def test_an_aliased_pii_column_masks_samples_and_keeps_values_out_of_detail():
    sql = f"SELECT 'expected' AS side, EMAIL AS value, COUNT(*) AS n FROM {TABLE} GROUP BY 2"
    out = qa_run.execute(FrameSession(["SIDE", "VALUE", "N"], SIDE_ROWS), _test(sql), [TABLE], {"EMAIL"}, {"CUSTOMER_ID"})
    assert out["outcome"] == "FAIL" and out["detail"] == "2 values differ" and out["measured"] == "2 values differ"
    assert "example.com" not in json.dumps({k: out[k] for k in ("detail", "measured", "sample")})
    assert set(out["masked"]) == {"SIDE", "VALUE", "N"}


def test_a_pii_like_name_in_the_sql_is_enough():
    sql = f"SELECT 'expected' AS side, c.CONTACT_EMAIL AS v1, COUNT(*) AS n FROM {TABLE} c GROUP BY 2"
    assert qa_run.mentions_pii(sql, set(), [TABLE])
    assert qa_run.hidden_columns({"sql": sql}, ["SIDE", "V1", "N", "CUSTOMER_ID"], set(), {"CUSTOMER_ID"}, False, [TABLE]) \
        == {"SIDE", "V1", "N"}
    # parts of allowed table names are not columns: COMPANY_ADDRESS the table does not make a test sensitive
    assert not qa_run.mentions_pii("SELECT ID FROM SILVER.COMPANY.COMPANY_ADDRESS", set(), ["SILVER.COMPANY.COMPANY_ADDRESS"])
    # a PII word inside a string literal is not a column reference
    assert not qa_run.mentions_pii(f"SELECT 'email' AS kind FROM {TABLE}", set(), [TABLE])


def test_zero_check_on_a_sensitive_test_reports_zero_or_not_zero_only():
    sql = f"SELECT COUNT(*) - COUNT(DISTINCT EMAIL) AS difference FROM {TABLE}"
    out = qa_run.execute(FrameSession(["DIFFERENCE"], [(1234567,)]), _test(sql, "difference = 0"), [TABLE], {"EMAIL"})
    assert out["outcome"] == "FAIL" and "1234567" not in out["detail"] + str(out["measured"]) and out["measured"] == "not 0"


def test_conservative_keeps_integer_counts_but_not_other_values():
    sql = f"SELECT SEGMENT AS difference FROM {TABLE}"
    count = qa_run.execute(FrameSession(["DIFFERENCE"], [(3,)]), _test(sql, "difference = 0"), [TABLE], set(), (), True)
    assert count["detail"] == "difference = 3"
    text = qa_run.execute(FrameSession(["DIFFERENCE"], [("GOLD",)]), _test(sql, "difference = 0"), [TABLE], set(), (), True)
    assert "GOLD" not in text["detail"] and text["measured"] is None
    salary = qa_run.execute(FrameSession(["DIFFERENCE"], [(5123.75,)]), _test(sql, "difference = 0"), [TABLE], set(), (), True)
    assert "5123" not in salary["detail"] + str(salary["measured"])


def test_snowflake_errors_lose_quoted_values_when_sensitive():
    err = RuntimeError("Numeric value 'ana@example.com' is not recognized")
    sql = f"SELECT EMAIL::NUMBER AS x FROM {TABLE}"
    out = qa_run.execute(FrameSession(["X"], [], err), _test(sql, "0 rows"), [TABLE], {"EMAIL"})
    assert out["outcome"] == "ERROR" and "ana@example.com" not in out["detail"] and "'...'" in out["detail"]


def test_jira_text_carries_counts_only():
    assert triage.measured_count("3 rows") == "3 rows" and triage.measured_count("2 values differ") == "2 values differ"
    assert triage.measured_count("not 0") == "not 0" and triage.measured_count("0") == "0"
    assert triage.measured_count("ana@example.com") == "" and triage.measured_count("5123.75") == ""
    text = triage.bug_markdown({"test_id": "t"}, {"measured": "'ana@example.com'", "detail": "1 values differ: ana@example.com"},
                               "DB.S.T", "http://x")
    assert "ana@example.com" not in text and "| Measured |  |" in text
    report = triage.report_markdown("QA-1", "run", "http://x", [{"title": "t", "outcome": "FAIL", "rows_returned": 1,
                                                                  "expected": "0 rows", "detail": "values: ana@example.com",
                                                                  "measured": "ana@example.com"}])
    assert "ana@example.com" not in report


def test_web_comment_draft_has_no_detail():
    source = (ROOT / "apps" / "web" / "app" / "qa" / "results-tab.tsx").read_text(encoding="utf-8")
    body = source[source.index("function resultText"):source.index("function LinkedComment")]
    assert "r.detail" not in body and "COUNT_TEXT.test" in body


# ---------------------------------------------------------------- 7. QA_ENGINEER can call every QA and Jira procedure

def test_qa_and_jira_procedures_are_granted_consistently():
    sql = "\n".join((ROOT / "snowflake" / "procedures" / name).read_text(encoding="utf-8") for name in ("pipeline.sql", "jira.sql"))
    procs = set(re.findall(r"PROCEDURE \{\{database\}\}\.((?:CONTRACT\.QA_\w+|JIRA\.\w+)\([^)]*\))", sql))
    signatures = {re.sub(r"\(.*\)", "", p) + "(" + ", ".join("VARCHAR" for _ in re.findall(r"VARCHAR", p)) + ")" for p in procs}
    assert "CONTRACT.QA_SAVE(VARCHAR, VARCHAR)" in signatures and "JIRA.TRIAGE_TABLE(VARCHAR, VARCHAR)" in signatures
    for sig in signatures:
        for role in ("DATA_ENGINEER", "REVIEWER", "QA_ENGINEER"):
            grant = f"GRANT USAGE ON PROCEDURE {{{{database}}}}.{sig} TO DATABASE ROLE {{{{database}}}}.{role};"
            assert grant in sql, grant


# ---------------------------------------------------------------- 8. V033: own rows only

def test_v033_limits_token_and_state_rows_to_their_user():
    text = (ROOT / "snowflake" / "database" / "migrations" / "V033__jira_row_access.sql").read_text(encoding="utf-8")
    assert "ROW_USER = CURRENT_USER()" in text
    for table in ("USER_TOKEN", "OAUTH_STATE"):
        assert f"ALTER TABLE {{{{database}}}}.JIRA.{table} ADD ROW ACCESS POLICY {{{{database}}}}.JIRA.OWN_ROWS ON (USER_NAME);" in text
    assert chr(0x2014) not in text and chr(0x2013) not in text   # no em or en dashes


# ---------------------------------------------------------------- 9. token cache keyed by version; 401 recovery

def test_cache_key_has_the_token_version_so_another_replicas_refresh_is_seen(monkeypatch):
    api = tj._api(monkeypatch)
    monkeypatch.setattr(api, "_http", tj.FakeHttp([]))
    db = tj.JiraDb(row=tj.token_row(access="acc-v4", ttl=1800, version=4),
                   token_rows=[{"cloud_id": tj.CLOUD, "site_url": "https://team.atlassian.net", "account_id": "acc",
                                "token_version": 4}])
    assert api._client(db)[0].token == "acc-v4"
    db.row.update(access="acc-v5", version=5)          # another replica refreshed
    db.token_rows[0]["token_version"] = 5
    assert api._client(db)[0].token == "acc-v5"
    assert [k[3] for k in api._access] == [5]          # the older version's entry is gone


def test_a_401_expires_the_token_refreshes_once_and_retries(monkeypatch):
    api = tj._api(monkeypatch)
    http = tj.FakeHttp([("GET https://api.atlassian.com/ex/jira", 401, {"message": "Unauthorized"}),
                        ("oauth/token", 200, {"access_token": "acc-new", "refresh_token": "refresh-2", "expires_in": 3600}),
                        ("GET https://api.atlassian.com/ex/jira", 200, {"accountId": "acc"})])
    monkeypatch.setattr(api, "_http", http)
    db = tj.JiraDb(row=tj.token_row(access="acc-old", ttl=1800, version=2))

    def expire(sql, params=()):
        db.executed.append((sql, params))
        if "SET ACCESS_EXPIRES_AT = NULL" in sql:
            db.row["ttl"] = None
    db.execute = expire
    client, _, _ = api._client(db)
    assert client.myself() == {"accountId": "acc"}
    assert any("SET ACCESS_EXPIRES_AT = NULL" in s for s, _ in db.executed)
    assert tj._refreshes(http) == 1 and client.token == "acc-new"
    assert http.calls[-1][2]["Authorization"] == "Bearer acc-new"


def test_a_second_401_asks_to_reconnect(monkeypatch):
    api = tj._api(monkeypatch)
    http = tj.FakeHttp([("GET https://api.atlassian.com/ex/jira", 401, {"message": "Unauthorized"}),
                        ("oauth/token", 200, {"access_token": "acc-new", "refresh_token": "refresh-2", "expires_in": 3600}),
                        ("GET https://api.atlassian.com/ex/jira", 401, {"message": "Unauthorized"})])
    monkeypatch.setattr(api, "_http", http)
    db = tj.JiraDb(row=tj.token_row(access="acc-old", ttl=1800, version=2))
    db.execute = lambda sql, params=(): db.row.update(ttl=None) if "ACCESS_EXPIRES_AT = NULL" in sql else None
    client, _, _ = api._client(db)
    with pytest.raises(HTTPException) as err:
        client.myself()
    assert err.value.status_code == api.NOT_CONNECTED and "connect" in err.value.detail.lower()
    assert not api._access


# ---------------------------------------------------------------- 10. one bug per test at a time; idempotent links

def test_a_second_request_while_a_bug_is_being_created_gets_409(api_bug):
    module, client = api_bug(Jira=ji.Jira(search_page=lambda *a: {"issues": [], "next": None}))
    db = ji.bug_db(**{"FROM JIRA.ACTION_LOG WHERE ISSUE_KEY = %s AND ACTION = 'BUG_CLAIM'": [{"status": "PENDING", "detail": "{}"}]})
    db.count = lambda sql, params: 0 if "BUG_CLAIM" in sql else 1
    with pytest.raises(HTTPException) as err:
        ji.bug(module, db)
    assert err.value.status_code == 409 and "being created" in err.value.detail
    assert not any(n in ("search_page", "create_issue") for n, _ in client.calls)


def test_a_loser_after_the_winner_finished_gets_its_key(api_bug):
    module, client = api_bug(Jira=ji.Jira())
    db = ji.bug_db(**{"FROM JIRA.ACTION_LOG WHERE ISSUE_KEY = %s AND ACTION = 'BUG_CLAIM'":
                      [{"status": "DONE", "detail": json.dumps({"key": "QA-77"})}]})
    db.count = lambda sql, params: 0 if "BUG_CLAIM" in sql else 1
    out = ji.bug(module, db)
    assert out["key"] == "QA-77" and out["created"] is False and client.calls == []


def test_the_claim_is_taken_before_creating_and_finished_with_the_key(api_bug):
    module, client = api_bug(Jira=ji.Jira(search_page=lambda *a: {"issues": [], "next": None},
                                          issue_types=lambda p: [{"id": "2", "name": "Bug", "subtask": False}],
                                          create_issue=lambda *a: {"id": "9", "key": "QA-9"}, remote_link=lambda *a: None))
    db = ji.bug_db()
    assert ji.bug(module, db)["key"] == "QA-9"
    claims = [(s, p) for s, p in db.executed if "MERGE INTO JIRA.ACTION_LOG" in s]
    assert len(claims) == 1 and claims[0][1][0] == module.bug_label("t1", "tt1") and "'BUG_CLAIM'" in claims[0][0]
    finished = [p for s, p in db.executed if "UPDATE JIRA.ACTION_LOG SET STATUS" in s]
    assert finished and finished[-1][0] == "DONE" and finished[-1][1] == "QA-9"


def test_the_claim_is_released_when_creation_fails(api_bug):
    module, _ = api_bug(Jira=ji.Jira(search_page=lambda *a: {"issues": [], "next": None},
                                     issue_types=lambda p: [{"id": "2", "name": "Bug", "subtask": False}],
                                     errors={"create_issue": JiraError(403, "no create permission")}))
    db = ji.bug_db()
    with pytest.raises(HTTPException):
        ji.bug(module, db)
    finished = [p for s, p in db.executed if "UPDATE JIRA.ACTION_LOG SET STATUS" in s]
    assert finished and finished[-1][0] == "RELEASED"


def test_save_link_is_one_merge_and_reports_an_existing_link():
    importlib.import_module("app.main")
    import app.jira_api as api

    db = ji.Db({"SELECT LINK_ID FROM JIRA.ISSUE_LINK WHERE": [{"link_id": "L-1"}]}, count=0)
    link_id, already = api._save_link(db, "QA-1", tj.CLOUD, {"summary": "s"}, qa_test_id="t1", target_table_id="tt1", origin="BUG")
    assert (link_id, already) == ("L-1", True)
    merge = next(s for s, _ in db.executed if "MERGE INTO JIRA.ISSUE_LINK" in s)
    assert "WHEN NOT MATCHED THEN INSERT" in merge and "T.TARGET_TABLE_ID" in merge and "T.ORIGIN" in merge
    assert not db.wrote("INTO JIRA.ACTION_LOG")       # nothing new was linked, nothing logged
    db = ji.Db(count=1)
    link_id, already = api._save_link(db, "QA-1", tj.CLOUD, {"summary": "s"}, run_id="r1")
    assert already is False and db.wrote("INTO JIRA.ACTION_LOG")


# ---------------------------------------------------------------- 11. one user's 404 is not a deletion

def test_a_404_marks_deleted_only_when_the_bot_confirms(api_bug, monkeypatch):
    module, client = api_bug(Jira=ji.Jira(errors={"issue": JiraError(404, "gone")}, search_page=lambda *a: {"issues": [], "next": None},
                                          issue_types=lambda p: [{"id": "2", "name": "Bug", "subtask": False}],
                                          create_issue=lambda *a: {"id": "91", "key": "QA-91"}, remote_link=lambda *a: None))
    monkeypatch.setattr(module, "_gone_for_everyone", lambda cfg, key: True)
    db = ji.bug_db(**{"ORIGIN = 'BUG'": [{"issue_key": "QA-5"}]})
    assert ji.bug(module, db)["key"] == "QA-91"
    assert db.wrote("SET ISSUE_STATE = 'DELETED'") == [("QA-5",)]


def test_without_a_bot_nothing_is_confirmed(monkeypatch):
    importlib.import_module("app.main")
    import app.jira_api as api

    monkeypatch.delenv("JIRA_BOT_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_BOT_TOKEN", raising=False)
    assert api._gone_for_everyone({"site_url": "https://team.atlassian.net"}, "QA-1") is False


# ---------------------------------------------------------------- 12. stored link summaries stay within Jira permissions

LINKS = [{"link_id": "1", "issue_key": "QA-1", "summary": "secret plan", "status": "Open"},
         {"link_id": "2", "issue_key": "QA-2", "summary": "public", "status": "Open"}]


def test_links_without_a_jira_connection_show_keys_and_status_only(monkeypatch):
    importlib.import_module("app.main")
    import app.jira_api as api

    def not_connected(db):
        raise HTTPException(api.NOT_CONNECTED, "Connect your Jira account first.")
    monkeypatch.setattr(api, "_client", not_connected)
    out = api.visible_links(ji.Db(), [dict(r) for r in LINKS])
    assert [(r["issue_key"], r["summary"], r["status"]) for r in out] == [("QA-1", None, "Open"), ("QA-2", None, "Open")]


def test_links_are_filtered_by_one_search_with_the_callers_token(monkeypatch):
    importlib.import_module("app.main")
    import app.jira_api as api

    client = ji.Jira(search=lambda jql, *a, **k: [ji.issue("QA-2", "In Progress", "indeterminate")])
    monkeypatch.setattr(api, "_client", lambda db: (client, dict(ji.CONN), dict(ji.CFG)))
    out = api.visible_links(ji.Db(), [dict(r) for r in LINKS])
    assert [(r["issue_key"], r["summary"], r["status"]) for r in out] == [("QA-2", "about QA-2", "In Progress")]
    assert [n for n, _ in client.calls] == ["search"] and client.calls[0][1][0] == "key in (QA-1, QA-2)"


def test_a_jql_400_falls_back_to_lookups_one_by_one(monkeypatch):
    importlib.import_module("app.main")
    import app.jira_api as api

    client = ji.Jira(issue=lambda key: ji.issue(key), errors={"search": JiraError(400, "An issue with key 'QA-1' does not exist"),
                                                              "issue": {"QA-1": JiraError(404, "not found")}})
    monkeypatch.setattr(api, "_client", lambda db: (client, dict(ji.CONN), dict(ji.CFG)))
    out = api.visible_links(ji.Db(), [dict(r) for r in LINKS])
    assert [r["issue_key"] for r in out] == ["QA-2"]


# ---------------------------------------------------------------- 13. bulk comments are idempotent per submit

def test_bulk_comment_skips_issues_already_commented_under_the_key(api):
    client = ji.Jira(add_comment=lambda key, doc: {"id": "c"})
    module = api(client)
    db = ji.Db({"WHERE IDEMPOTENCY_KEY = %s AND ACTION = 'COMMENT'": [{"issue_key": "QA-1"}]})
    body = module.BulkIn(action="comment", keys=["QA-1", "QA-2"], comment="Retested", idempotency_key="bulk-0001")
    out = module.bulk(body, db=db)["results"]
    assert out == [{"key": "QA-1", "ok": True, "already": True}, {"key": "QA-2", "ok": True}]
    assert [a[0] for n, a in client.calls if n == "add_comment"] == ["QA-2"]
    logged = [p for s, p in db.executed if "INTO JIRA.ACTION_LOG" in s and "IDEMPOTENCY_KEY" in s]
    assert logged and logged[0][-1] == "bulk-0001"


def test_bulk_stops_when_jira_asks_to_reconnect(api):
    def rejected(key, doc):
        raise HTTPException(428, "connect Jira again")
    module = api(ji.Jira(add_comment=rejected))
    with pytest.raises(HTTPException) as err:
        module.bulk(module.BulkIn(action="comment", keys=["QA-1", "QA-2"], comment="x"), db=ji.Db())
    assert err.value.status_code == 428


# ---------------------------------------------------------------- web: triage state, linkable tests, load order

def test_web_fixes_are_in_place():
    web = ROOT / "apps" / "web" / "app"
    triage_tab = (web / "qa" / "triage-tab.tsx").read_text(encoding="utf-8")
    assert "<IssueTriage key={nav.key}" in triage_tab
    workbench = (web / "runs" / "[runId]" / "qa" / "qa-workbench.tsx").read_text(encoding="utf-8")
    assert 't.scope !== "TABLE"' in workbench
    panel = (web / "runs" / "[runId]" / "qa" / "jira-panel.tsx").read_text(encoding="utf-8")
    assert "useSeq" in panel and "seq.current(n)" in panel
    inbox = (web / "qa" / "inbox-tab.tsx").read_text(encoding="utf-8")
    assert "idempotency_key: bulkKey" in inbox


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def api(monkeypatch):
    importlib.import_module("app.main")
    import app.jira_api as module

    def use(client):
        monkeypatch.setattr(module, "_client", lambda db: (client, dict(ji.CONN), dict(ji.CFG)))
        return module
    return use


@pytest.fixture
def api_bug(monkeypatch):
    importlib.import_module("app.main")
    import app.jira_api as module

    fake = type("Run", (), {"latest_table": staticmethod(lambda query, table_id, suite_id=None: ({"qa_run_id": "q1"}, [dict(ji.RESULT)]))})
    monkeypatch.setitem(sys.modules, "services.qa.run", fake)
    monkeypatch.setattr(module, "_gone_for_everyone", lambda cfg, key: False)

    def use(Jira):
        monkeypatch.setattr(module, "_client", lambda db: (Jira, dict(ji.CONN), dict(ji.CFG)))
        return module, Jira
    return use
