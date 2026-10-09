"""A ready-to-run Soda kit for a run's checks, so a team can scan the same model in its own environment.

Two flavours, from the same neutral checks:
  v3 (Soda Library / Soda Core 3): configuration.yml + checks.yml, `soda test-connection`, `soda scan`
  v4 (Soda Core 4 data contracts): ds_config.yml + contract.yaml, `soda data-source test`, `soda contract verify`
Secrets are never written: every credential is an environment variable placeholder. Syntax follows docs.soda.io
(SodaCL v3 reference, Snowflake data source reference, contract language reference).
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any, Dict, List

from services.quality.gx import FORMAT_REGEX
from services.soda.expectations import render_yaml

DATA_SOURCE = "snowflake_dq"


def _yq(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def configuration_v3(account: str, database: str, schema: str, warehouse: str, role: str) -> str:
    return f"""# Soda v3 (Soda Library / Soda Core) data source for Snowflake.
# Credentials come from environment variables; never commit real values.
data_source {DATA_SOURCE}:
  type: snowflake
  username: ${{SNOWFLAKE_USER}}
  password: ${{SNOWFLAKE_PASSWORD}}
  # key pair instead of a password:
  # authenticator: SNOWFLAKE_JWT
  # private_key_path: /path/to/rsa_key.p8
  # private_key_passphrase: ${{SNOWFLAKE_PRIVATE_KEY_PASSPHRASE}}
  account: {account}
  database: {database}
  schema: {schema}
  warehouse: {warehouse}
  role: {role}
  connection_timeout: 240
  client_session_keep_alive: false
  session_parameters:
    QUERY_TAG: soda-dq-scan

# Optional: send results to Soda Cloud (keys from Profile > API Keys in Soda Cloud)
# soda_cloud:
#   host: cloud.soda.io            # cloud.us.soda.io for the US region
#   api_key_id: ${{SODA_CLOUD_API_KEY_ID}}
#   api_key_secret: ${{SODA_CLOUD_API_KEY_SECRET}}
"""


def configuration_v4(account: str, database: str, warehouse: str, role: str) -> str:
    return f"""# Soda v4 data source for Snowflake (used by `soda data-source test` and `soda contract verify`).
type: snowflake
name: {DATA_SOURCE}
connection:
  account: {account}
  database: {database}
  warehouse: {warehouse}
  role: {role}
  connection_timeout: 240
  client_session_keep_alive: false
  user: ${{env.SNOWFLAKE_USER}}
  password: ${{env.SNOWFLAKE_PASSWORD}}
  # key pair instead of a password (do not set password with SNOWFLAKE_JWT):
  # authenticator: SNOWFLAKE_JWT
  # private_key_path: /path/to/rsa_key.p8
  # private_key_passphrase: ${{env.SNOWFLAKE_PRIVATE_KEY_PASSPHRASE}}
"""


SODA_CLOUD = """# Soda Cloud connection (v4: `soda cloud test -sc sc_config.yml`). Optional.
soda_cloud:
  host: cloud.soda.io          # cloud.us.soda.io for the US region
  api_key_id: ${env.SODA_CLOUD_API_KEY_ID}
  api_key_secret: ${env.SODA_CLOUD_API_KEY_SECRET}
"""


def _threshold_v4(threshold: Dict[str, Any] | None, warn: bool) -> List[str]:
    t = threshold or {}
    if "between" in t:
        lines = ["threshold:", "  must_be_between:", f"    greater_than_or_equal: {t['between'][0]}",
                 f"    less_than_or_equal: {t['between'][1]}"]
    else:
        key = {"<": "must_be_less_than", "<=": "must_be_less_than_or_equal", ">": "must_be_greater_than",
               ">=": "must_be_greater_than_or_equal", "=": "must_be", "!=": "must_not_be"}.get(str(t.get("op") or "="), "must_be")
        lines = ["threshold:", f"  {key}: {t.get('value', 0)}"]
    if warn:
        lines.append("  level: warn")
    return lines


def _block(head: str, name: str, body: List[str], indent: int) -> List[str]:
    pad = " " * indent
    out = [f"{pad}- {head}:"]
    if name:
        out.append(f"{pad}    name: {_yq(name[:120])}")
    out += [f"{pad}    {line}" for line in body]
    return out


def contract_v4(database: str, schema: str, table: str, checks: List[Dict[str, Any]]) -> str:
    """A Soda v4 data contract for one dataset. Checks with no v4 equivalent are listed as comments."""
    dataset_checks: List[str] = []
    columns: Dict[str, List[str]] = {}
    skipped: List[str] = []
    for c in checks:
        if str(c.get("status") or "").upper() == "REJECTED":
            continue
        d = c.get("definition") or {}
        kind = d.get("kind")
        col = c.get("target_column")
        name = c.get("requirement") or ""
        warn = str(c.get("severity") or "").upper() == "WARN"
        level = ["threshold:", "  must_be: 0", "  level: warn"] if warn else []
        if kind == "row_count":
            t = {"between": [d["min"], d["max"]]} if d.get("min") is not None and d.get("max") is not None \
                else {"op": ">", "value": d.get("gt", 0)}
            dataset_checks += _block("row_count", name, _threshold_v4(t, warn), 2)
        elif kind == "schema":
            dataset_checks += _block("schema", name, ["allow_extra_columns: true"], 2)
        elif kind == "freshness":
            hours = d.get("threshold") or "1d"
            n = re.match(r"(\d+)\s*([mhd]?)", str(hours))
            value, unit = (int(n.group(1)), {"m": "minute", "d": "day"}.get(n.group(2), "hour")) if n else (24, "hour")
            body = [f"column: {col or 'LOADED_AT'}", "threshold:", f"  unit: {unit}", f"  must_be_less_than: {value}"]
            if warn:
                body.append("  level: warn")
            dataset_checks += _block("freshness", name, body, 2)
        elif kind == "unique" and len(d.get("columns") or []) > 1:
            dataset_checks += _block("duplicate", name, [f"columns: [{', '.join(_yq(x) for x in d['columns'])}]"] + level, 2)
        elif kind == "failed_rows":
            if d.get("query"):
                body = ["query: |-"] + [f"  {line}" for line in str(d["query"]).strip().splitlines()]
            else:
                body = [f"expression: {_yq(d.get('condition'))}"]
            dataset_checks += _block("failed_rows", name, body + level, 2)
        elif kind == "metric":
            body = ([f"expression: {_yq(d.get('expression'))}"] if d.get("expression")
                    else ["query: |-"] + [f"  {line}" for line in str(d.get("query") or "").strip().splitlines()])
            dataset_checks += _block("metric", name, body + _threshold_v4(d.get("threshold"), warn), 2)
        elif kind in ("avg", "min", "max", "sum", "stddev") and col:
            body = [f"expression: {kind.upper()}({col})"] + _threshold_v4(d.get("threshold"), warn)
            dataset_checks += _block("metric", name or f"{kind} of {col}", body, 2)
        elif col and kind in ("not_null", "missing_percent"):
            body = []
            if d.get("missing_values"):
                body.append(f"missing_values: [{', '.join(_yq(v) for v in d['missing_values'])}]")
            if kind == "missing_percent":
                body += ["threshold:", "  metric: percent", f"  must_be_less_than: {d.get('max_percent', 0)}"] + (["  level: warn"] if warn else [])
            else:
                body += level
            columns.setdefault(col, []).extend(_block("missing", name, body, 6))
        elif col and kind in ("unique", "duplicate_percent"):
            body = level if kind == "unique" else ["threshold:", "  metric: percent",
                                                   f"  must_be_less_than: {d.get('max_percent', 0)}"] + (["  level: warn"] if warn else [])
            columns.setdefault(col, []).extend(_block("duplicate", name, body, 6))
        elif col and kind in ("accepted_values", "regex", "format", "range", "max_length"):
            if kind == "accepted_values":
                body = [f"valid_values: [{', '.join(_yq(v) for v in d.get('values') or [])}]"]
            elif kind in ("regex", "format"):
                regex = d.get("pattern") if kind == "regex" else FORMAT_REGEX.get(str(d.get("format") or "").lower())
                if not regex:
                    skipped.append(f"{col}: format {d.get('format')} has no regex")
                    continue
                body = ["valid_format:", f"  name: {_yq(d.get('format') or 'pattern')}", f"  regex: {_yq(regex)}"]
            elif kind == "range":
                body = ([f"valid_min: {d['min']}"] if d.get("min") is not None else []) + \
                       ([f"valid_max: {d['max']}"] if d.get("max") is not None else [])
            else:
                body = [f"valid_max_length: {d.get('max')}"]
            columns.setdefault(col, []).extend(_block("invalid", name, body + level, 6))
        elif kind == "reference" and col and d.get("reference_table"):
            ref = str(d["reference_table"])
            query = (f"SELECT * FROM {database}.{schema}.{table} T WHERE T.{col} IS NOT NULL AND NOT EXISTS "
                     f"(SELECT 1 FROM {ref} R WHERE R.{d.get('reference_column') or col} = T.{col})")
            dataset_checks += _block("failed_rows", name or f"{col} must exist in {ref}", ["query: |-", f"  {query}"] + level, 2)
        else:
            skipped.append(f"{col or table}: {kind} (use checks.yml with Soda v3)")
    lines = [f"dataset: {DATA_SOURCE}/{database}/{schema}/{table}", ""]
    if dataset_checks:
        lines += ["checks:"] + dataset_checks + [""]
    if columns:
        lines.append("columns:")
        for col, body in columns.items():
            lines += [f"  - name: {col}", "    checks:"] + body
    if skipped:
        lines += ["", "# Not expressible in a v4 contract (run them from checks.yml with Soda v3):"] + [f"#   {s}" for s in skipped]
    return "\n".join(lines) + "\n"


def readme(run_name: str, table_fqn: str, checks_count: int) -> str:
    return f"""# Soda data quality kit: {run_name}

Checks for `{table_fqn}` generated by the Agentic pipeline ({checks_count} checks). Run them in your own
environment, in CI, or publish results to Soda Cloud. Every credential is an environment variable.

## 1. Install
```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\\Scripts\\activate
pip install soda-snowflake          # Soda v4 data contracts (section 3b)
pip install soda-core-snowflake     # Soda Core v3 open source (section 3a)
```
Soda Library (v3 with Soda Cloud) installs from Soda's index: `pip install -i https://pypi.cloud.soda.io soda-snowflake`.

## 2. Credentials (never commit them)
```bash
export SNOWFLAKE_USER=...                # Windows PowerShell: $env:SNOWFLAKE_USER = "..."
export SNOWFLAKE_PASSWORD=...            # or key pair: see the comments in the config files
export SODA_CLOUD_API_KEY_ID=...         # optional, Soda Cloud only
export SODA_CLOUD_API_KEY_SECRET=...
```
The role in the config needs USAGE on the warehouse, database and schema and SELECT on the table(s).

## 3a. Soda v3 (Soda Library / Soda Core 3): checks.yml
```bash
soda test-connection -d {DATA_SOURCE} -c configuration.yml -V
soda scan -d {DATA_SOURCE} -c configuration.yml checks.yml
soda scan -d {DATA_SOURCE} -c configuration.yml -srf results.json checks.yml   # keep a JSON result file
```
`change for ...` checks need Soda Library and Soda Cloud history; they are not supported in Soda Core open source.

## 3b. Soda v4: data contract
```bash
soda data-source test -ds ds_config.yml
soda contract verify --data-source ds_config.yml --contract contract.yaml
soda cloud test -sc sc_config.yml                                              # optional
soda contract publish --contract contract.yaml --soda-cloud sc_config.yml      # optional
```
`soda contract verify` exits 0 when all checks pass, 1 when a check fails, 2 when checks only warn,
3 when verification could not run and 4 when results could not be sent to Soda Cloud.

## 4. CI
`.github/workflows/soda-scan.yml` runs the v3 scan on every push and nightly; add the secrets in the
repository settings.

## Troubleshooting
- **Authentication failed**: check SNOWFLAKE_USER / SNOWFLAKE_PASSWORD, or the key pair path and passphrase.
- **Object does not exist or not authorized**: the role lacks USAGE on the database/schema or SELECT on the
  table; or the model has not been built yet (dbt run first).
- **No active warehouse**: the role needs USAGE on the warehouse named in the config.
- **Network policy / IP not allowed**: run from an allowed network, or ask the Snowflake admin to allow the
  runner's IP.
- **Invalid identifier**: column names in the checks are upper case as Snowflake stores them; quoted mixed-case
  columns must match exactly.
- **Checks only warn**: WARN checks never fail the scan; promote them to FAIL in the platform when ready.
"""


def workflow() -> str:
    return f"""name: soda-scan
on:
  push:
  schedule:
    - cron: "0 3 * * *"
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install soda-core-snowflake
      - name: Soda scan
        env:
          SNOWFLAKE_USER: ${{{{ secrets.SNOWFLAKE_USER }}}}
          SNOWFLAKE_PASSWORD: ${{{{ secrets.SNOWFLAKE_PASSWORD }}}}
        run: soda scan -d {DATA_SOURCE} -c configuration.yml checks.yml
"""


def build_kit(run_name: str, account: str, database: str, schema: str, table: str, warehouse: str, role: str,
              checks: List[Dict[str, Any]]) -> Dict[str, str]:
    """{file name: content} for the kit."""
    live = [c for c in checks if str(c.get("status") or "").upper() != "REJECTED"]
    return {
        "README.md": readme(run_name, f"{database}.{schema}.{table}", len(live)),
        "configuration.yml": configuration_v3(account, database, schema, warehouse, role),
        "checks.yml": render_yaml(table, live),
        "ds_config.yml": configuration_v4(account, database, warehouse, role),
        "contract.yaml": contract_v4(database, schema, table, live),
        "sc_config.yml": SODA_CLOUD,
        ".github/workflows/soda-scan.yml": workflow(),
    }


def zip_kit(files: Dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buffer.getvalue()

