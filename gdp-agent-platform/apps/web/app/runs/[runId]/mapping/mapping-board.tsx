"use client";

import { useMemo, useState, useTransition } from "react";
import { Check } from "lucide-react";
import type { MappingOverview } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { saveMappingDecisions } from "../pipeline-actions";

type Candidate = MappingOverview["candidates"][number];

function evidenceOf(c: Candidate) {
  return [
    { label: "datatype", ok: Number(c.datatype_score) >= 0.5 },
    { label: "cardinality", ok: Number(c.statistical_score) >= 0.5 },
    { label: "semantic similarity", ok: Number(c.semantic_score) >= 0.5 },
    { label: "domain pattern", ok: Number(c.domain_score) >= 0.5 },
    { label: "historical approved mapping", ok: Number(c.historical_score) > 0 },
  ];
}

export function MappingBoard({ runId, data }: { runId: string; data: MappingOverview }) {
  const sources = useMemo(() => {
    const ids = [...new Set(data.candidates.map((c) => c.source_column_id))];
    return ids.map((id) => {
      const ranked = data.candidates.filter((c) => c.source_column_id === id);
      return { id, ranked, top: ranked[0], decision: data.decisions.find((d) => d.source_column_id === id) };
    });
  }, [data]);
  const firstOpen = sources.find((s) => !s.decision)?.id ?? sources[0]?.id ?? "";
  const [selected, setSelected] = useState(firstOpen);
  const current = sources.find((s) => s.id === selected) ?? sources[0];
  const [candidateId, setCandidateId] = useState(current?.top?.candidate_id ?? "");
  const [modifying, setModifying] = useState(false);
  const [targetId, setTargetId] = useState("");
  const [transformation, setTransformation] = useState(current?.top?.transformation ?? "");
  const [justification, setJustification] = useState("");
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
  const confidence = Math.round(Number(chosen.final_score) * 100);

  const save = (decision: "APPROVED" | "MODIFIED" | "REJECTED" | "ALTERNATIVE_TARGET") =>
    start(async () => {
      setError("");
      const result = await saveMappingDecisions(runId, [{
        decision,
        candidate_id: decision === "REJECTED" || decision === "ALTERNATIVE_TARGET" ? undefined : chosen.candidate_id,
        source_column_id: current.id,
        transformation: decision === "MODIFIED" || decision === "ALTERNATIVE_TARGET" ? transformation || undefined : chosen.transformation || undefined,
        business_justification: justification || undefined,
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
          {data.status.decided}/{data.status.source_columns} reviewed
          {data.status.complete ? ". Every column is decided — approve the stage from the overview." : ". Review required."}
          {data.status.missing_required_targets.length > 0 &&
            ` Still needed: ${data.status.missing_required_targets.join(", ")}.`}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="mb-4 flex flex-wrap gap-2">
          {sources.map((s) => (
            <button key={s.id} type="button" onClick={() => open(s.id)}
                    className={`rounded-full border px-2 py-1 text-xs ${s.id === current.id ? "bg-accent" : ""}`}>
              {s.top.source_column}
              {s.decision ? ` · ${s.decision.decision}` : ""}
            </button>
          ))}
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th className="py-2 font-medium">Source column</th>
              <th className="py-2 font-medium">Target candidate</th>
              <th className="py-2 font-medium">Confidence</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="py-3 font-medium">{current.top.source_column.toLowerCase()}</td>
              <td className="py-3">{chosen.target_column.toLowerCase()}</td>
              <td className="py-3">{confidence}%</td>
            </tr>
          </tbody>
        </table>
        <div className="mt-2 text-sm">
          <div className="mb-1 font-medium">Evidence</div>
          <ul className="space-y-1">
            {evidence.map((item) => (
              <li key={item.label} className="flex items-center gap-2">
                {item.ok ? <Check className="h-4 w-4 text-success" /> : <span className="inline-block h-4 w-4" />}
                <span className={item.ok ? "" : "text-muted-foreground"}>{item.label}</span>
              </li>
            ))}
          </ul>
          {chosen.generated_reason && <p className="mt-3 text-muted-foreground">{chosen.generated_reason}</p>}
        </div>
        {modifying && (
          <div className="mt-4 space-y-2">
            <Label>Other candidates</Label>
            <div className="flex flex-wrap gap-2">
              {current.ranked.map((c) => (
                <Button key={c.candidate_id} type="button" size="sm" variant={c.candidate_id === chosen.candidate_id ? "default" : "outline"}
                        onClick={() => { setCandidateId(c.candidate_id); setTransformation(c.transformation ?? ""); }}>
                  {c.target_column} {Math.round(Number(c.final_score) * 100)}%
                </Button>
              ))}
            </div>
            <Label htmlFor="alt">Or a different target</Label>
            <Select id="alt" value={targetId} onChange={(e) => setTargetId(e.target.value)}>
              <option value="">Keep the candidate above</option>
              {data.targets.map((t) => <option key={t.target_column_id} value={t.target_column_id}>{t.column_name}</option>)}
            </Select>
            <Label htmlFor="transformation">Transformation</Label>
            <Input id="transformation" value={transformation} onChange={(e) => setTransformation(e.target.value)} />
          </div>
        )}
        <Label htmlFor="justification">Business justification</Label>
        <Textarea id="justification" rows={3} value={justification} onChange={(e) => setJustification(e.target.value)}
                  placeholder="Required when you modify, reject, or approve a mapping that is not an automatic suggestion" />
        {current.decision && (
          <p className="mt-2 text-sm">
            Current decision <Badge variant={current.decision.decision === "REJECTED" ? "destructive" : "success"}>{current.decision.decision}</Badge>
          </p>
        )}
        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="outline" disabled={pending} onClick={() => save("REJECTED")}>Reject</Button>
          <Button variant="outline" disabled={pending} onClick={() => modifying ? save(targetId ? "ALTERNATIVE_TARGET" : "MODIFIED") : setModifying(true)}>
            {modifying ? "Save modification" : "Modify"}
          </Button>
          <Button disabled={pending} onClick={() => save("APPROVED")}>{pending ? "Saving…" : "Approve & Continue"}</Button>
        </div>
        {error && <p role="alert" className="mt-2 text-sm text-destructive">{error}</p>}
      </CardContent>
    </Card>
  );
}
