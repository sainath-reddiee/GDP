---
name: ai-code-sandbox-validation
description: "Validate and safely execute AI-generated Python via regex pattern scanning, AST import/call whitelisting, and a restricted namespace. Use when the user wants to: run LLM-generated code safely, sandbox generated Python, prevent dangerous operations, or execute auto-generated profiling/plotting code."
parent_skill: ai-data-modeling
---

# AI Code Sandbox & Validation

## When to Load
Before executing any Python produced by an LLM (e.g. histogram/profiling code from
`ai-deep-quality-analysis`). Defense-in-depth against malicious or accidental damage.

## Technique

Three layers:

1. **Regex scan** for dangerous patterns (parameterize the list, e.g. `eval`, `exec`,
   `os.system`, `subprocess`, `socket`, network/file-escape calls). Reject on match.
2. **AST parse + walk**: whitelist allowed imports and restrict function calls; reject imports
   or calls outside the allowlist. Allowlist is a parameter (e.g. pandas, numpy, matplotlib, re,
   json, math, statistics, collections, itertools, datetime, decimal).
3. **Restricted namespace**: build execution globals with a sandboxed `__builtins__` and a
   `safe_import` wrapper that only permits allowlisted modules; execute within that namespace.

## Cortex/SQL Functions
None — pure Python security utility.

## Parameters
- `dangerous_patterns`, `allowed_imports`, `max_length` (source cap).

## Inputs / Outputs
- In: Python source string.
- Out: `(is_valid, message)` and, on success, a safe globals dict for controlled execution.

## Notes
- Fail closed: if analysis errors, treat the code as unsafe.
- Never widen the allowlist to include process/network/file-system escape modules.
