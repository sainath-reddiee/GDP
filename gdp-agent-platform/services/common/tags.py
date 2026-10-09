"""User tags on profiles, runs and models (CORE.TAG / CORE.TAG_ASSIGNMENT), pure helpers.

A tag is lowercase letters, digits, '-', '_', '.', ':' or '/', at most 40 characters. Profiles are keyed by
DATABASE.SCHEMA.TABLE so a tag survives re-profiling; runs by RUN_ID; models by the target's DB.SCHEMA.TABLE.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional

ENTITY_TYPES = ("PROFILE", "RUN", "MODEL")
MAX_TAGS = 20
TAG = re.compile(r"^[a-z0-9][a-z0-9_.:/-]{0,39}$")
COLORS = ("slate", "blue", "green", "amber", "red", "violet", "pink", "teal")


def normalize_tag(raw: str) -> Optional[str]:
    text = re.sub(r"\s+", "-", str(raw or "").strip().lower()).lstrip("#")
    return text if TAG.match(text) else None


def normalize_tags(raw: Iterable[str]) -> List[str]:
    out: List[str] = []
    for t in raw or []:
        tag = normalize_tag(t)
        if tag and tag not in out:
            out.append(tag)
    return out[:MAX_TAGS]


def entity_key(entity_type: str, key: str) -> str:
    """Canonical key: upper-case dotted names for profiles and models, the id as-is for runs."""
    entity_type = str(entity_type or "").upper()
    assert entity_type in ENTITY_TYPES, f"entity type must be one of {', '.join(ENTITY_TYPES)}"
    key = str(key or "").strip()
    assert key and len(key) <= 1024, "a key is required"
    if entity_type in ("PROFILE", "MODEL"):
        parts = [p.strip().strip('"') for p in key.split(".")]
        assert len(parts) == 3 and all(parts), "use DATABASE.SCHEMA.TABLE"
        return ".".join(p.upper() for p in parts)
    return key


def color_for(tag: str) -> str:
    """Stable default colour per tag name."""
    return COLORS[sum(ord(c) for c in tag) % len(COLORS)]
