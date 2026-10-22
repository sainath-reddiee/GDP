"""Source control round trip (V043, CORE.GIT_PR): every pull request the app opens for a governed asset, in one
table, and one sync that reads their state back from GitHub.

An asset version is approved in the app, then published (services/dbt/publish.py renders its files from the approved
version and opens a pull request). `record` keeps that pull request here; `sync` reads every open one from GitHub
and applies the outcome to this table and to the asset's own version table:

  MODEL     CORE.MODEL_DESIGNS     merged -> the version is PUBLISHED
  DQ_SUITE  CORE.DQ_SUITE_VERSION  merged -> the version is PUBLISHED
  QA_PACK   CORE.DOMAIN_QA_PACKS   merged or closed is recorded (the deploy then sees the pack in step)
  STTM      (this table only)            the approved mapping version as sttm/<domain>/<target>.csv
  PERMANENT_FIX  CORE.PERMANENT_FIX (through its hook)  merged or closed ends the proposal; never retires the fix

Closed without merging leaves the version APPROVED with its state CLOSED, so it can be published again. Before V043
recording is skipped and the sync falls back to each asset's own table (services.dbt.publish.sync_*_prs).
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from services.common.sql import clip, rows

TABLE = "CORE.GIT_PR"
OPEN_STATES = ("OPEN", "DRAFT")
STATES = ("OPEN", "DRAFT", "MERGED", "CLOSED", "FAILED", "NO_CHANGES")

# kind: (label, the asset's version table, its id column, merged marks the version PUBLISHED, Store page)
KINDS: Dict[str, Dict[str, Any]] = {
    "MODEL": {"label": "Domain model", "table": "CORE.MODEL_DESIGNS", "key": "MODEL_ID", "publishes": True},
    "DQ_SUITE": {"label": "Data quality suite", "table": "CORE.DQ_SUITE_VERSION", "key": "SUITE_ID", "publishes": True},
    "QA_PACK": {"label": "QA pack", "table": "CORE.DOMAIN_QA_PACKS", "key": "PACK_ID", "publishes": False},
    "STTM": {"label": "Data Mapping", "table": None, "key": None, "publishes": False},
    # table model versions are published without an approval of their own (the domain model is the governed asset)
    "TABLE_MODEL": {"label": "Table model", "table": "CORE.TABLE_MODEL_VERSION", "key": "TABLE_MODEL_ID",
                    "publishes": True, "from": ("DRAFT", "PUBLISHED")},
    # the code change that removes the cause of a recurring known fix (V066); its own table is kept by the hook
    "PERMANENT_FIX": {"label": "Permanent fix", "table": None, "key": None, "publishes": False,
                      "hook": "services.resolve.permanent:on_pr_state"},
}


def outcome(result: Dict[str, Any]) -> tuple:
    """(state, pull request) of a services.dbt.github.publish result: OPEN with a pull request, NO_CHANGES when the
    repository already holds the files, FAILED otherwise."""
    pr = result.get("pull_request") or {}
    if result.get("status") == "PUBLISHED":
        return ("OPEN" if pr.get("url") else None), pr
    return ("NO_CHANGES" if result.get("status") == "NO_CHANGES" else "FAILED"), pr


MERGE_SQL = f"""MERGE INTO {TABLE} T
USING (SELECT ? AS KIND, ? AS ASSET_ID, ? AS VERSION, ? AS ASSET_NAME, NULLIF(?, '') AS DOMAIN_ID, ? AS REPO_ID,
              ? AS BRANCH, ? AS BASE_BRANCH, NULLIF(?, '')::NUMBER AS PR_NUMBER, NULLIF(?, '') AS PR_URL, ? AS STATE,
              ? AS TITLE, PARSE_JSON(?)::ARRAY AS FILES, NULLIF(?, '') AS DETAIL) S
   ON T.KIND = S.KIND AND T.ASSET_ID = S.ASSET_ID AND T.VERSION = S.VERSION
 WHEN MATCHED THEN UPDATE SET ASSET_NAME = S.ASSET_NAME, DOMAIN_ID = COALESCE(S.DOMAIN_ID, T.DOMAIN_ID), REPO_ID = S.REPO_ID,
      BRANCH = S.BRANCH, BASE_BRANCH = S.BASE_BRANCH, PR_NUMBER = S.PR_NUMBER, PR_URL = S.PR_URL, STATE = S.STATE,
      MERGED_SHA = NULL, TITLE = S.TITLE, FILES = S.FILES, DETAIL = S.DETAIL, OPENED_BY = CURRENT_USER(),
      OPENED_AT = CURRENT_TIMESTAMP(), SYNCED_AT = CURRENT_TIMESTAMP(), UPDATED_AT = CURRENT_TIMESTAMP()
 WHEN NOT MATCHED THEN INSERT (KIND, ASSET_ID, VERSION, ASSET_NAME, DOMAIN_ID, REPO_ID, BRANCH, BASE_BRANCH, PR_NUMBER, PR_URL,
      STATE, TITLE, FILES, DETAIL, SYNCED_AT)
      VALUES (S.KIND, S.ASSET_ID, S.VERSION, S.ASSET_NAME, S.DOMAIN_ID, S.REPO_ID, S.BRANCH, S.BASE_BRANCH, S.PR_NUMBER,
              S.PR_URL, S.STATE, S.TITLE, S.FILES, S.DETAIL, CURRENT_TIMESTAMP())"""


def record(session, kind: str, asset_id: str, version: int, result: Dict[str, Any], *, asset_name: str = "",
           domain_id: Optional[str] = None, repo_id: str = "", branch: str = "", title: str = "",
           files: Optional[List[str]] = None) -> bool:
    """Keep the pull request a publish opened (or why it failed). A version published again replaces its row.
    Never raises: before V043 nothing is recorded and the publish already succeeded or failed on its own."""
    assert kind in KINDS, f"unknown kind {kind}"
    state, pr = outcome(result)
    detail = "" if state == "OPEN" else clip(result.get("detail") or result.get("status") or "", 2000)
    try:
        session.sql(MERGE_SQL, params=[kind, asset_id, int(version), clip(asset_name, 800), domain_id or "", repo_id or None,
                                       clip(branch, 256), clip(result.get("base_branch") or "", 256) or None,
                                       str(pr.get("number") or ""), pr.get("url") or "", state, clip(title, 400),
                                       json.dumps(sorted(files or [])[:200]), detail]).collect()
        return True
    except Exception:
        return False


def apply_state(session, kind: str, asset_id: str, version: int, state: str, merged_sha: Optional[str]) -> None:
    """A pull request's state on the asset's own version table (nothing for STTM, which lives here only)."""
    k = KINDS[kind]
    if k.get("hook"):
        import importlib

        module, _, name = k["hook"].partition(":")
        getattr(importlib.import_module(module), name)(session, asset_id, state, merged_sha)
    if not k["table"]:
        return
    publish = k["publishes"] and state == "MERGED"
    session.sql(f"""UPDATE {k['table']} SET PR_STATE = ?, MERGED_SHA = NULLIF(?, ''), PR_SYNCED_AT = CURRENT_TIMESTAMP()
                           {", STATUS = 'PUBLISHED'" if publish else ""}
                     WHERE {k['key']} = ? AND VERSION = ?{f" AND STATUS IN {k.get('from', ('APPROVED', 'PUBLISHED'))}" if k['publishes'] else ""}""",
                params=[state, merged_sha or "", asset_id, int(version)]).collect()


def mirror(session, kind: str, asset_id: str, version: int, state: str, merged_sha: Optional[str]) -> None:
    """A state read by an asset's own sync (services.dbt.publish.sync_*_prs) copied here. Never raises."""
    try:
        session.sql(f"""UPDATE {TABLE} SET STATE = ?, MERGED_SHA = NULLIF(?, ''), SYNCED_AT = CURRENT_TIMESTAMP(),
                               UPDATED_AT = CURRENT_TIMESTAMP() WHERE KIND = ? AND ASSET_ID = ? AND VERSION = ?""",
                    params=[state, merged_sha or "", kind, asset_id, int(version)]).collect()
    except Exception:
        pass


def deployed(session) -> bool:
    try:
        rows(session, f"SELECT 1 FROM {TABLE} LIMIT 1")
        return True
    except Exception:
        return False


def sync(session, request: Dict[str, Any], status_fn: Callable[[str, int], Dict[str, Any]]) -> Dict[str, Any]:
    """Read every open pull request (optionally of one kind or asset) from GitHub through `status_fn(git_url, number)`
    and apply its state. Pull requests synced in the last minute are skipped unless `force`."""
    where, params = ["G.PR_NUMBER IS NOT NULL AND G.STATE IN ('OPEN', 'DRAFT') AND G.REPO_ID IS NOT NULL"], []
    if request.get("kind"):
        assert request["kind"] in KINDS, f"unknown kind {request['kind']}"
        where.append("G.KIND = ?")
        params.append(request["kind"])
    if request.get("asset_id"):
        where.append("G.ASSET_ID = ?")
        params.append(str(request["asset_id"]))
    if not request.get("force"):
        where.append("(G.SYNCED_AT IS NULL OR G.SYNCED_AT < DATEADD('second', -60, CURRENT_TIMESTAMP()))")
    found = rows(session, f"""SELECT G.KIND, G.ASSET_ID, G.VERSION, G.ASSET_NAME, G.PR_NUMBER, R.GIT_URL, G.DOMAIN_ID,
                                     G.BRANCH, G.PR_URL FROM {TABLE} G
                               JOIN CORE.REPO R ON R.REPO_ID = G.REPO_ID WHERE {' AND '.join(where)}
                              ORDER BY G.OPENED_AT LIMIT 300""", params)
    out: Dict[str, Any] = {"checked": 0, "merged": [], "closed": [], "open": 0, "errors": []}
    for r in found:
        ref = {"kind": r["KIND"], "asset_id": r["ASSET_ID"], "version": int(r["VERSION"]), "name": r.get("ASSET_NAME")}
        try:
            pr = status_fn(r["GIT_URL"], int(r["PR_NUMBER"]))
        except Exception as exc:
            out["errors"].append({**ref, "error": clip(exc, 300)})
            continue
        out["checked"] += 1
        state = pr["state"] if pr.get("state") in STATES else "OPEN"
        session.sql(f"""UPDATE {TABLE} SET STATE = ?, MERGED_SHA = NULLIF(?, ''), SYNCED_AT = CURRENT_TIMESTAMP(),
                               UPDATED_AT = CURRENT_TIMESTAMP() WHERE KIND = ? AND ASSET_ID = ? AND VERSION = ?""",
                    params=[state, pr.get("merge_commit_sha") or "", r["KIND"], r["ASSET_ID"], int(r["VERSION"])]).collect()
        if r["KIND"] in KINDS:
            apply_state(session, r["KIND"], r["ASSET_ID"], int(r["VERSION"]), state, pr.get("merge_commit_sha"))
        if state == "CLOSED" and r.get("DOMAIN_ID"):  # the repository did not take the version: remember why to look
            from services.knowledge.signals import keep, pr_closed

            keep(session, r["DOMAIN_ID"], pr_closed(r["KIND"], r["ASSET_ID"], r.get("ASSET_NAME"), int(r["VERSION"]),
                                                    r.get("BRANCH"), r.get("PR_URL")), "GIT")
        if state in ("MERGED", "CLOSED"):
            out[state.lower()].append(ref)
        else:
            out["open"] += 1
    return out


# ---------------------------------------------------------------- listing (API connector, %s binds)


LIST_SQL = f"""SELECT G.KIND, G.ASSET_ID, G.VERSION, G.ASSET_NAME, G.DOMAIN_ID, D.DOMAIN_NAME, G.REPO_ID, R.NAME AS REPO_NAME,
                      R.GIT_URL, G.BRANCH, G.BASE_BRANCH, G.PR_NUMBER, G.PR_URL, G.STATE, G.MERGED_SHA, G.TITLE, G.FILES,
                      G.DETAIL, G.OPENED_BY, G.OPENED_AT::VARCHAR AS OPENED_AT, G.SYNCED_AT::VARCHAR AS SYNCED_AT
                 FROM {TABLE} G
                 LEFT JOIN CORE.REPO R ON R.REPO_ID = G.REPO_ID
                 LEFT JOIN CORE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = G.DOMAIN_ID"""


def link(kind: str, asset_id: str, version: int) -> str:
    """The Store page where the asset version is reviewed."""
    return {"MODEL": f"/dashboard/modeling/{asset_id}?tab=publish",
            "DQ_SUITE": f"/dashboard/dq-store/suite?suite={asset_id}",
            "QA_PACK": "/dashboard/qa-store",
            "STTM": f"/dashboard/sttm/{asset_id}?version={version}",
            "TABLE_MODEL": f"/dashboard/modeling/tables/{asset_id}?version={version}",
            "PERMANENT_FIX": f"/ops/permanent-fixes/{asset_id}"}.get(kind, "/dashboard")


def list_sql(kind: Optional[str] = None, state: Optional[str] = None, domain_id: Optional[str] = None,
             q: Optional[str] = None, asset_id: Optional[str] = None) -> tuple:
    """(sql, params) of the Source control list with its filters; `state` OPEN also matches DRAFT, ATTENTION is
    failed or closed without merging."""
    where, params = [], []
    if kind:
        assert kind in KINDS, f"unknown kind {kind}"
        where.append("G.KIND = %s")
        params.append(kind)
    if state:
        state = state.upper()
        if state == "OPEN":
            where.append("G.STATE IN ('OPEN', 'DRAFT')")
        elif state == "ATTENTION":
            where.append("G.STATE IN ('FAILED', 'CLOSED')")
        else:
            assert state in STATES, f"unknown state {state}"
            where.append("G.STATE = %s")
            params.append(state)
    if asset_id:
        where.append("G.ASSET_ID = %s")
        params.append(asset_id)
    if domain_id:
        where.append("G.DOMAIN_ID = %s")
        params.append(domain_id)
    if q and q.strip():
        like = f"%{q.strip()[:100]}%"
        where.append("(G.ASSET_NAME ILIKE %s OR G.TITLE ILIKE %s OR G.BRANCH ILIKE %s)")
        params += [like, like, like]
    sql = LIST_SQL + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY G.OPENED_AT DESC LIMIT 500"
    return sql, tuple(params)


def shape(r: Dict[str, Any]) -> Dict[str, Any]:
    """A listed row for the page (lower-case keys from the API connector)."""
    out = {str(k).lower(): v for k, v in r.items()}
    files = out.get("files")
    if isinstance(files, str):
        try:
            files = json.loads(files)
        except ValueError:
            files = []
    out["files"] = files or []
    out["version"] = int(out["version"])
    out["pr_number"] = int(out["pr_number"]) if out.get("pr_number") is not None else None
    out["label"] = KINDS.get(out["kind"], {}).get("label", out["kind"])
    out["href"] = link(out["kind"], out["asset_id"], out["version"])
    return out


def counts(found: List[Dict[str, Any]]) -> Dict[str, int]:
    out = {"total": len(found), "open": 0, "merged": 0, "attention": 0, "no_changes": 0}
    for r in found:
        s = r.get("state")
        if s in OPEN_STATES:
            out["open"] += 1
        elif s == "MERGED":
            out["merged"] += 1
        elif s in ("FAILED", "CLOSED"):
            out["attention"] += 1
        elif s == "NO_CHANGES":
            out["no_changes"] += 1
    return out
