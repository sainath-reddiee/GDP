"""Edits made in the app survive the automatic paths that used to overwrite them: the deploy seed of repository
domains (H1), regenerating Soda checks (H2), regenerating the STTM (H3) and a run design saved over a table model
edited in the Model Store (H6)."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.knowledge.packs import SEEDED, domain_values, merge  # noqa: E402

PACK_CONFIG = {"silver_database": "SILVER", "signals": {"tables": {"ACCOUNT": 2}}, "standard": "GENERIC"}


def row(description="Customers", owner="DATA_TEAM", config=None):
    return ("d1", "COMPANY", description, owner, True, 1, dict(config if config is not None else PACK_CONFIG))


def seeded_current(**over):
    """The domain as the previous deploy left it, then edited per `over`."""
    first = domain_values(None, row())
    current = {"DESCRIPTION": first["description"], "OWNER": first["owner"], "ACTIVE_FLAG": True, "VERSION": 7,
               "CONFIG": json.dumps(first["config"])}
    current.update(over)
    return current


# ---------------------------------------------------------------- H1: deploy seed


def test_new_domain_takes_the_pack_and_records_what_was_seeded():
    out = domain_values(None, row())
    assert out["description"] == "Customers" and out["config"]["silver_database"] == "SILVER"
    assert out["config"][SEEDED]["description"] == "Customers"


def test_unedited_values_follow_the_pack():
    out = domain_values(seeded_current(), row(description="Customers and prospects",
                                              config={**PACK_CONFIG, "silver_database": "SILVER2"}))
    assert out["description"] == "Customers and prospects"
    assert out["config"]["silver_database"] == "SILVER2"
    assert out["version"] == 7  # the app's version, not the pack's 1


def test_edits_made_in_the_app_survive_a_deploy():
    config = json.loads(seeded_current()["CONFIG"])
    config["rules"] = {"min_score": 0.8}
    config["signals"] = {"tables": {"ACCOUNT": 2, "CLIENT": 2}}  # an accepted AI signal
    current = seeded_current(DESCRIPTION="Edited by the steward", OWNER="JANE", CONFIG=json.dumps(config))
    pack = row(description="Customers v2", owner="DATA_TEAM",
               config={**PACK_CONFIG, "signals": {"tables": {"ACCOUNT": 2, "PARTY": 3}}})
    out = domain_values(current, pack)
    assert out["description"] == "Edited by the steward" and out["owner"] == "JANE"
    assert out["config"]["rules"] == {"min_score": 0.8}
    # the user's signal stays and the pack's new one is added
    assert out["config"]["signals"]["tables"] == {"ACCOUNT": 2, "CLIENT": 2, "PARTY": 3}
    # the next deploy compares against what this one wrote
    assert out["config"][SEEDED]["description"] == "Customers v2"


def test_a_signal_removed_on_purpose_is_not_put_back():
    config = json.loads(seeded_current()["CONFIG"])
    config["signals"] = {"tables": {}}
    out = domain_values(seeded_current(CONFIG=json.dumps(config)), row())
    assert out["config"]["signals"]["tables"] == {}


def test_first_deploy_without_a_record_keeps_the_current_values():
    current = {"DESCRIPTION": "Maybe edited", "OWNER": "JANE", "ACTIVE_FLAG": True, "VERSION": 3,
               "CONFIG": json.dumps({"silver_database": "OLD", "rules": {"x": 1}})}
    out = domain_values(current, row())
    assert out["description"] == "Maybe edited" and out["owner"] == "JANE"
    assert out["config"]["silver_database"] == "OLD" and out["config"]["rules"] == {"x": 1}
    assert out["config"]["standard"] == "GENERIC"  # a key the domain did not have comes from the pack
    assert out["config"][SEEDED]["config"]["silver_database"] == "SILVER"


def test_a_deleted_domain_stays_deleted():
    config = json.loads(seeded_current()["CONFIG"])
    config["deleted_at"] = "2026-10-01"
    out = domain_values(seeded_current(ACTIVE_FLAG=False, CONFIG=json.dumps(config)), row())
    assert out["active"] is False and out["config"]["deleted_at"] == "2026-10-01"


def test_a_pack_import_takes_the_pack_but_keeps_app_only_keys():
    current = {"DESCRIPTION": "Old", "OWNER": "JANE", "ACTIVE_FLAG": True, "VERSION": 4,
               "CONFIG": json.dumps({"silver_database": "OLD", "rules": {"x": 1}})}
    out = domain_values(current, row(description="Imported"), origin="PACK_IMPORT")
    assert out["description"] == "Imported" and out["config"]["silver_database"] == "SILVER"
    assert out["config"]["rules"] == {"x": 1} and SEEDED not in out["config"]


def test_merge_reads_the_current_rows_and_writes_the_merged_values():
    issued = []
    current = seeded_current(DESCRIPTION="Edited")
    current["DOMAIN_ID"] = "d1"
    merge(lambda sql, params=None: issued.append((sql, params)), "DB",
          {"domains": [row(description="Pack text")], "tables": [], "columns": [], "knowledge": []},
          query=lambda sql, params: [current])
    sql, params = next((s, p) for s, p in issued if "DOMAIN_REGISTRY" in s)
    assert params[2] == "Edited" and params[5] == 7


# ---------------------------------------------------------------- H2: regenerating Soda checks

from services.soda.decisions import carry_decisions  # noqa: E402


def _check(column="EMAIL", kind="accepted_values", values=("A", "B"), check_type="ACCEPTED_VALUES"):
    return {"target_table": "CUSTOMER", "target_column": column, "check_type": check_type,
            "definition": {"kind": kind, "values": list(values)}, "severity": "FAIL", "origin": "STTM"}


def _prior(status, definition, generated=None, column="EMAIL", severity="WARN"):
    return {"TARGET_TABLE": "CUSTOMER", "TARGET_COLUMN": column, "CHECK_TYPE": "ACCEPTED_VALUES",
            "CHECK_DEFINITION": json.dumps(definition), "SEVERITY": severity, "CLIENT_REQUIREMENT": None,
            "STATUS": status, "REVIEWED_BY": "ANN", "REVIEWED_AT": "2026-10-01 10:00:00",
            "GENERATED_DEFINITION": json.dumps(generated) if generated is not None else None}


def test_same_proposal_keeps_the_decision_and_the_reviewers_edit():
    generated = {"kind": "accepted_values", "values": ["A", "B"]}
    modified = {"kind": "accepted_values", "values": ["A", "B", "C"]}
    checks = [_check()]
    counts = carry_decisions(checks, [_prior("APPROVED", modified, generated)])
    assert counts["kept"] == 1
    c = checks[0]
    assert c["status"] == "APPROVED" and c["definition"] == modified and c["severity"] == "WARN"
    assert c["reviewed_by"] == "ANN" and c["generated_definition"] == generated


def test_a_rejection_stands_even_when_the_proposal_changes():
    checks = [_check(values=("A", "B", "Z"))]
    carry_decisions(checks, [_prior("REJECTED", {"kind": "accepted_values", "values": ["A", "B"]},
                                    {"kind": "accepted_values", "values": ["A", "B"]})])
    assert checks[0]["status"] == "REJECTED"


def test_changed_input_sends_an_approval_back_for_review_keeping_a_modified_definition():
    generated_before = {"kind": "accepted_values", "values": ["A", "B"]}
    modified = {"kind": "accepted_values", "values": ["A", "B", "C"]}
    checks = [_check(values=("A", "B", "D"))]
    counts = carry_decisions(checks, [_prior("APPROVED", modified, generated_before)])
    assert counts["review_again"] == 1
    assert checks[0]["status"] == "PROPOSED" and checks[0]["definition"] == modified


def test_changed_input_on_an_unmodified_approval_takes_the_new_proposal():
    before = {"kind": "accepted_values", "values": ["A", "B"]}
    checks = [_check(values=("A", "B", "D"))]
    carry_decisions(checks, [_prior("APPROVED", before, before)])
    assert checks[0]["status"] == "PROPOSED"
    assert checks[0]["definition"] == {"kind": "accepted_values", "values": ["A", "B", "D"]}


def test_rows_decided_before_v045_and_undecided_rows():
    same = {"kind": "accepted_values", "values": ["A", "B"]}
    checks = [_check(), _check(column="PHONE")]
    proposed = {**_prior("PROPOSED", same, column="PHONE"), "REVIEWED_BY": None}
    counts = carry_decisions(checks, [_prior("APPROVED", same), proposed])
    assert checks[0]["status"] == "APPROVED" and counts["kept"] == 1
    assert "status" not in checks[1]  # never reviewed: stays a fresh proposal


# ---------------------------------------------------------------- H3: regenerating the STTM

from services.catalog.sttm import carry_store_edits  # noqa: E402


def _sttm_line(col, **over):
    line = {"target_column": col, "target_datatype": "VARCHAR", "mapping_type": "DIRECT", "source_table": "CRM",
            "source_column": col, "transformation": None, "business_rule": None, "business_definition": None,
            "filter_rule": None, "required": True}
    line.update(over)
    return line


def test_store_edits_are_applied_to_the_regenerated_sttm():
    baseline = {"lines": [_sttm_line("NAME"), _sttm_line("EMAIL"), _sttm_line("OLD")], "table_design": {"grain": "customer"}}
    latest = {"lines": [_sttm_line("NAME", transformation="trim(name)", business_rule="legal name"),
                        _sttm_line("EMAIL"), _sttm_line("SEGMENT", mapping_type="CONSTANT", transformation="'RETAIL'")],
              "table_design": {"grain": "customer", "filters": [{"table": "CRM", "condition": "active = 1"}]}}
    lines = [_sttm_line("NAME"), _sttm_line("EMAIL"), _sttm_line("OLD")]
    design = {"grain": "customer"}
    report = carry_store_edits(baseline, latest, lines, design)
    name = next(l for l in lines if l["target_column"] == "NAME")
    assert name["transformation"] == "trim(name)" and name["business_rule"] == "legal name" and name["human_approved"]
    assert [l["target_column"] for l in lines] == ["NAME", "EMAIL", "SEGMENT"]  # OLD removed, SEGMENT added back
    assert design["filters"] == [{"table": "CRM", "condition": "active = 1"}]
    assert report["removed"] == ["OLD"] and report["added"] == ["SEGMENT"] and not report["conflicts"]


def test_when_the_new_mapping_also_changed_a_field_it_wins_and_the_conflict_is_reported():
    baseline = {"lines": [_sttm_line("NAME")], "table_design": {}}
    latest = {"lines": [_sttm_line("NAME", source_column="FULL_NAME")], "table_design": {}}
    lines = [_sttm_line("NAME", source_column="DISPLAY_NAME")]  # the reviewer remapped it in the run meanwhile
    report = carry_store_edits(baseline, latest, lines, {})
    assert lines[0]["source_column"] == "DISPLAY_NAME"
    assert report["conflicts"][0]["field"] == "source_column"


def test_nothing_changes_without_store_edits():
    same = {"lines": [_sttm_line("NAME")], "table_design": {"grain": "g"}}
    lines = [_sttm_line("NAME")]
    report = carry_store_edits(same, same, lines, {"grain": "g"})
    assert report == {"applied": [], "added": [], "removed": [], "conflicts": []} and "human_approved" not in lines[0]


# ---------------------------------------------------------------- H6: run design saved over a store-edited table model

from services.modeling import table_model as tm, table_model_sync as tms  # noqa: E402

MID = tm.table_model_id("CRM", "CUSTOMER")


def _spec(*cols, **entity):
    attrs = [{"name": n, "datatype": "VARCHAR(50)", "source_columns": [f"CRM_ACCOUNT.{n}"], **extra} for n, extra in cols]
    return tm.single({"design": {"entities": [{"entity_name": "CUSTOMER", "attributes": attrs, **entity}]}}, MID,
                     domain="SALES")


def _attr(spec, name):
    return next((a for a in spec["design"]["entities"][0]["attributes"] if a["name"] == name), None)


def test_store_edits_win_and_untouched_fields_follow_the_run():
    base = _spec(("ID", {}), ("NAME", {}), ("LEGACY", {}), ("FAX", {}))
    ours = _spec(("ID", {"semantic_type": "SURROGATE_KEY"}), ("NAME", {"datatype": "VARCHAR(200)"}),
                 ("LEGACY", {}), ("NOTES", {}))  # FAX removed in the store, NOTES added there
    theirs = _spec(("ID", {"datatype": "NUMBER(38,0)"}), ("NAME", {"datatype": "VARCHAR(80)", "description": "Name"}),
                   ("FAX", {}), ("EMAIL", {}))  # LEGACY dropped by the run, EMAIL new
    out = tms.merge_run_spec(base, ours, theirs)
    assert _attr(out, "ID")["semantic_type"] == "SURROGATE_KEY" and _attr(out, "ID")["datatype"] == "NUMBER(38,0)"
    assert _attr(out, "NAME")["datatype"] == "VARCHAR(200)" and _attr(out, "NAME")["description"] == "Name"
    assert _attr(out, "NOTES") is not None and _attr(out, "EMAIL") is not None
    assert _attr(out, "FAX") is None  # removed in the store: stays removed
    assert _attr(out, "LEGACY") is None  # dropped by the run and never edited in the store


def test_a_column_edited_in_the_store_is_kept_when_the_run_drops_it():
    base = _spec(("ID", {}), ("LEGACY", {}))
    ours = _spec(("ID", {}), ("LEGACY", {"description": "still used by finance"}))
    out = tms.merge_run_spec(base, ours, _spec(("ID", {})))
    assert _attr(out, "LEGACY")["description"] == "still used by finance"


def test_without_a_run_version_the_store_owns_what_it_filled_in():
    ours = _spec(("ID", {"datatype": "NUMBER(38,0)"}), ("NAME", {}))
    theirs = _spec(("ID", {"datatype": "VARCHAR(10)"}), ("NAME", {"description": "From the run"}), ("EMAIL", {}))
    out = tms.merge_run_spec(None, ours, theirs)
    assert _attr(out, "ID")["datatype"] == "NUMBER(38,0)" and _attr(out, "NAME")["description"] == "From the run"
    assert _attr(out, "EMAIL") is not None


class _Row(dict):
    def as_dict(self):
        return dict(self)

    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else dict.__getitem__(self, key)


class _Session:
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
                        return [_Row(r) for r in found]
                return []
        return R()


def test_register_from_run_merges_into_a_store_edited_model():
    base = _spec(("ID", {}), ("NAME", {}))
    edited = _spec(("ID", {}), ("NAME", {"datatype": "VARCHAR(200)"}))
    design = {"target_database": "", "target_schema": "", "entities": [
        {"entity_name": "CUSTOMER", "attributes": [{"name": "ID", "datatype": "VARCHAR(50)", "source_columns": ["CRM_ACCOUNT.ID"]},
                                                   {"name": "NAME", "datatype": "VARCHAR(80)", "source_columns": ["CRM_ACCOUNT.NAME"]}]}]}
    header = {"TABLE_MODEL_ID": MID, "TABLE_NAME": "CUSTOMER", "SOURCE_ID": "s1", "SOURCE_NAME": "CRM", "SOURCE_TABLES": "[]",
              "DOMAIN_ID": "d1", "ORIGIN": "RUN", "ORIGIN_RUN_ID": "r1", "LAST_RUN_ID": "r1", "MODEL_LAYER": "SILVER",
              "TABLE_TYPE": "STANDARD", "CURRENT_VERSION": 2, "ETAG": "e2", "DIAGRAM_LAYOUT": None, "LINKED_MODEL_ID": None,
              "LINKED_ENTITY": None, "LAST_CHANGE_ID": None, "CREATED_BY": "A", "CREATED_AT": None, "UPDATED_BY": "A",
              "UPDATED_AT": None, "DOMAIN_NAME": "SALES", "ORIGIN_RUN_NAME": "crm"}
    versions = [{"VERSION": 2, "STATUS": "DRAFT", "ORIGIN": "USER"}, {"VERSION": 1, "STATUS": "DRAFT", "ORIGIN": "RUN"}]
    specs = {1: base, 2: edited}
    session = _Session({
        "FROM CORE.WORKFLOW_RUN": [{"RUN_ID": "r2", "SOURCE_SYSTEM_ID": "s1"}],
        "FROM CORE.SOURCE_REGISTRY": [{"SOURCE_SYSTEM_ID": "s1", "SOURCE_SYSTEM_NAME": "CRM"}],
        "FROM CORE.MODEL_DESIGN": [{"DESIGN_JSON": json.dumps(design)}],
        "SELECT TABLE_MODEL_ID FROM CORE.TABLE_MODEL WHERE": [{"TABLE_MODEL_ID": MID}],
        "FROM CORE.TABLE_MODEL M": [header],
        "SELECT SPEC FROM CORE.TABLE_MODEL_VERSION": lambda p: [{"SPEC": json.dumps(specs[int(p[1])])}],
        "FROM CORE.TABLE_MODEL_VERSION WHERE TABLE_MODEL_ID = ? ORDER BY VERSION DESC": versions,
        "COALESCE(MAX(VERSION), 0)": [{"V": 2}],
        "SELECT ETAG FROM CORE.TABLE_MODEL": lambda p: [{"ETAG": session.etag}],
        "UPDATE CORE.TABLE_MODEL SET CURRENT_VERSION": lambda p: setattr(session, "etag", p[1]) or [],
    })
    session.etag = None
    out = tms.register_from_run(session, "r2", 1, "ann")
    assert out[0]["status"] == "UNCHANGED"  # the run brings nothing the store had not already decided
    design["entities"][0]["attributes"].append({"name": "EMAIL", "datatype": "VARCHAR(80)", "source_columns": ["CRM_ACCOUNT.EMAIL"]})
    session.answers["FROM CORE.MODEL_DESIGN"] = [{"DESIGN_JSON": json.dumps(design)}]
    out = tms.register_from_run(session, "r2", 2, "ann")
    assert out[0]["status"] == "VERSIONED"
    stored = next(p for s, p in session.calls if s.startswith("INSERT INTO CORE.TABLE_MODEL_VERSION"))
    spec = json.loads(stored[2])
    assert _attr(spec, "NAME")["datatype"] == "VARCHAR(200)" and _attr(spec, "EMAIL") is not None
