"""A gold proposal aligned to the domain's reference gold model: the reference's tables, columns and keys are kept
exactly, matched tables take the reference names (and mapping lines follow), and only what the reference lacks is
added from silver, each with a note."""

from services.gold.align import align, match
from services.gold.propose import propose
from tests.unit.test_gold_propose import CONSUMES, HUB, MASTERED, ROLE


def attr(name, datatype="STRING", pk=False):
    return {"name": name, "datatype": datatype, "is_pk": pk, "nullable": not pk}


REFERENCE = {"design": {"entities": [
    {"entity_name": "OPPORTUNITY_FACT", "table_type": "TABLE", "attributes": [
        attr("OPPORTUNITY_FACT_SKEY", "NUMBER(38,0)", pk=True), attr("SOURCE_UNIQUE_ID"), attr("DIM_DATA_SOURCE_SKEY", "NUMBER(38,0)"),
        attr("DIM_COMPANY_SKEY", "NUMBER(38,0)"), attr("OPPORTUNITY_NAME", "VARCHAR(500)"), attr("DEAL_AMOUNT", "NUMBER(18,2)"),
        attr("CLOSE_DATE", "DATE"), attr("SENT_F", "BOOLEAN"), attr("NEXT_STEP")]},
    {"entity_name": "DIM_OPPORTUNITY_OWNER", "table_type": "DIMENSION", "attributes": [
        attr("DIM_OPPORTUNITY_OWNER_SKEY", "NUMBER(38,0)", pk=True), attr("OWNER_NAME")]},
]}, "relationships": [], "physical_defaults": {"database": "PRD_GOLD_OPPORTUNITY_DB", "schema": "OPPORTUNITY"}}


def _ent(spec, name):
    return next(e for e in spec["design"]["entities"] if e["entity_name"] == name)


def test_matching_by_name_subject_and_columns():
    out = propose("OPPORTUNITY", [HUB, ROLE], consumes=CONSUMES, mastered=MASTERED)
    pairs = match(out["spec"]["design"]["entities"], REFERENCE["design"]["entities"])
    assert pairs["FACT_OPPORTUNITY"] == ("OPPORTUNITY_FACT", "same subject")
    assert "DIM_COMPANY" not in pairs                          # a conformed dimension is never matched to a local table
    by_cols = match([{"entity_name": "FACT_DEALS", "attributes": [attr("OPPORTUNITY_NAME"), attr("DEAL_AMOUNT"), attr("NEXT_STEP")]}],
                    REFERENCE["design"]["entities"])
    assert by_cols["FACT_DEALS"][0] == "OPPORTUNITY_FACT" and "%" in by_cols["FACT_DEALS"][1]


def test_reference_is_kept_exactly_and_only_what_it_lacks_is_added():
    out = align(propose("OPPORTUNITY", [HUB], consumes=CONSUMES, mastered=MASTERED), REFERENCE, "your reference")
    spec = out["spec"]
    names = [e["entity_name"] for e in spec["design"]["entities"]]
    assert "FACT_OPPORTUNITY" not in names and names[0] == "OPPORTUNITY_FACT"
    fact = _ent(spec, "OPPORTUNITY_FACT")
    types = {a["name"]: a["datatype"] for a in fact["attributes"]}
    assert types["OPPORTUNITY_NAME"] == "VARCHAR(500)" and types["DEAL_AMOUNT"] == "NUMBER(18,2)"   # the reference's types
    assert "SENT_F" in types and "SENT_FLAG" not in types                                           # the reference's spelling
    assert "NEXT_STEP" in types                                                                       # reference-only column kept
    assert "WIN_RATE" in types and "Not in your reference" in next(a for a in fact["attributes"] if a["name"] == "WIN_RATE")["description"]
    assert fact["table_type"] == "FACT"                       # an untyped reference table takes the proposal's kind
    assert "DIM_OPPORTUNITY_OWNER" in names                    # reference-only table kept, with a note
    assert any("DIM_OPPORTUNITY_OWNER is in your reference" in n for n in out["notes"])
    assert any(n.startswith("DIM_OPPORTUNITY_STAGE is not in your reference") for n in out["notes"])
    assert spec["physical_defaults"]["database"] == "PRD_GOLD_OPPORTUNITY_DB"
    assert {"proposed": "FACT_OPPORTUNITY", "reference": "OPPORTUNITY_FACT", "how": "same subject"}.items() <= out["alignment"][0].items()


def test_mapping_lines_and_relationships_follow_the_reference_names():
    out = align(propose("OPPORTUNITY", [HUB], consumes=CONSUMES, mastered=MASTERED), REFERENCE, "your reference")
    tables = {l["gold_table"] for l in out["lines"]}
    assert "OPPORTUNITY_FACT" in tables and "FACT_OPPORTUNITY" not in tables
    cols = {l["gold_column"] for l in out["lines"] if l["gold_table"] == "OPPORTUNITY_FACT"}
    assert {"OPPORTUNITY_FACT_SKEY", "SENT_F", "DIM_COMPANY_SKEY"} <= cols and "SENT_FLAG" not in cols
    rels = {(r["from_entity"], r["from_column"], r["to_entity"]) for r in out["spec"]["relationships"]}
    assert ("OPPORTUNITY_FACT", "DIM_COMPANY_SKEY", "DIM_COMPANY") in rels


def test_a_reference_with_a_differently_named_key_takes_its_key():
    ref = {"design": {"entities": [{"entity_name": "FACT_OPPORTUNITY", "attributes": [attr("OPPORTUNITY_KEY", "NUMBER", pk=True),
                                                                                       attr("SOURCE_UNIQUE_ID")]}]}}
    out = align(propose("OPPORTUNITY", [HUB], consumes=CONSUMES, mastered=MASTERED), ref)
    key_line = next(l for l in out["lines"] if l["kind"] == "KEY" and l["gold_table"] == "FACT_OPPORTUNITY")
    assert key_line["gold_column"] == "OPPORTUNITY_KEY"
    assert "FACT_OPPORTUNITY_SKEY" not in [a["name"] for a in _ent(out["spec"], "FACT_OPPORTUNITY")["attributes"]]
