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
USAGE_VIEWS = (
    # (view, credits column, extra filter)
    ("SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FUNCTIONS_QUERY_USAGE_HISTORY", "TOKEN_CREDITS", ""),
    ("SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AI_FUNCTIONS_USAGE_HISTORY", "CREDITS", ""),
)


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


def calibrated_rates(query: Query) -> Dict[str, float]:
    """Credits per million tokens per model, from calls whose actual credits are known (last 90 days)."""
    try:
        found = query("""SELECT MODEL, SUM(ACTUAL_CREDITS) AS CREDITS, SUM(TOTAL_TOKENS) AS TOKENS
                           FROM AUDIT.COST_USAGE
                          WHERE ACTUAL_CREDITS IS NOT NULL AND TOTAL_TOKENS > 0
                            AND CREATED_AT >= DATEADD('day', -90, CURRENT_TIMESTAMP())
                          GROUP BY MODEL""", ())
    except Exception:
        return {}
    out = {}
    for r in found:
        get = (lambda k: r.get(k, r.get(k.lower())))
        tokens, credits = float(get("TOKENS") or 0), float(get("CREDITS") or 0)
        if get("MODEL") and tokens > 0 and credits > 0:
            out[str(get("MODEL"))] = round(credits / tokens * 1_000_000, 6)
    return out


def reconcile(query: Query, execute: Callable[[str, tuple], Any], days: int = 30) -> Dict[str, Any]:
    """Copy actual credits from Snowflake's Cortex usage views onto COST_USAGE by query id, then re-estimate
    calls that had no rate yet. Needs IMPORTED PRIVILEGES on the SNOWFLAKE database; without it the estimates stay."""
    errors: List[str] = []
    matched = 0
    for view, credits_col, extra in USAGE_VIEWS:
        try:
            execute(f"""
                MERGE INTO AUDIT.COST_USAGE C
                USING (SELECT QUERY_ID, SUM({credits_col}) AS CREDITS FROM {view}
                        WHERE QUERY_ID IN (SELECT QUERY_ID FROM AUDIT.COST_USAGE
                                            WHERE QUERY_ID IS NOT NULL AND ACTUAL_CREDITS IS NULL
                                              AND CREATED_AT >= DATEADD('day', -%s, CURRENT_TIMESTAMP())){extra}
                        GROUP BY QUERY_ID) U
                   ON C.QUERY_ID = U.QUERY_ID
                 WHEN MATCHED THEN UPDATE SET ACTUAL_CREDITS = U.CREDITS, RECONCILED_AT = CURRENT_TIMESTAMP(),
                                              COST_SOURCE = 'ACTUAL'""", (int(days),))
            matched = int((query("SELECT COUNT(*) AS N FROM AUDIT.COST_USAGE WHERE COST_SOURCE = 'ACTUAL' "
                                 "AND RECONCILED_AT >= DATEADD('minute', -5, CURRENT_TIMESTAMP())", ()) or [{}])[0]
                          .get("n") or 0)
            errors = []
            break
        except Exception as exc:
            errors.append(f"{view}: {str(exc)[:200]}")
    pending = query("""SELECT COUNT(*) AS N FROM AUDIT.COST_USAGE
                        WHERE QUERY_ID IS NOT NULL AND ACTUAL_CREDITS IS NULL""", ())
    return {"reconciled": matched, "awaiting_billing": int((pending or [{}])[0].get("n") or 0),
            "access": not errors, "detail": None if not errors else (
                "Actual credits need IMPORTED PRIVILEGES on the SNOWFLAKE database for this role "
                "(GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE <role>). Estimates are shown meanwhile. "
                + "; ".join(errors))}


def backfill_estimates(query: Query, execute: Callable[[str, tuple], Any], rate_card: Dict[str, Any],
                       legacy: Dict[str, Any]) -> int:
    """Calls recorded before any rate existed (estimate 0) get an estimate from the current rates."""
    calibrated = calibrated_rates(query)
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
