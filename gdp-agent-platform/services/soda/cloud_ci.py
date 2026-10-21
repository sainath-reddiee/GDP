"""Publishing approved data quality suites to Soda Cloud from CI.

The Data Quality Store publishes an approved suite as a pull request with dq/<domain>/<table>.soda.yml (SodaCL v3,
`checks for DATABASE.SCHEMA.TABLE:`). Once merged, this GitHub Actions workflow scans each changed file with Soda Core
and a Soda Cloud connection, so the checks and their results appear in Soda Cloud. The platform never holds Soda Cloud
keys: they are repository secrets, and every credential stays an environment placeholder (${VAR}) in the files the
script writes. A workflow file cannot be pushed by the platform's publisher (GitHub needs the `workflow` scope), so a
person commits these two files once.
"""

from __future__ import annotations

from typing import Dict, List

WORKFLOW_PATH = ".github/workflows/soda-cloud.yml"
SCRIPT_PATH = "scripts/soda_cloud_scan.py"
SECRETS: List[str] = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD", "SNOWFLAKE_WAREHOUSE", "SNOWFLAKE_ROLE",
                      "SODA_CLOUD_API_KEY_ID", "SODA_CLOUD_API_KEY_SECRET"]

SCRIPT = r'''"""Scan data quality suites with Soda Core and send the results to Soda Cloud.

Usage: python scripts/soda_cloud_scan.py [dq/<domain>/<table>.soda.yml ...]   (no arguments: every suite under dq/)
Environment: SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD, SNOWFLAKE_WAREHOUSE, SNOWFLAKE_ROLE,
SODA_CLOUD_API_KEY_ID, SODA_CLOUD_API_KEY_SECRET, optional SODA_CLOUD_HOST (default cloud.soda.io; cloud.us.soda.io
for the US region). Exits with the highest exit code Soda returned, so the job fails when a scan does not pass.
"""
import pathlib
import re
import subprocess
import sys
import tempfile

DATA_SOURCE = "snowflake_dq"
HEADER = re.compile(r'^checks for\s+"?([^".\s]+)"?\."?([^".\s]+)"?\."?([^":\s]+)"?\s*:\s*$')


def split(text):
    """(database, schema, checks with the table name only) from a suite file written by the platform."""
    lines = text.splitlines()
    first = next((i for i, l in enumerate(lines) if l.strip()), None)
    m = HEADER.match(lines[first].strip()) if first is not None else None
    if not m:
        raise ValueError("the first line must be: checks for DATABASE.SCHEMA.TABLE:")
    database, schema, table = m.groups()
    body = [f"checks for {table}:"] + lines[first + 1:]
    return database, schema, "\n".join(body) + "\n"


def configuration(database, schema):
    return f"""data_source {DATA_SOURCE}:
  type: snowflake
  username: ${{SNOWFLAKE_USER}}
  password: ${{SNOWFLAKE_PASSWORD}}
  account: ${{SNOWFLAKE_ACCOUNT}}
  warehouse: ${{SNOWFLAKE_WAREHOUSE}}
  role: ${{SNOWFLAKE_ROLE}}
  database: {database}
  schema: {schema}
  session_parameters:
    QUERY_TAG: soda-cloud-ci
soda_cloud:
  host: ${{SODA_CLOUD_HOST}}
  api_key_id: ${{SODA_CLOUD_API_KEY_ID}}
  api_key_secret: ${{SODA_CLOUD_API_KEY_SECRET}}
"""


def main(paths):
    files = [pathlib.Path(p) for p in paths] or sorted(pathlib.Path("dq").glob("**/*.soda.yml"))
    files = [f for f in files if f.exists() and f.name.endswith(".soda.yml")]
    if not files:
        print("no data quality suites to scan")
        return 0
    worst = 0
    for f in files:
        database, schema, checks = split(f.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            cfg, chk = pathlib.Path(tmp, "configuration.yml"), pathlib.Path(tmp, "checks.yml")
            cfg.write_text(configuration(database, schema), encoding="utf-8")
            chk.write_text(checks, encoding="utf-8")
            print(f"::group::{f} ({database}.{schema})")
            code = subprocess.call(["soda", "scan", "-d", DATA_SOURCE, "-c", str(cfg), str(chk)])
            print("::endgroup::")
        print(f"{f}: soda scan exited {code}")
        worst = max(worst, 0 if code == 1 else code)   # Soda exits 1 when checks only warn: not a failure
    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''

WORKFLOW = f"""# Scan approved data quality suites with Soda Core and publish the results to Soda Cloud.
# Runs when a Data Quality Store pull request (dq/<domain>/<table>.soda.yml) is merged, and on demand.
# Repository secrets: {", ".join(SECRETS)}
# Optional repository variable SODA_CLOUD_HOST (default cloud.soda.io; cloud.us.soda.io for the US region).
name: soda-cloud
on:
  push:
    branches: [main]
    paths: ["dq/**/*.soda.yml"]
  workflow_dispatch:
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 2
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install soda-core-snowflake
      - name: Scan and publish to Soda Cloud
        env:
          SNOWFLAKE_ACCOUNT: ${{{{ secrets.SNOWFLAKE_ACCOUNT }}}}
          SNOWFLAKE_USER: ${{{{ secrets.SNOWFLAKE_USER }}}}
          SNOWFLAKE_PASSWORD: ${{{{ secrets.SNOWFLAKE_PASSWORD }}}}
          SNOWFLAKE_WAREHOUSE: ${{{{ secrets.SNOWFLAKE_WAREHOUSE }}}}
          SNOWFLAKE_ROLE: ${{{{ secrets.SNOWFLAKE_ROLE }}}}
          SODA_CLOUD_HOST: ${{{{ vars.SODA_CLOUD_HOST || 'cloud.soda.io' }}}}
          SODA_CLOUD_API_KEY_ID: ${{{{ secrets.SODA_CLOUD_API_KEY_ID }}}}
          SODA_CLOUD_API_KEY_SECRET: ${{{{ secrets.SODA_CLOUD_API_KEY_SECRET }}}}
        run: |
          if [ "${{{{ github.event_name }}}}" = "push" ]; then
            changed=$(git diff --name-only HEAD~1 HEAD -- 'dq/**/*.soda.yml')
            python {SCRIPT_PATH} $changed
          else
            python {SCRIPT_PATH}
          fi
"""


def files() -> Dict[str, str]:
    """{repository path: content} to commit once."""
    return {WORKFLOW_PATH: WORKFLOW, SCRIPT_PATH: SCRIPT}
