"""Deterministic validation of a generated dbt project (pure).

Layout-agnostic: the model under test is whichever generated model carries the STTM's target columns (silver
ephemeral models today, marts in older projects), and the model YAML is any models/ YAML that declares models."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

LAYER_PREFIX = (
    ("models/staging/", "stg_"),
    ("models/intermediate/", "int_"),
    ("models/marts/", ("dim_", "fct_")),
)


def run(files: Dict[str, str], sttm_lines: List[Dict[str, Any]], soda_yaml: str) -> List[Dict[str, Any]]:
    results = [
        _check("NAMING", _naming(files)),
        _check("REQUIRED_COLUMNS", _required_columns(files, sttm_lines)),
        _check("STTM_CONSISTENCY", _sttm_consistency(files, sttm_lines)),
        _check("SODA_CONFIG", _soda_config(files, soda_yaml)),
        _check("DATATYPE", _datatypes(files, sttm_lines)),
        _check("SCHEMA", _schema_yml(files, sttm_lines)),
    ]
    return results


def summary(results: List[Dict[str, Any]]) -> Tuple[str, int, int]:
    errors = sum(r["error_count"] for r in results)
    warnings = sum(r["warning_count"] for r in results)
    return ("PASSED" if errors == 0 else "FAILED", errors, warnings)


PASSING = ("PASSED", "WARNING", "WARN")


def score(results: List[Dict[str, Any]]) -> Optional[float]:
    """The share of evaluated checks that passed, 0..100 unrounded (warnings pass; skipped checks are not evaluated),
    or None when nothing was evaluated. The pages compute the same number (lib/os/data.ts scoreOf)."""
    evaluated = [r for r in results if r.get("validation_type") != "SUMMARY" and r.get("status") != "SKIPPED"]
    if not evaluated:
        return None
    return 100.0 * sum(1 for r in evaluated if r.get("status") in PASSING) / len(evaluated)


def gate(results: List[Dict[str, Any]], min_score: float, block_on_errors: bool = True) -> Dict[str, Any]:
    """The validation gate: PASSED when the score reaches min_score and, while block_on_errors holds, no check
    reported an error. A FAILED gate carries the reasons, so the run says what to improve before validating again."""
    errors = sum(r["error_count"] for r in results)
    warnings = sum(r["warning_count"] for r in results)
    exact = score(results)
    value = None if exact is None else round(exact, 1)
    reasons = []
    if exact is None:
        reasons.append("no check was evaluated")
    elif exact < min_score:
        reasons.append(f"score {value:g}% is below the {min_score:g}% minimum")
    if block_on_errors and errors:
        reasons.append(f"{errors} error{'' if errors == 1 else 's'} must be fixed")
    return {"status": "FAILED" if reasons else "PASSED", "errors": errors, "warnings": warnings, "score": value,
            "min_score": min_score, "block_on_errors": block_on_errors, "reasons": reasons}


def _check(kind: str, findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    errors = sum(1 for f in findings if f["severity"] == "ERROR")
    warnings = sum(1 for f in findings if f["severity"] == "WARN")
    return {"validation_type": kind, "status": "FAILED" if errors else "PASSED",
            "error_count": errors, "warning_count": warnings, "findings": findings}


def _naming(files: Dict[str, str]) -> List[Dict[str, Any]]:
    findings = []
    if "dbt_project.yml" not in files:
        findings.append(_err("dbt_project.yml is missing"))
    for path in files:
        if not path.endswith(".sql") or path.startswith("macros/"):
            continue
        name = path.rsplit("/", 1)[-1]
        for folder, prefix in LAYER_PREFIX:
            if path.startswith(folder):
                ok = name.startswith(prefix) if isinstance(prefix, str) else name.startswith(prefix)
                if not ok:
                    findings.append(_err(f"{path} does not use the {folder.split('/')[1]} layer prefix {prefix}"))
    return findings


def _model_sql(files: Dict[str, str], lines: List[Dict[str, Any]] = ()) -> Tuple[str, str]:
    """The generated model that carries the STTM: the models/*.sql file aliasing the most target columns."""
    cols = [(line.get("target_column") or "").lower() for line in lines if line.get("target_column")]
    best, best_hits = ("", ""), -1
    for path, content in files.items():
        if not (path.startswith("models/") and path.endswith(".sql")):
            continue
        hits = sum(1 for c in cols if re.search(rf"\bas\s+{re.escape(c)}\b", content, re.I))
        if hits > best_hits:
            best, best_hits = (path, content), hits
    return best


def _mart_sql(files: Dict[str, str], lines: List[Dict[str, Any]] = ()) -> Tuple[str, str]:  # kept for callers
    return _model_sql(files, lines)


def _required_columns(files: Dict[str, str], lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    path, sql = _model_sql(files, lines)
    if not sql:
        return [_err("no model was generated under models/")]
    findings = []
    for line in lines:
        col = (line.get("target_column") or "").lower()
        if line.get("required") or (not line.get("nullable_rule") and (line.get("mapping_type") or "") != "UNMAPPED"):
            if not re.search(rf"\bas\s+{re.escape(col)}\b", sql, re.I):
                findings.append(_err(f"required column {col} is missing from {path}"))
    return findings


def _sttm_consistency(files: Dict[str, str], lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    path, sql = _model_sql(files, lines)
    findings = []
    for line in lines:
        col = (line.get("target_column") or "").lower()
        if not re.search(rf"\bas\s+{re.escape(col)}\b", sql, re.I):
            findings.append(_err(f"STTM column {col} has no expression in {path or 'the model'}"))
        mapping = (line.get("mapping_type") or "").upper()
        transform = (line.get("transformation") or "").strip()
        if mapping == "TRANSFORM" and transform and transform.split("(")[0].lower() not in sql.lower():
            findings.append(_warn(f"transformation for {col} does not appear in {path}"))
    return findings


def _soda_config(files: Dict[str, str], soda_yaml: str) -> List[Dict[str, Any]]:
    content = files.get("soda/checks.yml") or ""
    if not content.strip() or content.strip().startswith("#"):
        return [_err("soda/checks.yml is empty")]
    findings = []
    if soda_yaml and "row_count" not in content:
        findings.append(_warn("soda checks do not include a row_count check"))
    if "checks for" not in content:
        findings.append(_err("soda/checks.yml is not a SodaCL checks document"))
    return findings


def _datatypes(files: Dict[str, str], lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    _, sql = _model_sql(files, lines)
    findings = []
    for line in lines:
        if (line.get("mapping_type") or "").upper() in {"UNMAPPED", "DERIVED"}:
            continue
        dtype = (line.get("target_datatype") or "").upper()
        col = (line.get("target_column") or "").lower()
        source_type = (line.get("source_datatype") or "").upper()
        if source_type and source_type.split("(")[0] == dtype.split("(")[0]:
            continue  # same type end to end: Rule 2 says no cast
        if dtype.startswith("DATE") or dtype.startswith("TIMESTAMP") or dtype.startswith("NUMBER"):
            upper = sql.upper()
            if "CAST(" not in upper and "TRY_TO_" not in upper and "::" not in upper:
                findings.append(_warn(f"{col} ({dtype}) has no cast in the model SQL"))
    return findings


def _schema_yml(files: Dict[str, str], lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    yml = next((c for p, c in files.items()
                if p.startswith("models/") and p.endswith((".yml", ".yaml")) and re.search(r"^models:", c, re.M)), "")
    if not yml:
        return [_err("no model YAML (models: block) was generated under models/")]
    findings = []
    for line in lines:
        col = (line.get("target_column") or "").lower()
        if f"name: {col}" not in yml:
            findings.append(_err(f"schema.yml is missing column {col}"))
    return findings


def _err(message: str) -> Dict[str, Any]:
    return {"severity": "ERROR", "message": message}


def _warn(message: str) -> Dict[str, Any]:
    return {"severity": "WARN", "message": message}
