"""The skill scout: what counts as approved, how knowledge clusters, what is skipped, the generaliser (and its hard
failure), the transfer score, the cadence, the caps, and a scan that files a DRAFT with ORIGIN AI and never moves a label."""

import json
from datetime import datetime, timedelta, timezone

from services.common import platform_config
from services.knowledge import scout, scout_run, skill_drafts

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
CONF = scout.settings(None)


class FakeDb:
    def __init__(self):
        self.handlers, self.queries, self.writes = [], [], []

    def on(self, marker, value):
        self.handlers.insert(0, (marker, value))
        return self

    def query(self, sql, params=()):
        self.queries.append((sql, params))
        for marker, value in self.handlers:
            if marker in sql:
                found = value(sql, params) if callable(value) else value
                return [dict(r) for r in found]
        return []

    def execute(self, sql, params=()):
        self.writes.append((" ".join(sql.split()), params))

    def execute_count(self, sql, params=()):
        self.writes.append((" ".join(sql.split()), params))
        return 1

    def wrote(self, needle):
        return [p for s, p in self.writes if needle in s]


def item(i, domain="d1", ktype="MAPPING_PATTERN", tags=("SCD2",), **over):
    return {"knowledge_id": f"k{i}", "lineage_id": f"l{i}", "domain_id": domain, "domain_name": domain.upper(),
            "knowledge_type": ktype, "title": f"Track history with effective dates {i}",
            "content": f"Map ACME_DW.SALES.CUSTOMER_DIM valid_from from the source change date ({i}).",
            "tags": list(tags), "source_reference": f"ref{i}", "origin": "USER", "verified_by": None, "reviewed_by": None,
            "runs": 2, "observed": 4, "helpfulness": 0.8, **over}


# ---------------------------------------------------------------- settings and cadence


def test_settings_clamp_and_validate():
    s = scout.settings({"max_per_run": 99, "min_helpfulness": -1, "enabled": False, "junk": 1})
    assert s["max_per_run"] == 10 and s["min_helpfulness"] == 0.0 and s["enabled"] is False
    _, problems = scout.validate({"cadence_days": 0, "nope": 1})
    assert any("cadence_days" in p for p in problems) and any("unknown setting nope" in p for p in problems)
    assert "SKILL_SCOUT" in platform_config.SETTINGS and "SKILL_SCOUT" not in platform_config.ADMIN_SETTINGS
    assert platform_config.validate("SKILL_SCOUT", {"max_per_run": 2})[0]["max_per_run"] == 2


def test_due_follows_the_cadence():
    assert scout.due(None, NOW, 7)
    assert not scout.due((NOW - timedelta(days=3)).isoformat(), NOW, 7)
    assert scout.due((NOW - timedelta(days=7)).isoformat(), NOW, 7)
    assert scout.due("garbage", NOW, 7)


# ---------------------------------------------------------------- approved and clusters


def test_only_approved_knowledge_counts():
    assert scout.approved({"origin": "USER"}, CONF) and scout.approved({"verified_by": "ANA", "origin": "AI"}, CONF)
    assert not scout.approved({"origin": "AI", "helpfulness": 0.9, "observed": 1}, CONF)   # too little evidence
    assert scout.approved({"origin": "AI", "helpfulness": 0.7, "observed": 5}, CONF)
    assert not scout.approved({"origin": "AI", "helpfulness": 0.4, "observed": 9}, CONF)


def test_clusters_by_theme_and_title_and_keep_only_strong_ones():
    items = [item(i, domain=f"d{i % 2}") for i in range(5)]                       # SCD2 theme, two domains
    items += [item(10 + i, tags=(), ktype="SODA_PATTERN", domain="d1", runs=3,
                   title=f"Null rate check on key columns {i}") for i in range(5)]  # similar titles, one domain, 15 runs
    items += [item(20 + i, tags=("RARE",)) for i in range(3)]                      # too few
    items += [item(30 + i, origin="AI", helpfulness=None, tags=("UNREVIEWED",)) for i in range(6)]   # not approved
    found = scout.clusters(items, CONF)
    keys = [c["key"] for c in found]
    assert "MAPPING_PATTERN:SCD2" in keys and not any("RARE" in k or "UNREVIEWED" in k for k in keys)
    soda = next(c for c in found if c["type"] == "SODA_PATTERN")
    assert soda["key"].startswith("SODA_PATTERN:~") and len(soda["items"]) == 5 and soda["stage"] == "SODA"
    scd = next(c for c in found if c["key"] == "MAPPING_PATTERN:SCD2")
    assert scd["domains"] == 2 and scd["stage"] == "MAPPING"
    lonely = [item(40 + i, domain="d1", runs=0, tags=("ONE",)) for i in range(5)]
    assert scout.clusters(lonely, CONF) == []   # one domain and no runs: not repeated work


def test_skip_reasons():
    c = scout.clusters([item(i, domain=f"d{i % 2}") for i in range(6)], CONF)[0]
    assert scout.skip_reason(c, {"status": "PROPOSED", "evidence_count": 6}, set(), []) == "already proposed"
    assert "doubles" in scout.skip_reason(c, {"status": "DISMISSED", "evidence_count": 4}, set(), [])
    assert scout.skip_reason(c, {"status": "DISMISSED", "evidence_count": 3}, set(), []) is None
    assert "cites" in scout.skip_reason(c, None, {f"ref{i}" for i in range(4)}, [])
    similar = [{"skill_name": "SCD-HISTORY", "description": "Track history with effective dates"}]
    assert "similar to SCD-HISTORY" in scout.skip_reason(c, None, set(), similar)


# ---------------------------------------------------------------- generaliser and transfer


LEX = scout.lexicon({"DATABASE": ["ACME_DW"], "SCHEMA": ["SALES"], "TABLE": ["CUSTOMER_DIM"], "DOMAIN": ["Acme Retail"],
                     "DAG": ["acme_nightly_load"], "SOURCE": ["Salesforce"], "ENV": ["x"]})


def test_generalise_replaces_names_and_masks_secrets():
    text = ("For Acme Retail, load ACME_DW.SALES.CUSTOMER_DIM from Salesforce in acme_nightly_load; "
            "sales teams review it. password=hunter22")
    out, counts, residual = scout.generalise(text, LEX)
    assert "<DATABASE>.<SCHEMA>.<TABLE>" in out and "<DOMAIN>" in out and "<SOURCE_SYSTEM>" in out and "<DAG>" in out
    assert "sales teams" in out   # a generic word in prose stays
    assert "hunter22" not in out and residual == []
    assert counts == {"DATABASE": 1, "SCHEMA": 1, "TABLE": 1, "DOMAIN": 1, "SOURCE": 1, "DAG": 1}
    assert ("ENV", "x") not in LEX   # too short to replace safely


def test_generalise_fails_hard_on_unknown_qualified_names():
    _, _, residual = scout.generalise("select * from OTHER_DB.FIN.LEDGER", LEX)
    assert residual and "qualified name" in residual[0]
    _, _, ok = scout.generalise("select * from <DATABASE>.<SCHEMA>.<TABLE>", LEX)
    assert ok == []


def test_transfer_score_labels():
    good = scout.transfer(4, {"candidate_rate": 1.0, "baseline_rate": 0.5}, 2, 4000)
    assert good["label"] == "transfers well" and good["score"] >= 70
    narrow = scout.transfer(1, {"candidate_rate": 0.5, "baseline_rate": 0.5}, 80, 2000)
    assert narrow["label"] == "client specific"
    assert scout.transfer(2, None, 0, 1000)["parts"]["quality"] == 20.0


# ---------------------------------------------------------------- the scan


DRAFT = {"name": "EFFECTIVE-DATE-HISTORY", "title": "Effective date history", "category": "mapping",
         "description": "How to map slowly changing history with effective dates for <TABLE> targets.",
         "sections": [{"heading": "When to use", "markdown": "When <TABLE> keeps history."},
                      {"heading": "Steps", "markdown": "Map valid_from from the change date."}],
         "tests": [{"title": "a", "input": "map history", "expectation": "valid_from"},
                   {"title": "b", "input": "map current flag", "expectation": "is_current"}],
         "used_knowledge": ["ref0"]}


def fake_complete(draft=DRAFT, verdict=("candidate_pass", "baseline_pass")):
    calls = []

    def complete(prompt, schema, max_tokens):
        calls.append(prompt)
        if "sections" in schema.get("properties", {}):
            return dict(draft), {"total_tokens": 10}, "claude"
        if "answers" in schema.get("properties", {}):
            return {"answers": [{"index": 0, "answer": "x"}, {"index": 1, "answer": "y"}]}, {}, "claude"
        cand, base = verdict
        return {"results": [{"index": i, "candidate_pass": cand == "candidate_pass", "baseline_pass": base == "baseline_pass",
                             "reason": "r"} for i in range(2)]}, {}, "claude"
    complete.calls = calls
    return complete


def scan_db():
    items = [item(i, domain=f"d{i % 2}") for i in range(6)]
    db = FakeDb()
    db.on("FROM CORE.DOMAIN_KNOWLEDGE K", items)
    db.on("SELECT DOMAIN_NAME AS N", [{"n": "Acme Retail"}])
    db.on("TARGET_DATABASE AS N", [{"n": "ACME_DW"}])
    db.on("TARGET_SCHEMA AS N", [{"n": "SALES"}])
    db.on("TARGET_TABLE AS N", [{"n": "CUSTOMER_DIM"}])
    db.on("FROM CORE.SKILL_CATEGORY", [{"category_id": "mapping"}, {"category_id": "general"}])
    db.on("FROM CORE.SKILL_REGISTRY", [{"skill_id": "s0", "skill_name": "AI-SCHEMA-MAPPING", "description": "Map columns",
                                        "config": None, "eval_json": None}])
    db.on("FROM CORE.SKILL_STAGE_BINDING", [{"stage": "MAPPING", "skill_name": "AI-SCHEMA-MAPPING"}])
    db.on("FROM CORE.SKILL_LABEL L", [{"content": "# Mapping\nUse the catalogue.", "version": "1.0.0"}])
    return db


def test_scan_files_a_draft_with_origin_ai_and_never_a_label():
    db = scan_db()
    complete = fake_complete(verdict=("candidate_pass", "baseline_fail"))
    out = scout_run.scan(db, complete=complete)
    assert len(out["proposed"]) == 1 and not out["errors"], out
    assert "ACME_DW" not in complete.calls[0] and "<DATABASE>.<SCHEMA>.<TABLE>" in complete.calls[0]
    insert = db.wrote("INSERT INTO CORE.SKILL_REGISTRY")[0]
    assert insert[1] == "EFFECTIVE-DATE-HISTORY" and insert[7] == "AI"
    config = json.loads(insert[-1])["scout"]
    assert config["cluster_key"] == "MAPPING_PATTERN:SCD2" and config["evidence_count"] == 6
    assert {"from_kind": "database", "placeholder": "<DATABASE>", "count": 6} in config["replacements"]
    assert not [w for w in db.writes if "SKILL_LABEL" in w[0]]
    proposal = db.wrote("INSERT INTO CORE.SKILL_PROPOSAL")[0]
    assert proposal[2] == "PROPOSED" and proposal[3] == insert[0] and proposal[12] in ("transfers well", "transfers with care")


def test_scan_discards_worse_drafts_and_leaked_names():
    db = scan_db()
    out = scout_run.scan(db, complete=fake_complete(verdict=("candidate_fail", "baseline_pass")))
    assert out["discarded"] and "did worse" in out["discarded"][0]["reason"]
    assert not db.wrote("INSERT INTO CORE.SKILL_REGISTRY")
    leaky = {**DRAFT, "sections": DRAFT["sections"] + [{"heading": "Example", "markdown": "Use OTHER_DB.FIN.LEDGER."}]}
    db2 = scan_db()
    out2 = scout_run.scan(db2, complete=fake_complete(draft=leaky))
    assert out2["discarded"] and "client specific" in out2["discarded"][0]["reason"]
    assert db2.wrote("INSERT INTO CORE.SKILL_PROPOSAL")[0][2] == "DISCARDED"


def test_scan_renames_a_clashing_name_and_respects_the_cap():
    db = scan_db()
    db.on("FROM CORE.SKILL_REGISTRY", [{"skill_id": "s1", "skill_name": "EFFECTIVE-DATE-HISTORY", "description": "x",
                                        "config": None, "eval_json": None}])
    scout_run.scan(db, complete=fake_complete(verdict=("candidate_pass", "baseline_fail")))
    assert db.wrote("INSERT INTO CORE.SKILL_REGISTRY")[0][1] == "EFFECTIVE-DATE-HISTORY-SCOUT"
    many = scan_db()
    many.on("FROM CORE.DOMAIN_KNOWLEDGE K", [item(i, domain=f"d{i % 2}", tags=(f"T{i % 3}X",)) for i in range(30)])
    out = scout_run.scan(many, limit=1, complete=fake_complete(verdict=("candidate_pass", "baseline_fail")))
    assert len(out["proposed"]) + len(out["discarded"]) == 1


def test_plan_is_a_dry_run_without_ai():
    db = scan_db()
    db.on("FROM CORE.SKILL_PROPOSAL", [{"cluster_key": "MAPPING_PATTERN:SCD2", "status": "PROPOSED", "evidence_count": 6}])
    out = scout_run.plan(db)
    assert out["clusters"][0]["skip"] == "already proposed" and out["due"] == []


def test_weekly_runs_on_its_cadence(monkeypatch):
    db = FakeDb().on("FROM CORE.JOB_LEASE", [{"cursor_value": (NOW - timedelta(days=2)).isoformat()}])
    assert scout_run.weekly(db, now=NOW) == {"enabled": True, "due": False}
    ran = []
    monkeypatch.setattr(scout_run, "scan", lambda db, **kw: ran.append(1) or {"proposed": [], "discarded": [], "errors": []})
    old = FakeDb().on("FROM CORE.JOB_LEASE", [{"cursor_value": (NOW - timedelta(days=8)).isoformat()}])
    assert scout_run.weekly(old, now=NOW)["due"] and ran and old.wrote("SET CURSOR_VALUE")[0][0] == NOW.isoformat()
    off = FakeDb().on("FROM CORE.PLATFORM_CONFIG", [{"config_value": json.dumps({"enabled": False})}])
    assert scout_run.weekly(off, now=NOW) == {"enabled": False}


def test_skill_drafts_evaluate_and_insert():
    summary = skill_drafts.evaluate(fake_complete(verdict=("candidate_pass", "baseline_fail")), "content",
                                    DRAFT["tests"], "", "no skill")
    assert summary["verdict"] == "better" and summary["rows"][0]["candidate_answer"] == "x"
    db = FakeDb()
    sid = skill_drafts.insert_skill(db, "X-SKILL", "description long enough", "mapping", "body", "note", "AI",
                                    {"verdict": "better"}, {"scout": {"k": 1}})
    params = db.wrote("INSERT INTO CORE.SKILL_REGISTRY")[0]
    assert params[0] == sid and params[2] == "MAPPING" and json.loads(params[-1]) == {"scout": {"k": 1}}


def test_policy_and_worker_know_the_scout():
    import sys
    from pathlib import Path

    from services.governance import policy

    assert policy.privilege_for("POST", "/api/skill-proposals/scan")[0] == "SKILL.EDIT"
    assert policy.privilege_for("POST", "/api/skill-proposals/p1/dismiss")[0] == "SKILL.EDIT"
    assert policy.no_queue("POST", "/api/skill-proposals/scan")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))
    from app import worker

    assert worker.INTERVALS["skill_scout"] == 3600 and callable(worker.skill_scout)
