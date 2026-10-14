"""Documents uploaded to a domain: the original file is kept, its text is read, and its knowledge proposed for review.

Where things live (all in the platform database):
  @CONFLUENCE.DOCUMENT_FILES                the original files, server-side encrypted, at <domain_id>/<source_id>/<name>
  CONFLUENCE.SOURCE                         one row per document: file name, size, type, SHA-256, who uploaded it, the
                                            stage path, and how it was read (status, method, characters read of total)
  CONFLUENCE.PAGE                           the text read from it (one page row per document; Confluence: per page)
  KNOWLEDGE.DOMAIN_KNOWLEDGE                each proposed item (STATUS PROPOSED, ORIGIN DOCUMENT) with the exact quote
                                            and the source document in CONTENT_JSON, until a steward decides

How a file is read depends on its kind (classify):
  text      text, Markdown, CSV, JSON, SQL, XML and similar: decoded as they are
  excel     .xlsx: each sheet as rows
  document  PDF, Word, PowerPoint, HTML: Cortex AI_PARSE_DOCUMENT (layout, keeping tables; OCR for scans), with the
            built-in Word reader as a fallback for .docx
  image     PNG, JPEG, TIFF, WebP, GIF, BMP: Cortex OCR for the text in it, and an image model that transcribes it and
            describes diagrams (boxes, attributes, connections) so they can be proposed as knowledge
  binary    anything else (archives, old Office formats): kept and listed, not read
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from pathlib import PurePosixPath
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

STAGE = "CONFLUENCE.DOCUMENT_FILES"
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_FILES = 10
STALE_MINUTES = 30

TEXT = (".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".sql", ".ddl", ".xml", ".log",
        ".py", ".ini", ".toml", ".cfg", ".properties", ".avsc", ".rst")
EXCEL = (".xlsx", ".xlsm")
DOCUMENT = (".pdf", ".docx", ".pptx", ".html", ".htm")
IMAGE = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".gif", ".bmp")
OLD_OFFICE = {".doc": "Word (.docx)", ".xls": "Excel (.xlsx)", ".ppt": "PowerPoint (.pptx)"}
# images an image model can read through TO_FILE (others are read by OCR only)
VISION = (".png", ".jpg", ".jpeg", ".webp", ".gif")
# tried in order; which exist depends on the account and region (pixtral-large reads images on the DEV account)
VISION_MODELS = ("pixtral-large", "claude-sonnet-4-5", "claude-4-sonnet", "llama4-maverick")
INLINE = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"}

Query = Callable[[str, tuple], List[Dict[str, Any]]]


def suffix(name: str) -> str:
    return PurePosixPath((name or "").replace("\\", "/")).suffix.lower()


def safe_name(name: str) -> str:
    """A file name safe for a stage path and a download header: the base name, letters, digits, dot, dash and
    underscore, at most 150 characters, the extension kept."""
    base = PurePosixPath((name or "").replace("\\", "/")).name or "document"
    stem, ext = (base.rsplit(".", 1) + [""])[:2] if "." in base else (base, "")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "document"
    ext = re.sub(r"[^A-Za-z0-9]+", "", ext)[:10]
    return (stem[: 150 - len(ext) - 1] + ("." + ext if ext else ""))[:150]


def stage_path(domain_id: str, source_id: str, name: str) -> str:
    return f"{domain_id}/{source_id}/{safe_name(name)}"


def content_type(name: str) -> str:
    return mimetypes.guess_type(safe_name(name))[0] or "application/octet-stream"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def looks_like_text(data: bytes) -> bool:
    head = data[:4096]
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def classify(name: str, data: bytes = b"") -> Tuple[str, str]:
    """(kind, note): text, excel, document, image or binary, and why a file is not read."""
    ext = suffix(name)
    if ext in OLD_OFFICE:
        return "binary", f"Old Office files are kept but not read. Save it as {OLD_OFFICE[ext]} to have it read."
    if ext in EXCEL:
        return "excel", ""
    if ext in DOCUMENT or data[:5] == b"%PDF-":
        return "document", ""
    if ext in IMAGE:
        return "image", ""
    if ext in TEXT or (data and looks_like_text(data)):
        return "text", ""
    return "binary", "This file type is kept but cannot be read as text."


def page_text(pages: Iterable[str]) -> str:
    """Pages joined with [Page N] markers, so a quote can be traced to its page."""
    parts = [str(p or "").strip() for p in pages]
    if len(parts) <= 1:
        return parts[0] if parts else ""
    return "\n\n".join(f"[Page {i}]\n\n{p}" for i, p in enumerate(parts, start=1) if p)


def page_of(text: str, quote: str) -> Optional[int]:
    """The page a quote sits on (from the [Page N] markers), None when it is not found or there are no markers."""
    at = find(text, quote)
    if at is None:
        return None
    marks = [(m.start(), int(m.group(1))) for m in re.finditer(r"^\[Page (\d+)\]$", text, re.MULTILINE)]
    before = [n for pos, n in marks if pos <= at]
    return before[-1] if before else None


def find(text: str, quote: str) -> Optional[int]:
    """Where a quote starts in the text: exact, then ignoring case and runs of whitespace."""
    if not quote or not text:
        return None
    at = text.find(quote)
    if at >= 0:
        return at
    words = [re.escape(w) for w in quote.split()]
    if not words:
        return None
    m = re.search(r"\s+".join(words), text, re.IGNORECASE)
    return m.start() if m else None


def parsed_text(result: Any) -> Tuple[str, Optional[int]]:
    """(text, pages) from an AI_PARSE_DOCUMENT or PARSE_DOCUMENT answer; raises with its error when it failed."""
    if isinstance(result, str):
        result = json.loads(result)
    result = result or {}
    if result.get("errorInformation") or result.get("error"):
        raise ValueError(str(result.get("errorInformation") or result.get("error"))[:500])
    count = (result.get("metadata") or {}).get("pageCount")
    if isinstance(result.get("pages"), list):
        pages = [p.get("content") or "" for p in sorted(result["pages"], key=lambda p: p.get("index", 0))]
        return page_text(pages), int(count) if count is not None else len(pages)
    return str(result.get("content") or "").strip(), int(count) if count is not None else None


def why(exc: Exception) -> str:
    """A failed Cortex call in a few plain words (the raw message carries batch ids and service details)."""
    text = str(exc)
    low = text.lower()
    if "unknown model" in low or "model not found" in low or "not available" in low:
        return "not available in this account"
    if "please use other models" in low or "retire" in low or "deprecat" in low:
        return "retired by Snowflake"
    if "unknown function" in low or "invalid identifier" in low:
        return "function not available in this account"
    if "unsupported" in low or "not supported" in low:
        return "file type not supported"
    first = text.splitlines()[0] if text else "failed"
    return first[:120]


def _first(rows: List[Dict[str, Any]]) -> Any:
    return next(iter(rows[0].values())) if rows else None


def parse_document(q: Query, path: str, *, ocr_only: bool = False) -> Tuple[str, Optional[int], str, List[str]]:
    """Cortex reads the staged file: (text, pages, method, attempts that failed). Layout mode keeps headings and
    tables; OCR mode reads scans and images. The newer AI_PARSE_DOCUMENT is tried first, then PARSE_DOCUMENT."""
    attempts: List[Tuple[str, str, tuple]] = []
    for mode in (("OCR",) if ocr_only else ("LAYOUT", "OCR")):
        attempts.append((f"Cortex AI_PARSE_DOCUMENT ({mode.lower()})",
                         f"SELECT AI_PARSE_DOCUMENT(TO_FILE('@{STAGE}', %s), {{'mode': '{mode}', 'page_split': TRUE}}) AS R", (path,)))
    for mode in (("OCR",) if ocr_only else ("LAYOUT", "OCR")):
        attempts.append((f"Cortex PARSE_DOCUMENT ({mode.lower()})",
                         f"SELECT SNOWFLAKE.CORTEX.PARSE_DOCUMENT(@{STAGE}, %s, {{'mode': '{mode}'}}) AS R", (path,)))
    failed: List[str] = []
    for method, sql, params in attempts:
        try:
            text, pages = parsed_text(_first(q(sql, params)))
        except Exception as exc:
            failed.append(f"{method}: {why(exc)}")
            continue
        if text.strip():
            return text, pages, method, failed
        failed.append(f"{method}: no text")
    return "", None, "", failed


IMAGE_PROMPT = (
    "This image was uploaded as reference material for the {domain} data domain of a data platform. Describe it so "
    "that its knowledge can be extracted from text alone:\n"
    "1. Transcribe every piece of text exactly as written.\n"
    "2. If it is a diagram (entity-relationship, data model, process, architecture), list every box or entity with "
    "its attributes or fields, then every connection with its label, direction and cardinality if shown.\n"
    "3. If it holds a table, reproduce it as a Markdown table.\n"
    "Do not guess what is not visible; say 'unreadable' for parts you cannot read.")


def describe_image(q: Query, path: str, domain: str, models: Iterable[str] = VISION_MODELS) -> Tuple[str, str, List[str]]:
    """An image model reads the staged image: (text, model, attempts that failed)."""
    failed: List[str] = []
    prompt = IMAGE_PROMPT.format(domain=domain)
    for model in models:
        try:
            text = _first(q(f"SELECT AI_COMPLETE(%s, %s, TO_FILE('@{STAGE}', %s)) AS R", (model, prompt, path)))
        except Exception as exc:
            failed.append(f"{model}: {why(exc)}")
            continue
        text = (json.loads(text) if isinstance(text, str) and text.startswith('"') else text) or ""
        if str(text).strip():
            return str(text).strip(), model, failed
        failed.append(f"{model}: no answer")
    return "", "", failed


def read(q: Query, name: str, data: bytes, path: str, domain: str,
         vision_models: Iterable[str] = VISION_MODELS) -> Dict[str, Any]:
    """Read one stored file: {kind, text, method, pages, note, attempts}. `data` is the file (for the local
    readers); `path` is where it is staged (for Cortex)."""
    from services.agentic import attachments

    kind, note = classify(name, data)
    out: Dict[str, Any] = {"kind": kind, "text": "", "method": "", "pages": None, "note": note, "attempts": []}
    if kind == "binary":
        return out
    if kind == "text":
        out.update(text=attachments._decode(data).replace("\r\n", "\n"), method="Text")
        return out
    if kind == "excel":
        try:
            out.update(text=attachments._xlsx(data, max_rows=20000), method="Excel reader")
        except Exception as exc:
            out["note"] = f"The workbook could not be read ({str(exc)[:200]})."
        return out
    if kind == "document":
        text, pages, method, failed = parse_document(q, path)
        out["attempts"] = failed
        if text:
            out.update(text=text, pages=pages, method=method)
        elif suffix(name) == ".docx":
            try:
                out.update(text=attachments._docx(data), method="Word reader (Cortex could not read it)")
            except Exception as exc:
                out["note"] = f"The Word file could not be read ({str(exc)[:200]})."
        elif suffix(name) in (".html", ".htm"):
            plain = re.sub(r"<[^>]+>", " ", attachments._decode(data))
            out.update(text=re.sub(r"[ \t]+", " ", plain).strip(), method="HTML text")
        else:
            out["note"] = "Cortex could not read this document" + (f" ({failed[-1]})" if failed else "") + "."
        return out
    # image: the text in it (OCR) and a transcription and description by an image model
    ocr, pages, ocr_method, failed = parse_document(q, path, ocr_only=True)
    described, model, failed_v = ("", "", ["image models read PNG, JPEG, WebP and GIF"]) if suffix(name) not in VISION \
        else describe_image(q, path, domain, vision_models)
    out["attempts"] = failed + failed_v
    parts, methods = [], []
    if described:
        parts.append(f"Image description ({model}):\n\n{described}")
        methods.append(f"image model {model}")
    if ocr:
        parts.append(f"Text in the image (OCR):\n\n{ocr}")
        methods.append(ocr_method)
    if parts:
        out.update(text="\n\n".join(parts), method=" and ".join(methods), pages=pages)
    else:
        out["note"] = "No text could be read from the image" + (f" ({out['attempts'][-1]})" if out["attempts"] else "") + "."
    return out
