"use client";

import { useMemo, useState, useTransition } from "react";
import { Check, CircleSlash } from "lucide-react";
import type { MappingOverview } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { refineTransformation, saveMappingDecisions } from "../pipeline-actions";

type Candidate = MappingOverview["candidates"][number];
type SourceRow = {
  id: string;
  ranked: Candidate[];
  top: Candidate;
  decision?: MappingOverview["decisions"][number];
  mappedName?: string;
};

function evidenceOf(c: Candidate) {
  return [
    { label: "datatype", ok: Number(c.datatype_score) >= 0.5 },
    { label: "cardinality", ok: Number(c.statistical_score) >= 0.5 },
    { label: "semantic similarity", ok: Number(c.semantic_score) >= 0.5 },
    { label: "domain pattern", ok: Number(c.domain_score) >= 0.5 },
    { label: "historical approved mapping", ok: Number(c.historical_score) > 0 },
  ];
}

function tone(decision?: string, score = 0) {
  if (decision === "REJECTED") return "destructive" as const;
  if (decision) return "success" as const;
  if (score < 0.45) return "warning" as const;
  return "outline" as const;
}

export function MappingBoard({ runId, data }: { runId: string; data: MappingOverview }) {
  const byTarget = useMemo(
    () => Object.fromEntries(data.targets.map((t) => [t.target_column_id, t.column_name])),
    [data.targets],
  );
  const sources = useMemo<SourceRow[]>(() => {
    const ids = [...new Set(data.candidates.map((c) => c.source_column_id))];
    return ids.map((id) => {
      const ranked = data.candidates.filter((c) => c.source_column_id === id);
      const decision = data.decisions.find((d) => d.source_column_id === id);
      const mappedName = decision?.target_column_id
        ? byTarget[decision.target_column_id] || ranked.find((c) => c.target_column_id === decision.target_column_id)?.target_column
        : undefined;
      return { id, ranked, top: ranked[0], decision, mappedName };
    });
  }, [data, byTarget]);
  const firstOpen = sources.find((s) => !s.decision)?.id ?? sources[0]?.id ?? "";
  const [selected, setSelected] = useState(firstOpen);
  const current = sources.find((s) => s.id === selected) ?? sources[0];
  const [candidateId, setCandidateId] = useState(current?.decision?.candidate_id ?? current?.top?.candidate_id ?? "");
  const [modifying, setModifying] = useState(false);
  const [targetId, setTargetId] = useState("");
  const [transformation, setTransformation] = useState(current?.decision?.transformation ?? current?.top?.transformation ?? "");
  const [justification, setJustification] = useState("");
  const [ask, setAsk] = useState("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();

  const open = (id: string) => {
    const next = sources.find((s) => s.id === id);
    setSelected(id);
    setCandidateId(next?.decision?.candidate_id ?? next?.top?.candidate_id ?? "");
    setTransformation(next?.decision?.transformation ?? next?.top?.transformation ?? "");
    setJustification(next?.decision?.business_justification ?? "");
    setTargetId(next?.decision?.target_column_id ?? "");
    setModifying(false);
    setError("");
  };

  if (!current) return <p className="text-sm text-muted-foreground">No candidates yet.</p>;
  const chosen = current.ranked.find((c) => c.candidate_id === candidateId) ?? current.top;
  const evidence = evidenceOf(chosen);
  const score = Number(chosen.final_score);
  const confidence = Math.round(score * 100);
  const weak = score < 0.45;
  const decided = Boolean(current.decision);
  const unmapped = current.decision?.decision === "REJECTED";

  const save = (decision: "APPROVED" | "MODIFIED" | "REJECTED" | "ALTERNATIVE_TARGET", note?: string) =>
    start(async () => {
      setError("");
      const reason = (note || justification).trim();
      const result = await saveMappingDecisions(runId, [{
        decision,
        candidate_id: decision === "REJECTED" || decision === "ALTERNATIVE_TARGET" ? undefined : chosen.candidate_id,
        source_column_id: current.id,
        transformation: decision === "MODIFIED" || decision === "ALTERNATIVE_TARGET" ? transformation || undefined : chosen.transformation || undefined,
        business_justification: reason || undefined,
        target_column_id: decision === "ALTERNATIVE_TARGET" ? targetId || undefined : undefined,
      }]);
      if (!result.ok) {
        setError(result.error);
        return;
      }
      const remaining = sources.filter((s) => s.id !== current.id && !s.decision);
      if (remaining[0]) open(remaining[0].id);
    });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Source → target mapping</CardTitle>
        <CardDescription>
          {data.status.decided}/{data.status.source_columns} columns decided.
          {data.status.complete
            ? " Approve the mapping pack above to unlock STTM."
            : " Confirm a target, or leave the column unmapped so it loads as NULL."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-5 lg:grid-cols-[280px_1fr]">
          <ol className="max-h-[32rem] space-y-1 overflow-auto rounded-lg border p-1.5">
            {sources.map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  onClick={() => open(s.id)}
                  className={`flex w-full flex-col rounded-md px-2 py-1.5 text-left ${s.id === current.id ? "bg-accent" : "hover:bg-muted"}`}
                >
                  <span className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">{s.top.source_column}</span>
                    <Badge variant={tone(s.decision?.decision, Number(s.top.final_score))}>
                      {s.decision?.decision === "REJECTED" ? "NULL" : s.decision ? "mapped" : `${Math.round(Number(s.top.final_score) * 100)}%`}
                    </Badge>
                  </span>
                  <span className="truncate text-[11px] text-muted-foreground">
                    {s.decision?.decision === "REJECTED" ? "loads as NULL" : s.mappedName ? `→ ${s.mappedName}` : "needs a decision"}
                  </span>
                </button>
              </li>
            ))}
          </ol>

          <div className="min-w-0 space-y-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="font-mono text-xs text-muted-foreground">{current.top.source_table}</p>
                <h3 className="mt-0.5">{current.top.source_column}</h3>
                <p className="text-sm text-muted-foreground">
                  {current.top.source_datatype}
                  {unmapped
                    ? " · will load as NULL"
                    : current.mappedName
                      ? ` → ${current.mappedName}`
                      : ` · suggested ${chosen.target_column} (${confidence}%)`}
                </p>
              </div>
              <Badge variant={unmapped ? "destructive" : decided ? "success" : weak ? "warning" : "outline"}>
                {unmapped ? "UNMAPPED · NULL" : decided ? current.decision?.decision : weak ? "No strong match" : `${confidence}%`}
              </Badge>
            </div>

            {error && <p role="alert" className="text-sm text-destructive">{error}</p>}

            {(!decided || modifying) && (
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  disabled={pending}
                  onClick={() => save("REJECTED", justification || "No matching target column; load this source field as NULL.")}
                >
                  Leave unmapped (NULL)
                </Button>
                <Button
                  variant="outline"
                  disabled={pending}
                  onClick={() => (modifying ? save(targetId ? "ALTERNATIVE_TARGET" : "MODIFIED") : setModifying(true))}
                >
                  {modifying ? "Save other target" : "Map to another target"}
                </Button>
                <Button disabled={pending || weak} onClick={() => save("APPROVED")}>
                  {pending ? "Saving…" : `Approve ${chosen.target_column}`}
                </Button>
                {weak && (
                  <Button
                    variant="secondary"
                    disabled={pending}
                    onClick={() => save("APPROVED", justification || `Accepted a weak ${confidence}% match to ${chosen.target_column}.`)}
                  >
                    Approve weak match
                  </Button>
                )}
              </div>
            )}

            {decided && !modifying && (
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={() => setModifying(true)}>Change mapping</Button>
                {!unmapped && (
                  <Button
                    variant="outline"
                    disabled={pending}
                    onClick={() => save("REJECTED", justification || "No matching target column; load this source field as NULL.")}
                  >
                    Switch to NULL
                  </Button>
                )}
              </div>
            )}

            {weak && !decided && (
              <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm">
                None of the target columns is a confident match for <span className="font-medium">{current.top.source_column}</span>.
                Leave it unmapped to load NULL, or pick a different target if this is intentional.
              </div>
            )}

            {(!decided || modifying) && (
              <div className="overflow-hidden rounded-lg border">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b bg-muted/40 text-left text-muted-foreground">
                      <th className="px-3 py-2 font-medium">Target candidate</th>
                      <th className="px-3 py-2 font-medium">Type</th>
                      <th className="px-3 py-2 font-medium">Score</th>
                    </tr>
                  </thead>
                  <tbody>
                    {current.ranked.slice(0, 5).map((c) => (
                      <tr
                        key={c.candidate_id}
                        className={c.candidate_id === chosen.candidate_id ? "bg-accent/40" : "hover:bg-muted/40"}
                      >
                        <td className="px-3 py-2">
                          <button type="button" className="text-left font-medium" onClick={() => { setCandidateId(c.candidate_id); setTransformation(c.transformation ?? ""); }}>
                            {c.target_column}
                          </button>
                        </td>
                        <td className="px-3 py-2 text-muted-foreground">{c.target_datatype}</td>
                        <td className="px-3 py-2">{Math.round(Number(c.final_score) * 100)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {(!decided || modifying) && (
              <div>
                <div className="mb-1 text-sm font-medium">Evidence</div>
                <ul className="grid gap-1 sm:grid-cols-2">
                  {evidence.map((item) => (
                    <li key={item.label} className="flex items-center gap-2 text-sm">
                      {item.ok ? <Check className="h-4 w-4 text-success" /> : <CircleSlash className="h-4 w-4 text-muted-foreground" />}
                      <span className={item.ok ? "" : "text-muted-foreground"}>{item.label}</span>
                    </li>
                  ))}
                </ul>
                {chosen.generated_reason && <p className="mt-3 text-sm text-muted-foreground">{chosen.generated_reason}</p>}
              </div>
            )}

            {modifying && (
              <div className="space-y-2 rounded-lg border p-3">
                <Label htmlFor="alt">Map to a different target</Label>
                <Select id="alt" value={targetId} onChange={(e) => setTargetId(e.target.value)}>
                  <option value="">Keep {chosen.target_column}</option>
                  {data.targets.map((t) => (
                    <option key={t.target_column_id} value={t.target_column_id}>
                      {t.column_name}{t.nullable ? "" : " · required"}
                    </option>
                  ))}
                </Select>
                <Label htmlFor="transformation">Transformation</Label>
                <Input id="transformation" value={transformation} onChange={(e) => setTransformation(e.target.value)} />
                <Label htmlFor="ask">Ask AI (uses profile context)</Label>
                <Textarea
                  id="ask"
                  rows={2}
                  value={ask}
                  onChange={(e) => setAsk(e.target.value)}
                  placeholder="e.g. Cast to DATE with YYYY-MM-DD. Map Y/N to ACTIVE/INACTIVE."
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={pending || !ask.trim()}
                  onClick={() => start(async () => {
                    setError("");
                    const result = await refineTransformation(runId, {
                      prompt: ask,
                      target_column: chosen.target_column,
                      source_column: chosen.source_column,
                      source_table: chosen.source_table,
                      source_datatype: chosen.source_datatype,
                      target_datatype: chosen.target_datatype,
                      current_transformation: transformation,
                    });
                    if (!result.ok) setError(result.error);
                    else {
                      setTransformation(result.data.transformation);
                      if (result.data.rationale) setJustification(result.data.rationale);
                    }
                  })}
                >
                  Propose SQL
                </Button>
              </div>
            )}

            {(!decided || modifying) && (
              <>
                <Label htmlFor="justification">Business justification</Label>
                <Textarea
                  id="justification"
                  rows={2}
                  value={justification}
                  onChange={(e) => setJustification(e.target.value)}
                  placeholder="Required when you change the suggested target"
                />
              </>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
