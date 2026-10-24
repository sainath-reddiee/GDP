"""The code standards agent: an independent reviewer of a generated dbt project against the written standards.

It reads the whole generated project (models, YAML, sources, tests, macros) and judges it, area by area, against the
standards the run is held to: its own skill (CODE-STANDARDS-REVIEW, versioned in Skill Studio, so a better skill is a
better review) and the generation playbooks bound to the run (the dbt, Silver or Gold model and domain skills). It never
edits code. Each area becomes one validation check (STANDARDS_<AREA>), so the review moves the validation score and the
gate (validation.min_score) applies to it; a finding cites the rule it relies on, the file and the line.

The review runs inside VALIDATE_DBT while `validation.standards_review` is on, and alone through REVIEW_STANDARDS
(Build and Review, "Review standards"), which reports without recording a validation. With
`validation.standards_block` off its errors are advisory: they count as warnings and never fail a check.

Pure apart from `review` (the Cortex call): the prompt, the response schema and turning the answer into checks.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

SKILL = "CODE-STANDARDS-REVIEW"
STAGE = "STANDARDS"  # the Admin skill-binding stage: CODE-STANDARDS-REVIEW plus any standards a domain adds

# the areas the agent judges, each one validation check
AREAS: List[Tuple[str, str, str]] = [
    ("NAMING", "Naming and layering", "model, column and file names, layer prefixes, one model per file, folder layout"),
    ("TYPES", "Casts and data types", "explicit casts to the target types, text trimming and NULLIF, dates and numbers"),
    ("KEYS", "Keys, joins and dedup", "business and hash keys, join types and conditions, dedup order, SCD and HIST handling"),
    ("TESTS", "Tests and data quality", "not_null, unique and accepted_values tests on the right columns, Soda checks"),
    ("DOCS", "Documentation and sources", "model and column descriptions, sources declared once, refs instead of hardcoded names"),
    ("SQL", "SQL style and safety", "no DDL or DML, no SELECT *, CTE structure, Jinja that compiles, no hardcoded databases"),
]
AREA_KEYS = [a[0] for a in AREAS]

SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["summary", "areas"],
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "areas": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["area", "verdict", "findings"],
                "additionalProperties": False,
                "properties": {
                    "area": {"type": "string", "enum": AREA_KEYS},
                    "verdict": {"type": "string", "enum": ["PASS", "WARN", "FAIL"]},
                    "findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["severity", "rule", "file", "message", "line_hint"],
                            "additionalProperties": False,
                            "properties": {
                                "severity": {"type": "string", "enum": ["error", "warning"]},
                                "rule": {"type": "string", "description": "The standard cited, e.g. 'GDP-DBT-ONBOARD-SOURCE Rule 2: casts'"},
                                "file": {"type": "string"},
                                "message": {"type": "string"},
                                "line_hint": {"type": "string", "description": "Short code excerpt the finding is about"},
                            },
                        },
                    },
                },
            },
        },
    },
}

# what of the project goes in front of the agent: models and their YAML first, then everything else, within budget
FILE_ORDER = ("models/", "macros/", "tests/", "snapshots/", "seeds/", "soda/", "")
SKIP_FILES = ("mappings/",)  # the STTM exports: inputs, not generated code
MAX_FILE_CHARS = 8000
MAX_PROJECT_CHARS = 60000
MAX_FINDINGS_PER_AREA = 15


def pick_files(files: Dict[str, str], max_files: int, max_chars: int = MAX_PROJECT_CHARS) -> Tuple[List[Tuple[str, str]], List[str]]:
    """(files reviewed, files left out): code first (models, macros, tests ...), each cut at MAX_FILE_CHARS, until the
    project budget or `max_files` runs out."""
    def rank(path: str) -> Tuple[int, str]:
        return next(i for i, prefix in enumerate(FILE_ORDER) if path.startswith(prefix)), path

    picked: List[Tuple[str, str]] = []
    left: List[str] = []
    used = 0
    for path in sorted((p for p in files if not p.startswith(SKIP_FILES)), key=rank):
        body = (files[path] or "")[:MAX_FILE_CHARS]
        if len(picked) >= max_files or used + len(body) > max_chars:
            left.append(path)
            continue
        picked.append((path, body))
        used += len(body)
    return picked, left


def prompt(picked: List[Tuple[str, str]], skill: str, standards: str, context: str, pipeline: str) -> str:
    areas = "\n".join(f"- {key} ({label}): {what}" for key, label, what in AREAS)
    project = "\n\n".join(f"=== FILE: {path} ===\n{body}" for path, body in picked)
    return (
        f"You are the code standards agent. You independently review a dbt project generated for a {pipeline} run "
        "on Snowflake. You do not fix code. Judge it ONLY against the standards below: your review skill and the "
        "generation playbooks the run is held to. For every area give a verdict (PASS, WARN or FAIL) and the concrete "
        "violations, each citing the standard it breaks, the file and a short excerpt. An error is a violation the "
        "standards require; a warning is a deviation they discourage. Do not report what the standards do not say, "
        "and do not invent files, tables or columns.\n\n"
        f"AREAS:\n{areas}\n\n"
        f"=== REVIEW SKILL ({SKILL}) ===\n{skill}\n\n"
        f"=== STANDARDS THE RUN IS HELD TO ===\n{standards}\n\n"
        f"=== APPROVED MAPPING (target columns) ===\n{context}\n\n"
        f"=== GENERATED PROJECT ===\n{project}\n"
    )


def mapping_context(lines: List[Dict[str, Any]], limit: int = 4000) -> str:
    text = "\n".join(f"{line.get('target_column')} {line.get('target_datatype') or ''} {line.get('mapping_type') or ''}"
                     f"{' required' if line.get('required') else ''}" for line in lines if line.get("target_column"))
    return text[:limit] or "(no approved mapping lines)"


def to_checks(output: Dict[str, Any], block: bool = True) -> List[Dict[str, Any]]:
    """One validation check per area (STANDARDS_<AREA>) from the agent's answer. An area the answer leaves out is
    SKIPPED (not evaluated); with `block` off, errors are recorded as warnings and never fail the check."""
    by_area = {str(a.get("area") or "").upper(): a for a in output.get("areas") or [] if isinstance(a, dict)}
    out = []
    for key, label, _ in AREAS:
        area = by_area.get(key)
        if area is None:
            out.append({"validation_type": f"STANDARDS_{key}", "status": "SKIPPED", "error_count": 0, "warning_count": 0,
                        "findings": [{"severity": "INFO", "message": f"The standards agent did not judge {label.lower()}"}]})
            continue
        findings = []
        for f in (area.get("findings") or [])[:MAX_FINDINGS_PER_AREA]:
            if not isinstance(f, dict):
                continue
            error = str(f.get("severity") or "").lower() == "error"
            findings.append({"severity": "ERROR" if error and block else "WARN",
                             "message": str(f.get("message") or "")[:1000], "rule": str(f.get("rule") or "")[:200],
                             "file": str(f.get("file") or "")[:300], "line_hint": str(f.get("line_hint") or "")[:300]})
        errors = sum(1 for f in findings if f["severity"] == "ERROR")
        # a FAIL verdict with no error finding still fails the area (when errors block)
        if block and errors == 0 and str(area.get("verdict") or "").upper() == "FAIL":
            findings.append({"severity": "ERROR", "message": f"The standards agent failed {label.lower()} without a finding",
                             "rule": SKILL, "file": "", "line_hint": ""})
            errors = 1
        warnings = sum(1 for f in findings if f["severity"] == "WARN")
        out.append({"validation_type": f"STANDARDS_{key}", "status": "FAILED" if errors else "PASSED",
                    "error_count": errors, "warning_count": warnings, "findings": findings})
    return out


def unavailable(reason: str) -> List[Dict[str, Any]]:
    """The review could not run (Cortex unavailable, no answer): one SKIPPED check that says why. Skipped checks are
    not evaluated, so the gate stands on the other checks."""
    return [{"validation_type": "STANDARDS", "status": "SKIPPED", "error_count": 0, "warning_count": 1,
             "findings": [{"severity": "WARN", "message": f"The code standards agent could not review this generation: {reason}"[:1000]}]}]


def review(complete: Callable[..., Tuple[Dict[str, Any], Dict[str, Any], str]], files: Dict[str, str], skill: str,
           standards: str, lines: List[Dict[str, Any]], pipeline: str = "SILVER", max_files: int = 40,
           block: bool = True) -> Dict[str, Any]:
    """Review a generated project. `complete(prompt, schema, max_tokens)` is the Cortex call (llm.complete_json bound
    to a session). Returns {checks, summary, reviewed, left_out, usage, model, duration_ms}; never raises: a failed
    call becomes one SKIPPED check with the reason."""
    picked, left = pick_files(files, max_files)
    started = time.time()
    if not picked:
        return {"checks": unavailable("the generation has no files to review"), "summary": "", "reviewed": [],
                "left_out": left, "usage": {}, "model": None, "duration_ms": 0}
    try:
        output, usage, model = complete(prompt(picked, skill, standards, mapping_context(lines), pipeline), SCHEMA, 8000)
    except Exception as exc:  # Cortex errors, no structured answer
        return {"checks": unavailable(str(exc)[:300]), "summary": "", "reviewed": [p for p, _ in picked], "left_out": left,
                "usage": {}, "model": None, "duration_ms": int((time.time() - started) * 1000)}
    checks = to_checks(output if isinstance(output, dict) else {}, block)
    if left:
        for c in checks:
            if c["validation_type"] == "STANDARDS_DOCS":
                c["findings"].append({"severity": "INFO", "message": f"{len(left)} file(s) were outside the review budget: {', '.join(left[:10])}"})
    return {"checks": checks, "summary": str((output or {}).get("summary") or "")[:2000], "reviewed": [p for p, _ in picked],
            "left_out": left, "usage": usage or {}, "model": model, "duration_ms": int((time.time() - started) * 1000)}


def standards_text(skills: List[Dict[str, Any]], excerpt: Callable[[str, int], str], limit: int = 4000) -> str:
    """The generation playbooks the run is held to, each cut to `limit` characters."""
    return "\n\n".join(f"[{s['skill_name']} v{s['version']}]\n{excerpt(s.get('content') or '', limit)}" for s in skills) \
        or "(no generation playbook is bound to this run)"


def gate_note(result: Dict[str, Any]) -> Optional[str]:
    """One line for the run's audit: what the agent judged."""
    failed = [c["validation_type"].replace("STANDARDS_", "").lower() for c in result["checks"] if c["status"] == "FAILED"]
    if any(c["validation_type"] == "STANDARDS" for c in result["checks"]):
        return "standards review skipped"
    return f"standards: {len(result['reviewed'])} files, " + (f"failed {', '.join(failed)}" if failed else "every area passed")
