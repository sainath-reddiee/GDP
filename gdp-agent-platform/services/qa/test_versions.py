"""History of saved QA tests (V042, CORE.QA_TEST_VERSION).

Every create, edit, delete and restore of a saved test (a run's test or a table test of the QA workspace) records
the test as it was after the change, so a tester can see who changed what, compare any two versions and restore an
older one. Recording never fails the write it follows: before V042 it is skipped.
"""

from __future__ import annotations

import difflib
import json
from typing import Any, Dict, List, Optional

from services.common.sql import rows

VERSIONS = "CORE.QA_TEST_VERSION"
CHANGES = ("CREATED", "EDITED", "DELETED", "RESTORED", "BASELINE")
FIELDS = ("title", "objective", "sql_text", "expected", "severity", "suite_id", "is_deleted")

# ? binds: (test id, change, test id); the version number is the next one for the test
INSERT_SQL = f"""INSERT INTO {VERSIONS} (TEST_ID, VERSION, CHANGE, TITLE, OBJECTIVE, SQL_TEXT, EXPECTED, SEVERITY, SUITE_ID,
                                         IS_DELETED, CHANGED_BY, CHANGED_AT)
                 SELECT T.TEST_ID, COALESCE((SELECT MAX(V.VERSION) FROM {VERSIONS} V WHERE V.TEST_ID = T.TEST_ID), 0) + 1, ?,
                        T.TITLE, T.OBJECTIVE, T.SQL_TEXT, T.EXPECTED, T.SEVERITY, T.SUITE_ID, COALESCE(T.IS_DELETED, FALSE),
                        CURRENT_USER(), CURRENT_TIMESTAMP()
                   FROM CORE.QA_TEST_CASE T WHERE T.TEST_ID = ?"""


def record(session, test_id: str, change: str) -> bool:
    """Keep the test's current state as its next version (never raises)."""
    try:
        session.sql(INSERT_SQL, params=[change if change in CHANGES else "EDITED", test_id]).collect()
        return True
    except Exception:
        return False


def record_db(db, test_id: str, change: str) -> bool:
    """The same through the API's connector (%s binds)."""
    try:
        db.execute(INSERT_SQL.replace("?", "%s"), (change if change in CHANGES else "EDITED", test_id))
        return True
    except Exception:
        return False


def _low(r: Dict[str, Any]) -> Dict[str, Any]:
    v = {str(k).lower(): val for k, val in r.items()}
    v["version"] = int(v["version"])
    v["changed_at"] = str(v["changed_at"])[:19] if v.get("changed_at") else None
    v["is_deleted"] = bool(v.get("is_deleted"))
    return v


def compare(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """What changed from version `a` to version `b`: the fields, and a line diff of the SQL."""
    fields = [f for f in FIELDS if (a.get(f) or None) != (b.get(f) or None)]
    sql_diff = list(difflib.unified_diff(str(a.get("sql_text") or "").splitlines(), str(b.get("sql_text") or "").splitlines(),
                                         f"v{a['version']}", f"v{b['version']}", lineterm="", n=2))
    return {"from": a["version"], "to": b["version"], "fields": fields,
            "changes": {f: {"from": a.get(f), "to": b.get(f)} for f in fields if f != "sql_text"}, "sql_diff": sql_diff}


def history(session, test_id: str) -> Dict[str, Any]:
    """Every version of a test, newest first, each with what changed from the version before."""
    found = [_low(r) for r in rows(session, f"""SELECT VERSION, CHANGE, TITLE, OBJECTIVE, SQL_TEXT, EXPECTED, SEVERITY, SUITE_ID,
                                                       IS_DELETED, CHANGED_BY, CHANGED_AT
                                                  FROM {VERSIONS} WHERE TEST_ID = ? ORDER BY VERSION""", [test_id])]
    out = []
    for i, v in enumerate(found):
        out.append({**v, "diff": compare(found[i - 1], v) if i else None})
    return {"test_id": test_id, "versions": list(reversed(out))}


def diff_versions(session, test_id: str, a: int, b: int) -> Dict[str, Any]:
    found = {v["version"]: v for v in history(session, test_id)["versions"]}
    assert int(a) in found and int(b) in found, "version not found"
    return compare(found[int(a)], found[int(b)])


def restore(session, test_id: str, version: int) -> Dict[str, Any]:
    """Put an older version back (a deleted test comes back too). The SQL is checked again by the QA guard and the
    compiler as if it were typed now, through the same update path as an edit."""
    from services.qa import procedures

    found = rows(session, f"""SELECT TITLE, OBJECTIVE, SQL_TEXT, EXPECTED, SEVERITY, SUITE_ID FROM {VERSIONS}
                               WHERE TEST_ID = ? AND VERSION = ?""", [test_id, int(version)])
    assert found, f"version {version} not found"
    old = {str(k).lower(): v for k, v in found[0].items()}
    test = rows(session, "SELECT RUN_ID, SCOPE, IS_DELETED FROM CORE.QA_TEST_CASE WHERE TEST_ID = ?", [test_id])
    assert test, f"QA test {test_id} does not exist"
    payload = {"sql": str(old.get("sql_text") or "").rstrip().rstrip(";"), "title": old.get("title"),
               "objective": old.get("objective"), "expected": old.get("expected"), "severity": old.get("severity")}
    if test[0].get("IS_DELETED"):
        session.sql("UPDATE CORE.QA_TEST_CASE SET IS_DELETED = FALSE WHERE TEST_ID = ?", params=[test_id]).collect()
    try:
        if (test[0].get("SCOPE") or "RUN") == "TABLE":
            if old.get("suite_id"):
                payload["suite_id"] = old["suite_id"]
            out = procedures.update_table_test(session, test_id, json.dumps(payload), change="RESTORED")
        else:
            out = procedures.qa_update(session, test[0]["RUN_ID"], test_id, json.dumps(payload), change="RESTORED")
    except Exception:
        if test[0].get("IS_DELETED"):  # the old SQL no longer passes: leave the test deleted as it was
            session.sql("UPDATE CORE.QA_TEST_CASE SET IS_DELETED = TRUE WHERE TEST_ID = ?", params=[test_id]).collect()
        raise
    return {"test_id": test_id, "restored": int(version), "note": out.get("note")}


def latest_versions(session, test_ids: List[str]) -> Dict[str, Optional[int]]:
    """{test id: newest version} for listing pages."""
    if not test_ids:
        return {}
    try:
        found = rows(session, f"SELECT TEST_ID, MAX(VERSION) AS V FROM {VERSIONS} WHERE TEST_ID IN "
                              f"({', '.join(['?'] * len(test_ids))}) GROUP BY TEST_ID", list(test_ids))
    except Exception:
        return {}
    return {r["TEST_ID"]: int(r["V"]) for r in found}
