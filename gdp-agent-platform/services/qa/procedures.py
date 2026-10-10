"""CONTRACT.QA_SUITE / QA_ASK / QA_SAVE: functional test SQL for a run's target model; QA_TABLE_SUITE /
QA_TABLE_ASK / QA_TABLE_SAVE: the same for a domain target table, with or without a run.

The suite is derived from the current STTM on demand; testers save AI-written or edited queries. Nothing here runs a
test against data: queries are validated by the read-only guard and compiled with EXPLAIN, then handed to the tester.
Table tests (SCOPE 'TABLE') belong to a target table and a suite in CONTRACT.QA_TEST_SUITE, not to a run.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from services.common.audit import record_cost, tool_call
from services.common.llm import complete_json
from services.common.sql import clip, insert_rows, rows, variant
from services.qa.guard import check
from services.qa.tests import CATEGORIES, build_suite, ident, script

# categories the AI may add on top of the generated suite
AI_CATEGORIES = ("BUSINESS_RULE", "EDGE_CASE")
ALL_CATEGORIES = list(CATEGORIES) + list(AI_CATEGORIES) + ["CUSTOM"]
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
EXPECTED_RULE = ("`expected` must be machine-checkable: either '0 rows' (the query lists the violating rows, LIMIT 100) "
                 "or '<column> = 0' naming a numeric column of a single-row result (e.g. 'difference = 0').")
DEFAULT_SUITE = "Default"

ASK_SCHEMA = {
    "type": "object",
    "required": ["title", "category", "objective", "sql", "expected", "assumptions"],
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "category": {"type": "string", "enum": ALL_CATEGORIES},
        "objective": {"type": "string"},
        "sql": {"type": "string", "description": "One Snowflake SELECT (WITH allowed) using fully qualified table names"},
        "expected": {"type": "string", "description": "What a passing result looks like, e.g. '0 rows'"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
}


PLAN_SCHEMA = {
    "type": "object",
    "required": ["tests"],
    "additionalProperties": False,
    "properties": {
        "tests": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "category", "severity", "objective", "sql", "expected", "why"],
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "category": {"type": "string", "enum": ALL_CATEGORIES},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "target_column": {"type": "string"},
                    "objective": {"type": "string"},
                    "sql": {"type": "string"},
                    "expected": {"type": "string"},
                    "why": {"type": "string", "description": "The business rule, profile fact or check it comes from"},
                },
            },
        },
    },
}


def knowledge(session, domain_id: Optional[str], run_id: Optional[str],
              profile_run_id: Optional[str] = None) -> Dict[str, Any]:
    """What the domain already knows, for AI-written tests: rules, glossary, exceptions and proven tests; the
    profile facts of the source columns; and the approved data quality checks. Usage is recorded against run_id;
    the profile and checks come from profile_run_id (the run of a table's STTM), else run_id, else are skipped."""
    out: Dict[str, Any] = {"rules": [], "profile": [], "checks": []}
    facts_run = profile_run_id or run_id
    if domain_id:
        try:
            from services.knowledge.writer import NOT_OPERATIONAL_SQL

            found = rows(session, f"""
                SELECT KNOWLEDGE_ID, KNOWLEDGE_TYPE, TITLE, CONTENT FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                 WHERE DOMAIN_ID = ? AND IS_CURRENT AND COALESCE(STATUS, 'ACTIVE') = 'ACTIVE'
                   AND KNOWLEDGE_TYPE IN ('BUSINESS_RULE', 'GLOSSARY', 'TRANSFORMATION_RULE', 'EXCEPTION', 'QA_TEST')
                   AND {NOT_OPERATIONAL_SQL}
                 ORDER BY IFF(KNOWLEDGE_TYPE = 'BUSINESS_RULE', 0, 1), UPDATED_AT DESC NULLS LAST LIMIT 30""", [domain_id])
            out["rules"] = [f"[{r['KNOWLEDGE_TYPE']}] {r['TITLE']}: {clip(r['CONTENT'], 300)}" for r in found]
            from services.knowledge.writer import record_usage

            record_usage(session, run_id, "QA", [r["KNOWLEDGE_ID"] for r in found])
        except Exception:
            pass
    if not facts_run:
        return out
    try:
        out["profile"] = [
            f"{r['TABLE_NAME']}.{r['COLUMN_NAME']} {r['DATA_TYPE']} semantic={r['SEMANTIC_TYPE']} "
            f"nulls={r['NULL_PERCENTAGE']}% distinct={r['DISTINCT_PERCENTAGE']}%"
            + (" key" if r["POTENTIAL_KEY_FLAG"] else "") + (f" PII={r['PII_CLASSIFICATION']}" if r["PII_CLASSIFICATION"] not in (None, "NONE") else "")
            for r in rows(session, """SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, SEMANTIC_TYPE, NULL_PERCENTAGE,
                                             DISTINCT_PERCENTAGE, POTENTIAL_KEY_FLAG, PII_CLASSIFICATION
                                        FROM PROFILE.PROFILE_REGISTRY WHERE RUN_ID = ? AND IS_CURRENT
                                       ORDER BY TABLE_NAME, COLUMN_NAME LIMIT 120""", [facts_run])]
    except Exception:
        pass
    try:
        out["checks"] = [f"{r['TARGET_COLUMN'] or 'table'}: {r['CHECK_TYPE']} {clip(r['DEF'], 160)}" for r in rows(session, """
            SELECT TARGET_COLUMN, CHECK_TYPE, CHECK_DEFINITION::VARCHAR AS DEF FROM CONTRACT.SODA_EXPECTATION_REGISTRY
             WHERE RUN_ID = ? AND IS_CURRENT AND STATUS = 'APPROVED' ORDER BY TARGET_COLUMN LIMIT 60""", [facts_run])]
    except Exception:
        pass
    return out


def _fqn(row: Dict[str, Any], prefix: str) -> str:
    return ".".join(str(row[k]) for k in (f"{prefix}_DATABASE", f"{prefix}_SCHEMA", f"{prefix}_TABLE") if row.get(k))


def _lines(session, sttm_id: str) -> List[Dict[str, Any]]:
    return [{
        "target_column": r["TARGET_COLUMN"], "target_datatype": r["TARGET_DATATYPE"],
        "source_table": r["SOURCE_TABLE"], "source_column": r["SOURCE_COLUMN"], "mapping_type": r["MAPPING_TYPE"],
        "transformation": r["TRANSFORMATION"], "nullable_rule": r["NULLABLE_RULE"],
        "accepted_values": variant(r["ACCEPTED_VALUES"]) or [], "default_value": r["DEFAULT_VALUE"],
        "business_definition": r["BUSINESS_DEFINITION"], "target_fqn": _fqn(r, "TARGET"), "source_fqn": _fqn(r, "SOURCE"),
    } for r in rows(session, """SELECT * FROM CONTRACT.STTM_LINE WHERE STTM_ID = ? ORDER BY TARGET_COLUMN""", [sttm_id])]


def _build(session, sttm: Optional[Dict[str, Any]], registry: Optional[Dict[str, Any]] = None,
           target_columns: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The test context, shared by the run path (an STTM; the target registry row is looked up) and the table path
    (the registry row and its columns; an STTM when the table has one). On the table path the registry is the
    authority for the target location and business keys."""
    table_path = registry is not None
    design = (variant(sttm["TABLE_DESIGN"]) or {}) if sttm else {}
    lines = _lines(session, sttm["STTM_ID"]) if sttm else []
    target_fqn = next((l["target_fqn"] for l in lines if l["target_fqn"].count(".") == 2), "")
    spec: Dict[str, Any] = {}
    if registry is None and sttm:
        try:
            found = rows(session, """SELECT TARGET_DATABASE, TARGET_SCHEMA, TARGET_TABLE, MODEL_SPEC
                                      FROM KNOWLEDGE.TARGET_TABLE_REGISTRY WHERE TARGET_TABLE_ID = ?""", [sttm["TARGET_TABLE_ID"]])
            registry = found[0] if found else None
        except Exception:
            registry = None
    if registry:
        spec = variant(registry.get("MODEL_SPEC")) or {}
        registered = _fqn(registry, "TARGET")
        target_fqn = (registered or target_fqn) if table_path else (target_fqn or registered)
    if table_path and not lines:
        # no STTM: the registered target columns stand in for the lines (prompt text and the compile stand-in)
        lines = [{"target_column": c["name"], "target_datatype": c.get("data_type"), "source_table": None,
                  "source_column": None, "mapping_type": "TARGET_ONLY", "transformation": None,
                  "nullable_rule": None, "accepted_values": c.get("accepted_values") or [], "default_value": None,
                  "business_definition": c.get("definition"), "target_fqn": target_fqn, "source_fqn": ""}
                 for c in target_columns or []]
    source_run = sttm.get("RUN_ID") if sttm else None
    sources: Dict[str, str] = {}
    columns: Dict[str, List[str]] = {}
    if source_run:
        for r in rows(session, """SELECT SOURCE_TABLE, LANDING_DATABASE, LANDING_SCHEMA, LANDING_TABLE
                                    FROM SOURCE.LANDING_TABLE_REGISTRY WHERE RUN_ID = ? AND INGESTION_STATUS = 'COMPLETE'
                                  QUALIFY ROW_NUMBER() OVER (PARTITION BY SOURCE_TABLE ORDER BY CREATED_AT DESC) = 1""",
                      [source_run]):
            sources[str(r["SOURCE_TABLE"]).upper()] = ".".join(
                ident(str(r[k])) for k in ("LANDING_DATABASE", "LANDING_SCHEMA", "LANDING_TABLE"))
    for l in lines:
        table = str(l.get("source_table") or "").upper()
        if table and table not in sources and l["source_fqn"].count(".") == 2:
            sources[table] = l["source_fqn"]
    if source_run:
        for c in rows(session, """SELECT T.SOURCE_TABLE, C.COLUMN_NAME, C.DATA_TYPE
                                    FROM SOURCE.LANDING_TABLE_REGISTRY T
                                    JOIN SOURCE.LANDING_COLUMN_REGISTRY C ON C.LANDING_ID = T.LANDING_ID
                                   WHERE T.RUN_ID = ? AND T.INGESTION_STATUS = 'COMPLETE'
                                 QUALIFY DENSE_RANK() OVER (PARTITION BY T.SOURCE_TABLE ORDER BY T.CREATED_AT DESC) = 1
                                   ORDER BY T.SOURCE_TABLE, C.ORDINAL_POSITION""", [source_run]):
            columns.setdefault(str(c["SOURCE_TABLE"]).upper(), []).append(f"{c['COLUMN_NAME']} {c['DATA_TYPE']}")
    name = target_fqn.split(".")[-1] if target_fqn else (design.get("target_table") or "TARGET")
    allowed = list(sources.values()) + ([target_fqn] if target_fqn else [])
    if spec.get("role") == "spoke" and spec.get("hub") and target_fqn.count(".") == 2:
        allowed.append(".".join(target_fqn.split(".")[:2] + [str(spec["hub"]).upper()]))
    keys = [str(k).upper() for k in design.get("business_keys") or []]
    if table_path:
        keys = ([str(k).upper() for k in variant(registry.get("BUSINESS_KEYS")) or []] or keys
                or [str(c["name"]).upper() for c in target_columns or [] if c.get("is_key")])
    return {"sttm_id": sttm["STTM_ID"] if sttm else None,
            "domain_id": (sttm or {}).get("DOMAIN_ID") or (registry or {}).get("DOMAIN_ID"),
            "run_id": source_run, "target_table_id": (sttm or {}).get("TARGET_TABLE_ID") or (registry or {}).get("TARGET_TABLE_ID"),
            "design": design, "lines": lines, "spec": spec, "sources": sources,
            "source_columns": columns, "target": {"fqn": target_fqn, "name": name}, "business_keys": keys,
            "graph": design.get("join_graph") or {}, "allowed": allowed, "columns": target_columns or []}


def run_sttm(session, run_id: str) -> Dict[str, Any]:
    """The run's current STTM (REVIEW or APPROVED)."""
    sttm = rows(session, """SELECT STTM_ID, RUN_ID, TARGET_TABLE_ID, TABLE_DESIGN, DOMAIN_ID FROM CONTRACT.STTM_REGISTRY
                            WHERE RUN_ID = ? AND STATUS IN ('REVIEW', 'APPROVED')
                            ORDER BY STTM_VERSION DESC LIMIT 1""", [run_id])
    assert sttm, "no STTM for this run yet; generate it before preparing QA tests"
    return sttm[0]


def context(session, run_id: str) -> Dict[str, Any]:
    """Everything a test needs: target and source locations, STTM lines, keys, join plan and the domain spec."""
    return _build(session, run_sttm(session, run_id))


def _test(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "test_id": r["TEST_ID"], "category": r["CATEGORY"], "title": r["TITLE"], "objective": r["OBJECTIVE"],
        "sql": r["SQL_TEXT"], "expected": r["EXPECTED"], "severity": r["SEVERITY"], "target_column": r["TARGET_COLUMN"],
        "origin": r["ORIGIN"], "prompt": r["PROMPT"], "created_by": r["CREATED_BY"],
        "created_at": str(r["CREATED_AT"])[:19] if r.get("CREATED_AT") else None,
        "scope": r.get("SCOPE") or "RUN", "suite_id": r.get("SUITE_ID"), "target_table_id": r.get("TARGET_TABLE_ID"),
    }


def _saved(session, run_id: str) -> List[Dict[str, Any]]:
    return [_test(r) for r in rows(session, """SELECT * FROM CONTRACT.QA_TEST_CASE WHERE RUN_ID = ? AND NOT IS_DELETED
                                                ORDER BY CREATED_AT""", [run_id])]


def table_saved(session, target_table_id: str, suite_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Saved TABLE-scope tests of a target table (optionally of one suite), not deleted."""
    return [_test(r) for r in rows(session, """SELECT * FROM CONTRACT.QA_TEST_CASE
                                                WHERE SCOPE = 'TABLE' AND TARGET_TABLE_ID = ? AND NOT COALESCE(IS_DELETED, FALSE)
                                                  AND (? = '' OR SUITE_ID = ?)
                                                ORDER BY CREATED_AT""", [target_table_id, suite_id or "", suite_id or ""])]


def _payload(ctx: Dict[str, Any], tests: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"target": ctx["target"], "sources": ctx["sources"], "business_keys": ctx["business_keys"],
            "driving_table": ctx["graph"].get("driving_table"), "tests": tests,
            "script": script(ctx["target"]["name"], tests),
            "counts": {c: sum(1 for t in tests if t["category"] == c) for c in ALL_CATEGORIES}}


def _generated(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    names = {t: [c.split(" ")[0] for c in cols] for t, cols in ctx["source_columns"].items()}
    return build_suite(ctx["target"], ctx["sources"], ctx["lines"], ctx["business_keys"], ctx["graph"], ctx["spec"],
                       names)


def qa_suite(session, run_id: str) -> Dict[str, Any]:
    ctx = context(session, run_id)
    if not ctx["target"]["fqn"]:
        raise AssertionError("the STTM has no target location; register the target table first")
    generated = _generated(ctx)
    try:
        saved = _saved(session, run_id)
    except Exception:
        saved = []
    # the domain suite of the run's target runs with the run's QA, so the workbench lists it (scope TABLE)
    try:
        saved += table_saved(session, ctx["target_table_id"]) if ctx.get("target_table_id") else []
    except Exception:
        pass
    return _payload(ctx, generated + saved)


def _facts(known: Optional[Dict[str, Any]]) -> str:
    if not known:
        return ""
    parts = []
    if known.get("rules"):
        parts.append("Domain knowledge (business rules, glossary, known exceptions, proven tests):\n"
                     + "\n".join(f"- {r}" for r in known["rules"]))
    if known.get("profile"):
        parts.append("Source column profile:\n" + "\n".join(f"- {r}" for r in known["profile"]))
    if known.get("checks"):
        parts.append("Approved data quality checks (already covered, do not repeat):\n"
                     + "\n".join(f"- {r}" for r in known["checks"]))
    if known.get("code"):
        parts.append(known["code"])
    return "\n\n".join(parts) + "\n\n" if parts else ""


def _code(session, ctx: Dict[str, Any], run_id: Optional[str]) -> Dict[str, Any]:
    """The client's existing tests, schema files and models for this target (empty without connected repositories)."""
    from services.code.context import for_session, record_usage

    code = for_session(session, stage="QA", domain_id=ctx.get("domain_id"), target=(ctx.get("target") or {}).get("name"),
                       sources=list((ctx.get("sources") or {}).keys()),
                       columns=[l["target_column"] for l in ctx.get("lines") or []])
    record_usage(lambda sql, params: rows(session, sql, params), run_id, "QA", code["citations"])
    return code


def table_context(ctx: Dict[str, Any]) -> str:
    """The run's target, sources, join plan, STTM and allowed tables, as prompt text (shared by QA ask and Jira triage)."""
    lines = "\n".join(
        f"- {l['target_column']} ({l['target_datatype']}) <- "
        + (f"{l['source_table']}.{l['source_column']}" if l.get("source_column") else "no source")
        + f" [{l['mapping_type']}]" + (f" rule: {clip(l['transformation'], 200)}" if l.get("transformation") else "")
        for l in ctx["lines"])
    sources = "\n".join(f"- {fqn}  ({', '.join(ctx['source_columns'].get(t, [])[:60])})" for t, fqn in ctx["sources"].items())
    joins = "\n".join(f"- {j.get('left_table')} {j.get('join_type', 'LEFT')} JOIN {j.get('right_table')} ON {', '.join(j.get('keys') or [])}"
                      for j in ctx["graph"].get("joins") or []) or "- single source table"
    return (
        f"Target table: {ctx['target']['fqn']}\nBusiness keys: {', '.join(ctx['business_keys']) or 'none recorded'}\n"
        f"Source tables:\n{sources}\nJoin plan (driving table {ctx['graph'].get('driving_table') or 'n/a'}):\n{joins}\n"
        f"STTM (target column <- source):\n{lines}\n\n"
        f"Allowed tables: {', '.join(ctx['allowed'])}\n\n"
    )


def ask_prompt(ctx: Dict[str, Any], question: str, known: Optional[Dict[str, Any]] = None) -> str:
    return (
        "You are a data QA engineer. Write ONE Snowflake SQL query that tests the requirement below.\n"
        "Rules: a single SELECT (WITH allowed); never modify data; use only these fully qualified tables; quote "
        "nothing unless needed; list failing rows with LIMIT 100 or return counts that a tester can compare; "
        f"say in `expected` what a passing result looks like. {EXPECTED_RULE}\n\n"
        + table_context(ctx)
        + _facts(known)
        + f"Tester request: {question.strip()[:2000]}"
    )


def stand_in(ctx: Dict[str, Any]) -> str:
    """Typed, empty inline view with the STTM target columns: lets EXPLAIN compile a test before the model exists."""
    cols = [f"NULL::{l['target_datatype'] or 'VARCHAR'} AS {ident(str(l['target_column']))}" for l in ctx["lines"]]
    return "(SELECT " + ", ".join(cols or ["NULL AS X"]) + " WHERE FALSE)"


def missing_target(error: str, target: str) -> bool:
    """The error says the target table, or its schema or database, is not there yet (the model is not built)."""
    text = error.upper()
    parts = [p.strip('"').upper() for p in target.split(".")]
    if "DOES NOT EXIST" not in text or len(parts) != 3:
        return False
    return ".".join(parts) in text or f"SCHEMA '{parts[0]}.{parts[1]}'" in text or f"DATABASE '{parts[0]}'" in text


def compile_check(session, sql: str, ctx: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """(error, note). EXPLAIN reads no data. When the target model is not built yet, it compiles against the
    STTM columns instead and says so."""
    try:
        rows(session, f"EXPLAIN USING TEXT {sql}")
        return None, None
    except Exception as exc:
        error = clip(exc, 400)
    target = ctx["target"]["fqn"]
    if target and missing_target(error, target):
        pattern = re.compile(r"\b" + r"\.".join(f'"?{re.escape(p)}"?' for p in target.split(".")) + r"\b", re.I)
        try:
            rows(session, "EXPLAIN USING TEXT " + pattern.sub(stand_in(ctx), sql))
            return None, "The target model is not built yet; compiled against the STTM target columns."
        except Exception as exc:
            return clip(exc, 400), None
    return error, None


def _ask(session, ctx: Dict[str, Any], run_id: Optional[str], question: str) -> Dict[str, Any]:
    assert (question or "").strip(), "describe the test you want"
    started = time.time()
    with tool_call(session, run_id, "qa_ask", {"question": clip(question, 300),
                                               **({} if run_id else {"target_table_id": ctx.get("target_table_id")})}) as call:
        known = knowledge(session, ctx.get("domain_id"), run_id, ctx.get("run_id"))
        code = _code(session, ctx, run_id)
        known["code"] = code["text"]
        prompt = ask_prompt(ctx, question, known)
        try:
            output, usage, model = complete_json(session, prompt, ASK_SCHEMA, max_tokens=2500, stage="QA")
        except AssertionError:
            output, usage, model = complete_json(session, prompt, ASK_SCHEMA, max_tokens=4000, stage="QA")
        record_cost(session, run_id, "QA", model, usage, int((time.time() - started) * 1000), tool_calls=1)
        ok, problems, sql = check(output.get("sql") or "", ctx["allowed"])
        compile_error, note = compile_check(session, sql, ctx) if ok else (None, None)
        call.summary = "valid" if ok and not compile_error else "rejected"
    return {**output, "sql": sql, "valid": ok and not compile_error, "problems": problems,
            "compile_error": compile_error, "note": note, "model": model, "code_citations": code["citations"]}


def qa_ask(session, run_id: str, question: str) -> Dict[str, Any]:
    """Natural language to one guarded, read-only test query; compiled with EXPLAIN (no data is read)."""
    assert (question or "").strip(), "describe the test you want"
    return _ask(session, context(session, run_id), run_id, question)


def _fields(payload: Dict[str, Any]) -> Tuple[str, str]:
    category = str(payload.get("category") or "CUSTOM").upper()
    category = category if category in ALL_CATEGORIES else "CUSTOM"
    severity = str(payload.get("severity") or "MEDIUM").upper()
    severity = severity if severity in SEVERITIES else "MEDIUM"
    return category, severity


def qa_save(session, run_id: str, payload_json: str) -> Dict[str, Any]:
    payload = json.loads(payload_json or "{}")
    ctx = context(session, run_id)
    ok, problems, sql = check(payload.get("sql") or "", ctx["allowed"])
    assert ok, "; ".join(problems)
    category, severity = _fields(payload)
    test_id = str(uuid.uuid4())
    insert_rows(session, "CONTRACT.QA_TEST_CASE",
                ["TEST_ID", "RUN_ID", "STTM_ID", "CATEGORY", "TITLE", "OBJECTIVE", "SQL_TEXT", "EXPECTED", "SEVERITY",
                 "TARGET_COLUMN", "ORIGIN", "PROMPT"],
                ["?", "?", "?", "?", "?", "NULLIF(?, '')", "?", "NULLIF(?, '')", "?", "NULLIF(?, '')", "?", "NULLIF(?, '')"],
                [[test_id, run_id, ctx["sttm_id"], category, clip(payload.get("title") or "Custom test", 500),
                  clip(payload.get("objective"), 4000), sql + ";", clip(payload.get("expected"), 1000),
                  severity, payload.get("target_column"),
                  "AI" if payload.get("prompt") else "USER", clip(payload.get("prompt"), 4000)]])
    return {"test_id": test_id, "saved": True}


def qa_update(session, run_id: str, test_id: str, payload_json: str) -> Dict[str, Any]:
    """Edit a saved test in place (SQL, title, expected, severity); the guard checks the new SQL again."""
    payload = json.loads(payload_json or "{}")
    ctx = context(session, run_id)
    ok, problems, sql = check(payload.get("sql") or "", ctx["allowed"])
    assert ok, "; ".join(problems)
    severity = str(payload.get("severity") or "MEDIUM").upper()
    severity = severity if severity in SEVERITIES else "MEDIUM"
    compile_error, note = compile_check(session, sql, ctx)
    assert not compile_error, f"the query does not compile: {compile_error}"
    session.sql("""UPDATE CONTRACT.QA_TEST_CASE
                      SET SQL_TEXT = ?, TITLE = ?, EXPECTED = NULLIF(?, ''), OBJECTIVE = NULLIF(?, ''), SEVERITY = ?
                    WHERE RUN_ID = ? AND TEST_ID = ? AND NOT IS_DELETED""",
                params=[sql + ";", clip(payload.get("title") or "Custom test", 500), clip(payload.get("expected"), 1000),
                        clip(payload.get("objective"), 4000), severity, run_id, test_id]).collect()
    return {"test_id": test_id, "updated": True, "note": note}


def plan_prompt(ctx: Dict[str, Any], known: Dict[str, Any], existing: List[str], focus: str) -> str:
    return (
        ask_prompt(ctx, "", known).rsplit("Tester request:", 1)[0]
        .replace("Write ONE Snowflake SQL query that tests the requirement below.", "Write Snowflake SQL test queries.")
        + "Task: propose 4 to 8 NEW functional tests for this model that the generated suite does not already cover. "
        "Prefer, in order: business rules from the domain knowledge (category BUSINESS_RULE), edge cases such as "
        "late or duplicate records, nulls in keys, boundary dates, negative amounts, orphan references and SCD history "
        "(category EDGE_CASE), then anything a careful tester would add. Each test is one read-only SELECT over the "
        f"allowed tables. {EXPECTED_RULE} Set severity CRITICAL only for grain or money/regulatory rules. In `why`, "
        "name the rule, profile fact or column it comes from.\n\n"
        "Already in the suite (do not repeat):\n" + "\n".join(f"- {t}" for t in existing[:80])
        + (f"\n\nTester focus: {focus.strip()[:1000]}" if focus.strip() else "")
    )


def _plan(session, ctx: Dict[str, Any], run_id: Optional[str], existing: List[Dict[str, Any]],
          focus: str) -> Dict[str, Any]:
    known = knowledge(session, ctx.get("domain_id"), run_id, ctx.get("run_id"))
    code = _code(session, ctx, run_id)
    known["code"] = code["text"]
    started = time.time()
    with tool_call(session, run_id, "qa_plan", {"focus": clip(focus, 300)}) as call:
        output, usage, model = complete_json(session, plan_prompt(ctx, known, [t["title"] for t in existing], focus),
                                             PLAN_SCHEMA, max_tokens=6000, stage="QA")
        record_cost(session, run_id, "QA", model, usage, int((time.time() - started) * 1000), tool_calls=1)
        proposals = []
        for t in (output.get("tests") or [])[:8]:
            ok, problems, sql = check(t.get("sql") or "", ctx["allowed"])
            compile_error, note = compile_check(session, sql, ctx) if ok else (None, None)
            proposals.append({**t, "sql": sql, "valid": ok and not compile_error, "problems": problems,
                              "compile_error": compile_error, "note": note})
        call.summary = f"{sum(p['valid'] for p in proposals)}/{len(proposals)} valid"
    return {"tests": proposals, "model": model, "code_citations": code["citations"],
            "grounding": {"rules": len(known["rules"]), "profile_columns": len(known["profile"]),
                          "checks": len(known["checks"]), "code": len(code["citations"])}}


def qa_plan(session, run_id: str, focus: str = "") -> Dict[str, Any]:
    """An AI test plan grounded in domain knowledge, the profile and the approved checks. Every proposed test is
    checked by the read-only guard and compiled with EXPLAIN; nothing is saved until a tester keeps it."""
    ctx = context(session, run_id)
    generated = _generated(ctx)
    try:
        generated += _saved(session, run_id)
    except Exception:
        pass
    return _plan(session, ctx, run_id, generated, focus)


# ---- table scope: tests on a domain target table, with or without a run ----

def _table_ctx(session, target_table_id: str) -> Dict[str, Any]:
    from services.qa.scope import table_context as scoped

    return scoped(session, target_table_id)


def table_suite(session, target_table_id: str) -> Dict[str, Any]:
    """Generated tests (when the table has an STTM), the saved TABLE tests and the table's suites."""
    ctx = _table_ctx(session, target_table_id)
    assert ctx["target"]["fqn"], "the target table has no location; register it first"
    generated = _generated(ctx) if ctx.get("sttm_id") else []
    try:
        saved = table_saved(session, target_table_id)
    except Exception:
        saved = []
    try:
        suites = list_suites(session, target_table_id=target_table_id)
    except Exception:
        suites = []
    return {**_payload(ctx, generated + saved), "suites": suites, "target_table_id": target_table_id,
            "domain_id": ctx.get("domain_id"), "sttm_id": ctx.get("sttm_id"), "pii_basis": ctx.get("pii_basis"),
            "retired": ctx.get("retired"), "allowed": ctx["allowed"]}


def table_ask(session, target_table_id: str, question: str) -> Dict[str, Any]:
    """Natural language to one guarded, read-only test query on a target table; compiled with EXPLAIN."""
    assert (question or "").strip(), "describe the test you want"
    return _ask(session, _table_ctx(session, target_table_id), None, question)


def table_plan(session, target_table_id: str, focus: str = "") -> Dict[str, Any]:
    """An AI test plan for a target table (guarded and compiled, never saved)."""
    ctx = _table_ctx(session, target_table_id)
    existing = _generated(ctx) if ctx.get("sttm_id") else []
    try:
        existing += table_saved(session, target_table_id)
    except Exception:
        pass
    return _plan(session, ctx, None, existing, focus)


def _suite(session, suite_id: str) -> Dict[str, Any]:
    found = rows(session, """SELECT SUITE_ID, DOMAIN_ID, TARGET_TABLE_ID, NAME, IS_DEFAULT FROM CONTRACT.QA_TEST_SUITE
                              WHERE SUITE_ID = ? AND NOT COALESCE(IS_DELETED, FALSE)""", [suite_id])
    assert found, f"QA suite {suite_id} does not exist"
    return found[0]


def default_suite(session, target_table_id: str, domain_id: Optional[str]) -> str:
    """The table's default suite, created on demand."""
    found = rows(session, """SELECT SUITE_ID FROM CONTRACT.QA_TEST_SUITE
                              WHERE TARGET_TABLE_ID = ? AND IS_DEFAULT AND NOT COALESCE(IS_DELETED, FALSE)
                              ORDER BY CREATED_AT LIMIT 1""", [target_table_id])
    if found:
        return found[0]["SUITE_ID"]
    suite_id = str(uuid.uuid4())
    insert_rows(session, "CONTRACT.QA_TEST_SUITE",
                ["SUITE_ID", "DOMAIN_ID", "TARGET_TABLE_ID", "NAME", "DESCRIPTION", "IS_DEFAULT", "IS_DELETED",
                 "CREATED_BY", "CREATED_AT", "UPDATED_AT"],
                ["?", "NULLIF(?, '')", "?", "?", "NULLIF(?, '')", "TRUE", "FALSE", "CURRENT_USER()",
                 "CURRENT_TIMESTAMP()", "CURRENT_TIMESTAMP()"],
                [[suite_id, domain_id, target_table_id, DEFAULT_SUITE, "Tests saved without a suite"]])
    return suite_id


def table_save(session, target_table_id: str, payload_json: str) -> Dict[str, Any]:
    """Save a guarded TABLE-scope test into the payload's suite, else the table's default suite."""
    payload = json.loads(payload_json or "{}")
    ctx = _table_ctx(session, target_table_id)
    assert not ctx.get("retired"), "the target table is retired; reactivate it before adding tests"
    ok, problems, sql = check(payload.get("sql") or "", ctx["allowed"])
    assert ok, "; ".join(problems)
    category, severity = _fields(payload)
    suite_id = payload.get("suite_id")
    if suite_id:
        assert _suite(session, suite_id)["TARGET_TABLE_ID"] == target_table_id, "that suite belongs to another table"
    else:
        suite_id = default_suite(session, target_table_id, ctx.get("domain_id"))
    test_id = str(uuid.uuid4())
    insert_rows(session, "CONTRACT.QA_TEST_CASE",
                ["TEST_ID", "STTM_ID", "CATEGORY", "TITLE", "OBJECTIVE", "SQL_TEXT", "EXPECTED", "SEVERITY",
                 "TARGET_COLUMN", "ORIGIN", "PROMPT", "DOMAIN_ID", "TARGET_TABLE_ID", "SUITE_ID", "SCOPE", "TARGET_FQN"],
                ["?", "NULLIF(?, '')", "?", "?", "NULLIF(?, '')", "?", "NULLIF(?, '')", "?", "NULLIF(?, '')", "?",
                 "NULLIF(?, '')", "NULLIF(?, '')", "?", "?", "'TABLE'", "NULLIF(?, '')"],
                [[test_id, ctx.get("sttm_id"), category, clip(payload.get("title") or "Custom test", 500),
                  clip(payload.get("objective"), 4000), sql + ";", clip(payload.get("expected"), 1000),
                  severity, payload.get("target_column"), "AI" if payload.get("prompt") else "USER",
                  clip(payload.get("prompt"), 4000), ctx.get("domain_id"), target_table_id, suite_id,
                  ctx["target"]["fqn"]]])
    return {"test_id": test_id, "suite_id": suite_id, "saved": True}


def _table_test(session, test_id: str) -> Dict[str, Any]:
    found = rows(session, """SELECT TEST_ID, TARGET_TABLE_ID, SUITE_ID, SCOPE FROM CONTRACT.QA_TEST_CASE
                              WHERE TEST_ID = ? AND NOT COALESCE(IS_DELETED, FALSE)""", [test_id])
    assert found, f"QA test {test_id} does not exist"
    assert (found[0].get("SCOPE") or "RUN") == "TABLE", "that test belongs to a run; edit it from the run's QA tab"
    return found[0]


def update_table_test(session, test_id: str, payload_json: str) -> Dict[str, Any]:
    """Edit a saved TABLE test (SQL, title, expected, objective, severity, suite); guarded and compiled again."""
    payload = json.loads(payload_json or "{}")
    test = _table_test(session, test_id)
    ctx = _table_ctx(session, test["TARGET_TABLE_ID"])
    ok, problems, sql = check(payload.get("sql") or "", ctx["allowed"])
    assert ok, "; ".join(problems)
    _, severity = _fields(payload)
    compile_error, note = compile_check(session, sql, ctx)
    assert not compile_error, f"the query does not compile: {compile_error}"
    suite_id = payload.get("suite_id") or test.get("SUITE_ID") or ""
    if payload.get("suite_id"):
        assert _suite(session, suite_id)["TARGET_TABLE_ID"] == test["TARGET_TABLE_ID"], "that suite belongs to another table"
    session.sql("""UPDATE CONTRACT.QA_TEST_CASE
                      SET SQL_TEXT = ?, TITLE = ?, EXPECTED = NULLIF(?, ''), OBJECTIVE = NULLIF(?, ''), SEVERITY = ?,
                          SUITE_ID = NULLIF(?, '')
                    WHERE TEST_ID = ? AND SCOPE = 'TABLE' AND NOT COALESCE(IS_DELETED, FALSE)""",
                params=[sql + ";", clip(payload.get("title") or "Custom test", 500), clip(payload.get("expected"), 1000),
                        clip(payload.get("objective"), 4000), severity, suite_id, test_id]).collect()
    return {"test_id": test_id, "suite_id": suite_id or None, "updated": True, "note": note}


def delete_table_test(session, test_id: str) -> Dict[str, Any]:
    _table_test(session, test_id)
    session.sql("UPDATE CONTRACT.QA_TEST_CASE SET IS_DELETED = TRUE WHERE TEST_ID = ? AND SCOPE = 'TABLE'",
                params=[test_id]).collect()
    return {"test_id": test_id, "deleted": True}


def create_suite(session, target_table_id: str, name: str, description: Optional[str] = None) -> Dict[str, Any]:
    name = (name or "").strip()
    assert name, "name the suite"
    found = rows(session, "SELECT DOMAIN_ID FROM KNOWLEDGE.TARGET_TABLE_REGISTRY WHERE TARGET_TABLE_ID = ?",
                 [target_table_id])
    assert found, f"target table {target_table_id} does not exist"
    assert not rows(session, """SELECT 1 AS X FROM CONTRACT.QA_TEST_SUITE
                                 WHERE TARGET_TABLE_ID = ? AND UPPER(NAME) = UPPER(?) AND NOT COALESCE(IS_DELETED, FALSE)""",
                    [target_table_id, clip(name, 200)]), f"a suite named {name!r} already exists on this table"
    suite_id = str(uuid.uuid4())
    insert_rows(session, "CONTRACT.QA_TEST_SUITE",
                ["SUITE_ID", "DOMAIN_ID", "TARGET_TABLE_ID", "NAME", "DESCRIPTION", "IS_DEFAULT", "IS_DELETED",
                 "CREATED_BY", "CREATED_AT", "UPDATED_AT"],
                ["?", "NULLIF(?, '')", "?", "?", "NULLIF(?, '')", "FALSE", "FALSE", "CURRENT_USER()",
                 "CURRENT_TIMESTAMP()", "CURRENT_TIMESTAMP()"],
                [[suite_id, found[0].get("DOMAIN_ID"), target_table_id, clip(name, 200), clip(description, 4000)]])
    return {"suite_id": suite_id, "created": True}


def update_suite(session, suite_id: str, name: Optional[str] = None,
                 description: Optional[str] = None) -> Dict[str, Any]:
    """Rename or describe a suite; None keeps a field, an empty description clears it."""
    suite = _suite(session, suite_id)
    name = (name or "").strip()
    if name and name.upper() != str(suite.get("NAME") or "").upper():
        assert not rows(session, """SELECT 1 AS X FROM CONTRACT.QA_TEST_SUITE
                                     WHERE TARGET_TABLE_ID = ? AND UPPER(NAME) = UPPER(?) AND SUITE_ID <> ?
                                       AND NOT COALESCE(IS_DELETED, FALSE)""",
                        [suite["TARGET_TABLE_ID"], clip(name, 200), suite_id]), f"a suite named {name!r} already exists"
    session.sql("""UPDATE CONTRACT.QA_TEST_SUITE
                      SET NAME = COALESCE(NULLIF(?, ''), NAME),
                          DESCRIPTION = IFF(? = 'KEEP', DESCRIPTION, NULLIF(?, '')), UPDATED_AT = CURRENT_TIMESTAMP()
                    WHERE SUITE_ID = ? AND NOT COALESCE(IS_DELETED, FALSE)""",
                params=[clip(name, 200), "KEEP" if description is None else "SET", clip(description, 4000),
                        suite_id]).collect()
    return {"suite_id": suite_id, "updated": True}


def delete_suite(session, suite_id: str) -> Dict[str, Any]:
    """Soft-delete a suite and move its tests to the table's default suite; the default suite itself stays."""
    suite = _suite(session, suite_id)
    assert not suite.get("IS_DEFAULT"), "the default suite cannot be deleted"
    target = default_suite(session, suite["TARGET_TABLE_ID"], suite.get("DOMAIN_ID"))
    session.sql("""UPDATE CONTRACT.QA_TEST_CASE SET SUITE_ID = ?
                    WHERE SUITE_ID = ? AND NOT COALESCE(IS_DELETED, FALSE)""", params=[target, suite_id]).collect()
    session.sql("""UPDATE CONTRACT.QA_TEST_SUITE SET IS_DELETED = TRUE, UPDATED_AT = CURRENT_TIMESTAMP()
                    WHERE SUITE_ID = ?""", params=[suite_id]).collect()
    return {"suite_id": suite_id, "deleted": True, "moved_to": target}


def list_suites(session, target_table_id: Optional[str] = None, domain_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Suites (not deleted) with their test counts, for one table, one domain or all."""
    return [{
        "suite_id": r["SUITE_ID"], "domain_id": r.get("DOMAIN_ID"), "target_table_id": r["TARGET_TABLE_ID"],
        "name": r["NAME"], "description": r.get("DESCRIPTION"), "is_default": bool(r.get("IS_DEFAULT")),
        "tests": int(r.get("TESTS") or 0), "created_by": r.get("CREATED_BY"),
        "created_at": str(r["CREATED_AT"])[:19] if r.get("CREATED_AT") else None,
        "updated_at": str(r["UPDATED_AT"])[:19] if r.get("UPDATED_AT") else None,
    } for r in rows(session, """
        SELECT S.SUITE_ID, S.DOMAIN_ID, S.TARGET_TABLE_ID, S.NAME, S.DESCRIPTION, S.IS_DEFAULT, S.CREATED_BY,
               S.CREATED_AT, S.UPDATED_AT, COALESCE(C.N, 0) AS TESTS
          FROM CONTRACT.QA_TEST_SUITE S
          LEFT JOIN (SELECT SUITE_ID, COUNT(*) AS N FROM CONTRACT.QA_TEST_CASE
                      WHERE SCOPE = 'TABLE' AND NOT COALESCE(IS_DELETED, FALSE) GROUP BY SUITE_ID) C
            ON C.SUITE_ID = S.SUITE_ID
         WHERE NOT COALESCE(S.IS_DELETED, FALSE) AND (? = '' OR S.TARGET_TABLE_ID = ?) AND (? = '' OR S.DOMAIN_ID = ?)
         ORDER BY S.TARGET_TABLE_ID, IFF(S.IS_DEFAULT, 0, 1), S.NAME""",
        [target_table_id or "", target_table_id or "", domain_id or "", domain_id or ""])]
