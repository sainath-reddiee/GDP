"""Ready to build (services/workflow/readiness.py): design readiness and build status per run, grouped by domain."""

from services.workflow.readiness import board, leaf

APPROVED = {"d1": {"domain_name": "COMPANY", "approved_version": 3}, "d2": {"domain_name": "PROPERTY", "approved_version": None}}


def run(run_id, state, domain="d1", parent=None, **over):
    return {"run_id": run_id, "run_name": f"run {run_id}", "domain_id": domain, "current_state": state,
            "parent_run_id": parent, **over}


def test_a_run_is_ready_once_model_mapping_and_sttm_are_approved():
    for state in ("STTM_APPROVED", "SODA_REVIEW", "SODA_APPROVED", "DBT_PENDING"):
        r = leaf(run("a", state), model_approved=True)
        assert r["status"] == "READY" and r["percent"] == 100 and r["waiting"] is None, state
    # data quality approval is shown, never waited for
    assert leaf(run("a", "STTM_APPROVED"), True)["design"]["dq"] is False
    assert leaf(run("a", "SODA_APPROVED"), True)["design"]["dq"] is True


def test_design_done_but_the_domain_model_not_approved_waits_on_the_designer():
    r = leaf(run("a", "STTM_APPROVED"), model_approved=False)
    assert r["status"] == "MODEL" and r["percent"] == 67 and "approve the domain model" in r["waiting"]


def test_runs_still_in_design_say_who_acts_next_and_how_far_they_are():
    mapping = leaf(run("a", "MAPPING_REVIEW"), True)
    assert mapping["status"] == "DESIGN" and mapping["percent"] == 33 and "approve the field mapping" in mapping["waiting"]
    sttm = leaf(run("a", "STTM_REVIEW"), True)
    assert sttm["status"] == "DESIGN" and sttm["percent"] == 67 and sttm["design"]["mapping"] and not sttm["design"]["sttm"]
    assert leaf(run("a", "LANDING_COMPLETE"), False)["percent"] == 0
    enable = leaf(run("a", "SOURCE_REGISTERED"), True)
    assert enable["status"] == "ENABLE" and "Data OS Administrator" in enable["waiting"]


def test_build_states_after_the_design():
    assert leaf(run("a", "VALIDATION_RUNNING"), True)["status"] == "BUILDING"
    flagged = leaf(run("a", "VALIDATION_FAILED"), True)
    assert flagged["status"] == "FLAGGED" and "validate again" in flagged["waiting"]
    assert leaf(run("a", "DBT_REVIEW"), True)["status"] == "REVIEW"
    for state in ("DBT_APPROVED", "COMPLETED", "DEPLOYED"):
        assert leaf(run("a", state), True)["status"] == "BUILT"


def test_a_failed_run_needs_attention_and_keeps_the_design_it_had():
    r = leaf(run("a", "FAILED", failed_from_state="DBT_GENERATING", failure_reason="compile error"), True)
    assert r["status"] == "ATTENTION" and "compile error" in r["waiting"] and r["design"]["sttm"] and r["percent"] == 100


def test_the_board_builds_table_runs_not_their_parent_and_groups_by_domain():
    runs = [run("p", "MAPPING_APPROVED", source_system_name="ZOOMINFO"),
            run("c1", "STTM_APPROVED", domain=None, parent="p"), run("c2", "STTM_REVIEW", domain=None, parent="p"),
            run("x", "SODA_APPROVED", domain="d2"), run("gone", "CANCELLED"),
            run("done", "COMPLETED")]
    out = board(runs, APPROVED, history={"c1": {"last_score": 92.5, "attempts": 2}},
                requests={"p": {"request_id": "r1", "title": "COMPANY from ZoomInfo"}})
    company = next(d for d in out["domains"] if d["domain_name"] == "COMPANY")
    ids = [x["run_id"] for x in company["runs"]]
    assert "p" not in ids and "gone" not in ids and ids[0] == "c1"   # ready first
    c1 = company["runs"][0]
    # a table run inherits the parent's domain, source and request
    assert c1["status"] == "READY" and c1["source_system_name"] == "ZOOMINFO" and c1["parent_name"] == "run p"
    assert c1["request"] == {"request_id": "r1", "title": "COMPANY from ZoomInfo"} and c1["score"] == 92.5 and c1["attempts"] == 2
    assert company["counts"]["READY"] == 1 and company["counts"]["DESIGN"] == 1 and company["counts"]["BUILT"] == 1
    # the domain's readiness leaves built runs out: (100 + 67) / 2
    assert company["percent"] == 84 and company["model_approved"]
    prop = next(d for d in out["domains"] if d["domain_name"] == "PROPERTY")
    assert prop["runs"][0]["status"] == "MODEL" and not prop["model_approved"]
    assert out["domains"][0]["domain_name"] == "COMPANY"   # domains with something ready come first
    assert out["totals"]["READY"] == 1 and out["totals"]["MODEL"] == 1


def test_a_run_without_a_domain_is_grouped_apart_and_never_ready():
    out = board([run("n", "STTM_APPROVED", domain=None)], APPROVED)
    group = out["domains"][0]
    assert group["domain_name"] == "No domain" and group["runs"][0]["status"] == "MODEL"


def test_runs_of_retired_domains_are_one_group_apart_from_runs_without_a_domain():
    out = board([run("a", "STTM_REVIEW", domain="old1"), run("b", "STTM_REVIEW", domain="old2"),
                 run("c", "STTM_REVIEW", domain=None)], APPROVED)
    names = sorted(d["domain_name"] for d in out["domains"])
    assert names == ["Inactive domain", "No domain"]
    inactive = next(d for d in out["domains"] if d["domain_name"] == "Inactive domain")
    assert len(inactive["runs"]) == 2 and inactive["domain_id"] is None
