from services.knowledge import domain_versions as dv


def _snap(**kw):
    base = {"description": "Customers", "owner": "ANA", "active": True,
            "config": {"rules": {"quality.enum_max": 20}, "signals": {"tables": {"CUSTOMER": 1}}, "contract": "c.md",
                       "deleted_at": None},
            "targets": [{"table": "DB.S.DIM_CUSTOMER", "active": True, "columns": [
                {"name": "CUSTOMER_ID", "type": "NUMBER", "nullable": False, "key": True, "pii": False, "definition": None}]}]}
    base.update(kw)
    return base


def test_summary_lists_rule_owner_and_target_changes():
    a = _snap()
    b = _snap(owner="BEN", config={**a["config"], "rules": {"quality.enum_max": 30}},
              targets=a["targets"] + [{"table": "DB.S.DIM_ADDRESS", "active": True, "columns": []}])
    s = dv.summary(a, b)
    assert "Owner ANA -> BEN" in s
    assert "Rule quality.enum_max: 20 -> 30" in s
    assert "Target added: DIM_ADDRESS" in s
    assert dv.summary(None, a) == ["First recorded version"]
    assert dv.summary(a, a) == ["No visible change"]
    c = _snap(targets=[{**a["targets"][0], "columns": a["targets"][0]["columns"] + [{"name": "EMAIL"}]}])
    assert any(x.startswith("DIM_CUSTOMER: +1 columns") for x in dv.summary(a, c))


def test_diff_has_one_section_per_area_and_marks_changes():
    a, b = _snap(), _snap(description="Customers and prospects")
    files = {f["path"]: f for f in dv.diff(a, b)}
    assert set(files) == set(dv.SECTIONS.values())
    assert files["Description and owner"]["status"] == "changed"
    assert files["Rules"]["status"] == "same"


def test_rollback_restores_rules_but_keeps_deletion_and_origin():
    current = {"rules": {"x": 2}, "signals": {"new": 1}, "origin": "imported", "deleted_at": None, "standard": "GDP"}
    old = {"rules": {"x": 1}, "contract": "old.md"}
    out = dv.rollback_config(current, old)
    assert out["rules"] == {"x": 1} and out["contract"] == "old.md"
    assert "signals" not in out and "standard" not in out
    assert out["origin"] == "imported"


class FakeDb:
    def __init__(self):
        self.versions = []
        self.executed = []

    def query(self, sql, params):
        if "FROM KNOWLEDGE.DOMAIN_REGISTRY" in sql:
            return [{"DOMAIN_ID": "d1", "DOMAIN_NAME": "SALES", "DESCRIPTION": "Sales", "OWNER": "ANA",
                     "ACTIVE_FLAG": True, "CONFIG": '{"rules": {}}'}]
        if "FROM KNOWLEDGE.TARGET_TABLE_REGISTRY" in sql:
            return [{"TARGET_TABLE_ID": "t1", "TARGET_DATABASE": "db", "TARGET_SCHEMA": "s", "TARGET_TABLE": "orders",
                     "DESCRIPTION": None, "ACTIVE_FLAG": True, "TABLE_TYPE": "FACT", "GRAIN": None}]
        if "FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY" in sql:
            return [{"TARGET_TABLE_ID": "t1", "COLUMN_NAME": "ORDER_ID", "DATA_TYPE": "NUMBER", "ORDINAL_POSITION": 1,
                     "NULLABLE": False, "IS_BUSINESS_KEY": True, "IS_PII": False, "BUSINESS_DEFINITION": None}]
        if "FROM KNOWLEDGE.DOMAIN_VERSION" in sql:
            return [self.versions[-1]] if self.versions else []
        return []

    def execute(self, sql, params):
        self.executed.append(sql)
        if sql.lstrip().startswith("INSERT INTO KNOWLEDGE.DOMAIN_VERSION"):
            self.versions.append({"VERSION": params[1], "CHECKSUM": params[7]})


def test_snapshot_records_once_until_something_changes():
    db = FakeDb()
    assert dv.snapshot(db.query, db.execute, "d1", "DEPLOY") == 1
    assert dv.snapshot(db.query, db.execute, "d1", "DEPLOY") is None  # nothing changed
    snap = dv.state(db.query, "d1")
    assert snap["targets"][0]["table"] == "DB.S.ORDERS" and snap["targets"][0]["columns"][0]["key"] is True
    db.versions[-1]["CHECKSUM"] = "different"
    assert dv.snapshot(db.query, db.execute, "d1", "RULES") == 2
    assert any("UPDATE KNOWLEDGE.DOMAIN_REGISTRY SET VERSION" in s for s in db.executed)
