"""Knowledge the platform already has reaches the prompts: skill guidance without its front matter, knowledge picked
for the table and columns at hand, skills and search hits that used to be loaded and dropped, and code context that
used to be cut off by an earlier truncation."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.knowledge import select  # noqa: E402
from services.knowledge.usage import prompt_excerpt  # noqa: E402


class Row(dict):
    def as_dict(self):
        return dict(self)

    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else dict.__getitem__(self, key)


class Session:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def sql(self, sql, params=None):
        flat = " ".join(sql.split())
        self.calls.append((flat, list(params or [])))
        answers = self.answers

        class R:
            def collect(self_inner):
                for needle, value in answers.items():
                    if needle in flat:
                        found = value(list(params or [])) if callable(value) else value
                        return [Row(r) for r in found]
                return []
        return R()


def item(kid, title, content_json=None, kind="TRANSFORMATION_RULE"):
    return {"KNOWLEDGE_ID": kid, "KNOWLEDGE_TYPE": kind, "TITLE": title, "CONTENT": f"content of {title}",
            "CONTENT_JSON": json.dumps(content_json or {})}


# ---------------------------------------------------------------- skill excerpts


def test_prompt_excerpt_drops_front_matter_and_bundled_files():
    skill = ("---\nname: soda\ndescription: a very long description of the skill\n---\n\n# Soda\n\n"
             "## Rules\nAlways check keys.\n\n# references/contract.md\nBundled reference text")
    text = prompt_excerpt(skill)
    assert text.startswith("# Soda") and "Always check keys." in text
    assert "description:" not in text and "Bundled reference" not in text


def test_prompt_excerpt_cuts_at_a_paragraph():
    skill = "# S\n\n" + "\n\n".join("paragraph " + "x" * 300 for _ in range(10))
    text = prompt_excerpt(skill, 1000)
    assert len(text) <= 1004 and text.endswith("...") and "x\n..." in text


# ---------------------------------------------------------------- relevance


def test_the_target_columns_own_rule_beats_newer_ones():
    newest_first = [item(f"k{i}", f"Transform OTHER_{i}", {"target_column": f"OTHER_{i}", "target_table": "CUSTOMER"})
                    for i in range(12)]
    newest_first.append(item("mine", "Transform EMAIL", {"target_column": "EMAIL", "target_table": "CUSTOMER"}))
    picked = select.rank(newest_first, "CUSTOMER", ["EMAIL"], limit=8)
    assert picked[0]["KNOWLEDGE_ID"] == "mine" and len(picked) == 8


def test_items_learned_on_another_table_rank_last():
    items = [item("other", "x", {"target_column": "EMAIL", "target_table": "VENDOR"}),
             item("general", "y", {}),
             item("ours", "z", {"target_table": "DB.SILVER.CUSTOMER"})]
    picked = [r["KNOWLEDGE_ID"] for r in select.rank(items, "CUSTOMER", [], limit=3)]
    assert picked == ["ours", "general", "other"]


def test_type_order_breaks_ties():
    items = [item("t", "a", kind="TRANSFORMATION_RULE"), item("b", "b", kind="BUSINESS_RULE")]
    picked = select.rank(items, None, [], limit=2, type_order=["BUSINESS_RULE", "TRANSFORMATION_RULE"])
    assert [r["KNOWLEDGE_ID"] for r in picked] == ["b", "t"]


def test_relevant_records_usage_only_for_what_it_returns():
    found = [item(f"k{i}", f"t{i}") for i in range(5)] + [item("mine", "Transform EMAIL", {"target_column": "EMAIL"})]
    session = Session({"FROM CORE.DOMAIN_KNOWLEDGE": found})
    picked = select.relevant(session, "d1", ["TRANSFORMATION_RULE"], columns=["EMAIL"], limit=2, run_id="r1",
                             stage="STTM")
    assert picked[0]["KNOWLEDGE_ID"] == "mine"
    usage = " ".join(str(v) for s, p in session.calls if "KNOWLEDGE_USAGE" in s for v in p)
    assert "mine" in usage and "k0" in usage and "k4" not in usage


def test_no_domain_reads_nothing():
    session = Session({})
    assert select.relevant(session, None, ["TRANSFORMATION_RULE"]) == [] and not session.calls


# ---------------------------------------------------------------- stage prompts


def test_sttm_prompt_carries_the_loaded_skills_and_ranked_rules():
    from services.sttm.refine import refine_prompt

    prompt = refine_prompt({"target_column": "EMAIL", "prior_rules": [f"rule {i}" for i in range(8)],
                            "skills": "[STTM v1]\nLowercase emails."}, "clean it")
    assert "Lowercase emails." in prompt and "rule 7" in prompt


def test_soda_prompt_keeps_skills_and_code_however_much_knowledge_there_is():
    from services.soda.extract import extract_prompt

    prompt = extract_prompt("brief", "CUSTOMER", ["EMAIL"], [f"pattern {i}" for i in range(30)],
                            skills="[SODA v1]\nUse missing_count.", code="EXISTING CODE: models/customer.sql")
    assert "Use missing_count." in prompt and "EXISTING CODE" in prompt
    assert "pattern 11" in prompt and "pattern 12" not in prompt


def test_mapping_adjudication_sees_glossary_rules_and_search_hits(monkeypatch):
    from services.mapping import procedures as mp

    seen = {}

    def fake(session, prompt, schema, **kw):
        seen["prompt"] = prompt
        return {"columns": []}, {}, "m"

    monkeypatch.setattr(mp, "complete_json", fake)
    monkeypatch.setattr(mp, "record_cost", lambda *a, **k: None)
    source = {"column_name": "CUST_MAIL", "data_type": "VARCHAR", "semantic_type": "EMAIL", "null_percentage": 0,
              "distinct_percentage": 99}
    ranked = [{"target": {"column_name": "EMAIL", "definition": "Email"}, "final_score": 0.7}]
    knowledge = {"glossary": {"EMAIL": {"target_column": "EMAIL", "definition": "Primary contact address",
                                        "synonyms": ["MAIL", "E_MAIL"]}},
                 "rules": {"EMAIL": {"rule": "lower-case before loading"}}}
    hits = {"CUST_MAIL": [{"TITLE": "Mail columns", "CONTENT": "CRM mail fields hold the primary address"}]}
    mp._adjudicate(None, "r1", [(source, ranked)], knowledge=knowledge, hits=hits)
    prompt = seen["prompt"]
    assert "Primary contact address" in prompt and "E_MAIL" in prompt and "lower-case before loading" in prompt
    assert "CRM mail fields hold the primary address" in prompt


def test_mapping_assist_prompt_has_prior_decisions_and_target_knowledge():
    from services.mapping.assist import build_prompt

    targets = [{"column_name": "EMAIL", "target_column_id": "t1", "nullable": True}]
    prompt = build_prompt([], {}, targets, "CUSTOMER", {}, "", ["CUST_MAIL -> EMAIL: confirmed by steward"],
                          {"EMAIL": "glossary: Primary contact address"})
    assert "confirmed by steward" in prompt and "Primary contact address" in prompt


def test_dbt_review_and_enhance_keep_the_code_after_long_rules():
    from services.dbt.enhance import enhance_prompt
    from services.dbt.review import review_prompt

    long = "R" * 20000
    review = review_prompt("m.sql", "select 1", long, "ctx", "notes", code="EXISTING: conventions here")
    assert "EXISTING: conventions here" in review
    enhance = enhance_prompt("m.sql", "select 1", "add a column", context="C" * 20000, rules="RULES HERE",
                             code="EXISTING: conventions here")
    assert "RULES HERE" in enhance and "EXISTING: conventions here" in enhance


def test_qa_focus_reads_the_target_and_its_columns():
    from services.qa.procedures import focus

    ctx = {"target": {"name": "CUSTOMER"}, "lines": [{"target_column": "EMAIL"}], "columns": [{"name": "ID"}]}
    assert focus(ctx) == {"target": "CUSTOMER", "columns": ["EMAIL", "ID"]}


# ---------------------------------------------------------------- open items: suggestions, profiling, glossary


def test_mapping_glossary_is_scoped_to_the_target_table():
    from services.mapping import procedures as mp

    rows = [
        {"KNOWLEDGE_ID": "g1", "KNOWLEDGE_TYPE": "GLOSSARY",
         "CONTENT_JSON": json.dumps({"target_column": "STATUS", "target_table": "INVOICE", "definition": "invoice status"})},
        {"KNOWLEDGE_ID": "g2", "KNOWLEDGE_TYPE": "GLOSSARY",
         "CONTENT_JSON": json.dumps({"target_column": "STATUS", "target_table": "CUSTOMER", "definition": "customer status"})},
        {"KNOWLEDGE_ID": "g3", "KNOWLEDGE_TYPE": "GLOSSARY",
         "CONTENT_JSON": json.dumps({"target_column": "STATUS", "definition": "domain-wide status"})},
        {"KNOWLEDGE_ID": "g4", "KNOWLEDGE_TYPE": "GLOSSARY",
         "CONTENT_JSON": json.dumps({"target_column": "NAME", "definition": "domain-wide name"})},
    ]
    session = Session({"FROM CORE.DOMAIN_KNOWLEDGE": rows})
    known = mp.domain_knowledge(session, "d1", "r1", {"TARGET_TABLE": "CUSTOMER", "TARGET_TABLE_ID": "t1"})
    assert known["glossary"]["STATUS"]["definition"] == "customer status"
    assert known["glossary"]["NAME"]["definition"] == "domain-wide name"
    used = " ".join(str(v) for s, p in session.calls if "KNOWLEDGE_USAGE" in s for v in p)
    assert "g1" not in used  # the other table's term is not reported as used


def test_ai_suggestions_see_accepted_rules(monkeypatch):
    from services.common import suggestion_stages as ss

    monkeypatch.setattr(ss, "_run", lambda session, run_id: {"RUN_ID": run_id, "DOMAIN_ID": "d1", "SOURCE_SYSTEM_ID": "s1"})
    monkeypatch.setattr(ss, "_profiles", lambda session, run_id: {"CRM": [{"column": "EMAIL"}]})
    rules = [{"CONTENT_JSON": json.dumps({"source_system_id": "s1", "source_table": "CRM", "column_name": "EMAIL",
                                          "pii_classification": "EMAIL"})}]
    session = Session({"KNOWLEDGE_TYPE = 'COLUMN_RULE'": rules})
    (scope, context), = ss.contexts(session, "PROFILING", "r1")
    assert context["accepted_column_rules"] == [{"column_name": "EMAIL", "pii_classification": "EMAIL"}]


def test_ai_suggestion_prompt_says_to_follow_accepted_rules():
    from services.common.suggestion_stages import SPECS

    prompt = SPECS["STTM"].prompt({"known_rules": ["[TRANSFORMATION_RULE] Transform EMAIL: lower()"]}, [])
    assert "never suggest the opposite" in prompt and "Transform EMAIL" in prompt


def test_in_place_profiles_apply_accepted_column_rules(monkeypatch):
    from services.profiling import procedures as pp

    ref = pp.TableRef("CRM", "DB", "SCH", "CUSTOMER", "DB.SCH.CUSTOMER", 10, (("EMAIL", "VARCHAR"),))
    column = {"column_name": "EMAIL", "semantic_type": "TEXT", "pii_classification": "NONE",
              "sample_values": [{"value": "a@b.com"}], "statistics": {}}
    monkeypatch.setattr(pp, "ensure_active", lambda session: None)
    monkeypatch.setattr(pp, "_load_index", lambda session, tables: None)
    monkeypatch.setattr(pp, "_compute", lambda session, misses, limit: {ref.key: ([column], 10)})
    monkeypatch.setattr(pp, "_enrich", lambda *a, **k: None)
    rules = {"EMAIL": {"column_name": "EMAIL", "semantic_type": "EMAIL", "pii_classification": "EMAIL"}}
    out = pp.profile_tables(None, [ref], column_rules=lambda r: rules)
    profiled = out[ref.key].columns[0]
    assert profiled["semantic_type"] == "EMAIL" and profiled["pii_classification"] == "EMAIL"
    assert profiled["sample_values"][0]["value"] != "a@b.com"  # masked once it is PII
