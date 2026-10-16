"""A stand-in for the client's Airflow service account, for a local lab without a Snowflake credential in Airflow.

    python airflow_lab/sql_gateway.py        # one Snowflake sign-in; leave it running while the lab runs

The lab DAGs in WSL write each SQL statement as a request file into a shared folder; this process (on Windows, signed in
with the developer's own Snowflake session) runs it and writes back Snowflake's rows or its exact error (class, code,
SQL state and message), so a DAG fails exactly as it would with its own connection. No port is opened.

Guarded: only SELECT, INSERT and LIST, and every CORE object a statement names must be a lab object (CORE.LAB_*);
anything else is refused. The folder is %LOCALAPPDATA%\\gdp_lab_gateway; its WSL path is written to
~/airflow/lab_gateway.json, which the DAGs read (airflow_lab/dags/lab_dags.py).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DATABASE = os.environ.get("LAB_DATABASE", "DEV_AI_PLATFORM")
FOLDER = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "gdp_lab_gateway"
VERBS = ("SELECT", "INSERT", "LIST")
_CORE_REF = re.compile(r"@?\bCORE\.([A-Za-z_][A-Za-z0-9_$]*)", re.I)
_BARE = re.compile(r"\b(FROM|JOIN|INTO|TABLE)\s+(?![@(])(?![A-Za-z_][A-Za-z0-9_$]*\s*\.)[A-Za-z_]", re.I)
_QUALIFIED = re.compile(r"\b[A-Za-z_][A-Za-z0-9_$]*\.[A-Za-z_][A-Za-z0-9_$]*\.[A-Za-z_][A-Za-z0-9_$]*\b")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def check(sql: str) -> str:
    """The statement, or ValueError when it is not a lab statement."""
    text = sql.strip().rstrip(";").strip()
    if ";" in text:
        raise ValueError("one statement per request")
    verb = text.split(None, 1)[0].upper() if text else ""
    if verb not in VERBS:
        raise ValueError(f"only {', '.join(VERBS)} are allowed")
    refs = _CORE_REF.findall(text)
    if not refs or any(not r.upper().startswith("LAB_") for r in refs):
        raise ValueError("a lab statement names CORE.LAB_* objects only")
    if _BARE.search(text):
        raise ValueError("write every table as CORE.LAB_<name>")
    if _QUALIFIED.search(text):
        raise ValueError("no database-qualified names: the lab runs in the platform database")
    return text


def wsl_path(path: Path) -> str:
    p = str(path.resolve()).replace("\\", "/")
    return "/mnt/" + p[0].lower() + p[2:]


def publish_config() -> None:
    config = json.dumps({"dir": wsl_path(FOLDER), "database": DATABASE})
    subprocess.run(["wsl", "-e", "bash", "-c", "mkdir -p ~/airflow && cat > ~/airflow/lab_gateway.json"],
                   input=config, text=True, check=True)


def answer(cur, request: dict) -> dict:
    try:
        sql = check(str(request.get("sql") or ""))
    except ValueError as exc:
        return {"refused": str(exc)}
    try:
        cur.execute(sql)
        rows = [list(r) for r in cur.fetchall()] if cur.description else []
        return {"rows": json.loads(json.dumps(rows, default=str))}
    except Exception as exc:   # Snowflake's own error, passed through unchanged
        return {"error": {"type": type(exc).__name__, "module": type(exc).__module__, "text": str(exc)}}


def main() -> None:
    import snowflake.connector

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
    try:
        import app.db  # noqa: F401  (token cache shared with the API)
    except Exception:
        pass
    FOLDER.mkdir(parents=True, exist_ok=True)
    for old in FOLDER.glob("*.json"):
        old.unlink()
    log("Connecting to Snowflake: complete the sign-in in your browser...")
    kwargs = dict(connection_name="anblicksorg-anblicksorg_aws", database=DATABASE, schema="CORE", warehouse="DBT_WH",
                  login_timeout=600, client_session_keep_alive=True, client_store_temporary_credential=True)
    if os.environ.get("LAB_ROLE"):
        kwargs["role"] = os.environ["LAB_ROLE"]
    con = snowflake.connector.connect(**kwargs)
    cur = con.cursor()
    who = cur.execute("SELECT CURRENT_USER(), CURRENT_ROLE()").fetchone()
    publish_config()
    log(f"gateway ready as {who[0]} / {who[1]} on {DATABASE}.CORE; folder {FOLDER}")
    try:
        while True:
            for req in sorted(FOLDER.glob("req-*.json")):
                try:
                    request = json.loads(req.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    continue   # still being written
                result = answer(cur, request)
                out = FOLDER / req.name.replace("req-", "res-", 1)
                tmp = out.with_suffix(".tmp")
                tmp.write_text(json.dumps(result), encoding="utf-8")
                tmp.replace(out)
                req.unlink(missing_ok=True)
                status = "refused" if "refused" in result else ("error" if "error" in result else "ok")
                log(f"{request.get('dag', '?')}: {status}: {str(request.get('sql'))[:90]}")
            time.sleep(0.3)
    except KeyboardInterrupt:
        log("stopped")
    finally:
        con.close()


if __name__ == "__main__":
    main()
