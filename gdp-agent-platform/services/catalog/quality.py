"""Quality Center and Onboarding Hub (pure): readiness gates over a run's quality signals, and validation of the
files a domain is onboarded with (domain_pack.json and its data contract, YAML or the platform's markdown form)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

HEALTH_THRESHOLD = 90


def readiness(scan: Optional[Dict[str, Any]], qa: Optional[Dict[str, Any]], signoff: Optional[Dict[str, Any]],
              open_incidents: int = 0, threshold: int = HEALTH_THRESHOLD) -> Dict[str, Any]:
    """One run's gate: READY when the latest data quality scan is healthy with no failures, the latest QA run has no
    failures or errors, QA is signed off and no linked Jira issue is open. Every reason that holds it back is listed."""
    gates: List[Dict[str, Any]] = []

    def gate(name: str, ok: Optional[bool], detail: str) -> None:
        gates.append({"gate": name, "status": "PENDING" if ok is None else ("PASS" if ok else "FAIL"), "detail": detail})

    if not scan:
        gate("data_quality", None, "no data quality scan yet")
    else:
        health, failed = int(scan.get("health") or 0), int(scan.get("failed") or 0)
        gate("data_quality", health >= threshold and failed == 0,
             f"health {health} (needs {threshold}), {failed} failed checks")
    if not qa:
        gate("qa_tests", None, "no QA run yet")
    else:
        failed, errors, tests = int(qa.get("failed") or 0), int(qa.get("errors") or 0), int(qa.get("tests") or 0)
        gate("qa_tests", tests > 0 and failed == 0 and errors == 0, f"{tests} tests, {failed} failed, {errors} errors")
    if not signoff:
        gate("qa_signoff", None, "not signed off")
    else:
        gate("qa_signoff", str(signoff.get("decision") or "").upper() == "APPROVED",
             f"{str(signoff.get('decision') or '').lower()} by {signoff.get('decided_by') or 'unknown'}")
    gate("incidents", open_incidents == 0, f"{open_incidents} open linked issues" if open_incidents else "no open issues")
    statuses = [g["status"] for g in gates]
    status = "BLOCKED" if "FAIL" in statuses else ("PENDING" if "PENDING" in statuses else "READY")
    return {"status": status, "gates": gates,
            "score": round(100 * statuses.count("PASS") / len(statuses))}


# ---------------------------------------------------------------- contract YAML


class ContractColumn(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = Field(min_length=1, max_length=255)
    type: Optional[str] = Field(default=None, max_length=100)
    required: bool = False
    unique: bool = False
    description: Optional[str] = None
    accepted_values: Optional[List[Any]] = None


class ContractTable(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    grain: Optional[str] = None
    business_keys: List[str] = Field(default_factory=list)
    columns: List[ContractColumn] = Field(min_length=1)


class Contract(BaseModel):
    model_config = ConfigDict(extra="allow")
    domain: str = Field(min_length=1, max_length=64)
    version: Optional[str] = None
    owner: Optional[str] = None
    tables: List[ContractTable] = Field(min_length=1)


def validate_contract_yaml(text: str) -> Dict[str, Any]:
    """{ok, problems, summary, contract}. Accepts the platform's YAML contract shape: domain, owner, version and
    tables with columns (name, type, required, unique, accepted_values)."""
    try:
        import yaml
    except ImportError:  # pragma: no cover - pyyaml is in the API requirements
        return {"ok": False, "problems": ["YAML support is not installed on this host (pyyaml)"], "summary": {}}
    try:
        raw = yaml.safe_load(text or "")
    except yaml.YAMLError as exc:
        return {"ok": False, "problems": [f"not valid YAML: {str(exc).splitlines()[0]}"], "summary": {}}
    if not isinstance(raw, dict):
        return {"ok": False, "problems": ["the contract must be a YAML mapping with domain and tables"], "summary": {}}
    try:
        contract = Contract.model_validate(raw)
    except ValidationError as exc:
        problems = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:20]]
        return {"ok": False, "problems": problems, "summary": {}}
    problems = []
    for t in contract.tables:
        names = [c.name.upper() for c in t.columns]
        for dup in sorted({n for n in names if names.count(n) > 1}):
            problems.append(f"{t.name}: column {dup} appears twice")
        for k in t.business_keys:
            if k.upper() not in names:
                problems.append(f"{t.name}: business key {k} is not a column")
    summary = {"domain": contract.domain.upper(), "tables": len(contract.tables),
               "columns": sum(len(t.columns) for t in contract.tables)}
    return {"ok": not problems, "problems": problems, "summary": summary, "contract": contract.model_dump()}


def validate_contract_markdown(text: str) -> Dict[str, Any]:
    from services.knowledge.contracts import parse_contract

    try:
        parsed = parse_contract(text or "")
    except Exception as exc:
        return {"ok": False, "problems": [f"could not read the contract: {exc}"], "summary": {}}
    targets = parsed.get("targets") or []
    if isinstance(targets, dict):
        targets = list(targets.values())
    columns = sum(len(t.get("columns") or []) for t in targets)
    problems = [] if targets and columns else ["no canonical column contract section was found"]
    return {"ok": not problems, "problems": problems,
            "summary": {"tables": len(targets), "columns": columns,
                        "required_keys": len(parsed.get("required_mapping") or [])}}


def validate_contract(name: str, text: str) -> Dict[str, Any]:
    kind = "yaml" if str(name or "").lower().endswith((".yml", ".yaml")) else "markdown"
    result = validate_contract_yaml(text) if kind == "yaml" else validate_contract_markdown(text)
    return {"kind": kind, **result}
