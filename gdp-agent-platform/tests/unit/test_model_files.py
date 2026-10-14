"""Data model files: Erwin XML, data dictionaries (Excel, CSV), DDL and packs, into a model draft."""

import io
import zipfile

import pytest

from services.modeling import importers, model_files, render_v2, spec_v2
from services.modeling.model_files import ModelFileError, parse_dictionary, parse_erwin_xml, parse_file, snowflake_type

C, A, P, R = "{11111111-1111-1111-1111-111111111111}", "{22222222-2222-2222-2222-222222222222}", \
    "{33333333-3333-3333-3333-333333333333}", "{44444444-4444-4444-4444-444444444444}"
C_ID, O_CID, O_ID = "{55555555-5555-5555-5555-555555555555}", "{66666666-6666-6666-6666-666666666666}", \
    "{77777777-7777-7777-7777-777777777777}"

# Erwin 7/8 style: properties in <XxxProps> blocks
ERWIN_PROPS = f"""<?xml version="1.0" encoding="UTF-8"?>
<ERwin4 xmlns="http://www.ca.com/erwin/data" FileVersion="8.2">
 <Model id="{{00000000-0000-0000-0000-000000000000}}" name="Sales">
  <Entity_Groups>
   <Entity id="{C}" name="Customer">
    <EntityProps><Physical_Name>CUSTOMER</Physical_Name><Definition>A buyer of our products.</Definition></EntityProps>
    <Attribute_Groups>
     <Attribute id="{C_ID}" name="Customer Id">
      <AttributeProps><Physical_Name>%AttName</Physical_Name><Physical_Data_Type>NUMBER(10)</Physical_Data_Type>
       <Null_Option_Type>1</Null_Option_Type><Definition>Surrogate id</Definition></AttributeProps>
     </Attribute>
     <Attribute id="{{88888888-8888-8888-8888-888888888888}}" name="Customer Name">
      <AttributeProps><Physical_Name>CUSTOMER_NAME</Physical_Name><Physical_Data_Type>VARCHAR2(200 BYTE)</Physical_Data_Type>
       <Null_Option_Type>0</Null_Option_Type></AttributeProps>
     </Attribute>
    </Attribute_Groups>
    <Key_Group_Groups>
     <Key_Group id="{{99999999-9999-9999-9999-999999999999}}" name="XPKCustomer">
      <Key_GroupProps><Key_Group_Type>PK</Key_Group_Type></Key_GroupProps>
      <Key_Group_Member_Groups><Key_Group_Member id="{{aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa}}" name="Customer Id">
       <Key_Group_MemberProps><Attribute_Ref>{C_ID}</Attribute_Ref></Key_Group_MemberProps></Key_Group_Member>
      </Key_Group_Member_Groups>
     </Key_Group>
    </Key_Group_Groups>
   </Entity>
   <Entity id="{A}" name="Sales Order">
    <EntityProps><Physical_Name>SALES_ORDER</Physical_Name></EntityProps>
    <Attribute_Groups>
     <Attribute id="{O_ID}" name="Order Id"><AttributeProps><Physical_Data_Type>INTEGER</Physical_Data_Type>
      <Null_Option_Type>1</Null_Option_Type></AttributeProps></Attribute>
     <Attribute id="{O_CID}" name="Customer Id"><AttributeProps><Physical_Data_Type>NUMBER(10)</Physical_Data_Type>
      <Parent_Relationship_Ref>{R}</Parent_Relationship_Ref><Parent_Attribute_Ref>{C_ID}</Parent_Attribute_Ref>
      <Null_Option_Type>1</Null_Option_Type></AttributeProps></Attribute>
     <Attribute id="{{bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb}}" name="Ordered At"><AttributeProps>
      <Physical_Data_Type>DATETIME</Physical_Data_Type></AttributeProps></Attribute>
    </Attribute_Groups>
    <Key_Group_Groups><Key_Group id="{{cccccccc-cccc-cccc-cccc-cccccccccccc}}" name="XPKSales_Order">
     <Key_GroupProps><Key_Group_Type>PK</Key_Group_Type></Key_GroupProps>
     <Key_Group_Member_Groups><Key_Group_Member id="{{dddddddd-dddd-dddd-dddd-dddddddddddd}}">
      <Key_Group_MemberProps><Attribute_Ref>{O_ID}</Attribute_Ref></Key_Group_MemberProps></Key_Group_Member></Key_Group_Member_Groups>
    </Key_Group></Key_Group_Groups>
   </Entity>
  </Entity_Groups>
  <Relationship_Groups>
   <Relationship id="{R}" name="R/1"><RelationshipProps><Parent_Entity_Ref>{C}</Parent_Entity_Ref>
    <Child_Entity_Ref>{A}</Child_Entity_Ref><Null_Option_Type>1</Null_Option_Type></RelationshipProps></Relationship>
  </Relationship_Groups>
  <Subject_Area_Groups>
   <Subject_Area id="{P}" name="Orders"><Subject_AreaProps><Referenced_Entities>{A}</Referenced_Entities></Subject_AreaProps></Subject_Area>
  </Subject_Area_Groups>
 </Model>
</ERwin4>"""

# Erwin 9+ style: EMX namespace, <EMX:Properties>, relationship without attribute-level links
ERWIN_EMX = f"""<?xml version="1.0"?>
<erwin xmlns:EMX="http://www.erwin.com/dm/data" xmlns:EM2="http://www.erwin.com/dm/metadata">
 <EMX:ModelObjects>
  <EMX:Entity id="{C}" name="Customer">
   <EMX:Properties><EMX:Name>Customer</EMX:Name><EMX:Physical_Name>DIM_CUSTOMER</EMX:Physical_Name></EMX:Properties>
   <EMX:ModelObjects>
    <EMX:Attribute id="{C_ID}" name="Customer Key"><EMX:Properties><EMX:Physical_Data_Type>BIGINT</EMX:Physical_Data_Type>
     <EMX:Null_Option_Type>1</EMX:Null_Option_Type></EMX:Properties></EMX:Attribute>
    <EMX:Key_Group id="{P}" name="XPKCustomer"><EMX:Properties><EMX:Key_Group_Type>PK</EMX:Key_Group_Type></EMX:Properties>
     <EMX:ModelObjects><EMX:Key_Group_Member id="{{eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee}}"><EMX:Properties>
      <EMX:Attribute_Ref>{C_ID}</EMX:Attribute_Ref></EMX:Properties></EMX:Key_Group_Member></EMX:ModelObjects></EMX:Key_Group>
   </EMX:ModelObjects>
  </EMX:Entity>
  <EMX:Entity id="{A}" name="Sale">
   <EMX:Properties><EMX:Physical_Name>FACT_SALE</EMX:Physical_Name></EMX:Properties>
   <EMX:ModelObjects>
    <EMX:Attribute id="{O_CID}" name="Customer Key"><EMX:Properties><EMX:Physical_Data_Type>BIGINT</EMX:Physical_Data_Type></EMX:Properties></EMX:Attribute>
    <EMX:Attribute id="{O_ID}" name="Amount"><EMX:Properties><EMX:Physical_Data_Type>MONEY</EMX:Physical_Data_Type></EMX:Properties></EMX:Attribute>
   </EMX:ModelObjects>
  </EMX:Entity>
  <EMX:Relationship id="{R}" name="R/2"><EMX:Properties><EMX:Parent_Entity_Ref>{C}</EMX:Parent_Entity_Ref>
   <EMX:Child_Entity_Ref>{A}</EMX:Child_Entity_Ref></EMX:Properties></EMX:Relationship>
 </EMX:ModelObjects>
</erwin>"""


def _by(spec, name):
    return next(e for e in spec["design"]["entities"] if e["entity_name"] == name)


def test_erwin_props_format():
    spec = parse_erwin_xml(ERWIN_PROPS)
    cust, order = _by(spec, "CUSTOMER"), _by(spec, "SALES_ORDER")
    assert cust["description"] == "A buyer of our products."
    assert [a["name"] for a in cust["attributes"]] == ["CUSTOMER_ID", "CUSTOMER_NAME"]   # %AttName macro -> logical name
    assert cust["business_keys"] == ["CUSTOMER_ID"] and cust["attributes"][0]["is_pk"]
    assert cust["attributes"][1]["datatype"] == "VARCHAR(200)" and cust["attributes"][1]["nullable"] is True
    assert [a["name"] for a in order["attributes"]] == ["ORDER_ID", "CUSTOMER_ID", "ORDERED_AT"]
    assert order["attributes"][2]["datatype"] == "TIMESTAMP_NTZ"
    assert spec["relationships"] == [{"from_entity": "SALES_ORDER", "from_column": "CUSTOMER_ID", "to_entity": "CUSTOMER",
                                      "to_column": "CUSTOMER_ID", "cardinality": "N:1", "from_optional": False}]
    assert order["subject_area"] == "Orders" and "subject_area" not in cust


def test_erwin_emx_format_pairs_keys_by_name():
    spec = parse_erwin_xml(ERWIN_EMX)
    assert {e["entity_name"] for e in spec["design"]["entities"]} == {"DIM_CUSTOMER", "FACT_SALE"}
    assert _by(spec, "DIM_CUSTOMER")["business_keys"] == ["CUSTOMER_KEY"]
    assert _by(spec, "FACT_SALE")["attributes"][1]["datatype"] == "NUMBER(19,4)"
    assert spec["relationships"][0]["from_entity"] == "FACT_SALE" and spec["relationships"][0]["to_column"] == "CUSTOMER_KEY"


def test_erwin_rejects_entities_and_garbage():
    with pytest.raises(ModelFileError, match="DOCTYPE"):
        parse_erwin_xml('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>')
    with pytest.raises(ModelFileError, match="not well-formed"):
        parse_erwin_xml("<Model><Entity>")
    with pytest.raises(ModelFileError, match="No Erwin entities"):
        parse_erwin_xml("<root><table/></root>")


def _xlsx(sheets):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets>'
                   + "".join(f'<sheet name="{t}" sheetId="{i}"/>' for i, (t, _) in enumerate(sheets, 1)) + "</sheets></workbook>")
        for i, (_, rows) in enumerate(sheets, 1):
            body = "".join("<row>" + "".join(f'<c r="{chr(65 + j)}{n}" t="inlineStr"><is><t>{v}</t></is></c>'
                                             for j, v in enumerate(r) if v != "") + "</row>" for n, r in enumerate(rows, 1))
            z.writestr(f"xl/worksheets/sheet{i}.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       f"<sheetData>{body}</sheetData></worksheet>")
    return buf.getvalue()


def test_excel_dictionary_with_a_title_row_and_foreign_keys():
    data = _xlsx([("Columns", [
        ["Sales data dictionary v3"],
        ["Table Name", "Column Name", "Data Type", "Null Option", "PK", "Definition", "Parent Table", "Parent Column"],
        ["customer", "customer id", "number(10)", "NOT NULL", "Y", "Surrogate id", "", ""],
        ["customer", "name", "nvarchar(200)", "NULL", "", "Legal name", "", ""],
        ["sales order", "order id", "int", "NOT NULL", "Y", "", "", ""],
        ["sales order", "customer id", "number(10)", "NOT NULL", "", "", "customer", "customer id"],
        ["sales order", "region", "varchar(10)", "", "", "", "region", "region code"],
        ["", "", "", "", "", "", "", ""],
    ])])
    out = parse_file("dictionary.xlsx", data)
    spec = out["spec"]
    assert out["format"] == "Data dictionary (Excel)" and out["entities"] == 2 and out["columns"] == 5
    cust = _by(spec, "CUSTOMER")
    assert cust["business_keys"] == ["CUSTOMER_ID"] and cust["attributes"][0]["nullable"] is False
    assert cust["attributes"][1] == {"name": "NAME", "datatype": "VARCHAR(200)", "description": "Legal name", "nullable": True}
    assert spec["relationships"] == [{"from_entity": "SALES_ORDER", "from_column": "CUSTOMER_ID", "to_entity": "CUSTOMER",
                                      "to_column": "CUSTOMER_ID", "cardinality": "N:1"}]
    assert any("not in the file" in n for n in out["notes"])


def test_sheet_per_table_uses_the_sheet_name():
    data = _xlsx([("DIM_REGION", [["Column", "Type", "Required"], ["REGION_CODE", "VARCHAR(10)", "yes"], ["NAME", "VARCHAR", "no"]])])
    region = _by(parse_file("model.xlsx", data)["spec"], "DIM_REGION")
    assert [(a["name"], a["nullable"]) for a in region["attributes"]] == [("REGION_CODE", False), ("NAME", True)]


def test_our_own_dictionary_export_round_trips():
    original = spec_v2.normalize_v2(importers.parse_ddl(
        "CREATE TABLE S.CUSTOMER (CUSTOMER_ID NUMBER NOT NULL PRIMARY KEY, NAME VARCHAR(200) COMMENT 'Legal name');"), "m1")
    csv_text = render_v2.data_dictionary(original)
    back = parse_file("customer_dictionary.csv", csv_text.encode("utf-8"))["spec"]
    cust = _by(back, "CUSTOMER")
    assert cust["business_keys"] == ["CUSTOMER_ID"]
    assert [(a["name"], a["datatype"], a.get("nullable")) for a in cust["attributes"]] == [
        ("CUSTOMER_ID", "NUMBER", False), ("NAME", "VARCHAR(200)", True)]
    assert cust["attributes"][1]["description"] == "Legal name"


def test_dispatch_by_file_kind():
    assert parse_file("model.sql", b"CREATE TABLE A (X INT);")["format"] == "DDL"
    assert parse_file("erwin.xml", ERWIN_PROPS.encode("utf-8"))["entities"] == 2
    pack = b'{"domain": {"name": "X"}, "targets": [{"table": "T", "columns": [{"name": "C", "type": "TEXT"}]}]}'
    assert parse_file("domain_pack.json", pack, "m1")["format"] == "Domain pack"
    with pytest.raises(ModelFileError, match="Save As, XML"):
        parse_file("sales.erwin", b"\x00\x01binary")
    with pytest.raises(ModelFileError, match=".xlsx or CSV"):
        parse_file("old.xls", b"\xd0\xcf")
    with pytest.raises(ModelFileError, match="No data dictionary"):
        parse_file("notes.csv", b"a,b\n1,2\n")
    with pytest.raises(ModelFileError, match="No CREATE TABLE"):
        parse_file("notes.sql", b"select 1;")


def test_imported_file_merges_into_a_draft():
    base = spec_v2.normalize_v2({"design": {"entities": []}, "relationships": []}, "m1")
    merged = importers.merge(base, parse_erwin_xml(ERWIN_PROPS), "m1", False)
    names = {e["entity_name"] for e in merged["design"]["entities"]}
    assert names == {"CUSTOMER", "SALES_ORDER"}
    assert len(merged["relationships"]) == 1
    assert not [i for i in spec_v2.validate(merged) if i["severity"] == "ERROR"]


@pytest.mark.parametrize("raw,expected", [("VARCHAR2(50 BYTE)", "VARCHAR(50)"), ("nvarchar(max)", "VARCHAR"), ("bit", "BOOLEAN"),
                                          ("NUMBER(38,0)", "NUMBER(38,0)"), ("", "VARCHAR"), ("uniqueidentifier", "VARCHAR(36)"),
                                          ("datetime2(7)", "TIMESTAMP_NTZ"), ("char(2)", "CHAR(2)")])
def test_snowflake_type(raw, expected):
    assert snowflake_type(raw) == expected


def test_size_limit():
    with pytest.raises(ModelFileError, match="larger than"):
        parse_file("big.sql", b"x" * (model_files.MAX_BYTES + 1))


def test_domain_diagram_marks_what_a_draft_changes():
    from services.modeling import diagram

    approved = spec_v2.normalize_v2(importers.parse_ddl(
        "CREATE TABLE CUSTOMER (CUSTOMER_ID NUMBER NOT NULL PRIMARY KEY, NAME VARCHAR(100), FAX VARCHAR(20));"
        "CREATE TABLE OLD_TABLE (X INT);"), "m1")
    draft = importers.merge(approved, importers.parse_ddl(
        "CREATE TABLE CUSTOMER (CUSTOMER_ID NUMBER NOT NULL PRIMARY KEY, NAME VARCHAR(200), EMAIL VARCHAR(200));"
        "CREATE TABLE SALES_ORDER (ORDER_ID INT PRIMARY KEY, CUSTOMER_ID NUMBER REFERENCES CUSTOMER(CUSTOMER_ID));"), "m1", True)
    cust_id = next(e["id"] for e in draft["design"]["entities"] if e["entity_name"] == "CUSTOMER")
    out = diagram.build(draft, model_id="m1", approved=approved, layout={cust_id: {"x": 10, "y": 20}, "gone": {"x": 1, "y": 1}},
                        knowledge={"customer": 4}, sttm=["DB.SCH.CUSTOMER"], table_models=["SALES_ORDER"])
    assert {e["entity_name"] for e in out["entities"]} == {"CUSTOMER", "SALES_ORDER"}   # retired OLD_TABLE left out
    assert out["positions"] == {"CUSTOMER": {"x": 10, "y": 20}}
    h = out["highlight"]
    assert h["entities"]["SALES_ORDER"] == "added" and h["entities"]["CUSTOMER"] == "changed"
    assert h["columns"]["CUSTOMER.EMAIL"] == "added" and h["columns"]["CUSTOMER.NAME"] == "changed"
    assert h["columns"]["CUSTOMER.FAX"] == "removed"
    assert out["coverage"]["CUSTOMER"] == {"knowledge": 4, "sttm": True, "table_model": False}
    assert out["coverage"]["SALES_ORDER"]["table_model"] is True
    assert out["summary"] == {"tables": 2, "columns": 5, "relationships": 1}
    plain = diagram.build(approved, model_id="m1")
    assert plain["highlight"]["entities"] == {} and plain["summary"]["tables"] == 2
