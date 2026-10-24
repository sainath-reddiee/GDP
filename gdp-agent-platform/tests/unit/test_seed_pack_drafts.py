"""A changed domain pack reaches a governed domain (one with an approved Model Workbench version) as a draft version
to approve, instead of being skipped; pack knowledge the pack no longer has is retired. The PROPERTY case: the old
pack had PROPERTY_OWNER and 7 knowledge items, the new one drops PROPERTY_OWNER and has 40 items."""

import json

from infrastructure.seed_knowledge import (ROOT, load_domain_packs, pack_changes, pack_digest, pack_drafts,
                                           retired_knowledge)
from services.knowledge.packs import domain_id
from services.modeling import spec_v2

NEW = json.loads((ROOT / "domain" / "property" / "domain_pack.json").read_text(encoding="utf-8"))


def _old_pack():
    """The previous PROPERTY pack: the same tables plus PROPERTY_OWNER."""
    old = json.loads(json.dumps(NEW))
    old["targets"].append({"schema": old["targets"][0]["schema"], "table": "PROPERTY_OWNER", "table_type": "SPOKE",
                           "business_keys": ["SOURCE_UNIQUE_ID"], "columns": [
                               {"name": "SOURCE_UNIQUE_ID", "type": "VARCHAR", "nullable": False},
                               {"name": "OWNER_NAME", "type": "VARCHAR", "nullable": True}]})
    return old


def _entities(spec):
    return {e["entity_name"]: e.get("status") for e in spec["design"]["entities"]}


def test_an_unchanged_pack_opens_no_draft():
    approved = spec_v2.spec_from_pack(NEW, "m1")
    assert pack_changes(approved, NEW, "m1") is None


def test_a_wording_change_alone_opens_no_draft():
    approved = spec_v2.spec_from_pack(NEW, "m1")
    reworded = json.loads(json.dumps(NEW))
    reworded["targets"][0]["description"] = "Different words"
    assert pack_changes(approved, reworded, "m1") is None


def test_the_new_property_pack_retires_property_owner_in_the_draft():
    approved = spec_v2.spec_from_pack(_old_pack(), "m1")
    merged = pack_changes(approved, NEW, "m1")
    assert merged is not None
    status = _entities(merged)
    assert status["PROPERTY_OWNER"] == "RETIRED"
    assert all(status[t["table"]] != "RETIRED" for t in NEW["targets"])
    assert not spec_v2.blocking(spec_v2.validate(merged, check_names=False))


def test_a_new_column_opens_a_draft_with_it():
    approved = spec_v2.spec_from_pack(NEW, "m1")
    grown = json.loads(json.dumps(NEW))
    grown["targets"][0]["columns"].append({"name": "LEED_RATING", "type": "VARCHAR", "nullable": True})
    merged = pack_changes(approved, grown, "m1")
    names = [a["name"] for e in merged["design"]["entities"] if e["entity_name"] == grown["targets"][0]["table"]
             for a in e["attributes"]]
    assert "LEED_RATING" in names


def test_only_unedited_seeded_pack_knowledge_the_pack_dropped_is_retired():
    did = domain_id("PROPERTY")
    row = {"DOMAIN_ID": did, "ORIGIN": "SEED", "CREATED_BY": "SEED", "VERSION": 1}
    current = [
        {**row, "SOURCE_REFERENCE": "property.model.property_owner"},          # dropped from the pack: retired
        {**row, "SOURCE_REFERENCE": "property.model.property_core"},           # still in the pack
        {**row, "SOURCE_REFERENCE": "property.guide.known_sources", "VERSION": 2},     # edited by a person
        {**row, "SOURCE_REFERENCE": "property.guide.minimum_mapping", "CREATED_BY": "ALICE"},
        {**row, "SOURCE_REFERENCE": "qa.pack.p1.row_count"},                   # a QA pack item: its own seeder
        {**row, "DOMAIN_ID": "someone-else", "SOURCE_REFERENCE": "property.x"},
    ]
    assert retired_knowledge(current, load_domain_packs()) == [(did, "property.model.property_owner")]


class Cur:
    """A connector cursor over one governed PROPERTY model: approved v1 (the old pack), no open version."""

    def __init__(self, versions):
        self.sql, self.description, self._rows, self.versions = [], [], [], versions
        self.spec = spec_v2.spec_from_pack(_old_pack(), "m1")

    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        self._rows, self.description = [], [("X",)]
        if "FROM CORE.MODEL_DESIGNS" in sql and "SELECT SPEC" in sql:
            self._rows, self.description = [(json.dumps(self.spec),)], [("SPEC",)]
        elif "FROM CORE.MODEL_DESIGNS" in sql and "CHANGE_NOTE" in sql and sql.lstrip().startswith("SELECT"):
            cols = ["VERSION", "STATUS", "CHANGE_NOTE"]
            self.description = [(c,) for c in cols]
            self._rows = [(v["version"], v["status"], v.get("change_note")) for v in self.versions]

    def fetchall(self):
        return self._rows


def test_a_pack_already_proposed_once_is_not_proposed_again(monkeypatch):
    did = domain_id("PROPERTY")
    note = f"Pack update from domain/property/domain_pack.json ({pack_digest(NEW)})"
    cur = Cur([{"version": 2, "status": "REJECTED", "change_note": note}, {"version": 1, "status": "APPROVED"}])
    assert pack_drafts(cur, "DEV", {did: 1}) == []
    assert not any("INSERT" in s for s, _ in cur.sql)


def test_an_open_version_is_left_alone():
    did = domain_id("PROPERTY")
    cur = Cur([{"version": 2, "status": "DRAFT"}, {"version": 1, "status": "APPROVED"}])
    log = pack_drafts(cur, "DEV", {did: 1})
    assert len(log) == 1 and "version 2 is already open" in log[0]


def test_a_draft_never_drops_a_column_the_pack_does_not_list():
    # the approved model has a live column the contract does not list: no draft for that alone, and a draft opened for
    # another reason keeps it
    approved = spec_v2.spec_from_pack(NEW, "m1")
    first = approved["design"]["entities"][0]
    first["attributes"].append({**first["attributes"][0], "name": "LIVE_ONLY_COLUMN", "id": "x-live"})
    approved = spec_v2.normalize_v2(approved, "m1")
    assert pack_changes(approved, NEW, "m1") is None
    grown = json.loads(json.dumps(NEW))
    grown["targets"][1]["columns"].append({"name": "NEW_COL", "type": "VARCHAR", "nullable": True})
    merged = pack_changes(approved, grown, "m1")
    names = {a["name"] for e in merged["design"]["entities"] if e["entity_name"] == first["entity_name"] for a in e["attributes"]}
    assert "LIVE_ONLY_COLUMN" in names
