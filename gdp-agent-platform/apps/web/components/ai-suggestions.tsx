"use client";

import { SuggestionsPanel } from "@/components/suggestions-panel";
import {
  askSuggestions, decideSuggestion, loadSuggestions,
  type SuggestionItem, type SuggestionStage,
} from "@/app/runs/[runId]/pipeline-actions";

const ACCEPT_EFFECT: Record<SuggestionStage, string> = {
  PROFILING: "Accepting stores a column rule for this source and updates the run's profile.",
  DOMAIN: "Accepting switches the run to that knowledge pack.",
  STTM: "Accepting applies the transformation and stores it as a rule for this target.",
  SODA: "Accepting adds the check to this run and stores it as a pattern.",
  DBT: "Accepting updates the column's role in the target registry, which the generator reads.",
};

function summary(stage: SuggestionStage, item: SuggestionItem): { title: string; detail: string } {
  const s = (k: string) => (item[k] == null ? "" : String(item[k]));
  switch (stage) {
    case "PROFILING":
      return {
        title: s("column"),
        detail: [s("semantic_type") && `type ${s("semantic_type")}`, s("pii_classification") && `PII ${s("pii_classification")}`,
                 s("date_format") && `date format ${s("date_format")}`].filter(Boolean).join(" · "),
      };
    case "DOMAIN":
      return { title: s("domain_name"), detail: "knowledge pack" };
    case "STTM":
      return { title: s("target_column"), detail: s("transformation") };
    case "SODA":
      return { title: `${s("target_column") || "table"} · ${s("check_type")}`, detail: s("requirement") };
    case "DBT":
      return { title: s("target_column"), detail: `role ${s("role").toLowerCase().replace(/_/g, " ")}` };
  }
}

/** AI suggestions next to the rule results of a run stage. */
export function AiSuggestions({ runId, stage, canAct = true, placement = "top" }: {
  runId: string; stage: SuggestionStage; canAct?: boolean; placement?: "inline" | "top";
}) {
  return (
    <SuggestionsPanel
      placement={placement}
      canAct={canAct}
      effect={ACCEPT_EFFECT[stage]}
      summary={(item) => summary(stage, item)}
      source={{
        key: `${runId}:${stage}`,
        load: () => loadSuggestions(runId, stage),
        ask: (refresh) => askSuggestions(runId, stage, refresh),
        decide: (body) => decideSuggestion(runId, stage, body),
      }}
    />
  );
}
