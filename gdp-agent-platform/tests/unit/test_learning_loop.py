"""The learning loop: reviewer decisions become rules, a change is replayed against approved mappings before it is
approved, and the engine asks where it is unsure."""

import pytest

from services.knowledge.validate import normalize_content
from services.learning import patterns, questions, replay
from services.mapping import features


def ev(run, table, src, tgt, *, decision="APPROVED", st="VARCHAR", tt="VARCHAR", tx=None, proposed=None, ptx=None,
       pattern=None):
    return {"run_id": run, "source_table": table, "source_column": src, "source_type": st, "pattern": pattern,
            "target_table": "OPPORTUNITY_CORE", "target_column": tgt, "target_type": tt, "decision": decision,
            "transformation": tx, "proposed_target": proposed or tgt, "proposed_transformation": ptx}


def test_template_puts_the_source_column_in_a_placeholder():
    assert patterns.template("TRY_TO_DATE(o.START_DT, 'MM/DD/YYYY')", "start_dt") == "TRY_TO_DATE({col}, 'MM/DD/YYYY')"
    assert patterns.template('UPPER(TRIM("Stage"))', "Stage") == "UPPER(TRIM({col}))"
    assert patterns.template("START_DT", "START_DT") is None and patterns.template("CURRENT_DATE()", "X") is None
    assert patterns.template("COALESCE(START_DT_LOCAL, START_DT)", "START_DT") == "COALESCE(START_DT_LOCAL, {col})"


DATES = [ev("r1", "SF_OPP", "CLOSE_DT", "CLOSE_DATE", tt="DATE", tx="TRY_TO_DATE(CLOSE_DT, 'MM/DD/YYYY')", pattern="99/99/9999"),
         ev("r1", "SF_OPP", "OPEN_DT", "OPEN_DATE", tt="DATE", tx="TRY_TO_DATE(OPEN_DT, 'MM/DD/YYYY')", pattern="99/99/9999"),
         ev("r2", "YARDI_LEASE", "SIGN_DT", "SIGNED_DATE", tt="DATE", tx="TRY_TO_DATE(SIGN_DT, 'MM/DD/YYYY')", pattern="99/99/9999")]


def test_a_transformation_habit_across_tables_becomes_a_rule():
    found = patterns.learn(DATES, layer="SILVER")
    rule = next(c for c in found if c["habit"] == "TRANSFORMATION")
    assert rule["kind"] == "TRANSFORMATION_RULE"
    assert rule["content_json"]["expression"] == "TRY_TO_DATE({col}, 'MM/DD/YYYY')"
    assert (rule["content_json"]["source_family"], rule["content_json"]["target_family"]) == ("TEXT", "DATE")
    assert rule["content_json"]["source_pattern"] == "99/99/9999"
    assert rule["evidence"]["decisions"] == 3 and rule["evidence"]["tables"] == 2 and rule["evidence"]["share"] == 1.0
    assert rule["key"].startswith("learned.transform.SILVER.")
    assert normalize_content("TRANSFORMATION_RULE", rule["content_json"])


def test_no_rule_from_one_table_too_few_cases_or_what_the_engine_already_does():
    assert not [c for c in patterns.learn(DATES[:2]) if c["habit"] == "TRANSFORMATION"]          # one table, two cases
    already = [dict(e, proposed_transformation=e["transformation"]) for e in DATES]
    assert not [c for c in patterns.learn(already) if c["habit"] == "TRANSFORMATION"]           # nothing to correct
    contested = DATES + [ev("r3", "X", f"D{i}_DT", f"D{i}_DATE", tt="DATE") for i in range(4)]   # mostly direct
    assert not [c for c in patterns.learn(contested) if c["habit"] == "TRANSFORMATION"]


def test_a_repeated_override_becomes_a_domain_wide_pattern():
    evs = [ev("r1", "SF_OPP", "AMOUNT", "DEAL_AMOUNT", proposed="AMOUNT_USD"),
           ev("r2", "SF_OPP2", "AMOUNT", "DEAL_AMOUNT", proposed="AMOUNT_USD"),
           ev("r3", "SF_OPP3", "AMOUNT", "DEAL_AMOUNT")]
    rule = next(c for c in patterns.learn(evs) if c["habit"] == "OVERRIDE")
    assert rule["content_json"]["source_column"] == "AMOUNT" and rule["content_json"]["target_column"] == "DEAL_AMOUNT"
    assert rule["content_json"]["proposed_target"] == "AMOUNT_USD" and rule["evidence"]["runs"] == 3
    assert not [c for c in patterns.learn(evs[:1]) if c["habit"] == "OVERRIDE"]     # one run is not a habit


def test_a_suffix_rename_becomes_a_pattern_the_keyword_score_reads():
    evs = [ev("r1", "A", "SENT_F", "SENT_FLAG", proposed="SENT_DATE"), ev("r1", "A", "WON_F", "WON_FLAG"),
           ev("r2", "B", "ACTIVE_F", "ACTIVE_FLAG")]
    rule = next(c for c in patterns.learn(evs) if c["habit"] == "RENAME")
    assert rule["content_json"]["source_suffix"] == "_F" and rule["content_json"]["target_suffix"] == "_FLAG"
    assert normalize_content("MAPPING_PATTERN", rule["content_json"])

    src = {"column_name": "CLOSED_F", "table_name": "SF_OPP", "data_type": "VARCHAR"}
    flag = {"column_name": "CLOSED_FLAG", "table_name": "OPPORTUNITY_CORE", "data_type": "BOOLEAN"}
    plain = features.all_features(src, flag, {}, None)["scores"]["keyword"]
    learned = features.all_features(src, flag, patterns.apply({}, [rule]), None)
    assert learned["scores"]["keyword"] > plain and learned["evidence"]["keyword"]["learned_rename"] == "_F -> _FLAG"


def test_a_learned_transformation_rule_is_proposed_by_type_families():
    rule = next(c for c in patterns.learn(DATES) if c["habit"] == "TRANSFORMATION")
    src = {"column_name": "EXPIRY_DT", "data_type": "VARCHAR", "pattern": "99/99/9999"}
    tgt = {"column_name": "EXPIRY_DATE", "data_type": "DATE"}
    expr, why = features.propose_transformation(src, tgt, patterns.apply({}, [rule]))
    assert why == "learned transformation rule" and "TRY_TO_DATE(" in expr and "'MM/DD/YYYY'" in expr
    other = features.propose_transformation({**src, "pattern": "9999-99-99"}, tgt, patterns.apply({}, [rule]))
    assert other[1] != "learned transformation rule"          # learned from another value pattern


def test_replay_scores_first_choices_against_approved_mappings():
    sources = [{"source_column_id": "s1", "column_name": "CLOSED_F", "table_name": "SF_OPP", "data_type": "VARCHAR"},
               {"source_column_id": "s2", "column_name": "NAME", "table_name": "SF_OPP", "data_type": "VARCHAR"}]
    targets = [{"target_column_id": "t1", "column_name": "CLOSED_FLAG", "table_name": "OPPORTUNITY_CORE", "data_type": "VARCHAR"},
               {"target_column_id": "t2", "column_name": "CLOSED_DATE", "table_name": "OPPORTUNITY_CORE", "data_type": "VARCHAR"},
               {"target_column_id": "t3", "column_name": "NAME", "table_name": "OPPORTUNITY_CORE", "data_type": "VARCHAR"}]
    truth = {"s1": {"target_column": "CLOSED_FLAG"}, "s2": {"target_column": "NAME"}}
    weights = {"semantic": 0.0, "keyword": 1.0, "datatype": 0.0, "statistical": 0.0, "domain": 0.0, "context": 0.0, "historical": 0.0}
    thresholds = {"auto_suggest": 0.85, "human_review": 0.6, "ambiguity_margin": 0.05}
    base = replay.evaluate(sources, targets, truth, {}, {}, weights, thresholds)
    rename = {"kind": "MAPPING_PATTERN", "content_json": {"source_suffix": "_F", "target_suffix": "_FLAG"}}
    after = replay.evaluate(sources, targets, truth, patterns.apply({}, [rename]), {}, weights, thresholds)
    assert after["hits"] >= base["hits"] and after["picks"]["s1"][0] == "CLOSED_FLAG"
    b, a = replay.summarize([{"x": base}], "x"), replay.summarize([{"x": after}], "x")
    assert replay.verdict(b, a) in ("BETTER", "SAME")
    assert replay.verdict(a, {**a, "target_hits": a["target_hits"] - 1}) == "WORSE"
    assert replay.verdict({**b, "columns": 0}, a) == "NO_EVIDENCE"


def cand(name, conf, *, ambiguous=False, hist=0.0, ev=None):
    return {"target": {"column_name": name, "definition": f"{name} def"}, "confidence": conf, "final_score": conf,
            "ambiguous": ambiguous, "scores": {"historical": hist}, "evidence": ev or {}}


def test_questions_for_unsure_columns_only():
    ranked = [({"column_name": "STG", "table_name": "DB.S.SF_OPP", "data_type": "VARCHAR", "values": ["A", "B"]},
               [cand("STAGE", 0.55), cand("STATUS", 0.5)]),
              ({"column_name": "NAME", "table_name": "SF_OPP"}, [cand("NAME", 0.95)]),                         # sure
              ({"column_name": "AMT", "table_name": "SF_OPP"}, [cand("AMOUNT", 0.5, hist=1.0)]),               # decided before
              ({"column_name": "KEY", "table_name": "SF_OPP"}, [cand("DIM_X_SKEY", 0.4, ev={"gold_rule": {"kind": "RESOLVE_MDM"}})]),  # gold rule
              ({"column_name": "TYPE", "table_name": "SF_OPP"}, [cand("OPP_TYPE", 0.8, ambiguous=True), cand("TYPE_CODE", 0.79)])]
    qs = questions.mapping_questions(ranked, "DB.S.OPPORTUNITY_CORE")
    assert [q["subject_key"] for q in qs] == ["mapping.OPPORTUNITY_CORE.SF_OPP.STG", "mapping.OPPORTUNITY_CORE.SF_OPP.TYPE"]
    assert qs[0]["options"] == ["STAGE", "STATUS", "NOT_LOADED"] and qs[0]["question"] == "Which OPPORTUNITY_CORE column does SF_OPP.STG feed?"


def test_an_answer_becomes_knowledge():
    q = questions.mapping_questions([({"column_name": "STG", "table_name": "SF_OPP"}, [cand("STAGE", 0.5), cand("STATUS", 0.4)])],
                                    "OPPORTUNITY_CORE")[0]
    picked = questions.knowledge_for(q, "status", "Stage codes are statuses here")
    assert picked["kind"] == "MAPPING_PATTERN" and picked["key"] == "feedback.OPPORTUNITY_CORE.SF_OPP.STG"
    assert picked["content_json"]["target_column"] == "STATUS" and picked["content_json"]["overridden"]
    skipped = questions.knowledge_for(q, "NOT_LOADED", None)
    assert skipped["content_json"]["rejected_target"] == "STAGE"            # an anti-pattern for the top candidate
    with pytest.raises(AssertionError):
        questions.knowledge_for(q, "OTHER", None)
    note = questions.note_questions(["DIM_COMPANY_INDUSTRY: the business key is assumed to be X alone; confirm what makes a row unique",
                                     "COMPANY_MERGE does not carry X, so it cannot hang off DIM_COMPANY; left out"], "MODEL")
    assert len(note) == 1 and note[0]["subject_key"].startswith("note.model.")
    rule = questions.knowledge_for({**note[0], "context": {"stage": "MODEL"}}, None, "Company id and industry code")
    assert rule["kind"] == "BUSINESS_RULE" and "Answer: Company id and industry code" in rule["content"]
