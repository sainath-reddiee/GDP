"""Oracle, python-oracledb and Snowflake egress errors in plain words: what happened, how to fix it, and whether a
retry can help. Used by the connection diagnostics, the API (HTTP status) and the UI."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

# (code or text pattern, title, fix, http status, retryable)
CATALOG: Tuple[Tuple[str, str, str, int, bool], ...] = (
    ("ORA-01017", "Wrong user name or password",
     "Check the user and the password in the secret (passwords are case-sensitive).", 401, False),
    ("ORA-28000", "The Oracle account is locked",
     "Ask the Oracle DBA to unlock it: ALTER USER <user> ACCOUNT UNLOCK.", 401, False),
    ("ORA-28001", "The Oracle password has expired",
     "Set a new password in Oracle, then use Update password here so the secret matches.", 401, False),
    ("ORA-28002", "The Oracle password expires soon",
     "Change it before it expires and update the secret here.", 200, False),
    ("ORA-01045", "The user may not log in (missing CREATE SESSION)",
     "Ask the DBA: GRANT CREATE SESSION TO <user>.", 403, False),
    ("ORA-28040", "This Oracle release uses an authentication protocol the driver does not support",
     "Oracle 12.1 or later is required; ask the DBA to check SQLNET.ALLOWED_LOGON_VERSION_SERVER.", 400, False),
    ("DPY-3015", "The password uses an old verifier the driver cannot read",
     "Ask the DBA to reset the password so a 12c (or 11g) verifier is created.", 400, False),
    ("DPY-3010", "This Oracle release is too old for the thin driver",
     "Oracle Database 12.1 or later is required.", 400, False),
    ("ORA-12514", "The listener does not know that service name",
     "Check the service name (lsnrctl services on the server lists them); for a SID choose Connect by SID.", 400, False),
    ("ORA-12505", "The listener does not know that SID",
     "Check the SID, or connect by service name instead.", 400, False),
    ("ORA-12541", "Nothing is listening on that host and port",
     "Check the port (1521 by default, 1522 for Autonomous Database TLS) and that the listener is running.", 502, True),
    ("ORA-12545", "The host name could not be resolved",
     "Check the host name; from Snowflake it must resolve on the public internet (or over PrivateLink).", 502, False),
    ("ORA-12170", "The connection timed out",
     "A firewall is probably dropping the traffic. Allow the runtime's egress to host:port.", 504, True),
    ("ORA-12535", "The connection timed out",
     "A firewall is probably dropping the traffic. Allow the runtime's egress to host:port.", 504, True),
    ("ORA-03113", "The connection was dropped", "Retry; if it repeats, check network stability or the DB alert log.",
     502, True),
    ("ORA-03135", "The connection was lost", "Retry; long idle connections are cut by some firewalls.", 502, True),
    ("ORA-01555", "Snapshot too old during a long read",
     "Retry at a quieter time, or ask the DBA to raise UNDO_RETENTION; incremental loads read less.", 503, True),
    ("ORA-00942", "Table or view not found, or no SELECT privilege",
     "Ask the DBA: GRANT SELECT ON <owner>.<table> TO <user>.", 403, False),
    ("ORA-01031", "Insufficient privileges", "Ask the DBA for SELECT on the tables you want to read.", 403, False),
    ("ORA-00904", "A column no longer exists (the table changed)", "Refresh the table list and run the load again.",
     409, False),
    ("ORA-01882", "Unknown time zone region in the data",
     "The database time zone file is older than the data; ask the DBA to upgrade the DST file.", 400, False),
    ("ORA-24247", "The database blocked network access", "Not expected for reads; check the database ACLs.", 403, False),
    ("Connection refused", "Nothing is listening on that host and port",
     "Check the port and that the listener is running.", 502, True),
    ("10061", "Nothing is listening on that host and port",
     "Check the port and that the listener is running.", 502, True),
    ("getaddrinfo failed", "The host name could not be resolved", "Check the host name.", 502, False),
    ("Name or service not known", "The host name could not be resolved", "Check the host name.", 502, False),
    ("timed out", "The connection timed out",
     "A firewall is probably dropping the traffic. Allow the runtime's egress to host:port.", 504, True),
    ("certificate verify failed", "The TLS certificate was not trusted",
     "For Autonomous Database use TLS with the published host; for a private CA, run on this platform's server "
     "with the CA in the wallet.", 400, False),
    ("SSL", "The TLS handshake failed",
     "Check the port and protocol: TLS (TCPS) is usually 1522 or 2484, plain TCP 1521.", 400, False),
    ("DPY-6005", "Could not open a connection", "Check host, port, service and that the runtime can reach Oracle.",
     502, True),
    ("DPY-6000", "The listener refused the connection", "Check the service name or SID and the listener status.",
     502, False),
    ("DPY-4011", "The database closed the connection", "Retry; check the database alert log if it repeats.", 502, True),
    ("DPY-4027", "No connect string was given", "Fill in host, port and a service name or SID.", 400, False),
    ("not allowed by the network rule", "Snowflake blocked the outbound connection",
     "The network rule must allow exactly host:port; run Set up again after changing the connection.", 403, False),
    ("External access", "Snowflake blocked the outbound connection",
     "Check the external access integration is enabled and lists this network rule and secret.", 403, False),
    ("No module named 'oracledb'", "The oracledb package is not available in Snowflake",
     "Accept the Anaconda terms (Admin > Billing & Terms) or run on this platform's server.", 400, False),
    ("specified packages", "The oracledb package is not available in Snowflake",
     "Accept the Anaconda terms (Admin > Billing & Terms) or run on this platform's server.", 400, False),
    ("does not exist or not authorized", "A Snowflake object is missing or not granted",
     "Check the secret and integration exist and that this role has USAGE on them.", 403, False),
)


def _text(exc: Any) -> str:
    return str(exc) if not isinstance(exc, str) else exc


def code_of(exc: Any) -> Optional[str]:
    found = re.search(r"\b(ORA|DPY|DPI)-\d{4,5}\b", _text(exc))
    return found.group(0) if found else None


def explain(exc: Any) -> Dict[str, Any]:
    """{code, title, fix, status, retryable, detail}; unknown errors keep their own text as the title."""
    text = _text(exc)
    for pattern, title, fix, status, retryable in CATALOG:
        if pattern in text:
            return {"code": code_of(text) or pattern, "title": title, "fix": fix, "status": status,
                    "retryable": retryable, "detail": text[:600]}
    return {"code": code_of(text), "title": text.splitlines()[0][:200] if text else "Unknown error", "fix": None,
            "status": 500, "retryable": False, "detail": text[:600]}
