"""Check, from the API host, that Operations can reach the client's Airflow (Amazon MWAA) and Datadog, before adding
them in the app. Read only: nothing is written to Snowflake, Airflow or Datadog.

    python infrastructure/ops_check.py --mwaa-env <MWAA environment name> --region <aws region>
    python infrastructure/ops_check.py --datadog
    python infrastructure/ops_check.py --mwaa-env prod-mwaa --region us-east-1 --datadog --dag <dag id>

MWAA uses the host's default AWS credential chain, exactly as the API does (the instance or task role, AWS_PROFILE,
or AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in the environment). It prints who the host is (STS), the environment
(GetEnvironment), the Airflow version and DAGs (InvokeRestApi), and the latest runs of one DAG; each failure says
which IAM action is missing.

Datadog reads DATADOG_SITE, DATADOG_API_KEY, DATADOG_APP_KEY and DATADOG_APP_URL from the environment: this shell
first, then docker/api.env (the containers) and apps/api/.env (an API run locally), like the API itself. It checks the API key, a log search and the monitor list. Keys are
never printed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.ops import datadog as dd  # noqa: E402
from services.ops.mwaa import REQUIRED_ACTIONS, Mwaa, MwaaError  # noqa: E402

OK, FAIL, NOTE = "  ok    ", "  FAIL  ", "  note  "
HINTS = {
    "credentials": "No AWS credentials on this host. Attach an instance or task role, or set AWS_PROFILE "
                   "(aws sso login first), or AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY.",
    "access_denied": "The AWS principal lacks a permission. It needs " + ", ".join(REQUIRED_ACTIONS)
                     + "; airflow:InvokeRestApi on arn:aws:airflow:<region>:<account>:role/<environment>/<Airflow role>"
                       " (User or Op is enough to read; Op to clear tasks).",
    "not_found": "No MWAA environment by that name in that region (check --mwaa-env and --region).",
}


def load_env_file(path: Path) -> int:
    """KEY=value lines of docker/api.env into os.environ, without overriding what the shell already set."""
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
            count += 1
    return count


def check_mwaa(env_name: str, region: str, dag: str = "") -> bool:
    print(f"\nAmazon MWAA: {env_name} in {region}")
    good = True
    try:
        import boto3

        who = boto3.session.Session().client("sts", region_name=region).get_caller_identity()
        print(f"{OK}AWS identity {who.get('Arn')}")
    except ImportError:
        print(f"{FAIL}boto3 is not installed (pip install -r apps/api/requirements.txt)")
        return False
    except Exception as exc:
        print(f"{FAIL}AWS identity: {type(exc).__name__}. {HINTS['credentials']}")
        return False
    try:
        info = boto3.session.Session().client("mwaa", region_name=region).get_environment(Name=env_name)["Environment"]
        print(f"{OK}environment {info.get('Status')}, Airflow {info.get('AirflowVersion')}, "
              f"web server {info.get('WebserverUrl')}")
        logging_conf = (info.get("LoggingConfiguration") or {}).get("TaskLogs") or {}
        if not logging_conf.get("Enabled"):
            print(f"{NOTE}task logs are not sent to CloudWatch; the platform reads task logs through Airflow, and "
                  "Datadog can stand in")
    except Exception as exc:
        code = str(((getattr(exc, "response", None) or {}).get("Error") or {}).get("Code") or type(exc).__name__)
        kind = "not_found" if code == "ResourceNotFoundException" else "access_denied" if "AccessDenied" in code else ""
        print(f"{FAIL}GetEnvironment: {code}. {HINTS.get(kind, '')}")
        good = False
    mw = Mwaa(env_name, region)
    try:
        version = mw.version()
        print(f"{OK}Airflow REST API {version['api_version']}, Airflow {version['version']}")
        dags, total = mw.dags(limit=10)
        names = [str(d.get("dag_id")) for d in dags if d.get("dag_id")]
        paused = sum(1 for d in dags if d.get("is_paused"))
        print(f"{OK}{total} DAGs visible; first {len(names)}: {', '.join(names) or 'none'}"
              + (f" ({paused} of them paused)" if paused else ""))
        target = dag or (names[0] if names else "")
        if target:
            from urllib.parse import quote

            found = mw.invoke("GET", f"/dags/{quote(target, safe='')}/dagRuns", {"limit": 5, "order_by": "-start_date"}) or {}
            runs, count = list(found.get("dag_runs") or []), found.get("total_entries", "?")
            states = ", ".join(f"{r.get('state')} {str(r.get('start_date') or r.get('run_after') or r.get('logical_date') or '')[:19]}"
                               for r in runs) or "no runs"
            print(f"{OK}{target}: {count} runs; latest {states}")
    except MwaaError as exc:
        print(f"{FAIL}InvokeRestApi: {exc}. {HINTS.get(exc.kind, '')}")
        good = False
    return good


def check_datadog() -> bool:
    cfg = dd.config_from_env()
    shown = dd.public(cfg)
    print(f"\nDatadog: site {shown['site']}, web {shown['app_url']}, API key {'set' if shown['api_key_set'] else 'missing'}, "
          f"application key {'set' if shown['app_key_set'] else 'missing'}")
    if cfg["problems"]:
        for p in cfg["problems"]:
            print(f"{FAIL}{p}")
        return False
    result = dd.Client(cfg).check()
    print(f"{OK if result['api_key'] else FAIL}API key " + ("valid" if result["api_key"] else result["errors"].get("api_key", "invalid")))
    if result["api_key"]:
        logs_ok, mons_ok = "logs" not in result["errors"], "monitors" not in result["errors"]
        print(f"{OK if logs_ok else FAIL}log search: " + (f"{result['logs']} entries in the last 15 minutes" if logs_ok
              else f"{result['errors']['logs']} (the application key needs logs_read_data)"))
        print(f"{OK if mons_ok else FAIL}monitors: " + (f"{result['monitors']} visible" if mons_ok
              else f"{result['errors']['monitors']} (the application key needs monitors_read)"))
    return bool(result["api_key"]) and not result["errors"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mwaa-env", help="the MWAA environment name")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
                    help="the AWS region (default AWS_REGION)")
    ap.add_argument("--dag", default="", help="a DAG whose latest runs to read (default the first one)")
    ap.add_argument("--datadog", action="store_true", help="check the Datadog keys in the environment")
    ap.add_argument("--env-file", action="append",
                    help="read missing variables from this file (default docker/api.env and apps/api/.env)")
    args = ap.parse_args(argv)
    if not args.mwaa_env and not args.datadog:
        ap.error("give --mwaa-env and --region, or --datadog, or both")
    for name in args.env_file or [str(ROOT / "docker" / "api.env"), str(ROOT / "apps" / "api" / ".env")]:
        loaded = load_env_file(Path(name))
        if loaded:
            print(f"read {loaded} variable(s) from {name}")
    results = []
    if args.mwaa_env:
        if not args.region:
            ap.error("--region is required with --mwaa-env (or set AWS_REGION)")
        results.append(check_mwaa(args.mwaa_env, args.region, args.dag))
    if args.datadog:
        results.append(check_datadog())
    print("\nAll checks passed." if all(results) else "\nSome checks failed; see FAIL above.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
