"""Start the platform API or worker against the lab's test copy, wired to the WSL Airflow.

    python airflow_lab/start_platform.py api      # uvicorn on 127.0.0.1:8021
    python airflow_lab/start_platform.py worker   # the ops worker (poll, detect, resolve, clear)

Sets AIP_DATABASE (DEV_AI_PLATFORM unless LAB_DATABASE says otherwise), role SYSADMIN (or LAB_ROLE), and the local
Airflow credentials for the environment
`local-lab` (AIP_AIRFLOW_LOCAL_LAB_USER / _PASSWORD, read from the generated simple-auth password file in WSL, never
printed). Jira bot credentials are removed, so no real ticket is ever raised from the lab.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATABASE = os.environ.get("LAB_DATABASE", "DEV_AI_PLATFORM")
ROLE = os.environ.get("LAB_ROLE", "SYSADMIN")


def main() -> None:
    what = (sys.argv[1:] or ["api"])[0]
    assert what in ("api", "worker"), "api or worker"
    out = subprocess.run(["wsl", "-e", "bash", "-lc", "cat ~/airflow/simple_auth_manager_passwords.json.generated"],
                         capture_output=True, text=True, check=True).stdout
    env = {**os.environ, "AIP_AUTH": "dev", "AIP_DATABASE": DATABASE, "AIP_ROLE": ROLE, "AIP_WAREHOUSE": "DBT_WH",
           "AIP_AIRFLOW_LOCAL_LAB_USER": "admin", "AIP_AIRFLOW_LOCAL_LAB_PASSWORD": json.loads(out)["admin"],
           "PYTHONUNBUFFERED": "1"}
    for key in ("JIRA_BOT_EMAIL", "JIRA_BOT_TOKEN"):
        env.pop(key, None)
    api = ROOT / "apps" / "api"
    cmd = ([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8021"] if what == "api"
           else [sys.executable, "-m", "app.worker"])
    sys.exit(subprocess.call(cmd, cwd=str(api), env=env))


if __name__ == "__main__":
    main()
