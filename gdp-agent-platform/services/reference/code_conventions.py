"""What a team's existing dbt code says about how code is written (pure), so generated code follows it.

From the files of a dbt project (uploaded, or read from an indexed repository): the folder each layer's models live
in, the model name prefixes, the materializations and incremental strategies, the macros models call (and the
history hook gold models end with), how surrogate keys and hash keys are made, and the data-source dimension gold facts
join. Each finding carries the evidence count, so a single odd file does not set a convention.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict

CONFIG = re.compile(r"\{\{\s*config\((.*?)\)\s*\}\}", re.S)
MACRO_CALL = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\(", re.I)
KV = re.compile(r"(\w+)\s*=\s*['\"]([^'\"]+)['\"]")
POST_HOOK = re.compile(r"post_hook\s*=\s*\[?\s*['\"]\{\{\s*([a-z_][a-z0-9_]*)\s*\(", re.I)
NEXTVAL = re.compile(r"(\w+\.)?(seq_\w+)\.nextval", re.I)
HASH = re.compile(r"\b(sha2|md5|hash)\s*\(", re.I)
BUILTIN = {"ref", "source", "config", "var", "this", "is_incremental", "env_var", "log", "return", "adapter", "run_query",
           "target", "statement", "exceptions", "dbt_utils"}


def _top(counter: Counter, n: int = 3) -> list:
    return [{"value": k, "count": v} for k, v in counter.most_common(n)]


def learn(files: Dict[str, str]) -> Dict[str, Any]:
    """Conventions found in the project, each with how often it was seen."""
    models = {p: t for p, t in files.items() if p.lower().endswith(".sql") and "/models/" in f"/{p.lower()}"}
    folders, prefixes, materialized, strategies, macros, hooks, keys, hashes = (Counter() for _ in range(8))
    data_source = Counter()
    for path, text in models.items():
        low = path.lower()
        parts = low.split("/models/", 1)[-1].split("/")
        if len(parts) > 1:
            folders[parts[0]] += 1
        name = parts[-1].rsplit(".", 1)[0]
        prefix = re.match(r"^([a-z]+_)", name)
        if prefix:
            prefixes[prefix.group(1)] += 1
        for cfg in CONFIG.findall(text):
            kv = dict(KV.findall(cfg))
            if kv.get("materialized"):
                materialized[kv["materialized"]] += 1
            if kv.get("incremental_strategy"):
                strategies[kv["incremental_strategy"]] += 1
            for hook in POST_HOOK.findall(cfg):
                hooks[hook] += 1
        for m in MACRO_CALL.findall(text):
            if m.lower() not in BUILTIN:
                macros[m.lower()] += 1
        if NEXTVAL.search(text):
            keys["sequence"] += 1
        if HASH.search(text):
            hashes[HASH.search(text).group(1).lower()] += 1
        if re.search(r"ref\(\s*['\"]dim_data_source['\"]", text, re.I):
            data_source["ref('dim_data_source')"] += 1
    out: Dict[str, Any] = {}
    if models:
        out["models"] = len(models)
    for key, counter in (("layer_folders", folders), ("name_prefixes", prefixes), ("materializations", materialized),
                         ("incremental_strategies", strategies), ("macros", macros), ("history_hook", hooks),
                         ("surrogate_keys", keys), ("hash_functions", hashes), ("data_source", data_source)):
        if counter:
            out[key] = _top(counter, 5 if key == "macros" else 3)
    macros_defined = [p for p in files if p.lower().endswith(".sql") and "/macros/" in f"/{p.lower()}"]
    if macros_defined:
        out["macro_files"] = len(macros_defined)
    return out
