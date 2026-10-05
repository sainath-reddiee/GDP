"use client";

import { useMemo, useState, useTransition } from "react";
import { Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label, Textarea } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { applyTransformation, exportSttmCsv, refineTransformation, type TransformProposal } from "../pipeline-actions";

export type SttmLine = {
  sttm_line_id: string;
  source_table: string | null;
  source_column: string | null;
  source_datatype?: string | null;
  target_column: string;
  target_datatype: string;
  mapping_type: string;
  transformation: string | null;
  business_definition: string | null;
  human_approved: boolean;
};

export type ProfileCol = {
  table_name: string;
  column_name: string;
  data_type: string;
  semantic_type: string | null;
  null_percentage: number | null;
  distinct_percentage: number | null;
  generated_description: string | null;
};

export function SttmBoard({
  runId, lines, profiles, canEdit,
}: {
  runId: string;
  lines: SttmLine[];
  profiles: ProfileCol[];
  canEdit: boolean;
}) {
  const [open, setOpen] = useState(lines[0]?.sttm_line_id ?? "");
  const [prompt, setPrompt] = useState("");
  const [proposal, setProposal] = useState<TransformProposal | null>(null);
  const [stagePath, setStagePath] = useState("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const line = useMemo(() => lines.find((l) => l.sttm_line_id === open) ?? lines[0], [lines, open]);
  const profile = useMemo(() => {
    if (!line?.source_column) return undefined;
    return profiles.find(
      (p) => p.column_name.toUpperCase() === line.source_column!.toUpperCase()
        && (!line.source_table || p.table_name.toUpperCase() === line.source_table.toUpperCase()),
    );
  }, [line, profiles]);

  const propose = () => {
    if (!line) return;
    start(async () => {
      setError("");
      const result = await refineTransformation(runId, {
        sttm_line_id: line.sttm_line_id,
        prompt,
        target_column: line.target_column,
        source_column: line.source_column,
        source_table: line.source_table,
        source_datatype: line.source_datatype,
        target_datatype: line.target_datatype,
        current_transformation: line.transformation,
        business_definition: line.business_definition,
      });
      if (!result.ok) {
        setError(result.error);
        return;
      }
      setProposal(result.data);
    });
  };

  const apply = () => {
    if (!line || !proposal) return;
    start(async () => {
      setError("");
      const result = await applyTransformation(runId, {
        sttm_line_id: line.sttm_line_id,
        transformation: proposal.transformation,
        prompt,
        rationale: proposal.rationale,
        dbt_notes: proposal.dbt_notes,
        soda_checks: proposal.soda_checks,
      });
      if (!result.ok) setError(result.error);
      else setProposal(null);
    });
  };

  const exportCsv = () => start(async () => {
    setError("");
    const result = await exportSttmCsv(runId);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setStagePath(result.data.stage_path);
    const blob = new Blob([result.data.csv], { type: "text/csv;charset=utf-8" });
    const href = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = href;
    a.download = `sttm_v${result.data.sttm_version}.csv`;
    a.click();
    URL.revokeObjectURL(href);
  });

  if (!line) return null;

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-3">
          <div>
            <CardTitle>Lines</CardTitle>
            <CardDescription>
              Prompt a change in plain English. Cortex uses the profile, current SQL, and stored transform rules.
              Apply writes the contract and feeds Soda / dbt.
            </CardDescription>
          </div>
          <Button variant="outline" onClick={exportCsv} disabled={pending}>
            {pending && <Loader2 className="h-4 w-4 animate-spin" />}
            Export CSV to stage
          </Button>
        </CardHeader>
        <CardContent className="space-y-3">
          {stagePath && <p className="text-sm text-muted-foreground">Stored at {stagePath}</p>}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <Table>
            <THead><TR><TH>Target</TH><TH>Type</TH><TH>Mapping</TH><TH>Source</TH><TH>Transformation</TH><TH>Approved</TH></TR></THead>
            <TBody>
              {lines.map((l) => (
                <TR key={l.sttm_line_id}>
                  <TD>
                    <button type="button" className="font-medium text-left hover:underline" onClick={() => { setOpen(l.sttm_line_id); setProposal(null); }}>
                      {l.target_column}
                    </button>
                  </TD>
                  <TD className="font-mono text-xs">{l.target_datatype}</TD>
                  <TD><Badge variant={l.sttm_line_id === line.sttm_line_id ? "success" : "outline"}>{l.mapping_type}</Badge></TD>
                  <TD>{l.source_table ? `${l.source_table}.${l.source_column}` : "—"}</TD>
                  <TD className="font-mono text-xs">{l.transformation}</TD>
                  <TD>{l.human_approved ? "yes" : ""}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{line.target_column}</CardTitle>
          <CardDescription>
            {line.source_table ? `${line.source_table}.${line.source_column}` : "derived"} · {line.target_datatype}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {profile && (
            <div className="rounded-lg border bg-muted/30 p-3 text-sm">
              <div className="font-medium">Profile context</div>
              <p className="mt-1 text-muted-foreground">{profile.generated_description || profile.semantic_type || profile.data_type}</p>
              <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
                <span>null {profile.null_percentage ?? "—"}%</span>
                <span>distinct {profile.distinct_percentage ?? "—"}%</span>
                <span>{profile.data_type}</span>
              </div>
            </div>
          )}
          <div>
            <div className="text-sm font-medium">Current SQL</div>
            <pre className="mt-1 overflow-x-auto rounded-md border bg-card p-2 font-mono text-xs">{line.transformation || "(direct)"}</pre>
          </div>
          {canEdit ? (
            <>
              <Label htmlFor="transform-prompt">Ask for a change</Label>
              <Textarea
                id="transform-prompt"
                rows={3}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="e.g. Cast END_DATE as DATE using YYYY-MM-DD. Map Y/N on EXTRACTION_STATUS to ACTIVE/INACTIVE."
              />
              <div className="flex flex-wrap gap-2">
                <Button onClick={propose} disabled={pending || !prompt.trim()}>
                  {pending && <Loader2 className="h-4 w-4 animate-spin" />}
                  Propose SQL
                </Button>
                {proposal && (
                  <Button variant="outline" onClick={apply} disabled={pending}>
                    Apply to contract
                  </Button>
                )}
              </div>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              Reopen STTM review to change transforms. You can still export the CSV.
            </p>
          )}
          {proposal && (
            <div className="space-y-2 rounded-lg border p-3">
              <div className="text-sm font-medium">Proposed SQL</div>
              <pre className="overflow-x-auto font-mono text-xs">{proposal.transformation}</pre>
              {proposal.rationale && <p className="text-sm text-muted-foreground">{proposal.rationale}</p>}
              {proposal.dbt_notes && <p className="text-xs text-muted-foreground">dbt: {proposal.dbt_notes}</p>}
              {(proposal.soda_checks?.length ?? 0) > 0 && (
                <ul className="list-disc pl-5 text-sm">
                  {proposal.soda_checks!.map((c, i) => (
                    <li key={i}>{c.check_type}: {c.requirement}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
