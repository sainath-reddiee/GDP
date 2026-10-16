"""P9 cleanup: leave nothing in the old schemas once the consolidation is verified.

    python -m infrastructure.consolidation.cleanup --connection <c> --database DEV_AI_PLATFORM            # dry run
    python -m infrastructure.consolidation.cleanup --connection <c> --database DEV_AI_PLATFORM --execute
    python -m infrastructure.consolidation.cleanup ... --execute --drop-schemas                             # last step

For every object still in an old schema:
- procedures, Cortex Search services, tasks and views that the deploy or the runtime step re-created in CORE: dropped
  (only when the CORE copy exists);
- dbt projects: renamed into CORE (supported by Snowflake), versions compared before and after, and the stored names in
  CORE.GIT_PUBLICATION re-pointed;
- git repository clones: created in CORE from the same origin and integration (one clone per origin when two old
  clones share a name and origin), fetched, the stored names in CORE.REPO and CORE.PLATFORM_CONFIG re-pointed, then the
  old clone dropped;
- network rules: the CORE copy goes into the GitHub external access integration (when this role may alter it), then the
  old rule is dropped; otherwise the statement is printed for the integration's owner;
- secrets are never copied (their value cannot be read). The script prints the steps that move the GitHub token into
  a CORE secret with the value re-entered by its owner, and leaves the old secret until the new one works.
--drop-schemas drops an old schema only when SHOW OBJECTS and every SHOW <kind> list nothing in it.
Stored names are rewritten with REPLACE of the exact old full name; the zero-copy backup taken before V061 keeps the
previous rows. Prints every statement; runs only with --execute.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List

from infrastructure.consolidation.common import (LEGACY_SCHEMAS, PLATFORM_SCHEMA, connect, database_name,
                                                 finish_run, parser, show, start_run, try_q, write_json)
from infrastructure.consolidation.runtime import Plan

KINDS = ("PROCEDURES", "USER FUNCTIONS", "CORTEX SEARCH SERVICES", "TASKS", "VIEWS", "TABLES", "STAGES",
         "FILE FORMATS", "SEQUENCES", "DBT PROJECTS", "GIT REPOSITORIES", "NETWORK RULES", "SECRETS",
         "ROW ACCESS POLICIES", "MASKING POLICIES", "STREAMS", "PIPES", "ALERTS", "STREAMLITS", "NOTEBOOKS", "AGENTS")


def legacy(cur, db: str) -> Dict[str, List[Dict[str, Any]]]:
    return {k: [r for r in show(cur, k, db) if r.get("schema_name") in LEGACY_SCHEMAS] for k in KINDS}


def in_core(cur, db: str, kind: str) -> set:
    return {r["name"] for r in show(cur, kind, db) if r.get("schema_name") == PLATFORM_SCHEMA}


def rewrite_rows(plan: Plan, db: str, old: str, new: str) -> None:
    """Re-point stored names: the exact old full name, in text and in JSON columns."""
    o, n = old.replace("'", "''"), new.replace("'", "''")
    plan.run(f"UPDATE {db}.{PLATFORM_SCHEMA}.REPO SET GIT_REPOSITORY = '{n}' WHERE UPPER(GIT_REPOSITORY) = UPPER('{o}')",
             "code repositories that use this clone")
    plan.run(f"UPDATE {db}.{PLATFORM_SCHEMA}.REPO SET CREATED_OBJECTS = PARSE_JSON(REPLACE(TO_JSON(CREATED_OBJECTS), "
             f"'{o}', '{n}')) WHERE CONTAINS(TO_JSON(CREATED_OBJECTS), '{o}')", "objects the platform created for them")
    plan.run(f"UPDATE {db}.{PLATFORM_SCHEMA}.PLATFORM_CONFIG SET CONFIG_VALUE = PARSE_JSON(REPLACE(TO_JSON(CONFIG_VALUE), "
             f"'{o}', '{n}')) WHERE CONTAINS(TO_JSON(CONFIG_VALUE), '{o}')", "platform settings that name it")


def main() -> None:
    p = parser(__doc__)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--drop-schemas", action="store_true")
    args = p.parse_args()
    db = database_name(args)
    con = connect(args)
    cur = con.cursor()
    plan = Plan(cur, args.execute)
    notes: List[str] = []
    left = legacy(cur, db)

    for kind, core_kind, word in (("PROCEDURES", "PROCEDURES", "PROCEDURE"), ("USER FUNCTIONS", "USER FUNCTIONS", "FUNCTION")):
        core = in_core(cur, db, core_kind)
        for r in left[kind]:
            sig = str(r.get("arguments") or "").split(" RETURN ")[0]
            if r["name"] in core:
                plan.run(f"DROP {word} IF EXISTS {db}.{r['schema_name']}.{sig}", "old copy; CORE has it")
            else:
                notes.append(f"{word} {r['schema_name']}.{sig}: no CORE copy, kept")
    for kind, word in (("CORTEX SEARCH SERVICES", "CORTEX SEARCH SERVICE"), ("TASKS", "TASK"), ("VIEWS", "VIEW")):
        core = in_core(cur, db, kind)
        for r in left[kind]:
            if r["name"] in core:
                plan.run(f"DROP {word} IF EXISTS {db}.{r['schema_name']}.{r['name']}", "old copy; CORE has it")
            else:
                notes.append(f"{word} {r['schema_name']}.{r['name']}: no CORE copy, kept")

    core_projects = in_core(cur, db, "DBT PROJECTS")
    for r in left["DBT PROJECTS"]:
        old, new = f"{db}.{r['schema_name']}.{r['name']}", f"{db}.{PLATFORM_SCHEMA}.{r['name']}"
        if r["name"] in core_projects:
            notes.append(f"DBT PROJECT {old}: {new} already exists, kept")
            continue
        before = sorted(str(v.get("name")) for v in try_q(cur, f"SHOW VERSIONS IN DBT PROJECT {old}"))
        if plan.run(f"ALTER DBT PROJECT {old} RENAME TO {new}", "move the project with its versions") and args.execute:
            after = sorted(str(v.get("name")) for v in try_q(cur, f"SHOW VERSIONS IN DBT PROJECT {new}"))
            notes.append(f"DBT PROJECT {r['name']}: versions before {before}, after {after}"
                         f"{'' if before == after else ' (CHANGED)'}")
        o = old.replace("'", "''")
        plan.run(f"UPDATE {db}.{PLATFORM_SCHEMA}.GIT_PUBLICATION SET DBT_PROJECT = '{new}' "
                 f"WHERE UPPER(DBT_PROJECT) = UPPER('{o}')", "publications that name it")

    core_repos = {r["name"]: r for r in show(cur, "GIT REPOSITORIES", db) if r.get("schema_name") == PLATFORM_SCHEMA}
    for r in left["GIT REPOSITORIES"]:
        old = f"{db}.{r['schema_name']}.{r['name']}"
        twin = core_repos.get(r["name"])
        if twin and twin.get("origin") != r.get("origin"):
            name = f"{r['schema_name']}_{r['name']}"   # same name, different origin: keep both, prefixed
            twin = core_repos.get(name)
        else:
            name = r["name"]
        new = f"{db}.{PLATFORM_SCHEMA}.{name}"
        if not twin:
            creds = f" GIT_CREDENTIALS = {r['git_credentials']}" if r.get("git_credentials") else ""
            comment = str(r.get("comment") or "").replace("'", "")
            if plan.run(f"CREATE GIT REPOSITORY IF NOT EXISTS {new} ORIGIN = '{r['origin']}' "
                        f"API_INTEGRATION = {r['api_integration']}{creds} COMMENT = '{comment}'", "clone in CORE"):
                core_repos[name] = {"origin": r.get("origin")}
        plan.run(f"ALTER GIT REPOSITORY {new} FETCH", "fetch it")
        rewrite_rows(plan, db, old, new)
        plan.run(f"DROP GIT REPOSITORY IF EXISTS {old}", "old clone (the remote is the source of truth)")

    config = try_q(cur, f"SELECT CONFIG_VALUE FROM {db}.{PLATFORM_SCHEMA}.PLATFORM_CONFIG "
                        "WHERE CONFIG_KEY = 'GITHUB_PUBLISH' AND IS_CURRENT")
    publish = json.loads(config[0]["config_value"]) if config and isinstance(config[0]["config_value"], str) else \
        (config[0]["config_value"] if config else {})
    eai = (publish or {}).get("external_access_integration")
    for r in left["NETWORK RULES"]:
        old, new = f"{db}.{r['schema_name']}.{r['name']}", f"{db}.{PLATFORM_SCHEMA}.{r['name']}"
        if r["name"] not in in_core(cur, db, "NETWORK RULES"):
            notes.append(f"NETWORK RULE {old}: no CORE copy yet (run runtime apply), kept")
            continue
        current = [str(d.get("property_value") or "") for d in try_q(cur, f"DESCRIBE INTEGRATION {eai}")
                   if str(d.get("property")).upper() == "ALLOWED_NETWORK_RULES"] if eai else []
        rules = [x.strip() for x in (current[0] if current else "").strip("[]").split(",") if x.strip()]
        swapped = [new if x.replace('"', "").upper() == old.upper() else x for x in rules] or [new]
        if new not in swapped:
            swapped.append(new)
        repointed = bool(eai) and plan.run(
            f"ALTER EXTERNAL ACCESS INTEGRATION {eai} SET ALLOWED_NETWORK_RULES = ({', '.join(swapped)})",
            f"GitHub publishing uses the CORE rule (was: {', '.join(rules) or 'unknown'})")
        if repointed:   # Plan.run returns False when the statement failed (for example: not this role's integration)
            plan.run(f"DROP NETWORK RULE IF EXISTS {old}", "old rule, no longer used")
        else:
            notes.append(f"NETWORK RULE {old}: kept; the integration's owner runs ALTER EXTERNAL ACCESS INTEGRATION "
                         f"{eai or '<name>'} SET ALLOWED_NETWORK_RULES = ({new}); then drop the old rule")

    for r in left["SECRETS"]:
        old, new = f"{db}.{r['schema_name']}.{r['name']}", f"{db}.{PLATFORM_SCHEMA}.{r['name']}"
        notes.append(
            f"SECRET {old}: its value cannot be read, so it is not copied. To move it: (1) CREATE SECRET {new} TYPE = "
            f"{r.get('secret_type') or 'GENERIC_STRING'} SECRET_STRING = 'x'; (2) the owner sets the real value "
            f"(Admin, GitHub publishing: rotate token, after GITHUB_PUBLISH.secret points at {new}); (3) add {new} to "
            f"{eai or 'the integration'} ALLOWED_AUTHENTICATION_SECRETS and redeploy (the publisher procedure binds it); "
            f"(4) test publishing; (5) DROP SECRET {old}.")

    for kind in ("TABLES", "STAGES", "FILE FORMATS", "SEQUENCES", "ROW ACCESS POLICIES", "MASKING POLICIES", "STREAMS",
                 "PIPES", "ALERTS", "STREAMLITS", "NOTEBOOKS", "AGENTS"):
        for r in left[kind]:
            notes.append(f"{kind[:-1]} {r['schema_name']}.{r['name']}: unexpected in an old schema, kept for review")

    if args.drop_schemas:
        after = legacy(cur, db) if args.execute else left
        for schema in LEGACY_SCHEMAS:
            held = [f"{k[:-1]} {r['name']}" for k, rows_ in after.items() for r in rows_ if r["schema_name"] == schema]
            held += [f"{o.get('kind')} {o.get('name')}" for o in try_q(cur, f"SHOW OBJECTS IN SCHEMA {db}.{schema}")]
            if not try_q(cur, f"SHOW SCHEMAS LIKE '{schema}' IN DATABASE {db}"):
                continue
            if held:
                notes.append(f"SCHEMA {schema}: not empty, kept ({', '.join(sorted(set(held))[:8])})")
            else:
                plan.run(f"DROP SCHEMA IF EXISTS {db}.{schema}", "empty: every object lives in CORE")

    for n in notes:
        print(f"note: {n}")
    if not args.execute:
        print("dry run: nothing executed (pass --execute)")
        con.close()
        return
    run_id = start_run(cur, db, "CLEANUP", False, {})
    finish_run(cur, db, run_id, not plan.failed, {"executed": plan.done, "failed": plan.failed, "notes": notes})
    path = write_json(args, "cleanup", {"run_id": run_id, "executed": plan.done, "failed": plan.failed, "notes": notes})
    print(f"cleanup {run_id}: {len(plan.done)} statement(s), {len(plan.failed)} failed; written {path}")
    con.close()
    sys.exit(1 if plan.failed else 0)


if __name__ == "__main__":
    main()
