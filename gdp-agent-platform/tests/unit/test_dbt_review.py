from services.dbt.review import refs, review_file, skill_excerpt, validate_revision

ORIGINAL = "select * from {{ ref('lightbox_addresses') }} as o\nleft join {{ source('s', 'b') }} as b on o.id = b.id\n"


def test_validate_revision_blocks_new_or_dropped_refs_and_ddl():
    assert validate_revision(ORIGINAL, "") == []
    assert validate_revision(ORIGINAL, ORIGINAL.replace("left join", "left  join")) == []
    assert any("adds refs" in p for p in validate_revision(ORIGINAL, ORIGINAL + "join {{ ref('other') }}"))
    assert any("drops refs" in p for p in validate_revision(ORIGINAL, "select 1 from {{ ref('lightbox_addresses') }}"))
    assert any("DDL" in p for p in validate_revision(ORIGINAL, ORIGINAL + "\ndrop table x"))
    assert any("Jinja" in p for p in validate_revision(ORIGINAL, ORIGINAL + "{{ oops"))
    assert refs("{{ ref( 'a' ) }}") == {("ref", "'a'")}


def test_skill_excerpt_prefers_rule_sections():
    text = "intro\n## Standard Rules (MUST)\nRule 1 text\n## Workflow\nsteps\n# references/sttm-mapping-rules.md\nrulebook"
    out = skill_excerpt(text)
    assert out.startswith("## Standard Rules") and "rulebook" in out and "steps" not in out


def test_review_file_parses_findings_and_rejects_unsafe_revision():
    import json

    def fake(sql, params):
        assert "AI_COMPLETE" in sql and "SKILL RULES" in params[1]
        payload = {"summary": "1 issue", "findings": [{"severity": "warning", "rule": "Rule 2", "message": "m",
                                                        "line_hint": "x"}],
                   "revised_content": ORIGINAL + "join {{ ref('invented') }}"}
        return [{"r": json.dumps({"structured_output": [{"raw_message": payload}], "model": "m1"})}]
    out = review_file(fake, "models/x.sql", ORIGINAL, "## Standard Rules\nR", "ctx", "notes")
    assert out["findings"][0]["rule"] == "Rule 2"
    assert out["revised_content"] == "" and out["rejected_revision"]
