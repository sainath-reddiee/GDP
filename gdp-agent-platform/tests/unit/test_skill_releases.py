from infrastructure.seed_knowledge import list_skills
from services.knowledge.skills import (bound, category_for, compact, diff_files, next_version, pick, split_files,
                                       version_key)
from services.governance.policy import privilege_for


def test_category_from_frontmatter_folder_and_type():
    assert category_for("soda", None, "SODA", "SODA_SKILL") == "data-quality"
    assert category_for("gdp/silver-model", None, "DBT", "SILVER-MODEL") == "dbt"
    assert category_for("gdp_domain", None, "DBT", "GDP_DOMAIN_SKILL") == "domain-standards"
    assert category_for("modeling/ai-data-modeling", None, "DOMAIN", "AI-DATA-MODELING") == "data-modeling"
    # sub-skills of the modeling umbrella land where their name says
    assert category_for("modeling/ai-data-modeling/skills/x", None, "MAPPING", "AI-SCHEMA-MAPPING", "AI-DATA-MODELING") == "mapping"
    assert category_for("anything", "Data Quality", "DBT", "X") == "data-quality"
    assert category_for("new-folder", None, "UNKNOWN", "X") == "general"


def test_every_repository_skill_gets_a_category_and_a_version():
    skills = list_skills()
    assert all(s["category"] for s in skills)
    assert all(s["version"] for s in skills)
    undeclared = next(s for s in skills if s["name"] == "SILVER-MODEL")
    assert undeclared["version"].startswith("1.0.0+") and undeclared["checksum"].startswith(undeclared["version"][6:])
    assert next(s for s in skills if s["name"] == "SODA_SKILL")["version"] == "2.0.0"


def test_semantic_version_order_and_bump():
    assert version_key("1.10.0") > version_key("1.9.0")
    assert version_key("2.0.0") > version_key("1.99.99+abc")
    assert next_version("1.0.0+abcd1234") == "1.0.1"
    assert next_version("2.0.0", ["2.0.1", "2.0.2+x"]) == "2.0.3"


def test_resolver_order_override_then_production_then_latest():
    vs = [{"skill_id": "a", "revision": 1, "version": "1.0.0", "status": "ACTIVE"},
          {"skill_id": "b", "revision": 2, "version": "1.0.1", "status": "ACTIVE"},
          {"skill_id": "c", "revision": 3, "version": "1.0.2", "status": "DRAFT"}]
    assert pick(vs, "c", "a")["skill_id"] == "c"
    assert pick(vs, None, "a")["skill_id"] == "a"
    assert pick(vs, "missing", None)["skill_id"] == "b"  # drafts are never picked as "latest"


def test_bindings_fall_back_and_respect_standard_and_order():
    assert bound([], "SODA", "GDP", ["SODA_SKILL"]) == ["SODA_SKILL"]
    rows = [{"stage": "DBT", "skill_name": "B", "position": 20, "enabled": True, "standard": "ANY"},
            {"stage": "DBT", "skill_name": "A", "position": 10, "enabled": True, "standard": "GDP"},
            {"stage": "DBT", "skill_name": "C", "position": 5, "enabled": False, "standard": "ANY"}]
    assert bound(rows, "DBT", "GDP", []) == ["A", "B"]
    assert bound(rows, "DBT", "COMPANY", []) == ["B"]


def test_files_split_and_diff():
    old = "# Body\nline one\nline two\n\n# references/rules.md\nkeep\nold rule"
    new = "# Body\nline one\nline 2\n\n# references/rules.md\nkeep\nold rule\n\n# assets/x.sql\nselect 1"
    assert [f["path"] for f in split_files(new)] == ["SKILL.md", "references/rules.md", "assets/x.sql"]
    files = {f["path"]: f for f in diff_files(old, new)}
    assert files["SKILL.md"]["status"] == "changed" and files["SKILL.md"]["added"] == 1 and files["SKILL.md"]["removed"] == 1
    assert files["references/rules.md"]["status"] == "same"
    assert files["assets/x.sql"]["status"] == "added"
    long = [[" ", i, i, "x"] for i in range(1, 30)] + [["+", None, 30, "new"]]
    shown = compact(long, context=2)
    assert shown[0][0] == "…" and shown[-1][0] == "+" and len(shown) == 4


def test_skill_routes_are_governed():
    assert privilege_for("POST", "/api/skills/SODA_SKILL/labels/production")[0] == "SKILL.RELEASE"
    assert privilege_for("POST", "/api/skills/SODA_SKILL/labels/candidate")[0] == "SKILL.EDIT"
    assert privilege_for("PUT", "/api/skills/bindings")[0] == "SKILL.RELEASE"
    assert privilege_for("PUT", "/api/runs/r1/skills/SODA_SKILL")[0] == "SKILL.EDIT"
    assert privilege_for("GET", "/api/skills")[0] is None
