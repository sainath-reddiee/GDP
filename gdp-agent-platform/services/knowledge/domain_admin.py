"""Which domains may be deleted from the UI, and what a delete touches.

Only domains added through the UI (pack import or AI draft) can be deleted. Domains shipped as repository packs
(domain/*/domain_pack.json) are re-registered on every deploy, so deleting them in the UI would not stick: they are
retired by removing the pack file. GDP and GENERAL are platform domains and are never deleted.

Delete is soft: the domain, its targets and its knowledge are deactivated, but runs, STTMs and quality history keep
their domain id, so nothing is orphaned and a restore brings everything back.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set, Tuple

from services.knowledge.packs import GDP_DOMAIN_ID

PLATFORM_DOMAINS = {"GDP", "GENERAL"}
ROOT = Path(__file__).resolve().parents[2]


def repository_pack_names(root: Path = ROOT) -> Set[str]:
    names = set()
    for path in (root / "domain").glob("*/domain_pack.json"):
        try:
            names.add(str(json.loads(path.read_text(encoding="utf-8"))["domain"]["name"]).strip().upper())
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return names


def can_delete(domain_id: str, name: str, config: Optional[Dict[str, Any]],
               repository_names: Iterable[str]) -> Tuple[bool, Optional[str]]:
    upper = str(name or "").strip().upper()
    origin = str((config or {}).get("origin") or "").lower()
    if domain_id == GDP_DOMAIN_ID or upper in PLATFORM_DOMAINS:
        return False, f"{upper} is a platform domain and cannot be deleted."
    if origin == "repository" or upper in {n.upper() for n in repository_names}:
        return False, (f"{upper} ships with the platform as a repository pack and is re-registered on every deploy. "
                       "Remove its domain_pack.json to retire it.")
    return True, None
