"""Text from a file attached to a copilot conversation (pure, standard library only).

Text and code files are decoded as they are. Word (.docx) and Excel (.xlsx) files are read from their XML parts, so
requirement documents and mapping sheets work without extra packages; each sheet becomes CSV-like lines. Anything else
(PDF, images, archives) is kept by name only, with a note asking for a text export.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Dict, List, Tuple
from xml.etree import ElementTree

MAX_BYTES = 10 * 1024 * 1024        # per file
MAX_TEXT = 200_000                  # characters kept per file
MAX_UNZIPPED = 60 * 1024 * 1024     # a Word or Excel part larger than this when unpacked is refused (zip bombs)
TEXT_SUFFIXES = (".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml", ".sql", ".ddl", ".xml", ".py", ".log",
                 ".ini", ".toml", ".html", ".htm", ".avsc", ".jsonl", ".ndjson")
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = data.decode(encoding)
            if encoding == "utf-16" and not data[:2] in (b"\xff\xfe", b"\xfe\xff"):
                continue
            return text
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read(z: zipfile.ZipFile, name: str) -> bytes:
    if z.getinfo(name).file_size > MAX_UNZIPPED:
        raise ValueError("part too large when unpacked")
    return z.read(name)


def _docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ElementTree.fromstring(_read(z, "word/document.xml"))
    lines = []
    for para in root.iter(f"{_W}p"):
        text = "".join(t.text or "" for t in para.iter(f"{_W}t"))
        lines.append(text)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _col(ref: str) -> int:
    m = re.match(r"[A-Z]+", ref or "")
    if not m:
        raise ValueError(f"bad cell reference {ref!r}")
    n = 0
    for ch in m.group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _xlsx(data: bytes, max_rows: int = 2000) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        shared: List[str] = []
        if "xl/sharedStrings.xml" in names:
            for si in ElementTree.fromstring(_read(z, "xl/sharedStrings.xml")).iter(f"{_S}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_S}t")))
        titles: Dict[str, str] = {}
        if "xl/workbook.xml" in names:
            for i, sheet in enumerate(ElementTree.fromstring(_read(z, "xl/workbook.xml")).iter(f"{_S}sheet"), start=1):
                titles[f"xl/worksheets/sheet{i}.xml"] = sheet.get("name") or f"Sheet{i}"
        out: List[str] = []
        sheets = [n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)]
        for path in sorted(sheets, key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1))):  # sheet2 before sheet10
            out.append(f"# sheet: {titles.get(path, path.rsplit('/', 1)[-1])}")
            for count, row in enumerate(ElementTree.fromstring(_read(z, path)).iter(f"{_S}row")):
                if count >= max_rows:
                    out.append(f"... more rows not shown (first {max_rows} kept)")
                    break
                cells: Dict[int, str] = {}
                for c in row.iter(f"{_S}c"):
                    v = c.find(f"{_S}v")
                    inline = c.find(f"{_S}is")
                    value = v.text if v is not None else "".join(t.text or "" for t in inline.iter(f"{_S}t")) if inline is not None else ""
                    if c.get("t") == "s" and value not in (None, ""):
                        value = shared[int(value)] if int(value) < len(shared) else value
                    if c.get("r"):
                        cells[_col(c.get("r"))] = str(value or "")
                if cells:
                    out.append(",".join(cells.get(i, "") for i in range(max(cells) + 1)))
    return "\n".join(out)


def extract(name: str, data: bytes) -> Tuple[str, str]:
    """(text, note). The note says why there is no text, or that the text was cut."""
    lower = (name or "").lower()
    if len(data) > MAX_BYTES:
        return "", f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB; attach a smaller extract."
    try:
        if lower.endswith(".pdf") or data[:5] == b"%PDF-":
            return "", "PDF text cannot be read here yet. Paste the relevant part into the chat, or attach it as Word or text."
        if lower.endswith(".docx"):
            text = _docx(data)
        elif lower.endswith((".xlsx", ".xlsm")):
            text = _xlsx(data)
        elif lower.endswith(TEXT_SUFFIXES) or b"\x00" not in data[:4096]:
            text = _decode(data)
        else:
            return "", "This file type cannot be read as text. Attach a PDF as text, a Word or Excel file, or a CSV."
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, ValueError, AttributeError, IndexError):
        return "", "The file could not be read; it may be damaged or password protected."
    text = text.replace("\r\n", "\n")
    if len(text) > MAX_TEXT:
        return text[:MAX_TEXT], f"Only the first {MAX_TEXT:,} characters were kept."
    return text, ""
