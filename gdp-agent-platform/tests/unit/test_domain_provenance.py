"""How a domain is built (services/knowledge/provenance.py) and how complete it is (coverage.py)."""

from services.knowledge import coverage, provenance

DOMAIN = {"domain": {"domain_name": "COMPANY"}, "origin": "repository",
          "contract": "snowflake/skills/gdp/dbt-onboard-source/references/company-contract.md",
          "targets": [{"target_database": "DB", "target_schema": "COMPANY", "target_table": "COMPANY_CORE",
                       "hkey_columns": ["DUNS_NUMBER", "COMPANY_NAME"]},
                      {"target_database": "DB", "target_schema": "COMPANY", "target_table": "COMPANY_ADDRESS", "hkey_columns": []}]}


def row(kid, origin, kind, title, ref="", verified=None, status="ACTIVE", by="SEED", at="2026-10-01 10:00:00"):
    return {"KNOWLEDGE_ID": kid, "ORIGIN": origin, "KNOWLEDGE_TYPE": kind, "TITLE": title, "SOURCE_REFERENCE": ref,
            "VERIFIED_BY": verified, "STATUS": status, "CREATED_BY": by, "CREATED_AT": at}


ROWS = [row("k1", "SEED", "MODEL_DEFINITION", "COMPANY_CORE (hub)", "company.model.company_core"),
        row("k2", "SEED", "GLOSSARY", "DUNS_NUMBER", "company.glossary.duns_number", verified="ANA"),
        row("k3", "SODA", "SODA_PATTERN", "missing keys", "soda.x", at="2026-10-05 09:00:00"),
        row("k4", "USER", "BUSINESS_RULE", "Active companies only", "company.rule.active", by="ANA", status="DRAFT"),
        row("k5", "NEW_THING", "MYSTERY", "odd", "x")]


def test_sources_feed_parts_with_counts_and_samples():
    out = provenance.build(DOMAIN, ROWS, {"k1": 3, "k3": 1}, [{"name": "analytics", "matched": 4}, {"name": "empty", "matched": 0}],
                           [{"VERSION": 1, "CHANGE_KIND": "DEPLOY", "CHANGE_NOTE": "Recorded by deploy", "CREATED_AT": "2026-10-09 12:00:00"}])
    sources = {s["id"]: s for s in out["sources"]}
    assert sources["pack"]["count"] == 2 and sources["pack"]["verified"] == 1 and sources["pack"]["used"] == 1
    assert sources["pack"]["files"] == ["domain/company/domain_pack.json"]
    assert sources["people"]["people"] == ["ANA"] and sources["people"]["draft"] == 1
    assert sources["code"]["repos"] == [{"name": "analytics", "matched": 4}]   # a repository with no match is left out
    assert sources["other"]["label"] == "Other"                                # an unknown origin still shows
    assert out["upstream"][0]["file"].endswith("company-contract.md") and out["upstream"][0]["feeds"] == "pack"
    edges = {(e["source"], e["part"]): e["count"] for e in out["edges"]}
    assert edges[("pack", "model")] == 1 and edges[("pack", "targets")] == 2 and edges[("code", "targets")] == 4
    assert [p["id"] for p in out["parts"]][0] == "targets"
    assert out["totals"] == {"items": 5, "verified": 1, "used": 2, "draft": 1}
    assert sources["pack"]["items"][0]["knowledge_id"] == "k1"                 # most used first
    assert out["timeline"][0]["label"].startswith("Version 1") and any(e["label"] == "Learned in data quality: 1 item" for e in out["timeline"])


def test_coverage_finds_gaps_and_proposes_copilot_requests():
    fqn = "DB.COMPANY.COMPANY_CORE"
    out = coverage.score("COMPANY", DOMAIN["targets"], ROWS, sttm={fqn}, dq=set(),
                         qa_sql=["select * from DB.COMPANY.COMPANY_CORE group by 1 having count(*) > 1"], usage={"k1": 1})
    core, address = out["targets"]
    assert core["checks"] == {"model": True, "keys": True, "glossary": False, "sttm": True, "qa": True, "dq": False}
    assert core["glossary_missing"] == ["COMPANY_NAME"] and core["score"] == 67
    assert address["checks"]["keys"] is False and address["checks"]["glossary"] is False
    gloss = next(a for a in out["actions"] if a["table"] == "COMPANY_CORE" and a["check"] == "glossary")
    assert "COMPANY_NAME" in gloss["prompt"] and "DUNS_NUMBER" not in gloss["prompt"]
    assert out["knowledge"] == {"items": 5, "verified": 1, "used_90d": 1, "unused_90d": 4, "awaiting_review": 1}
    assert {c["id"]: (c["done"], c["of"]) for c in out["checks"]}["sttm"] == (1, 2)
    assert 0 <= out["score"] <= 100


def test_an_empty_domain_scores_zero_without_errors():
    out = coverage.score("NEW", [], [], set(), set(), [])
    assert out["score"] == 0 and out["targets"] == [] and out["actions"] == []
    assert provenance.build({"domain": {"domain_name": "NEW"}}, [])["sources"] == []


def test_manifest_names_the_skill_pack_stage_skills_repos_and_runs():
    m = provenance.manifest(
        DOMAIN, standard="gdp",
        bindings=[{"STAGE": "DBT", "SKILL_NAME": "dbt-onboard-source", "ENABLED": True, "POSITION": 1, "STANDARD": "GDP"},
                  {"STAGE": "DBT", "SKILL_NAME": "generic-dbt", "ENABLED": True, "POSITION": 2, "STANDARD": "ANY"},
                  {"STAGE": "QA", "SKILL_NAME": "other-standard", "ENABLED": True, "POSITION": 1, "STANDARD": "ACME"},
                  {"STAGE": "QA", "SKILL_NAME": "switched-off", "ENABLED": False, "POSITION": 1, "STANDARD": "ANY"}],
        loaded=[{"SKILL": "dbt-onboard-source", "VERSION": "3", "N": 5, "RUNS": 2, "LAST": "2026-10-10 09:00"}],
        repos=[{"REPO_ID": "r1", "NAME": "DBT_DEMO", "GIT_URL": "https://github.com/x/dbt_demo", "BRANCH": "main",
                "LAST_COMMIT": "abc123", "LAST_INDEXED_AT": "2026-10-12 10:00"}],
        files=[{"REPO_ID": "r1", "PATH": "models/company/company_core.sql", "NAME": "company_core", "KIND": "DBT_MODEL"}],
        runs=[{"RUN_ID": "run1", "RUN_NAME": "RAW modeling", "ITEMS": 28, "KINDS": ["MAPPING_PATTERN"], "CURRENT_STATE": "DONE"}],
        rows=ROWS)
    assert m["skill"] == {"standard": "GDP", "name": "dbt-onboard-source", "path": "snowflake/skills/gdp/dbt-onboard-source",
                          "contract": DOMAIN["contract"]}
    assert m["pack"] == {"file": "domain/company/domain_pack.json", "generator": "infrastructure/contracts_to_packs.py",
                         "items": 2, "shipped": True}
    dbt = next(s for s in m["stages"] if s["stage"] == "DBT")
    assert [k["skill"] for k in dbt["skills"]] == ["dbt-onboard-source", "generic-dbt"]
    assert dbt["skills"][0]["loaded"] == [{"version": "3", "loads": 5, "runs": 2, "last": "2026-10-10 09:00"}]
    assert all(s["stage"] != "QA" for s in m["stages"])   # another standard's skill and a switched-off one are left out
    assert m["repos"][0]["last_commit"] == "abc123" and m["repos"][0]["files"][0]["path"] == "models/company/company_core.sql"
    assert m["runs"][0]["run_name"] == "RAW modeling" and m["people"] == ["ANA"]


def test_flow_lanes_follow_the_lifecycle_with_trust_states():
    detail = {**DOMAIN, "silver": {"database": "DB", "schema": "COMPANY"}}
    m = provenance.manifest(detail, rows=ROWS, repos=[{"REPO_ID": "r1", "NAME": "DBT_DEMO"}],
                            files=[{"REPO_ID": "r1", "PATH": "models/company/company_core.sql", "NAME": "company_core"}])
    out = provenance.flow(detail, m, ROWS, domain_model={"MODEL_ID": "dm1", "APPROVED_VERSION": 1, "OPEN_VERSION": 2},
                          table_models=[{"TABLE_MODEL_ID": "t1", "TABLE_NAME": "COMPANY_CORE", "CURRENT_VERSION": 3}],
                          sttm=[{"CATALOG_ID": "c1", "VERSION": 2, "TARGET_ENTITY": "DB.COMPANY.COMPANY_CORE", "STATUS": "APPROVED"},
                                {"CATALOG_ID": "c2", "VERSION": 1, "TARGET_ENTITY": "DB.COMPANY.COMPANY_ADDRESS", "STATUS": "REVIEW"}],
                          ingestion=[{"LANDING_DATABASE": "L", "LANDING_SCHEMA": "S", "LANDING_TABLE": "T", "IS_ACTIVE": True}],
                          jira=[{"ISSUE_KEY": "GDPQA-1"}, {"ISSUE_KEY": "GDPQA-1"}], proposed=3, stale=4)
    existing, ingest = out["lanes"]
    steps = {s["id"]: s for s in existing["steps"]}
    assert [s["id"] for s in existing["steps"]] == ["model", "knowledge", "modeling", "mapping", "code"]
    assert steps["model"]["items"][0]["label"] == "Contract: company-contract.md"
    assert steps["model"]["items"][1]["label"] == "Domain model v1 (approved), v2 open" and steps["model"]["state"] == "ok"
    assert "No Erwin model or Confluence page linked" in steps["model"]["facts"]
    assert steps["knowledge"]["facts"] == ["5 items, 1 verified by a person", "3 proposed, waiting for review", "4 due for review or never verified"]
    assert steps["knowledge"]["state"] == "partial"
    assert steps["mapping"]["facts"] == ["2 mappings, 1 approved"] and steps["mapping"]["state"] == "partial"
    assert steps["code"]["state"] == "partial" and steps["modeling"]["state"] == "partial"
    req = {s["id"]: s for s in ingest["steps"]}
    assert [i["label"] for i in req["requests"]["items"]] == ["GDPQA-1"]   # one issue, once
    assert ingest["items"][0]["label"] == "L.S.T" and req["snaplogic"]["state"] == "ok"


def test_needs_verification_lists_unverified_active_items_most_used_first():
    out = provenance.needs_verification(ROWS, {"k3": 9, "k1": 2})
    assert [i["knowledge_id"] for i in out] == ["k3", "k1", "k5"]   # k2 verified, k4 a draft
    assert out[0]["source"] == "Learned in data quality" and out[0]["uses"] == 9
