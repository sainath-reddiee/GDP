"""Confluence Cloud, read as the signed-in person: the same Atlassian OAuth app and token as Jira (services/jira),
with the Confluence read scopes added. Pages come back as Atlassian Document Format, turned into text here.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode, urlparse

from services.jira import adf
from services.jira.client import API, JiraClient

SCOPES = ["read:confluence-content.all", "read:confluence-space.summary", "search:confluence"]
PAGE_ID = re.compile(r"^\d{1,20}$")
SPACE_KEY = re.compile(r"^~?[A-Za-z0-9_]{1,255}$")


def parse_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """(space key, page id) from a Confluence link: /wiki/spaces/KEY/pages/123/Title, /wiki/spaces/KEY(/overview),
    or ?pageId=123. A bare page id or space key is accepted too."""
    text = (url or "").strip()
    if PAGE_ID.match(text):
        return None, text
    if SPACE_KEY.match(text) and not text.isdigit():
        return text, None
    try:
        u = urlparse(text)
    except ValueError:
        return None, None
    if u.scheme not in ("https", "http"):
        return None, None
    m = re.search(r"/spaces/([^/]+)/pages/(\d+)", u.path)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"pageId=(\d+)", u.query or "")
    if m:
        return None, m.group(1)
    m = re.search(r"/spaces/([^/]+)", u.path)
    if m:
        return m.group(1), None
    return None, None


class ConfluenceClient(JiraClient):
    """The Jira client's transport (retries, errors) pointed at Confluence on the same Atlassian site."""

    def __init__(self, http, cloud_id: str, access_token: str):
        super().__init__(http, cloud_id, access_token)
        self.base = f"{API}/ex/confluence/{cloud_id}"

    def page(self, page_id: str) -> Dict[str, Any]:
        """{id, title, version, space, url, text} of one page (its current version)."""
        if not PAGE_ID.match(str(page_id)):
            raise ValueError("invalid page id")
        data = self._call("GET", f"/wiki/rest/api/content/{page_id}?expand=body.atlas_doc_format,version,space") or {}
        return shape(data)

    def space_pages(self, space_key: str, limit: int = 50) -> List[Dict[str, Any]]:
        """The current pages of a space (ids, titles and versions only; read each with page())."""
        if not SPACE_KEY.match(space_key or ""):
            raise ValueError("invalid space key")
        q = urlencode({"spaceKey": space_key, "type": "page", "status": "current", "limit": max(1, min(limit, 100)),
                       "expand": "version"})
        data = self._call("GET", f"/wiki/rest/api/content?{q}") or {}
        return [{"id": str(p.get("id")), "title": p.get("title"), "version": (p.get("version") or {}).get("number")}
                for p in data.get("results") or []]

    def space(self, space_key: str) -> Dict[str, Any]:
        if not SPACE_KEY.match(space_key or ""):
            raise ValueError("invalid space key")
        data = self._call("GET", f"/wiki/rest/api/space/{quote(space_key)}") or {}
        return {"key": data.get("key"), "name": data.get("name")}


def shape(data: Dict[str, Any]) -> Dict[str, Any]:
    body = ((data.get("body") or {}).get("atlas_doc_format") or {}).get("value")
    try:
        doc = json.loads(body) if isinstance(body, str) else body
    except ValueError:
        doc = None
    links = data.get("_links") or {}
    url = (links.get("base") or "") + (links.get("webui") or "") if links.get("webui") else None
    return {"id": str(data.get("id") or ""), "title": data.get("title") or "", "version": (data.get("version") or {}).get("number"),
            "space": (data.get("space") or {}).get("key"), "url": url, "text": adf.to_text(doc) if doc else ""}
