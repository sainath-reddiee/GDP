"""Uploaded documents: any file kept and classified, read by the right reader (Cortex parsing, OCR, image model),
traced to the passage and page each proposal quotes."""

import json

import pytest

from services.governance import policy
from services.knowledge import document_files as df
from services.knowledge import documents


@pytest.mark.parametrize("name,data,kind", [
    ("rules.md", b"# Rules", "text"), ("dict.xlsx", b"PK..", "excel"), ("spec.pdf", b"%PDF-1.7", "document"),
    ("scan.bin", b"%PDF-1.4 x", "document"), ("deck.pptx", b"PK", "document"), ("page.html", b"<html>", "document"),
    ("erd.PNG", b"\x89PNG", "image"), ("photo.jpeg", b"\xff\xd8", "image"), ("scan.tiff", b"II*", "image"),
    ("old.doc", b"\xd0\xcf", "binary"), ("bundle.zip", b"PK\x03\x04\x00\x00", "binary"), ("noext", b"plain words here", "text"),
    ("blob", b"\x00\x01\x02", "binary"),
])
def test_classify(name, data, kind):
    assert df.classify(name, data)[0] == kind


def test_old_office_says_what_to_save_as():
    assert ".xlsx" in df.classify("book.xls", b"\xd0\xcf")[1]


def test_safe_names_and_paths():
    assert df.safe_name("C:\\Users\\me\\Customer Rules (v2).docx") == "Customer_Rules_v2.docx"
    assert df.safe_name("../../etc/passwd") == "passwd"
    assert df.safe_name("") == "document"
    assert df.safe_name("x" * 400 + ".pdf").endswith(".pdf") and len(df.safe_name("x" * 400 + ".pdf")) <= 150
    assert df.stage_path("d1", "s1", "a b.png") == "d1/s1/a_b.png"
    assert df.content_type("a.pdf") == "application/pdf" and df.content_type("a.weird") == "application/octet-stream"


def test_pages_quotes_and_page_numbers():
    text = df.page_text(["Customers are buyers.", "", "A vendor  supplies\ngoods."])
    assert text.startswith("[Page 1]") and "[Page 3]" in text and "[Page 2]" not in text
    assert df.page_of(text, "a vendor supplies goods.") == 3
    assert df.page_of(text, "Customers are buyers.") == 1
    assert df.page_of(text, "not there") is None
    assert df.page_text(["only"]) == "only"
    assert df.find("abc", "") is None


def test_parsed_text_shapes():
    assert df.parsed_text({"pages": [{"index": 1, "content": "two"}, {"index": 0, "content": "one"}],
                           "metadata": {"pageCount": 2}}) == ("[Page 1]\n\none\n\n[Page 2]\n\ntwo", 2)
    assert df.parsed_text(json.dumps({"content": " all ", "metadata": {"pageCount": 1}})) == ("all", 1)
    with pytest.raises(ValueError):
        df.parsed_text({"errorInformation": "unsupported"})


def _q(answers):
    """A query stub: the first answer whose key is in the SQL (an Exception is raised)."""
    calls = []

    def q(sql, params):
        calls.append(sql)
        for key, value in answers:
            if key in sql:
                if isinstance(value, Exception):
                    raise value
                return [{"R": value}]
        raise RuntimeError("unknown function")
    return q, calls


def test_document_falls_back_from_layout_to_ocr_to_legacy():
    q, calls = _q([("'LAYOUT', 'page_split'", RuntimeError("Unknown function AI_PARSE_DOCUMENT")),
                   ("'OCR', 'page_split'", RuntimeError("Unknown function AI_PARSE_DOCUMENT")),
                   ("PARSE_DOCUMENT(@", {"content": "Legacy text", "metadata": {"pageCount": 3}})])
    out = df.read(q, "spec.pdf", b"%PDF-", "d/s/spec.pdf", "COMPANY")
    assert out["text"] == "Legacy text" and out["pages"] == 3 and out["method"] == "Cortex PARSE_DOCUMENT (layout)"
    assert len(out["attempts"]) == 2


def test_word_falls_back_to_the_built_in_reader(monkeypatch):
    from services.agentic import attachments

    monkeypatch.setattr(attachments, "_docx", lambda data: "Paragraph one")
    q, _ = _q([])
    out = df.read(q, "rules.docx", b"PK", "d/s/rules.docx", "COMPANY")
    assert out["text"] == "Paragraph one" and out["method"].startswith("Word reader")


def test_image_gets_a_description_and_its_text():
    q, calls = _q([("AI_COMPLETE", "Entity CUSTOMER (ID, NAME) -> ORDER"), ("'OCR'", {"content": "CUSTOMER ORDER"})])
    out = df.read(q, "erd.png", b"\x89PNG", "d/s/erd.png", "COMPANY", vision_models=("model-a",))
    assert "Image description (model-a)" in out["text"] and "Text in the image (OCR)" in out["text"]
    assert "image model model-a" in out["method"]
    assert any("COMPANY data domain" in c or "AI_COMPLETE" in c for c in calls)


def test_image_model_failure_is_recorded_and_ocr_still_used():
    q, _ = _q([("AI_COMPLETE", RuntimeError("model not available")), ("'OCR'", {"content": "Some text"})])
    out = df.read(q, "shot.jpg", b"\xff\xd8", "p", "D", vision_models=("m1", "m2"))
    assert out["text"].startswith("Text in the image") and [a.split(":")[0] for a in out["attempts"]] == ["m1", "m2"]


def test_tiff_is_read_by_ocr_only():
    q, calls = _q([("'OCR'", {"content": "scanned"})])
    out = df.read(q, "scan.tif", b"II*", "p", "D")
    assert "scanned" in out["text"] and not any("AI_COMPLETE" in c for c in calls)


def test_unreadable_things_are_kept_with_a_note():
    q, calls = _q([])
    out = df.read(q, "bundle.zip", b"PK\x03\x04\x00", "p", "D")
    assert out["text"] == "" and out["kind"] == "binary" and out["note"] and not calls
    out = df.read(q, "blank.pdf", b"%PDF-", "p", "D")
    assert out["text"] == "" and "could not read" in out["note"]


def test_text_and_excel_are_read_locally():
    q, calls = _q([])
    assert df.read(q, "a.csv", b"a,b\r\n1,2", "p", "D")["text"] == "a,b\n1,2" and not calls


def test_split_reports_what_is_left_unread():
    text = "\n\n".join(["y" * 900] * 10)
    parts, unread = documents.split(text, size=2000, limit=3)
    assert len(parts) == 3 and unread > 0
    assert documents.split(text, size=2000, limit=100)[1] == 0
    assert documents.split("")[0] == []


def test_upload_and_read_again_are_governed_and_not_queued():
    for path in ("/api/domains/d1/documents/upload", "/api/domains/d1/documents/s1/refresh"):
        assert policy.privilege_for("POST", path)[0] == "KNOWLEDGE.EDIT"
        assert policy.no_queue("POST", path)


def test_failures_are_said_plainly():
    assert df.why(RuntimeError("512513 (P0000): Request failed ... '400 'unknown model \"x\"''; batch-id: 1")) == "not available in this account"
    assert df.why(RuntimeError("remote service error: '400 'This model is retired on October 14, 2026, please use other models.''")) == "retired by Snowflake"
    assert df.why(RuntimeError("SQL compilation error: Unknown function AI_PARSE_DOCUMENT")) == "function not available in this account"
    assert df.why(RuntimeError("")) == "failed"
    assert df.VISION_MODELS[0] == "pixtral-large"
