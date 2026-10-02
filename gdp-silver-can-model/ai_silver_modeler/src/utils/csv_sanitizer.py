"""Helpers for producing CSV-safe text payloads."""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

import pandas as pd

# Control characters except tab/newline/carriage return
_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
_WHITESPACE_PATTERN = re.compile(r"[\r\n\t]+")
_BOM = "\ufeff"


def sanitize_text(value: Any, *, max_length: int = 4000) -> str:
    """Convert arbitrary values into CSV-safe strings.

    - Replaces binary payloads with a marker
    - Removes control characters
    - Normalizes whitespace and trims overly long text
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "N/A"

    if isinstance(value, (bytes, bytearray)):
        return "<binary data>"

    try:
        text = str(value)
    except Exception:
        return "<unprintable>"

    if not text:
        return "N/A"

    # Remove BOMs and control characters we never want in CSV files
    text = text.replace(_BOM, "")
    text = _CONTROL_PATTERN.sub("", text)

    # Normalize whitespace (collapse CR/LF/TAB into single spaces)
    text = _WHITESPACE_PATTERN.sub(" ", text)
    text = text.strip()

    if not text:
        return "N/A"

    if len(text) > max_length:
        text = text[: max_length - 3] + "..."

    return text


def sanitize_dataframe(df: pd.DataFrame, columns: Optional[Iterable[str]] = None) -> pd.DataFrame:
    """Return a copy of *df* with CSV-safe text columns."""
    if df.empty:
        return df

    target_cols = columns or df.select_dtypes(include=["object", "string"]).columns
    if len(target_cols) == 0:
        return df

    df_copy = df.copy()
    for col in target_cols:
        df_copy[col] = df_copy[col].apply(sanitize_text)
    return df_copy
