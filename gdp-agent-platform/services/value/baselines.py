"""Effort baselines of the value ledger: how long each kind of work takes a person without the platform.

The counts the ledger multiplies are measured (what the platform did, read from its own tables); the minutes per item
are assumptions. They ship as Anblicks defaults and an admin tunes them per client (PLATFORM_CONFIG key VALUE_BASELINES,
versioned, so every change is kept with who made it). The ledger always shows which minutes are the shipped default and
which an admin set, so a reader never takes an assumption for a measurement.

Partial credit: work a person had to change counts for part of its baseline (a mapping the reviewer modified, a test
someone edited), and rejected work counts for nothing.

Hours are always an estimate. Money (hours x the blended rate) is shown only once an admin confirms the baselines fit
this client and says where they come from (`confirmed`, `basis_note`); until then the ledger gives hours and no amount.
`exclude` keeps test and lab activity out of the counts.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Tuple

KEY = "VALUE_BASELINES"

# key -> label, group, unit, default minutes per item, where the count comes from
ACTIVITIES: Dict[str, Dict[str, Any]] = {
    "mapping_column": {"label": "Column mappings", "group": "build", "unit": "column", "minutes": 8,
                       "source": "reviewed mapping decisions (modified ones count half)"},
    "sttm_line": {"label": "Data Mapping lines", "group": "build", "unit": "line", "minutes": 5,
                  "source": "STTM lines approved by a person"},
    "dbt_file": {"label": "dbt files", "group": "build", "unit": "file", "minutes": 60,
                 "source": "files of the latest dbt generation of each run"},
    "qa_test": {"label": "QA tests", "group": "build", "unit": "test", "minutes": 20,
                "source": "AI-written QA tests kept (edited ones count half)"},
    "dq_check": {"label": "Data quality checks", "group": "build", "unit": "check", "minutes": 10,
                 "source": "AI-proposed checks approved (changed ones count half)"},
    "auto_fix": {"label": "Failures fixed automatically", "group": "ops", "unit": "fix", "minutes": 45,
                 "source": "known fixes that ran successfully"},
    "rca_ticket": {"label": "Root causes analysed", "group": "ops", "unit": "analysis", "minutes": 30,
                   "source": "failures analysed and ticketed with a cause"},
    "incident_diagnosis": {"label": "Incidents diagnosed", "group": "ops", "unit": "diagnosis", "minutes": 15,
                           "source": "AI diagnoses of incidents"},
    "case_triage": {"label": "Cases triaged", "group": "ops", "unit": "case", "minutes": 20,
                    "source": "support cases triaged by the platform"},
}

DEFAULTS: Dict[str, Any] = {
    "activities": {k: {"minutes": a["minutes"]} for k, a in ACTIVITIES.items()},
    "blended_rate_usd": 75,
    # share of the baseline credited by what a person did with the platform's work
    "credit": {"APPROVED": 1.0, "MODIFIED": 0.5, "ALTERNATIVE_TARGET": 0.25, "REJECTED": 0.0, "EDITED": 0.5},
    "knowledge_weights": {"auto_fix_share": 0.5, "dag_coverage": 0.25, "fix_coverage": 0.25},
    "min_samples": 5,
    # activity that is not real work: test runs, and the platform's own lab (its failures are injected on purpose)
    "exclude": {"run_environments": ["TEST"], "airflow_envs": ["local-lab"]},
    # money is shown only once an admin confirms the minutes and the rate fit this client, saying where they come from
    "confirmed": False,
    "basis_note": "",
}
NAME = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")


def _number(value: Any, name: str, problems: List[str], low: float = 0.0, high: float = 1e6) -> Any:
    try:
        number = float(value)
    except (TypeError, ValueError):
        problems.append(f"{name} must be a number")
        return None
    if not low <= number <= high:
        problems.append(f"{name} must be between {low:g} and {high:g}")
        return None
    return number


def validate(value: Any) -> Tuple[Dict[str, Any], List[str]]:
    """(cleaned baselines, problems). Unknown activities and keys are refused; anything left out keeps its default."""
    problems: List[str] = []
    if not isinstance(value, dict):
        return copy.deepcopy(DEFAULTS), ["baselines must be an object"]
    out = copy.deepcopy(DEFAULTS)
    unknown = sorted(set(value) - set(DEFAULTS))
    problems += [f"unknown baseline setting {k}" for k in unknown]
    for key, item in (value.get("activities") or {}).items():
        if key not in ACTIVITIES:
            problems.append(f"unknown activity {key}")
            continue
        minutes = _number((item or {}).get("minutes") if isinstance(item, dict) else item, f"{key} minutes",
                          problems, 0, 10000)
        if minutes is not None:
            out["activities"][key] = {"minutes": minutes}
    if "blended_rate_usd" in value:
        rate = _number(value["blended_rate_usd"], "blended rate", problems, 0, 10000)
        if rate is not None:
            out["blended_rate_usd"] = rate
    for key, credit in (value.get("credit") or {}).items():
        if key not in DEFAULTS["credit"]:
            problems.append(f"unknown credit {key}")
            continue
        number = _number(credit, f"credit {key}", problems, 0, 1)
        if number is not None:
            out["credit"][key] = number
    weights = value.get("knowledge_weights")
    if weights is not None:
        clean = {}
        for key in DEFAULTS["knowledge_weights"]:
            number = _number(weights.get(key, DEFAULTS["knowledge_weights"][key]), f"weight {key}", problems, 0, 1)
            clean[key] = number if number is not None else DEFAULTS["knowledge_weights"][key]
        if abs(sum(clean.values()) - 1.0) > 0.001:
            problems.append("knowledge weights must add up to 1")
        out["knowledge_weights"] = clean
    if "min_samples" in value:
        samples = _number(value["min_samples"], "minimum samples", problems, 1, 1000)
        if samples is not None:
            out["min_samples"] = int(samples)
    exclude = value.get("exclude")
    if exclude is not None:
        if not isinstance(exclude, dict):
            problems.append("exclude must be an object of lists")
        else:
            problems += [f"unknown exclusion {k}" for k in sorted(set(exclude) - set(DEFAULTS["exclude"]))]
            for key in DEFAULTS["exclude"]:
                items = exclude.get(key, DEFAULTS["exclude"][key])
                if not isinstance(items, list) or len(items) > 50:
                    problems.append(f"{key} must be a list of at most 50 names")
                    continue
                bad = [str(i) for i in items if not NAME.match(str(i).strip())]
                if bad:
                    problems.append(f"{key}: {bad[0][:40]} is not a valid name")
                    continue
                names = [str(i).strip() for i in items]
                out["exclude"][key] = sorted({n.upper() for n in names} if key == "run_environments" else set(names))
    if "basis_note" in value:
        out["basis_note"] = str(value.get("basis_note") or "").strip()[:1000]
    if "confirmed" in value:
        out["confirmed"] = value.get("confirmed") is True
        if out["confirmed"] and len(out["basis_note"]) < 10:
            problems.append("say where the minutes and the rate come from (a time study, timesheets, the client's "
                            "estimate) before confirming them")
    return out, problems


def effective(stored: Any) -> Dict[str, Any]:
    """The baselines in force, each activity marked 'default' (the shipped assumption) or 'edited' (an admin set it)."""
    clean, _problems = validate(stored if isinstance(stored, dict) else {})
    stored_acts = (stored or {}).get("activities") if isinstance(stored, dict) else {}
    out = copy.deepcopy(clean)
    for key, act in out["activities"].items():
        default = ACTIVITIES[key]["minutes"]
        edited = isinstance(stored_acts, dict) and key in stored_acts and float(act["minutes"]) != float(default)
        out["activities"][key] = {**act, "default_minutes": default, "basis": "edited" if edited else "default",
                                  **{k: ACTIVITIES[key][k] for k in ("label", "group", "unit", "source")}}
    out["rate_basis"] = ("edited" if isinstance(stored, dict) and "blended_rate_usd" in stored
                         and float(out["blended_rate_usd"]) != float(DEFAULTS["blended_rate_usd"]) else "default")
    return out
