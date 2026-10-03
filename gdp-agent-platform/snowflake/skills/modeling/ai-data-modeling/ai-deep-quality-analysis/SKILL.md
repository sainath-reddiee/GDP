---
name: ai-deep-quality-analysis
description: "Deep per-column data-quality analysis with Cortex AI_COMPLETE: semantic type inference, quality scoring, issue detection, remediation SQL, and generated histogram code. Use when the user wants to: deeply analyze a column, infer its semantic type, score data quality, get fix/remediation SQL, or auto-generate profiling charts."
parent_skill: ai-data-modeling
---

# AI Deep Quality Analysis (Column Companion)

## When to Load
When a single column needs richer analysis than aggregate profiling — semantic typing, a
quality score, concrete remediation, and a chart.

## Technique

1. Build a context-rich prompt from `{column stats, sample rows, top values, semantic hint,
   optional business context}` and call `AI_COMPLETE('{llm_model}', '{prompt}')`.
2. Request a **structured JSON** response: semantic type inference, quality issues
   (`{issue, severity, affected_pct}`), quality score (0–100), recommendations, transformation
   logic, remediation SQL, and Python code to render a histogram.
3. **Multi-stage JSON repair** on the response: unescape → strip markdown fences → locate the
   outermost braces → sanitize stray backslashes/control chars → parse (JSON, then a literal-eval
   fallback).
4. Execute the generated histogram code in a **restricted sandbox** (see
   `ai-code-sandbox-validation`) and save the chart to an ephemeral/temp directory.

## Cortex/SQL Functions
`AI_COMPLETE('{llm_model}', '{prompt}')`.

## Parameters
- `llm_model` (required), `semantic_hints`, `business_context` (optional),
  `sample_limit` (analysis rows), `histogram_sample_limit`, `output_dir` (temp).

## Inputs / Outputs
- In: table/column identifiers + profile dict + optional description/business context.
- Out: dict with `analysis_text`, `column_type_inference`, `quality_issues`, `quality_score`,
  `recommendations`, `transformation_logic`, `remediation_sql`, `generated_code`, `histogram_path`.

## Notes
- Always validate generated code before executing it — never `exec` raw LLM output directly.
- LLM JSON is unreliable; the repair pipeline is essential. Degrade gracefully to the raw text.
