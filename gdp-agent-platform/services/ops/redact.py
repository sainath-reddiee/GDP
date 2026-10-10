"""Redaction of secrets and personal data in Airflow logs and error text (pure).

Applied before an error excerpt is stored, before a log is returned to the browser, and (later) before anything is
sent to AI, Jira or Teams. It is deliberately greedy: a false positive hides a harmless value, a false negative leaks.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Tuple

MASK = "[REDACTED]"

_RULES: list[tuple[re.Pattern, str]] = [
    # PEM private keys (RSA, EC, OPENSSH, encrypted)
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)"),
     "[REDACTED PRIVATE KEY]"),
    # user:password@ in URIs (postgres://u:p@h, snowflake://u:p@acct, https://u:p@host)
    (re.compile(r"(\b[A-Za-z][A-Za-z0-9+.\-]*://[^:/\s@]+:)([^@/\s]+)(@)"), r"\1" + MASK + r"\3"),
    # Authorization headers
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9\-._~+/]{8,}=*"), r"\1 " + MASK),
    # JWTs, which includes Snowflake programmatic access tokens and OAuth access tokens
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), MASK),
    # well-known token formats: GitHub, Slack, Atlassian API tokens, Snowflake PAT prefixes
    (re.compile(r"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{20,}"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), MASK),
    (re.compile(r"\bATATT[A-Za-z0-9_\-=]{20,}"), MASK),
    (re.compile(r"\bver:\d+-hint:\d+-[A-Za-z0-9+/=_\-]+"), MASK),
    # AWS access key ids
    (re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[A-Z0-9]{16}(?![A-Z0-9])"), MASK),
    # key = value pairs whose key names a secret (password=, "api_key": "...", AWS_SECRET_ACCESS_KEY=...)
    (re.compile(r"(?i)(\b[A-Za-z0-9_.\-]*(?:password|passwd|pwd|secret|token|api[_\-]?key|access[_\-]?key|private[_\-]?key"
                r"|credentials?|passphrase)[\"']?\s*[:=]\s*[\"']?)(?!\[REDACTED)([^\s\"',;&}\]]+)"), r"\1" + MASK),
    # personal data
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"(?<![\w/.:\-])\+\d{1,3}[\s.\-]?\(?\d{1,4}\)?(?:[\s.\-]?\d{2,4}){2,3}(?![\w/.:\-])"), "[PHONE]"),
    (re.compile(r"(?<![\w/.:\-])(?:\(\d{3}\)\s?|\d{3}[\s.\-])\d{3}[\s.\-]\d{4}(?![\w/.:\-])"), "[PHONE]"),
]


def redact_count(text: str) -> Tuple[str, int]:
    """(redacted text, number of replacements)."""
    if not text:
        return text or "", 0
    total = 0
    for pattern, repl in _RULES:
        text, n = pattern.subn(repl, text)
        total += n
    return text, total


def redact(text: str) -> str:
    return redact_count(text)[0]


LOG_FIELDS = {"error", "log", "logs", "message", "traceback", "exception", "stderr", "stdout", "detail", "note"}
PAYLOAD_TEXT_MAX = 4000


def redact_payload(value: Any, depth: int = 0) -> Any:
    """A JSON payload fit to store (OPS.EVENT): every string redacted; error text and log-like fields keep their
    last 4000 characters."""
    if depth > 8:
        return None
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            item = redact_payload(v, depth + 1)
            if isinstance(item, str) and str(k).lower() in LOG_FIELDS:
                item = item[-PAYLOAD_TEXT_MAX:]
            out[k] = item
        return out
    if isinstance(value, list):
        return [redact_payload(v, depth + 1) for v in value[:500]]
    if isinstance(value, str):
        return redact(value)
    return value
