"""Name tokenisation shared by domain identification and mapping (pure)."""

from __future__ import annotations

import re
from typing import Iterable, List, Set

from services.common.rules import rule

ABBREVIATIONS = {
    "CUST": ["CUSTOMER"], "CLT": ["CLIENT"], "NM": ["NAME"], "DOB": ["BIRTH", "DATE"], "DT": ["DATE"],
    "TS": ["TIMESTAMP"], "ADDR": ["ADDRESS"], "CD": ["CODE"], "AMT": ["AMOUNT"], "QTY": ["QUANTITY"],
    "DESC": ["DESCRIPTION"], "STAT": ["STATUS"], "STS": ["STATUS"], "RGN": ["REGION"], "EML": ["EMAIL"],
    "PH": ["PHONE"], "TEL": ["PHONE"], "CRT": ["CREATED"], "CRE": ["CREATED"], "UPD": ["UPDATED"],
    "ACCT": ["ACCOUNT"], "ORG": ["ORGANIZATION"], "CCY": ["CURRENCY"], "NBR": ["NUMBER"], "NUM": ["NUMBER"],
    "DIM": [], "FCT": [], "STG": [], "INT": [], "CRM": ["CRM"],
}
# Tokens that name the same idea; collapsed to one canonical token before comparison.
CANONICAL = {"NUMBER": "ID", "NO": "ID", "ID": "ID", "IDENTIFIER": "ID", "KEY": "ID", "CODE": "CODE",
             "DATE": "DATE", "DAY": "DATE", "CREATE": "CREATED", "EMAIL": "EMAIL", "MAIL": "EMAIL"}
STOPWORDS = {"THE", "OF", "A", "AN", "AND"}


def split_name(name: str) -> List[str]:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1_\2", name or "")
    return [t for t in re.split(r"[^A-Za-z0-9]+", spaced.upper()) if t]


def tokens(name: str) -> List[str]:
    out: List[str] = []
    for t in split_name(name):
        for expanded in {**ABBREVIATIONS, **(rule("hints.abbreviations") or {})}.get(t, [t]):
            if expanded not in STOPWORDS:
                out.append(CANONICAL.get(expanded, expanded))
    return out


def token_set(name: str) -> Set[str]:
    return set(tokens(name))


def entity_tokens(table_name: str) -> Set[str]:
    """Business entity of a table name: tokens minus system prefixes (dim_, stg_, crm_)."""
    return {t for t in tokens(table_name) if t not in {"CRM", "ID", "CODE"}}


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 0.0
