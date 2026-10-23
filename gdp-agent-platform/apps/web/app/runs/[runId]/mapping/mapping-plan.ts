import type { MappingOverview, MappingSuggestion } from "@/lib/types";

export type Candidate = MappingOverview["candidates"][number];
export type Decision = MappingOverview["decisions"][number];

/** One source column for one target table: a run maps to a hub and its spokes, so a source column can have a row in
 * each table it was proposed for, decided on its own. */
export type MappingRow = {
  id: string;
  sourceId: string;
  tableId: string;
  targetTable: string;
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

/** The key of a row: its source column and target table. */
export function rowKey(sourceId: string, tableId: string | null | undefined) {
  return `${sourceId}::${tableId ?? ""}`;
}

/** The table a candidate, decision or target belongs to; rows written before a run had several tables have none and
 * belong to the run's first table. */
export function firstTableId(data: MappingOverview) {
  return data.target_tables?.[0]?.target_table_id ?? data.target_table?.target_table_id ?? "";
}

export function buildRows(data: MappingOverview): MappingRow[] {
  const first = firstTableId(data);
  const names = Object.fromEntries(data.targets.map((t) => [t.target_column_id, t.column_name]));
  const tableNames = Object.fromEntries((data.target_tables ?? (data.target_table ? [data.target_table] : []))
    .map((t) => [t.target_table_id, t.target_table]));
  const order = new Map((data.target_tables ?? []).map((t, i) => [t.target_table_id, i]));
  const groups = new Map<string, Candidate[]>();
  for (const c of data.candidates) {
    const key = rowKey(c.source_column_id, c.target_table_id || first);
    groups.set(key, [...(groups.get(key) ?? []), c]);
  }
  const rows = [...groups.entries()].map(([id, cands]) => {
    const ranked = [...cands].sort((a, b) => a.rank - b.rank);
    const top = ranked[0];
    const tableId = top.target_table_id || first;
    const decision = data.decisions.find((d) => d.source_column_id === top.source_column_id && (d.target_table_id || first) === tableId);
    const mappedTargetId = decision && decision.decision !== "REJECTED" ? decision.target_column_id : null;
    const row: MappingRow = {
      id, sourceId: top.source_column_id, tableId, targetTable: top.target_table || tableNames[tableId] || "",
      table: top.source_table, column: top.source_column, datatype: top.source_datatype,
      ranked, top, score: Number(top.final_score), decision, mappedTargetId,
      mappedName: mappedTargetId
        ? names[mappedTargetId] || ranked.find((c) => c.target_column_id === mappedTargetId)?.target_column
        : undefined,
      status: !decision ? "pending" : decision.decision === "REJECTED" ? "null" : "mapped",
    };
    return row;
  });
  // hub first, then each spoke, in the order the run's target set gives
  return rows.sort((a, b) => (order.get(a.tableId) ?? 0) - (order.get(b.tableId) ?? 0));
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
      decision: "APPROVED", source_column_id: r.sourceId, target_table_id: r.tableId || undefined,
      candidate_id: r.top.candidate_id, transformation: r.top.transformation || undefined,
      _needs_reason: r.top.recommendation !== "AUTO_SUGGEST",
    });
  }
  return plan;
}

export function planNull(rows: MappingRow[], selected: Set<string>): BulkPlan {
  return {
    decisions: rows.filter((r) => selected.has(r.id) && r.status !== "null").map((r) => ({
      decision: "REJECTED", source_column_id: r.sourceId, target_table_id: r.tableId || undefined,
    })),
    skipped: [],
    needsReason: false,
  };
}

/** The row a suggestion is for. */
export function suggestionKey(s: MappingSuggestion, first: string) {
  return rowKey(s.source_column_id, s.target_table_id || first);
}

/** Accept AI suggestions (keyed by row); collisions with existing decisions outside the batch are skipped. */
export function planAcceptAi(rows: MappingRow[], suggestions: Record<string, MappingSuggestion>, selected?: Set<string>): BulkPlan {
  const picked = Object.entries(suggestions).filter(([key]) => !selected || selected.has(key));
  const ids = new Set(picked.map(([key]) => key));
  const claimed = claimedTargets(rows, ids);
  const byId = new Map(rows.map((r) => [r.id, r]));
  const plan: BulkPlan = { decisions: [], skipped: [], needsReason: false };
  for (const [key, s] of [...picked].sort((a, b) => b[1].confidence - a[1].confidence)) {
    const column = byId.get(key)?.column ?? s.source_column_id;
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

/** The row each saved decision was for. */
export function savedKeys(decisions: Record<string, unknown>[], first: string) {
  return new Set(decisions.map((d) => rowKey(String(d.source_column_id), (d.target_table_id as string | undefined) || first)));
}
