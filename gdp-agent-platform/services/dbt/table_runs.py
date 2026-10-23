"""Multi-target mapping, phase 5: a Silver run's table runs (one per target table, hub first) published as one pull
request. Each table run's code keeps its own paths (staging and hub or spoke models, their YAML, snapshots and
watermarks are already named after the table); the files every generation writes at one fixed path get a path per
table; a file two tables share (the domain's macros, dbt_project.yml) is kept once, the hub's copy first, and differing
copies are reported. Gold star schemas merge their own way (services/gold/star.merge_files).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from services.dbt.github import SKIP_PATHS

# fixed paths of a generation, and where each table's copy goes ({entity} is the table, lower case)
PER_TABLE = {
    "mappings/sttm.json": "mappings/{entity}.sttm.json",
    "mappings/sttm.csv": "mappings/{entity}.sttm.csv",
    "soda/checks.yml": "soda/{entity}.yml",
    "release/generation-report.json": "release/{entity}.generation-report.json",
    "release/hub_patch.sql": "release/{entity}.hub_patch.sql",
}
SHARED_QUIETLY = ("dbt_project.yml",)   # each run writes the project file for the same domain; the hub's is kept


def entity_slug(entity: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(entity or "table").lower()).strip("_") or "table"


def merge_files(children: List[Dict[str, Any]]) -> Dict[str, Any]:
    """{"files", "conflicts", "moved"}: every table run's generated files as one pull request (pure). children:
    [{"entity", "files": {path: text}}], hub first. moved: {entity: {fixed path: its path in the pull request}}."""
    out: Dict[str, str] = {}
    owner: Dict[str, str] = {}
    conflicts: List[str] = []
    moved: Dict[str, Dict[str, str]] = {}
    for child in children:
        entity = entity_slug(child.get("entity"))
        for path, text in (child.get("files") or {}).items():
            if path in SKIP_PATHS or text is None:
                continue
            if path in PER_TABLE:
                target = PER_TABLE[path].format(entity=entity)
                moved.setdefault(str(child.get("entity")), {})[path] = target
                path = target
            if path in out:
                if out[path] != text and path not in SHARED_QUIETLY:
                    conflicts.append(f"{path} (kept {owner[path]}'s)")
                continue
            out[path] = text
            owner[path] = str(child.get("entity"))
    return {"files": out, "conflicts": sorted(set(conflicts)), "moved": moved}
