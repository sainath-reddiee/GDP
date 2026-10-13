"""Approve and Publish executor, attachments and the conversation tools (services/agentic)."""

import asyncio
import io
import zipfile

import pytest

from services.agentic import attachments, changeset, publish, tools


def run(gen):
    async def go():
        return [e async for e in gen]
    return asyncio.run(go())


class Api:
    def __init__(self, replies):
        self.replies, self.calls = replies, []

    async def __call__(self, method, path, body=None, params=None):
        self.calls.append((method, path, body))
        reply = self.replies.get((method, path))
        return reply(body) if callable(reply) else (reply or (200, {}))


def change(key, kind, ops, cid):
    c = changeset.make(key, kind, key, ops)
    c["change_id"], c["seq"] = cid, int(cid[-1])
    return c


def test_publish_applies_in_order_resolves_refs_and_reports_approvals():
    changes = [
        change("JIRA:A-1", "JIRA", [{"method": "POST", "path": "/api/jira/issues/A-1/comment", "body": {"markdown": "x"}}], "c1"),
        change("QA_PACK:new", "QA", [{"method": "POST", "path": "/api/domains/d/qa-packs", "body": {}},
                                     {"method": "POST", "path": "/api/qa-packs/{op0.pack_id}/submit", "body": {}}], "c2"),
        change("STTM:s", "STTM", [{"method": "POST", "path": "/api/hub/sttm/s/edit", "body": {}}], "c3"),
    ]
    api = Api({("POST", "/api/domains/d/qa-packs"): (200, {"pack_id": "p9"}),
               ("POST", "/api/hub/sttm/s/edit"): (202, {"pending_approval": True, "request_id": "r1", "approver_role": "STTM_APPROVER"})})
    events = run(publish.run(api, changes))
    assert [c[1] for c in api.calls] == ["/api/hub/sttm/s/edit", "/api/domains/d/qa-packs", "/api/qa-packs/p9/submit",
                                         "/api/jira/issues/A-1/comment"]
    status = {c["change_id"]: c["status"] for c in changes}
    assert status == {"c1": "APPLIED", "c2": "APPLIED", "c3": "PENDING_APPROVAL"}
    assert changes[2]["request_id"] == "r1" and events[-1]["type"] == "done"


def test_a_failure_stops_everything_after_it():
    changes = [change("TABLE_MODEL:m", "MODEL", [{"method": "PUT", "path": "/api/hub/table-models/m", "body": {}}], "c1"),
               change("JIRA:A-1", "JIRA", [{"method": "POST", "path": "/api/jira/issues/A-1/comment", "body": {}}], "c2")]
    api = Api({("PUT", "/api/hub/table-models/m"): (409, {"detail": "stale etag"})})
    events = run(publish.run(api, changes))
    assert changes[0]["status"] == "FAILED" and "stale etag" in changes[0]["result"]["detail"]
    assert changes[1]["status"] == "PROPOSED" and len(api.calls) == 1
    assert any(e["type"] == "stopped" for e in events)


def test_unresolved_reference_fails_the_change():
    changes = [change("QA_PACK:new", "QA", [{"method": "POST", "path": "/api/domains/d/qa-packs", "body": {}},
                                            {"method": "POST", "path": "/api/qa-packs/{op0.pack_id}/submit", "body": {}}], "c1")]
    run(publish.run(Api({}), changes))
    assert changes[0]["status"] == "FAILED" and "pack_id" in changes[0]["result"]["detail"]


def test_publish_only_selected_changes():
    changes = [change("A", "MODEL", [{"method": "PUT", "path": "/api/a", "body": {}}], "c1"),
               change("B", "DQ", [{"method": "POST", "path": "/api/b", "body": {}}], "c2")]
    api = Api({})
    run(publish.run(api, changes, ["c2"]))
    assert [c[1] for c in api.calls] == ["/api/b"] and changes[0]["status"] == "PROPOSED"


def _zip(parts):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in parts.items():
            z.writestr(name, text)
    return buf.getvalue()


def test_attachment_text_extraction():
    assert attachments.extract("a.csv", b"id,name\n1,x")[0] == "id,name\n1,x"
    doc = _zip({"word/document.xml": '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                                     '<w:body><w:p><w:r><w:t>Load daily</w:t></w:r></w:p></w:body></w:document>'})
    assert attachments.extract("req.docx", doc)[0] == "Load daily"
    sheet = _zip({"xl/sharedStrings.xml": '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>COL</t></si></sst>',
                  "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets><sheet name="Map"/></sheets></workbook>',
                  "xl/worksheets/sheet1.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                                              '<row><c r="A1" t="s"><v>0</v></c><c r="B1"><v>5</v></c></row></sheetData></worksheet>'})
    assert attachments.extract("map.xlsx", sheet)[0] == "# sheet: Map\nCOL,5"
    text, note = attachments.extract("spec.pdf", b"%PDF-1.7 ...")
    assert text == "" and "PDF" in note
    assert attachments.extract("x.docx", b"not a zip")[1]


def test_read_attachment_pages_through_the_text():
    async def reader(_id):
        return "a" * 20000

    ctx = {"attachments": [{"attachment_id": "f1", "file_name": "spec.txt"}], "read_attachment": reader}
    first = asyncio.run(tools.read_attachment(Api({}), {"attachment_id": "f1"}, ctx))["summary"]
    assert len(first["text"]) == 15000 and first["next_offset"] == 15000
    rest = asyncio.run(tools.read_attachment(Api({}), {"attachment_id": "f1", "offset": 15000}, ctx))["summary"]
    assert len(rest["text"]) == 5000 and rest["next_offset"] is None
    with pytest.raises(tools.ToolError):
        asyncio.run(tools.read_attachment(Api({}), {"attachment_id": "nope"}, ctx))


def test_onboarding_catalog_lists_the_fixed_choices():
    catalog = tools.onboarding_catalog()
    assert "SALESFORCE" in catalog["source"]["source_type"] and "DAILY" in catalog["load"]["load_frequency"]
    assert "vault" in catalog["snaplogic"]["api_key"]
