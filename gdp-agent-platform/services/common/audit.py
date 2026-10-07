"""Tool-call and cost records. Inputs are redacted to identifiers and counts; never sample values or secrets."""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from services.common.sql import clip, config_value, insert_rows, rows


class ToolCall:
    def __init__(self, tool: str, inputs: Dict[str, Any]):
        self.tool = tool
        self.inputs = inputs
        self.summary = ""
        self.status = "SUCCEEDED"
        self.error: Optional[str] = None
        self.query_id: Optional[str] = None


@contextmanager
def tool_call(session, run_id: Optional[str], tool: str, inputs: Dict[str, Any]) -> Iterator[ToolCall]:
    """Records one AUDIT.AGENT_TOOL_CALL row for a stage action, including failures."""
    call = ToolCall(tool, inputs)
    started = time.time()
    try:
        yield call
    except Exception as exc:
        call.status, call.error = "FAILED", clip(exc)
        raise
    finally:
        insert_rows(
            session, "AUDIT.AGENT_TOOL_CALL",
            ["TOOL_CALL_ID", "RUN_ID", "TOOL_NAME", "INPUT_JSON", "OUTPUT_SUMMARY", "STATUS", "DURATION_MS",
             "QUERY_ID", "ERROR", "CALLED_BY"],
            ["?", "NULLIF(?, '')", "?", "PARSE_JSON(?)", "NULLIF(?, '')", "?", "?::NUMBER", "NULLIF(?, '')",
             "NULLIF(?, '')", "CURRENT_USER()"],
            [[str(uuid.uuid4()), run_id, tool, call.inputs, clip(call.summary), call.status,
              int((time.time() - started) * 1000), call.query_id, call.error]],
        )


def record_cost(session, run_id: Optional[str], stage: str, model: Optional[str], usage: Dict[str, Any],
                duration_ms: int, tool_calls: int = 0, search_calls: int = 0, code_calls: int = 0,
                agent: str = "PLATFORM") -> None:
    from services.common.cost import calibrated_rates, estimate, rates_for

    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or prompt + completion)
    estimated = 0.0
    if total:
        rates = rates_for(model, config_value(session, "RATE_CARD", {}) or {},
                          calibrated_rates(lambda sql, params: rows(session, sql, list(params))),
                          config_value(session, "CREDITS_PER_MILLION_TOKENS", {}) or {})
        estimated = estimate(prompt, completion, rates)
    row = [str(uuid.uuid4()), run_id, stage, agent, model, prompt, completion, total, tool_calls, search_calls,
           code_calls, duration_ms, estimated, usage.get("query_id"), "ESTIMATE" if total else "NONE"]
    columns = ["COST_USAGE_ID", "RUN_ID", "STAGE", "AGENT", "MODEL", "INPUT_TOKENS", "OUTPUT_TOKENS", "TOTAL_TOKENS",
               "TOOL_CALL_COUNT", "SEARCH_CALL_COUNT", "CODE_CALL_COUNT", "DURATION_MS", "ESTIMATED_COST"]
    exprs = ["?", "NULLIF(?, '')", "?", "?", "NULLIF(?, '')", "?::NUMBER", "?::NUMBER", "?::NUMBER", "?::NUMBER",
             "?::NUMBER", "?::NUMBER", "?::NUMBER", "?::NUMBER(18,6)"]
    try:
        insert_rows(session, "AUDIT.COST_USAGE", columns + ["QUERY_ID", "COST_SOURCE"],
                    exprs + ["NULLIF(?, '')", "?"], [row])
    except Exception:  # before V014: no query id / cost source columns yet
        insert_rows(session, "AUDIT.COST_USAGE", columns, exprs, [row[:13]])
