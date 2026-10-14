"""What the connected dbt repository already does for gold (pure), learned from indexed code (CODE.CODE_CHUNK rows:
path, kind, name, text), so generated gold uses the project's own macros instead of guessing them.

  history_hook   the history post-hook call, taken from an existing gold model, when it is model-independent
                 (for example `{{ m_insert_history_gold(this) }}`)
  data_source    the data-source dimension a gold fact's DIM_DATA_SOURCE_SKEY comes from, when a model of that name
                 is indexed with the expected columns
Each learned item names the file it came from.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple

HISTORY = re.compile(r"\{\{\s*m_insert_history_gold\s*\(([^)]*)\)\s*\}\}")


def learn(chunks: Iterable[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    """(conventions, notes) from indexed code chunks."""
    conventions: Dict[str, Any] = {}
    notes: List[str] = []
    seen_specific = None
    for c in chunks:
        path, text = str(c.get("path") or ""), str(c.get("text") or "")
        if "gold" not in path.lower():
            continue
        m = HISTORY.search(text)
        if m and "history_hook" not in conventions:
            args = m.group(1).strip()
            if args in ("", "this") or re.fullmatch(r"this(\s*,\s*'[^']*')*", args) and not re.search(r"'dim_|'fact_", args, re.I):
                conventions["history_hook"] = m.group(0)
                conventions["history_hook_from"] = path
            else:
                seen_specific = (m.group(0), path)
    if "history_hook" not in conventions and seen_specific:
        notes.append(f"the history hook is called with model-specific arguments ({seen_specific[0]} in {seen_specific[1]}); "
                     "add it per model in review")
    for c in chunks:
        if str(c.get("name") or "").lower() == "dim_data_source" and str(c.get("kind") or "").lower() in ("model", "sql", "dbt_model"):
            cols = {str(x).upper() for x in (c.get("columns") or [])}
            text = str(c.get("text") or "").upper()
            name_col = next((n for n in ("DATA_SOURCE_NAME", "DIM_DATA_SOURCE_NAME", "SOURCE_NAME") if n in cols or n in text), None)
            if "DIM_DATA_SOURCE_SKEY" in (cols | ({"DIM_DATA_SOURCE_SKEY"} if "DIM_DATA_SOURCE_SKEY" in text else set())) and name_col:
                conventions["data_source"] = {"relation": "{{ ref('dim_data_source') }}", "skey": "DIM_DATA_SOURCE_SKEY",
                                              "name": name_col, "from": c.get("path")}
                break
    return conventions, notes
