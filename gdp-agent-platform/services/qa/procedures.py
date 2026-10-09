"""CONTRACT.QA_SUITE / QA_ASK / QA_SAVE: functional test SQL for a run's target model.

The suite is derived from the current STTM on demand; testers save AI-written or edited queries. Nothing here runs a
test against data: queries are validated by the read-only guard and compiled with EXPLAIN, then handed to the tester.
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


def knowledge(session, domain_id: Optional[str], run_id: str) -> Dict[str, Any]:
    """What the domain already knows, for AI-written tests: rules, glossary, exceptions and proven tests; the
    profile facts of the source columns; and the approved data quality checks."""
    out: Dict[str, Any] = {"rules": [], "profile": [], "checks": []}
    if domain_id:
        try:
            found = rows(session, """
                SELECT KNOWLEDGE_ID, KNOWLEDGE_TYPE, TITLE, CONTENT FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                 WHERE DOMAIN_ID = ? AND IS_CURRENT AND COALESCE(STATUS, 'ACTIVE') = 'ACTIVE'
                   AND KNOWLEDGE_TYPE IN ('BUSINESS_RULE', 'GLOSSARY', 'TRANSFORMATION_RULE', 'EXCEPTION', 'QA_TEST')
                 ORDER BY IFF(KNOWLEDGE_TYPE = 'BUSINESS_RULE', 0, 1), UPDATED_AT DESC NULLS LAST LIMIT 30""", [domain_id])
            out["rules"] = [f"[{r['KNOWLEDGE_TYPE']}] {r['TITLE']}: {clip(r['CONTENT'], 300)}" for r in found]
            from services.knowledge.writer import record_usage

            record_usage(session, run_id, "QA", [r["KNOWLEDGE_ID"] for r in found])
        except Exception:
            pass
    try:
        out["profile"] = [
            f"{r['TABLE_NAME']}.{r['COLUMN_NAME']} {r['DATA_TYPE']} semantic={r['SEMANTIC_TYPE']} "
            f"nulls={r['NULL_PERCENTAGE']}% distinct={r['DISTINCT_PERCENTAGE']}%"
            + (" key" if r["POTENTIAL_KEY_FLAG"] else "") + (f" PII={r['PII_CLASSIFICATION']}" if r["PII_CLASSIFICATION"] not in (None, "NONE") else "")
            for r in rows(session, """SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, SEMANTIC_TYPE, NULL_PERCENTAGE,
                                             DISTINCT_PERCENTAGE, POTENTIAL_KEY_FLAG, PII_CLASSIFICATION
                                        FROM PROFILE.PROFILE_REGISTRY WHERE RUN_ID = ? AND IS_CURRENT
                                       ORDER BY TABLE_NAME, COLUMN_NAME LIMIT 120""", [run_id])]
    except Exception:
        pass
    try:
        out["checks"] = [f"{r['TARGET_COLUMN'] or 'table'}: {r['CHECK_TYPE']} {clip(r['DEF'], 160)}" for r in rows(session, """
            SELECT TARGET_COLUMN, CHECK_TYPE, CHECK_DEFINITION::VARCHAR AS DEF FROM CONTRACT.SODA_EXPECTATION_REGISTRY
             WHERE RUN_ID = ? AND IS_CURRENT AND STATUS = 'APPROVED' ORDER BY TARGET_COLUMN LIMIT 60""", [run_id])]
    except Exception:
        pass
    return out


def context(session, run_id: str) -> Dict[str, Any]:
    """Everything a test needs: target and source locations, STTM lines, keys, join plan and the domain spec."""
    sttm = rows(session, """SELECT STTM_ID, TARGET_TABLE_ID, TABLE_DESIGN, DOMAIN_ID FROM CONTRACT.STTM_REGISTRY
                            WHERE RUN_ID = ? AND STATUS IN ('REVIEW', 'APPROVED')
                            ORDER BY STTM_VERSION DESC LIMIT 1""", [run_id])
    assert sttm, "no STTM for this run yet; generate it before preparing QA tests"
    sttm = sttm[0]
    design = variant(sttm["TABLE_DESIGN"]) or {}
    lines = [{
        "target_column": r["TARGET_COLUMN"], "target_datatype": r["TARGET_DATATYPE"],
        "source_table": r["SOURCE_TABLE"], "source_column": r["SOURCE_COLUMN"], "mapping_type": r["MAPPING_TYPE"],
        "transformation": r["TRANSFORMATION"], "nullable_rule": r["NULLABLE_RULE"],
        "accepted_values": variant(r["ACCEPTED_VALUES"]) or [], "default_value": r["DEFAULT_VALUE"],
        "business_definition": r["BUSINESS_DEFINITION"], "target_fqn": ".".join(
            str(r[k]) for k in ("TARGET_DATABASE", "TARGET_SCHEMA", "TARGET_TABLE") if r.get(k)),
        "source_fqn": ".".join(str(r[k]) for k in ("SOURCE_DATABASE", "SOURCE_SCHEMA", "SOURCE_TABLE") if r.get(k)),
    } for r in rows(session, """SELECT * FROM CONTRACT.STTM_LINE WHERE STTM_ID = ? ORDER BY TARGET_COLUMN""",
                    [sttm["STTM_ID"]])]
    target_fqn = next((l["target_fqn"] for l in lines if l["target_fqn"].count(".") == 2), "")
    spec: Dict[str, Any] = {}
    try:
        found = rows(session, """SELECT TARGET_DATABASE, TARGET_SCHEMA, TARGET_TABLE, MODEL_SPEC
                                  FROM KNOWLEDGE.TARGET_TABLE_REGISTRY WHERE TARGET_TABLE_ID = ?""", [sttm["TARGET_TABLE_ID"]])
        if found:
            spec = variant(found[0].get("MODEL_SPEC")) or {}
            target_fqn = target_fqn or ".".join(str(found[0][k]) for k in ("TARGET_DATABASE", "TARGET_SCHEMA", "TARGET_TABLE"))
    except Exception:
        pass
    sources: Dict[str, str] = {}
    for r in rows(session, """SELECT SOURCE_TABLE, LANDING_DATABASE, LANDING_SCHEMA, LANDING_TABLE
                                FROM SOURCE.LANDING_TABLE_REGISTRY WHERE RUN_ID = ? AND INGESTION_STATUS = 'COMPLETE'
                              QUALIFY ROW_NUMBER() OVER (PARTITION BY SOURCE_TABLE ORDER BY CREATED_AT DESC) = 1""", [run_id]):
        sources[str(r["SOURCE_TABLE"]).upper()] = ".".join(
            ident(str(r[k])) for k in ("LANDING_DATABASE", "LANDING_SCHEMA", "LANDING_TABLE"))
    for l in lines:
        table = str(l.get("source_table") or "").upper()
        if table and table not in sources and l["source_fqn"].count(".") == 2:
            sources[table] = l["source_fqn"]
    columns: Dict[str, List[str]] = {}
    for c in rows(session, """SELECT T.SOURCE_TABLE, C.COLUMN_NAME, C.DATA_TYPE
                                FROM SOURCE.LANDING_TABLE_REGISTRY T
                                JOIN SOURCE.LANDING_COLUMN_REGISTRY C ON C.LANDING_ID = T.LANDING_ID
                               WHERE T.RUN_ID = ? AND T.INGESTION_STATUS = 'COMPLETE'
                             QUALIFY DENSE_RANK() OVER (PARTITION BY T.SOURCE_TABLE ORDER BY T.CREATED_AT DESC) = 1
                               ORDER BY T.SOURCE_TABLE, C.ORDINAL_POSITION""", [run_id]):
        columns.setdefault(str(c["SOURCE_TABLE"]).upper(), []).append(f"{c['COLUMN_NAME']} {c['DATA_TYPE']}")
    name = target_fqn.split(".")[-1] if target_fqn else (design.get("target_table") or "TARGET")
    allowed = list(sources.values()) + ([target_fqn] if target_fqn else [])
    if spec.get("role") == "spoke" and spec.get("hub") and target_fqn.count(".") == 2:
        allowed.append(".".join(target_fqn.split(".")[:2] + [str(spec["hub"]).upper()]))
    return {"sttm_id": sttm["STTM_ID"], "domain_id": sttm.get("DOMAIN_ID"), "design": design, "lines": lines, "spec": spec, "sources": sources,
            "source_columns": columns, "target": {"fqn": target_fqn, "name": name},
            "business_keys": [str(k).upper() for k in design.get("business_keys") or []],
            "graph": design.get("join_graph") or {}, "allowed": allowed}


def _saved(session, run_id: str) -> List[Dict[str, Any]]:
    return [{
        "test_id": r["TEST_ID"], "category": r["CATEGORY"], "title": r["TITLE"], "objective": r["OBJECTIVE"],
        "sql": r["SQL_TEXT"], "expected": r["EXPECTED"], "severity": r["SEVERITY"], "target_column": r["TARGET_COLUMN"],
        "origin": r["ORIGIN"], "prompt": r["PROMPT"], "created_by": r["CREATED_BY"],
        "created_at": str(r["CREATED_AT"])[:19] if r.get("CREATED_AT") else None,
    } for r in rows(session, """SELECT * FROM CONTRACT.QA_TEST_CASE WHERE RUN_ID = ? AND NOT IS_DELETED
                                 ORDER BY CREATED_AT""", [run_id])]


def qa_suite(session, run_id: str) -> Dict[str, Any]:
    ctx = context(session, run_id)
    if not ctx["target"]["fqn"]:
        raise AssertionError("the STTM has no target location; register the target table first")
    names = {t: [c.split(" ")[0] for c in cols] for t, cols in ctx["source_columns"].items()}
    generated = build_suite(ctx["target"], ctx["sources"], ctx["lines"], ctx["business_keys"], ctx["graph"], ctx["spec"],
                            names)
    try:
        saved = _saved(session, run_id)
    except Exception:
        saved = []
    tests = generated + saved
    return {"target": ctx["target"], "sources": ctx["sources"], "business_keys": ctx["business_keys"],
            "driving_table": ctx["graph"].get("driving_table"), "tests": tests,
            "script": script(ctx["target"]["name"], tests),
            "counts": {c: sum(1 for t in tests if t["category"] == c) for c in ALL_CATEGORIES}}


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
    return "\n\n".join(parts) + "\n\n" if parts else ""


def ask_prompt(ctx: Dict[str, Any], question: str, known: Optional[Dict[str, Any]] = None) -> str:
    lines = "\n".join(
        f"- {l['target_column']} ({l['target_datatype']}) <- "
        + (f"{l['source_table']}.{l['source_column']}" if l.get("source_column") else "no source")
        + f" [{l['mapping_type']}]" + (f" rule: {clip(l['transformation'], 200)}" if l.get("transformation") else "")
        for l in ctx["lines"])
    sources = "\n".join(f"- {fqn}  ({', '.join(ctx['source_columns'].get(t, [])[:60])})" for t, fqn in ctx["sources"].items())
    joins = "\n".join(f"- {j.get('left_table')} {j.get('join_type', 'LEFT')} JOIN {j.get('right_table')} ON {', '.join(j.get('keys') or [])}"
                      for j in ctx["graph"].get("joins") or []) or "- single source table"
    return (
        "You are a data QA engineer. Write ONE Snowflake SQL query that tests the requirement below.\n"
        "Rules: a single SELECT (WITH allowed); never modify data; use only these fully qualified tables; quote "
        "nothing unless needed; list failing rows with LIMIT 100 or return counts that a tester can compare; "
        f"say in `expected` what a passing result looks like. {EXPECTED_RULE}\n\n"
        f"Target table: {ctx['target']['fqn']}\nBusiness keys: {', '.join(ctx['business_keys']) or 'none recorded'}\n"
        f"Source tables:\n{sources}\nJoin plan (driving table {ctx['graph'].get('driving_table') or 'n/a'}):\n{joins}\n"
        f"STTM (target column <- source):\n{lines}\n\n"
        f"Allowed tables: {', '.join(ctx['allowed'])}\n\n"
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


def qa_ask(session, run_id: str, question: str) -> Dict[str, Any]:
    """Natural language to one guarded, read-only test query; compiled with EXPLAIN (no data is read)."""
    assert (question or "").strip(), "describe the test you want"
    ctx = context(session, run_id)
    started = time.time()
    with tool_call(session, run_id, "qa_ask", {"question": clip(question, 300)}) as call:
        prompt = ask_prompt(ctx, question, knowledge(session, ctx.get("domain_id"), run_id))
        try:
            output, usage, model = complete_json(session, prompt, ASK_SCHEMA, max_tokens=2500, stage="QA")
        except AssertionError:
            output, usage, model = complete_json(session, prompt, ASK_SCHEMA, max_tokens=4000, stage="QA")
        record_cost(session, run_id, "QA", model, usage, int((time.time() - started) * 1000), tool_calls=1)
        ok, problems, sql = check(output.get("sql") or "", ctx["allowed"])
        compile_error, note = compile_check(session, sql, ctx) if ok else (None, None)
        call.summary = "valid" if ok and not compile_error else "rejected"
    return {**output, "sql": sql, "valid": ok and not compile_error, "problems": problems,
            "compile_error": compile_error, "note": note, "model": model}


def qa_save(session, run_id: str, payload_json: str) -> Dict[str, Any]:
    payload = json.loads(payload_json or "{}")
    ctx = context(session, run_id)
    ok, problems, sql = check(payload.get("sql") or "", ctx["allowed"])
    assert ok, "; ".join(problems)
    category = str(payload.get("category") or "CUSTOM").upper()
    category = category if category in ALL_CATEGORIES else "CUSTOM"
    severity = str(payload.get("severity") or "MEDIUM").upper()
    severity = severity if severity in SEVERITIES else "MEDIUM"
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


def qa_plan(session, run_id: str, focus: str = "") -> Dict[str, Any]:
    """An AI test plan grounded in domain knowledge, the profile and the approved checks. Every proposed test is
    checked by the read-only guard and compiled with EXPLAIN; nothing is saved until a tester keeps it."""
    ctx = context(session, run_id)
    known = knowledge(session, ctx.get("domain_id"), run_id)
    names = {t: [c.split(" ")[0] for c in cols] for t, cols in ctx["source_columns"].items()}
    generated = build_suite(ctx["target"], ctx["sources"], ctx["lines"], ctx["business_keys"], ctx["graph"], ctx["spec"],
                            names)
    try:
        generated += _saved(session, run_id)
    except Exception:
        pass
    started = time.time()
    with tool_call(session, run_id, "qa_plan", {"focus": clip(focus, 300)}) as call:
        output, usage, model = complete_json(session, plan_prompt(ctx, known, [t["title"] for t in generated], focus),
                                             PLAN_SCHEMA, max_tokens=6000, stage="QA")
        record_cost(session, run_id, "QA", model, usage, int((time.time() - started) * 1000), tool_calls=1)
        proposals = []
        for t in (output.get("tests") or [])[:8]:
            ok, problems, sql = check(t.get("sql") or "", ctx["allowed"])
            compile_error, note = compile_check(session, sql, ctx) if ok else (None, None)
            proposals.append({**t, "sql": sql, "valid": ok and not compile_error, "problems": problems,
                              "compile_error": compile_error, "note": note})
        call.summary = f"{sum(p['valid'] for p in proposals)}/{len(proposals)} valid"
    return {"tests": proposals, "model": model,
            "grounding": {"rules": len(known["rules"]), "profile_columns": len(known["profile"]),
                          "checks": len(known["checks"])}}
