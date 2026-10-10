"""Atlassian Document Format (ADF): Jira's rich text. Read it as plain markdown-like text (never as HTML, so issue
content cannot inject markup into the platform), and write comments from a small markdown subset: headings, paragraphs,
bullet and numbered lists, fenced code, tables, **bold**, `code` and links."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

MAX_TEXT = 60_000


# ---------------------------------------------------------------- ADF -> text

def to_text(node: Any, limit: int = MAX_TEXT) -> str:
    """Readable text of an ADF document (or a plain string, for Jira fields that are not ADF)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node[:limit]
    out = _render(node, 0).strip()
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out[:limit]


def _children(node: Dict[str, Any], depth: int, sep: str = "") -> str:
    return sep.join(_render(c, depth) for c in node.get("content") or [])


def _render(node: Any, depth: int) -> str:
    if not isinstance(node, dict):
        return ""
    kind = node.get("type")
    if kind == "text":
        text = str(node.get("text") or "")
        for mark in node.get("marks") or []:
            m = mark.get("type")
            if m == "code":
                text = f"`{text}`"
            elif m == "strong":
                text = f"**{text}**"
            elif m == "link":
                href = (mark.get("attrs") or {}).get("href")
                if href and href != text:
                    text = f"{text} ({href})"
        return text
    if kind == "hardBreak":
        return "\n"
    if kind in ("mention",):
        return "@" + str((node.get("attrs") or {}).get("text") or "").lstrip("@")
    if kind in ("emoji",):
        return str((node.get("attrs") or {}).get("text") or "")
    if kind in ("inlineCard", "blockCard"):
        return str((node.get("attrs") or {}).get("url") or "")
    if kind == "paragraph":
        return _children(node, depth) + "\n\n"
    if kind == "heading":
        level = int((node.get("attrs") or {}).get("level") or 2)
        return "#" * max(1, min(level, 6)) + " " + _children(node, depth) + "\n\n"
    if kind in ("bulletList", "orderedList"):
        lines = []
        for i, item in enumerate(node.get("content") or [], 1):
            bullet = f"{i}." if kind == "orderedList" else "-"
            body = _children(item, depth + 1).strip().replace("\n\n", "\n").replace("\n", "\n" + "  " * (depth + 1))
            lines.append("  " * depth + f"{bullet} {body}")
        return "\n".join(lines) + "\n\n"
    if kind == "codeBlock":
        lang = (node.get("attrs") or {}).get("language") or ""
        return f"```{lang}\n{_children(node, depth)}\n```\n\n"
    if kind == "blockquote":
        return "\n".join("> " + line for line in _children(node, depth).strip().splitlines()) + "\n\n"
    if kind == "rule":
        return "---\n\n"
    if kind == "table":
        rows = []
        for row in node.get("content") or []:
            cells = [_children(c, depth).strip().replace("\n", " ").replace("|", "/") for c in row.get("content") or []]
            rows.append("| " + " | ".join(cells) + " |")
        if rows:
            width = rows[0].count("|") - 1
            rows.insert(1, "|" + " --- |" * width)
        return "\n".join(rows) + "\n\n"
    if kind in ("mediaSingle", "mediaGroup", "media"):
        return "[attachment]\n\n"
    if kind == "panel":
        return _children(node, depth) + "\n"
    return _children(node, depth)


# ---------------------------------------------------------------- markdown -> ADF

INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?://[^)\s]+\))")


def _inline(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            out.append({"type": "text", "text": part[2:-2], "marks": [{"type": "strong"}]})
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            out.append({"type": "text", "text": part[1:-1], "marks": [{"type": "code"}]})
        elif part.startswith("[") and "](" in part:
            label, href = part[1:-1].split("](", 1)
            out.append({"type": "text", "text": label, "marks": [{"type": "link", "attrs": {"href": href}}]})
        else:
            out.append({"type": "text", "text": part})
    return out


def _paragraph(text: str) -> Dict[str, Any]:
    return {"type": "paragraph", "content": _inline(text)} if text else {"type": "paragraph"}


def _cells(line: str) -> List[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def from_markdown(markdown: str) -> Dict[str, Any]:
    """An ADF document for a comment. Unknown syntax stays as text; nothing is lost."""
    lines = (markdown or "").replace("\r\n", "\n").split("\n")
    content: List[Dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        fence = re.match(r"^```(\w*)\s*$", line)
        if fence:
            body = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            block: Dict[str, Any] = {"type": "codeBlock", "content": [{"type": "text", "text": "\n".join(body)}] if body else []}
            if fence.group(1):
                block["attrs"] = {"language": fence.group(1)}
            content.append(block)
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            content.append({"type": "heading", "attrs": {"level": len(heading.group(1))}, "content": _inline(heading.group(2).strip())})
            i += 1
            continue
        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1]):
            header = _cells(line)
            rows = [header]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            table_rows = []
            for r, cells in enumerate(rows):
                kind = "tableHeader" if r == 0 else "tableCell"
                cells = (cells + [""] * len(header))[:len(header)]
                table_rows.append({"type": "tableRow", "content": [{"type": kind, "content": [_paragraph(c)]} for c in cells]})
            content.append({"type": "table", "content": table_rows})
            continue
        listing = re.match(r"^\s*([-*]|\d+[.)])\s+", line)
        if listing:
            ordered = listing.group(1)[0].isdigit()
            items = []
            while i < len(lines):
                m = re.match(r"^\s*([-*]|\d+[.)])\s+(.*)$", lines[i])
                if not m or m.group(1)[0].isdigit() != ordered:
                    break
                items.append({"type": "listItem", "content": [_paragraph(m.group(2).strip())]})
                i += 1
            content.append({"type": "orderedList" if ordered else "bulletList", "content": items})
            continue
        para = [line.strip()]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,6}\s|```|\s*([-*]|\d+[.)])\s|\s*\|)", lines[i]):
            para.append(lines[i].strip())
            i += 1
        paragraph: Dict[str, Any] = {"type": "paragraph", "content": []}
        for n, part in enumerate(para):
            if n:
                paragraph["content"].append({"type": "hardBreak"})
            paragraph["content"].extend(_inline(part))
        content.append(paragraph)
    return {"type": "doc", "version": 1, "content": content or [_paragraph("")]}


def plain(markdown: Optional[str], limit: int = 400) -> str:
    """One line of plain text for logs and previews."""
    return re.sub(r"\s+", " ", re.sub(r"[`*#|>]", "", markdown or "")).strip()[:limit]
