"""The starter domain_pack.json and contract.yaml offered on the Onboarding page must pass the same validation an
import runs, so a team that starts from them never hits an error on its first upload."""

import json
import re
from pathlib import Path

from services.catalog.quality import validate_contract_yaml
from services.knowledge.packs import prepare
from services.knowledge.validate import validate_pack

TEMPLATES = (Path(__file__).resolve().parents[2] / "apps" / "web" / "app" / "dashboard" / "onboarding" / "templates.ts").read_text(encoding="utf-8")


def _template(name: str) -> str:
    match = re.search(rf"export const {name} = `(.*?)`;", TEMPLATES, re.S)
    assert match, f"{name} not found"
    assert "${" not in match.group(1), "templates are plain text, not interpolated"
    return match.group(1)


def test_the_pack_template_imports_cleanly():
    pack, problems = prepare(json.loads(_template("PACK_TEMPLATE")))
    assert problems == [] and validate_pack(pack) == []
    assert pack["domain"]["name"] == "CUSTOMER" and pack["targets"][0]["columns"][0]["business_key"]


def test_the_contract_template_validates():
    result = validate_contract_yaml(_template("CONTRACT_TEMPLATE"))
    assert result["ok"], result["problems"]
    assert result["summary"] == {"domain": "CUSTOMER", "tables": 1, "columns": 4}
