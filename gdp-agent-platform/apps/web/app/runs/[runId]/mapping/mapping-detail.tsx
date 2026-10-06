"use client";

import { useEffect, useState, useTransition } from "react";
import { Bot, Check, CircleSlash, KeyRound, Sparkles, X } from "lucide-react";
import type { MappingOverview, MappingProfile, MappingSuggestion } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { refineTransformation, saveMappingDecisions } from "../pipeline-actions";
import type { Candidate, MappingRow } from "./mapping-plan";

const SCORES: { key: keyof Candidate; label: string }[] = [
  { key: "semantic_score", label: "Semantic" },
  { key: "keyword_score", label: "Name" },
  { key: "datatype_score", label: "Type" },
  { key: "statistical_score", label: "Stats" },
  { key: "domain_score", label: "Domain" },
  { key: "context_score", label: "Context" },
  { key: "historical_score", label: "History" },
];

export function pct(v: unknown) {
  return `${Math.round(Number(v || 0) * 100)}%`;
}

export function scoreTone(score: number) {
  return score >= 0.8 ? "bg-emerald-500" : score >= 0.6 ? "bg-sky-500" : score >= 0.45 ? "bg-amber-500" : "bg-rose-500";
}

export function ScoreBar({ value, className }: { value: number; className?: string }) {
  return (
    <span className={cn("inline-flex h-1.5 w-16 overflow-hidden rounded-full bg-muted", className)}>
      <span className={cn("h-full", scoreTone(value))} style={{ width: `${Math.max(4, Math.round(value * 100))}%` }} />
    </span>
  );
}

export function MappingDetail({
  runId, row, data, profile, suggestion, takenBy, onSaved, onClose,
}: {
  runId: string;
  row: MappingRow;
  data: MappingOverview;
  profile?: MappingProfile;
  suggestion?: MappingSuggestion;
  takenBy: Map<string, string>;
  onSaved: () => void;
  onClose: () => void;
}) {
  const initial = row.decision?.candidate_id ?? row.top.candidate_id;
  const [candidateId, setCandidateId] = useState(initial);
  const [targetId, setTargetId] = useState("");
  const [transformation, setTransformation] = useState(row.decision?.transformation ?? row.top.transformation ?? "");
  const [justification, setJustification] = useState(row.decision?.business_justification ?? "");
  const [ask, setAsk] = useState("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();

  useEffect(() => {
    setCandidateId(row.decision?.candidate_id ?? row.top.candidate_id);
    setTargetId(row.decision?.decision === "ALTERNATIVE_TARGET" ? row.decision.target_column_id ?? "" : "");
    setTransformation(row.decision?.transformation ?? row.top.transformation ?? "");
    setJustification(row.decision?.business_justification ?? "");
    setAsk("");
    setError("");
  }, [row.id, row.decision, row.top]);

  const chosen = row.ranked.find((c) => c.candidate_id === candidateId) ?? row.top;
  const alt = data.targets.find((t) => t.target_column_id === targetId);
  const effectiveTargetId = alt?.target_column_id ?? chosen.target_column_id;
  const holder = takenBy.get(effectiveTargetId);
  const conflict = holder && holder !== row.column ? holder : null;
  const changed = Boolean(alt) || chosen.candidate_id !== row.top.candidate_id
    || (transformation || "") !== (chosen.transformation || "");
  const needsReason = changed || chosen.recommendation !== "AUTO_SUGGEST";

  const save = (decision: Record<string, unknown>) => start(async () => {
    setError("");
    const result = await saveMappingDecisions(runId, [{ source_column_id: row.id, ...decision }]);
    if (!result.ok) { setError(result.error); return; }
    onSaved();
  });

  const approve = () => {
    const why = justification.trim();
    if (needsReason && !why) { setError("Add a business justification for this decision."); return; }
    if (alt) {
      save({ decision: "ALTERNATIVE_TARGET", target_column_id: alt.target_column_id, transformation: transformation || undefined, business_justification: why });
    } else if ((transformation || "") !== (chosen.transformation || "")) {
      save({ decision: "MODIFIED", candidate_id: chosen.candidate_id, transformation, business_justification: why });
    } else {
      save({ decision: "APPROVED", candidate_id: chosen.candidate_id, transformation: chosen.transformation || undefined, business_justification: why || undefined });
    }
  };

  const applySuggestion = () => {
    if (!suggestion) return;
    if (suggestion.action === "NULL") {
      save({ decision: "REJECTED", business_justification: `AI copilot: ${suggestion.reason}` });
      return;
    }
    if (suggestion.candidate_id) setCandidateId(suggestion.candidate_id);
    setTargetId(suggestion.action === "ALTERNATIVE" ? suggestion.target_column_id ?? "" : "");
    if (suggestion.transformation) setTransformation(suggestion.transformation);
    setJustification(`AI copilot: ${suggestion.reason}`);
  };

  const target = data.targets.find((t) => t.target_column_id === effectiveTargetId);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-start gap-2 border-b p-4">
        <div className="min-w-0 flex-1">
          <p className="font-mono text-[11px] text-muted-foreground">{row.table}</p>
          <h3 className="break-all text-base font-semibold leading-tight">{row.column}</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {row.datatype}
            {row.status === "null" ? " · loads as NULL" : row.mappedName ? ` · mapped to ${row.mappedName}` : " · needs a decision"}
          </p>
        </div>
        <Badge variant={row.status === "null" ? "destructive" : row.status === "mapped" ? "success" : "outline"}>
          {row.status === "null" ? "NULL" : row.decision?.decision ?? "PENDING"}
        </Badge>
        <button type="button" aria-label="Close details" onClick={onClose} className="text-muted-foreground hover:text-foreground">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        {profile && (
          <section className="rounded-lg border bg-muted/20 p-3">
            <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Source profile</p>
            {profile.description && <p className="mb-2 text-sm">{profile.description}</p>}
            <div className="flex flex-wrap gap-1.5 text-[11px]">
              {profile.semantic_type && <Badge variant="outline">{profile.semantic_type}</Badge>}
              {profile.null_percentage != null && <Badge variant="outline">null {Number(profile.null_percentage).toFixed(0)}%</Badge>}
              {profile.distinct_percentage != null && <Badge variant="outline">distinct {Number(profile.distinct_percentage).toFixed(0)}%</Badge>}
              {profile.pii && profile.pii !== "NONE" && <Badge variant="warning">PII · {profile.pii}</Badge>}
            </div>
            {profile.values?.length ? (
              <p className="mt-2 truncate font-mono text-[11px] text-muted-foreground">e.g. {profile.values.slice(0, 6).map(String).join(", ")}</p>
            ) : null}
          </section>
        )}

        {suggestion && (
          <section className="rounded-lg border border-violet-300 bg-violet-50 p-3 text-sm dark:bg-violet-950/30">
            <p className="flex items-center gap-1.5 text-xs font-semibold text-violet-700"><Bot className="h-3.5 w-3.5" /> AI copilot · {pct(suggestion.confidence)}</p>
            <p className="mt-1 font-medium">
              {suggestion.action === "NULL" ? "Leave unmapped (NULL)" : `Map to ${suggestion.target_column}`}
              {suggestion.transformation && <span className="block font-mono text-xs font-normal">{suggestion.transformation}</span>}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">{suggestion.reason}</p>
            {suggestion.note && <p className="mt-1 text-xs text-amber-700">{suggestion.note}</p>}
            <Button size="sm" variant="outline" className="mt-2" onClick={applySuggestion} disabled={pending}>
              <Sparkles className="h-3.5 w-3.5" /> {suggestion.action === "NULL" ? "Accept: set NULL" : "Use this suggestion"}
            </Button>
          </section>
        )}

        <section>
          <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Ranked candidates</p>
          <div className="space-y-1.5">
            {row.ranked.slice(0, 5).map((c) => {
              const active = !alt && c.candidate_id === chosen.candidate_id;
              const owner = takenBy.get(c.target_column_id);
              return (
                <button
                  key={c.candidate_id}
                  type="button"
                  onClick={() => { setCandidateId(c.candidate_id); setTargetId(""); setTransformation(c.transformation ?? ""); }}
                  className={cn("w-full rounded-lg border p-2.5 text-left transition-colors", active ? "border-primary bg-primary/5" : "hover:bg-muted/50")}
                >
                  <span className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">#{c.rank}</span>
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">{c.target_column}</span>
                    {c.is_business_key && <KeyRound className="h-3.5 w-3.5 text-amber-500" />}
                    {c.llm_preferred && c.rank === 1 && (
                      <Badge variant={c.llm_agrees ? "success" : "warning"} className="text-[10px]">
                        <Bot className="mr-0.5 h-3 w-3" />{c.llm_agrees ? "AI agrees" : `AI: ${c.llm_preferred}`}
                      </Badge>
                    )}
                    <span className="w-9 text-right text-xs font-semibold tabular-nums">{pct(c.final_score)}</span>
                  </span>
                  <span className="mt-1 flex items-center gap-2 text-[11px] text-muted-foreground">
                    <span>{c.target_datatype}</span>
                    {owner && owner !== row.column && <span className="text-amber-700">· taken by {owner}</span>}
                  </span>
                  {active && (
                    <span className="mt-2 grid grid-cols-7 gap-1">
                      {SCORES.map((s) => (
                        <span key={s.key} className="flex flex-col items-center gap-0.5" title={`${s.label} ${pct(c[s.key])}`}>
                          <span className="flex h-10 w-2.5 items-end overflow-hidden rounded bg-muted">
                            <span className={cn("w-full", scoreTone(Number(c[s.key] || 0)))} style={{ height: `${Math.max(6, Number(c[s.key] || 0) * 100)}%` }} />
                          </span>
                          <span className="text-[9px] text-muted-foreground">{s.label}</span>
                        </span>
                      ))}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          {chosen.generated_reason && !alt && <p className="mt-2 text-xs text-muted-foreground">{chosen.generated_reason}</p>}
        </section>

        <section className="space-y-2">
          <Label htmlFor="alt" className="mt-0">Or map to another target</Label>
          <Select id="alt" value={targetId} onChange={(e) => setTargetId(e.target.value)}>
            <option value="">Use selected candidate ({chosen.target_column})</option>
            {data.targets.map((t) => {
              const owner = takenBy.get(t.target_column_id);
              return (
                <option key={t.target_column_id} value={t.target_column_id}>
                  {t.column_name} · {t.data_type}{t.nullable ? "" : " · required"}{owner && owner !== row.column ? ` · taken by ${owner}` : ""}
                </option>
              );
            })}
          </Select>
          {target?.definition && <p className="text-[11px] text-muted-foreground">{target.definition}</p>}
          {conflict && (
            <p className="rounded-md bg-amber-50 px-2 py-1.5 text-xs text-amber-800">
              {target?.column_name} is already mapped from {conflict}. Set that column to NULL or remap it first.
            </p>
          )}

          <Label htmlFor="transformation">Transformation (SQL)</Label>
          <Input id="transformation" className="font-mono text-xs" value={transformation} onChange={(e) => setTransformation(e.target.value)} placeholder={`e.g. TRIM(${row.column})`} />
          <div className="flex gap-2">
            <Input value={ask} onChange={(e) => setAsk(e.target.value)} placeholder="Ask AI: cast to DATE, trim, map Y/N to TRUE/FALSE…" className="text-xs" />
            <Button
              type="button" variant="outline" size="sm" disabled={pending || !ask.trim()}
              onClick={() => start(async () => {
                setError("");
                const result = await refineTransformation(runId, {
                  prompt: ask, target_column: target?.column_name ?? chosen.target_column, source_column: row.column,
                  source_table: row.table, source_datatype: row.datatype,
                  target_datatype: target?.data_type ?? chosen.target_datatype, current_transformation: transformation,
                });
                if (!result.ok) setError(result.error);
                else {
                  setTransformation(result.data.transformation);
                  if (result.data.rationale && !justification) setJustification(result.data.rationale);
                }
              })}
            >
              <Sparkles className="h-3.5 w-3.5" /> SQL
            </Button>
          </div>

          <Label htmlFor="justification">Business justification{needsReason ? " (required)" : ""}</Label>
          <Textarea id="justification" rows={2} value={justification} onChange={(e) => setJustification(e.target.value)}
            placeholder={needsReason ? "Why is this the right target?" : "Optional for a confident suggested match"} />
        </section>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      </div>

      <div className="flex flex-wrap gap-2 border-t p-3">
        <Button disabled={pending || Boolean(conflict)} onClick={approve} className="flex-1">
          <Check className="h-4 w-4" /> {pending ? "Saving…" : alt ? `Map to ${alt.column_name}` : `Approve ${chosen.target_column}`}
        </Button>
        <Button
          variant="outline" disabled={pending || row.status === "null"}
          onClick={() => save({ decision: "REJECTED", business_justification: justification.trim() || "No matching target column; load this source field as NULL." })}
        >
          <CircleSlash className="h-4 w-4" /> NULL
        </Button>
      </div>
    </div>
  );
}
