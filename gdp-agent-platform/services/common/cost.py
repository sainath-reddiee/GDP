"""What AI calls cost: an immediate estimate per call, replaced by the credits Snowflake actually bills.

Estimate (at call time), in this order:
  1. the admin's rate card for the model: credits per million input and per million output tokens
  2. the model's calibrated rate: actual credits / tokens over this account's reconciled calls
  3. the legacy single rate (CREDITS_PER_MILLION_TOKENS)
Actual (later): SNOWFLAKE.ACCOUNT_USAGE Cortex usage views report TOKEN_CREDITS per query id; reconcile() copies them
onto AUDIT.COST_USAGE (Snowflake's usage views lag behind by up to a few hours). No price is hardcoded here: the
platform learns this account's real rates from its own bill.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

Query = Callable[[str, tuple], List[Dict[str, Any]]]
# Where Snowflake reports AI credits per query. Accounts differ in which one is populated (AI_COMPLETE usage
# lands in CORTEX_AI_FUNCTIONS_USAGE_HISTORY on current accounts), so reconcile reads every one it can.
USAGE_VIEWS = (
    ("SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AI_FUNCTIONS_USAGE_HISTORY", "CREDITS"),
    ("SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FUNCTIONS_QUERY_USAGE_HISTORY", "TOKEN_CREDITS"),
)
# The account's own billed rate per model (credits per million tokens) from its Cortex AI usage history.
ACCOUNT_RATES_SQL = """
    SELECT MODEL_NAME AS MODEL, SUM(CREDITS) AS CREDITS, SUM(TOKENS) AS TOKENS
      FROM (SELECT H.QUERY_ID, H.START_TIME, H.FUNCTION_NAME, H.MODEL_NAME, ANY_VALUE(H.CREDITS) AS CREDITS,
                   SUM(IFF(F.VALUE:key:unit::STRING = 'tokens', F.VALUE:value::NUMBER, 0)) AS TOKENS
              FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AI_FUNCTIONS_USAGE_HISTORY H, LATERAL FLATTEN(INPUT => H.METRICS) F
             WHERE H.START_TIME >= DATEADD('day', -90, CURRENT_TIMESTAMP()) AND H.MODEL_NAME <> ''
             GROUP BY 1, 2, 3, 4)
     GROUP BY 1"""


def rates_for(model: Optional[str], rate_card: Dict[str, Any], calibrated: Dict[str, float],
              legacy: Dict[str, Any]) -> Tuple[float, float, str]:
    """(credits per M input tokens, credits per M output tokens, where the rate came from)."""
    card = (rate_card or {}).get(model or "") or {}
    if isinstance(card, dict) and (card.get("input") is not None or card.get("output") is not None):
        return float(card.get("input") or 0), float(card.get("output") or card.get("input") or 0), "rate card"
    if model and calibrated.get(model):
        return calibrated[model], calibrated[model], "calibrated"
    legacy = legacy or {}
    rate = legacy.get(model or "", legacy.get("default", 0)) or 0
    return float(rate), float(rate), "single rate" if rate else "none"


def estimate(input_tokens: int, output_tokens: int, rates: Tuple[float, float, str]) -> float:
    return round((int(input_tokens or 0) * rates[0] + int(output_tokens or 0) * rates[1]) / 1_000_000, 6)


def _per_million(found: List[Dict[str, Any]]) -> Dict[str, float]:
    out = {}
    for r in found or []:
        get = (lambda k: r.get(k, r.get(k.lower())))
        tokens, credits = float(get("TOKENS") or 0), float(get("CREDITS") or 0)
        if get("MODEL") and tokens > 0 and credits > 0:
            out[str(get("MODEL"))] = round(credits / tokens * 1_000_000, 6)
    return out


def calibrated_rates(query: Query) -> Dict[str, float]:
    """Credits per million tokens per model as this account is actually billed: the platform's own reconciled calls
    first, then the account's whole Cortex AI usage history (last 90 days). Reading ACCOUNT_USAGE takes seconds, so
    reconcile computes this and stores it (CALIBRATED_RATES); per-call estimates read the stored copy."""
    rates: Dict[str, float] = {}
    try:
        rates.update(_per_million(query(ACCOUNT_RATES_SQL, ())))
    except Exception:
        pass
    try:
        rates.update(_per_million(query("""SELECT MODEL, SUM(ACTUAL_CREDITS) AS CREDITS, SUM(TOTAL_TOKENS) AS TOKENS
                                             FROM AUDIT.COST_USAGE
                                            WHERE ACTUAL_CREDITS IS NOT NULL AND TOTAL_TOKENS > 0
                                              AND CREATED_AT >= DATEADD('day', -90, CURRENT_TIMESTAMP())
                                            GROUP BY MODEL""", ())))
    except Exception:
        pass
    return rates


def _count(query: Query, sql: str) -> int:
    found = query(sql, ()) or [{}]
    return int(found[0].get("n", found[0].get("N")) or 0)


def reconcile(query: Query, execute: Callable[[str, tuple], Any], days: int = 30) -> Dict[str, Any]:
    """Copy actual credits from Snowflake's Cortex usage views onto COST_USAGE by query id. Needs IMPORTED PRIVILEGES
    on the SNOWFLAKE database; without it the estimates stay."""
    actual_sql = "SELECT COUNT(*) AS N FROM AUDIT.COST_USAGE WHERE ACTUAL_CREDITS IS NOT NULL"
    before = _count(query, actual_sql)
    errors: List[str] = []
    readable = 0
    for view, credits_col in USAGE_VIEWS:
        try:
            execute(f"""
                MERGE INTO AUDIT.COST_USAGE C
                USING (SELECT QUERY_ID, SUM({credits_col}) AS CREDITS FROM {view}
                        WHERE QUERY_ID IN (SELECT QUERY_ID FROM AUDIT.COST_USAGE
                                            WHERE QUERY_ID IS NOT NULL AND ACTUAL_CREDITS IS NULL
                                              AND CREATED_AT >= DATEADD('day', -%s, CURRENT_TIMESTAMP()))
                        GROUP BY QUERY_ID) U
                   ON C.QUERY_ID = U.QUERY_ID AND C.ACTUAL_CREDITS IS NULL
                 WHEN MATCHED THEN UPDATE SET ACTUAL_CREDITS = U.CREDITS, RECONCILED_AT = CURRENT_TIMESTAMP(),
                                              COST_SOURCE = 'ACTUAL'""", (int(days),))
            readable += 1
        except Exception as exc:
            errors.append(f"{view.rsplit('.', 1)[-1]}: {str(exc)[:160]}")
    matched = _count(query, actual_sql) - before
    pending = _count(query, "SELECT COUNT(*) AS N FROM AUDIT.COST_USAGE WHERE QUERY_ID IS NOT NULL AND ACTUAL_CREDITS IS NULL")
    untracked = _count(query, "SELECT COUNT(*) AS N FROM AUDIT.COST_USAGE WHERE QUERY_ID IS NULL AND TOTAL_TOKENS > 0")
    return {"reconciled": max(matched, 0), "awaiting_billing": pending, "without_query_id": untracked,
            "access": readable > 0, "detail": None if readable else (
                "Actual credits need IMPORTED PRIVILEGES on the SNOWFLAKE database for this role "
                "(GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE <role>). Estimates are shown meanwhile. "
                + "; ".join(errors))}


def backfill_estimates(query: Query, execute: Callable[[str, tuple], Any], rate_card: Dict[str, Any],
                       legacy: Dict[str, Any], calibrated: Optional[Dict[str, float]] = None) -> int:
    """Calls recorded before any rate existed (estimate 0) get an estimate from the current rates."""
    calibrated = calibrated_rates(query) if calibrated is None else calibrated
    models = [r.get("model", r.get("MODEL")) for r in query(
        "SELECT DISTINCT MODEL FROM AUDIT.COST_USAGE WHERE ACTUAL_CREDITS IS NULL AND COALESCE(ESTIMATED_COST, 0) = 0 "
        "AND TOTAL_TOKENS > 0 AND MODEL IS NOT NULL", ())]
    updated = 0
    for model in models:
        rin, rout, source = rates_for(model, rate_card, calibrated, legacy)
        if source == "none":
            continue
        execute("""UPDATE AUDIT.COST_USAGE
                      SET ESTIMATED_COST = ROUND((INPUT_TOKENS * %s + OUTPUT_TOKENS * %s) / 1000000, 6),
                          COST_SOURCE = 'ESTIMATE'
                    WHERE MODEL = %s AND ACTUAL_CREDITS IS NULL AND COALESCE(ESTIMATED_COST, 0) = 0
                      AND TOTAL_TOKENS > 0""", (rin, rout, model))
        updated += 1
    return updated
