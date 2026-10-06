import type { MappingOverview, MappingSuggestion } from "@/lib/types";

export type Candidate = MappingOverview["candidates"][number];
export type Decision = MappingOverview["decisions"][number];

export type MappingRow = {
  id: string;
  table: string;
  column: string;
  datatype: string;
  ranked: Candidate[];
  top: Candidate;
  score: number;
  decision?: Decision;
  mappedTargetId: string | null;
  mappedName?: string;
  status: "mapped" | "null" | "pending";
};

export type BulkPlan = {
  decisions: Record<string, unknown>[];
  skipped: { column: string; reason: string }[];
  needsReason: boolean;
};

export function buildRows(data: MappingOverview): MappingRow[] {
  const names = Object.fromEntries(data.targets.map((t) => [t.target_column_id, t.column_name]));
  const ids = [...new Set(data.candidates.map((c) => c.source_column_id))];
  return ids.map((id) => {
    const ranked = data.candidates.filter((c) => c.source_column_id === id).sort((a, b) => a.rank - b.rank);
    const top = ranked[0];
    const decision = data.decisions.find((d) => d.source_column_id === id);
    const mappedTargetId = decision && decision.decision !== "REJECTED" ? decision.target_column_id : null;
    return {
      id, table: top.source_table, column: top.source_column, datatype: top.source_datatype,
      ranked, top, score: Number(top.final_score), decision, mappedTargetId,
      mappedName: mappedTargetId
        ? names[mappedTargetId] || ranked.find((c) => c.target_column_id === mappedTargetId)?.target_column
        : undefined,
      status: !decision ? "pending" : decision.decision === "REJECTED" ? "null" : "mapped",
    };
  });
}

/** Targets already claimed by decided rows outside `excluding`. */
function claimedTargets(rows: MappingRow[], excluding: Set<string>) {
  const claimed = new Map<string, string>();
  for (const r of rows) {
    if (!excluding.has(r.id) && r.mappedTargetId) claimed.set(r.mappedTargetId, r.column);
  }
  return claimed;
}

/** Approve the top-ranked candidate for each selected row, skipping collisions (highest score wins). */
export function planApproveTop(rows: MappingRow[], selected: Set<string>, minScore = 0): BulkPlan {
  const chosen = rows.filter((r) => selected.has(r.id)).sort((a, b) => b.score - a.score);
  const claimed = claimedTargets(rows, selected);
  const plan: BulkPlan = { decisions: [], skipped: [], needsReason: false };
  for (const r of chosen) {
    if (r.score < minScore) {
      plan.skipped.push({ column: r.column, reason: `score ${Math.round(r.score * 100)}% is below ${Math.round(minScore * 100)}%` });
      continue;
    }
    const holder = claimed.get(r.top.target_column_id);
    if (holder) {
      plan.skipped.push({ column: r.column, reason: `${r.top.target_column} is already mapped from ${holder}` });
      continue;
    }
    claimed.set(r.top.target_column_id, r.column);
    if (r.top.recommendation !== "AUTO_SUGGEST") plan.needsReason = true;
    plan.decisions.push({
      decision: "APPROVED", source_column_id: r.id, candidate_id: r.top.candidate_id,
      transformation: r.top.transformation || undefined,
      _needs_reason: r.top.recommendation !== "AUTO_SUGGEST",
    });
  }
  return plan;
}

export function planNull(rows: MappingRow[], selected: Set<string>): BulkPlan {
  return {
    decisions: rows.filter((r) => selected.has(r.id) && r.status !== "null").map((r) => ({
      decision: "REJECTED", source_column_id: r.id,
    })),
    skipped: [],
    needsReason: false,
  };
}

/** Accept AI suggestions; collisions with existing decisions outside the batch are skipped. */
export function planAcceptAi(rows: MappingRow[], suggestions: MappingSuggestion[], selected?: Set<string>): BulkPlan {
  const picked = suggestions.filter((s) => !selected || selected.has(s.source_column_id));
  const ids = new Set(picked.map((s) => s.source_column_id));
  const claimed = claimedTargets(rows, ids);
  const byId = new Map(rows.map((r) => [r.id, r]));
  const plan: BulkPlan = { decisions: [], skipped: [], needsReason: false };
  for (const s of [...picked].sort((a, b) => b.confidence - a.confidence)) {
    const column = byId.get(s.source_column_id)?.column ?? s.source_column_id;
    if (s.target_column_id) {
      const holder = claimed.get(s.target_column_id);
      if (holder) {
        plan.skipped.push({ column, reason: `${s.target_column} is already mapped from ${holder}` });
        continue;
      }
      claimed.set(s.target_column_id, column);
    }
    plan.decisions.push(s.decision);
  }
  return plan;
}

/** Fill in one justification for every decision that needs one; drop internal markers. */
export function withJustification(plan: BulkPlan, note: string): Record<string, unknown>[] {
  return plan.decisions.map(({ _needs_reason, ...d }) => {
    const reason = String(d.business_justification || "").trim() || note.trim();
    return reason ? { ...d, business_justification: reason } : d;
  });
}
