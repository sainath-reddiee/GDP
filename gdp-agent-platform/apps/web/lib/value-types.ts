// The value ledger contract (GET /api/value, /api/value/summary; services/value on the API).

export type ValueActivity = {
  activity: string; label: string; group: "build" | "ops"; unit: string; available: boolean;
  count: number | null; credited: number | null; minutes_each: number; hours: number | null;
  count_basis: "measured"; minutes_basis: "assumed default" | "admin set"; source: string;
};

export type ValueNet = {
  /** hours are always an estimate (measured counts x assumed minutes); money only once the baselines are confirmed */
  hours: number; value_usd: number | null; ai_credits: number | null; ai_cost_usd: number | null; net_usd: number | null;
  needs_price: boolean; needs_confirmation?: boolean; estimate?: boolean;
};

export type RepairSide = { n: number; median_min: number | null };
export type RepairComparison = {
  label: string; measured: boolean; change_pct: number | null; cutover?: string | null; failures?: number;
} & Record<string, RepairSide | unknown>;

export type KnowledgeComponent = { value: number | null; previous: number | null; numerator?: number | null; denominator?: number | null };
export type ValueKnowledge = {
  score: number | null; previous_score: number | null;
  components: Record<"auto_fix_share" | "dag_coverage" | "fix_coverage", KnowledgeComponent>;
  counts: { knowledge_items?: number | null; knowledge_new?: number | null; known_fixes?: number | null; runbooks?: number | null };
};

export type BaselineActivity = {
  minutes: number; default_minutes: number; basis: "default" | "edited"; label: string; group: string; unit: string; source: string;
};

export type ValueReport = {
  window_days: number; generated_at: string; activities: ValueActivity[]; hours_by_group: Record<string, number>;
  value: ValueNet; weekly: { week: string; hours: number; ai_credits: number; ai_cost_usd: number | null }[];
  repair_time: RepairComparison[] | null; knowledge: ValueKnowledge | null;
  baselines: {
    blended_rate_usd: number; rate_basis: "default" | "edited"; credit: Record<string, number>; min_samples: number;
    activities: Record<string, BaselineActivity>; version: number | null; changed_by: string | null; changed_at: string | null;
    confirmed?: boolean; basis_note?: string; confirmed_by?: string | null; confirmed_at?: string | null;
  };
  /** what every count leaves out */
  scope?: { excluded_run_environments: string[]; excluded_airflow_envs: string[]; always_counted: string[]; ai_cost: string };
  credit_price_usd: number | null;
};

export type ValueSummary = {
  window_days: number; value: ValueNet; auto_fix_share: number | null; knowledge_score: number | null;
  previous_knowledge_score: number | null; weekly_hours: number[];
};
