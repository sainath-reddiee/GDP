"""How close the platform's own output came to a person's reference (pure): a proposed model against the reference
model, a mapping against the reference STTM, generated code against the reference dbt project.

Each comparison is of what the engine decided before it saw the reference (the raw proposal, the ranked mapping
before a reference rule promoted anything), so the score measures the engine and moves when skills, knowledge, rules
or conventions change. Every score is {pct, matched, total, missing, extra, details}.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple

from services.gold.align import _cols, _column_in, _name, match


def _pct(matched: int, total: int) -> float:
    return round(100.0 * matched / total, 1) if total else 0.0


def _base_type(t: Any) -> str:
    s = re.sub(r"\s+", "", str(t or "").upper()).split("(")[0]
    return {"VARCHAR": "STRING", "TEXT": "STRING", "CHAR": "STRING", "NVARCHAR": "STRING", "LONG": "NUMBER",
            "INT": "NUMBER", "INTEGER": "NUMBER", "BIGINT": "NUMBER", "DECIMAL": "NUMBER", "NUMERIC": "NUMBER",
            "FLOAT": "NUMBER", "DOUBLE": "NUMBER", "TIMESTAMP": "TIMESTAMP_NTZ", "DATETIME": "TIMESTAMP_NTZ"}.get(s, s)


def model(proposed: Dict[str, Any], reference: Dict[str, Any]) -> Dict[str, Any]:
    """Reference columns the proposal produced (in the table matched to theirs), and their types."""
    p_entities = list(((proposed or {}).get("design") or {}).get("entities") or [])
    r_entities = list(((reference or {}).get("design") or {}).get("entities") or [])
    pairs = match(p_entities, r_entities)
    by_ref = {r: p for p, (r, _how) in pairs.items()}
    p_by_name = {_name(e): e for e in p_entities}
    total = matched = typed = 0
    missing: List[str] = []
    tables = []
    for r in r_entities:
        rname, rcols = _name(r), _cols(r)
        total += len(rcols)
        p = p_by_name.get(by_ref.get(rname, ""))
        if not p:
            missing.append(rname)
            tables.append({"reference": rname, "proposed": None, "matched": 0, "columns": len(rcols)})
            continue
        p_types = {}
        for a in p.get("attributes") or []:
            col = _column_in(str(a.get("name") or ""), rcols)
            own_key = str(a.get("name") or "").upper() == f"{_name(p)}_SKEY"
            if own_key and not col:
                col = next((str(x["name"]).upper() for x in r.get("attributes") or [] if x.get("is_pk")), None)
            if col:
                p_types[col] = a.get("datatype")
        hit = [c for c in rcols if c in p_types]
        matched += len(hit)
        r_types = {str(a["name"]).upper(): a.get("datatype") for a in r.get("attributes") or []}
        typed += sum(1 for c in hit if _base_type(p_types[c]) == _base_type(r_types.get(c)))
        missing += [f"{rname}.{c}" for c in rcols if c not in p_types][:20]
        tables.append({"reference": rname, "proposed": _name(p), "how": pairs[_name(p)][1], "matched": len(hit),
                       "columns": len(rcols)})
    extra = [_name(e) for e in p_entities if _name(e) not in pairs and not e.get("conformed_from")]
    return {"pct": _pct(matched, total), "matched": matched, "total": total, "missing": missing[:50], "extra": extra[:50],
            "details": {"tables": tables, "types_agree_pct": _pct(typed, matched),
                        "tables_matched": len(pairs), "reference_tables": len(r_entities)}}


def mapping(chosen: Iterable[Tuple[str, str]], reference_lines: Dict[str, Dict[str, Any]],
            source_columns: Iterable[str]) -> Dict[str, Any]:
    """Reference lines (whose source column the run has) the mapping's first choice got right.
    chosen: (source column, target column) the engine ranked first; reference_lines: by target column."""
    picks = {str(s).upper(): str(t).upper() for s, t in chosen}
    have = {str(s).upper() for s in source_columns}
    total = matched = 0
    missing: List[str] = []
    for target, line in reference_lines.items():
        src = str(line.get("source_column") or "").upper().split(".")[-1]
        if not src or src not in have or line.get("mapping_type") == "UNMAPPED":
            continue
        total += 1
        if picks.get(src) == str(target).upper():
            matched += 1
        else:
            missing.append(f"{src} -> {target} (engine: {picks.get(src) or 'none'})")
    expected = {str(l.get("source_column") or "").upper().split(".")[-1] for l in reference_lines.values()}
    extra = sorted(s for s in picks if s not in expected)[:50]
    return {"pct": _pct(matched, total), "matched": matched, "total": total, "missing": missing[:50], "extra": extra,
            "details": {}}


def code(generated: Iterable[str], reference_files: Iterable[str]) -> Dict[str, Any]:
    """Reference dbt models (by model name) the generated code also has."""
    def models(paths: Iterable[str]) -> set:
        return {p.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() for p in paths
                if p.lower().endswith(".sql") and "/models/" in "/" + p.lower().replace("\\", "/")}
    mine, ref = models(generated), models(reference_files)
    hit = mine & ref
    return {"pct": _pct(len(hit), len(ref)), "matched": len(hit), "total": len(ref), "missing": sorted(ref - mine)[:50],
            "extra": sorted(mine - ref)[:50], "details": {}}
