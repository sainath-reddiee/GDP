---
name: mapping-approval-workflow
description: "Record and track user approve/reject decisions for AI-generated mappings, with bulk operations and review statistics. Use when the user wants to: approve or reject mappings, review a crosswalk, bulk-approve high-confidence mappings, flag low-confidence ones, or track approval history and rates."
parent_skill: ai-data-modeling
---

# Mapping Approval Workflow

## When to Load
When mappings need human sign-off and an auditable review trail.

## Technique

1. Maintain an **approval ledger** (JSON; parameterize path, in-memory fallback for read-only FS).
2. `record_approval` appends a timestamped record: the mapping snapshot, status
   (APPROVED/REJECTED/PENDING), reviewer, notes, and a generated id.
3. **Bulk operations**:
   - auto-approve mappings at/above a confidence threshold,
   - flag mappings at/below a low-confidence threshold for review.
4. Compute **statistics**: total reviews, approved/rejected counts, approval and rejection rates.

## Cortex/SQL Functions
None.

## Parameters
- `approval_store_path`, `auto_approve_threshold`, `flag_threshold`, `reviewer`.

## Inputs / Outputs
- In: mapping dicts + status/reviewer/notes.
- Out: approval ids; statistics dict; persisted ledger `{ approvals: [...], metadata: {...} }`.
