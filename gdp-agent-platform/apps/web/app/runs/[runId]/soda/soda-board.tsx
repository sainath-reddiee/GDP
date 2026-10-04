"use client";

import { useState, useTransition } from "react";
import { Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label, Textarea } from "@/components/ui/input";
import { importSoda, saveSodaDecisions } from "../pipeline-actions";

type Check = {
  expectation_id: string;
  target_table: string;
  target_column: string | null;
  check_type: string;
  check_definition?: Record<string, unknown> | string | null;
  severity: string;
  origin: string;
  client_requirement: string | null;
  status: string;
  sodacl?: string;
};

export function SodaBoard({
  runId, checks, yaml, brief, canImport, canReview,
}: {
  runId: string;
  checks: Check[];
  yaml: string;
  brief: { title: string; content: string } | null;
  canImport: boolean;
  canReview: boolean;
}) {
  const [text, setText] = useState("");
  const [filename, setFilename] = useState("");
  const [error, setError] = useState("");
  const [open, setOpen] = useState(checks.find((c) => c.status === "PROPOSED")?.expectation_id ?? checks[0]?.expectation_id ?? "");
  const [justification, setJustification] = useState("");
  const [requirement, setRequirement] = useState("");
  const [pending, start] = useTransition();
  const current = checks.find((c) => c.expectation_id === open) ?? checks[0];

  const readFile = (file: File) => {
    setFilename(file.name);
    const reader = new FileReader();
    reader.onload = () => setText(String(reader.result || ""));
    reader.readAsText(file);
  };

  const upload = () => start(async () => {
    setError("");
    if (!text.trim()) {
      setError("Upload a file or paste the client quality brief first.");
      return;
    }
    const result = await importSoda(runId, { brief: text, filename });
    if (!result.ok) setError(result.error);
  });

  const decide = (decision: "APPROVED" | "REJECTED" | "MODIFIED") => {
    if (!current) return;
    start(async () => {
      setError("");
      const result = await saveSodaDecisions(runId, [{
        expectation_id: current.expectation_id,
        decision,
        justification: justification || undefined,
        requirement: requirement || current.client_requirement || undefined,
      }]);
      if (!result.ok) setError(result.error);
    });
  };

  return (
    <div className="space-y-5">
      {canImport && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Upload the client quality pack — CSV, JSON, Markdown, or pasted notes.
            Cortex extracts SodaCL checks against the approved STTM. Confirm each
            check against the business need. Every decision is stored as a Soda pattern.
          </p>
          <Label htmlFor="soda_file">Client brief</Label>
          <input
            id="soda_file"
            type="file"
            accept=".csv,.json,.txt,.md,.yml,.yaml"
            className="block text-sm"
            onChange={(e) => e.target.files?.[0] && readFile(e.target.files[0])}
          />
          <Textarea
            rows={6}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste requirements, SLAs, or accepted-value lists. Example: EMAIL must be a valid email; CUSTOMER_STATUS in ACTIVE, INACTIVE; load must be fresher than 1 day."
          />
          <Button type="button" disabled={pending} onClick={upload}>
            {pending && <Loader2 className="h-4 w-4 animate-spin" />}
            {pending ? "Extracting checks…" : "Extract checks from brief"}
          </Button>
        </div>
      )}

      {brief && (
        <div className="rounded-lg border bg-muted/40 p-3 text-sm">
          <p className="font-medium">{brief.title}</p>
          <p className="mt-1 whitespace-pre-wrap text-muted-foreground">{brief.content.slice(0, 800)}</p>
        </div>
      )}

      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}

      {current && (
        <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
          <ol className="space-y-1">
            {checks.map((c) => (
              <li key={c.expectation_id}>
                <button
                  type="button"
                  onClick={() => { setOpen(c.expectation_id); setJustification(""); setRequirement(c.client_requirement || ""); }}
                  className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${c.expectation_id === current.expectation_id ? "bg-accent" : "hover:bg-muted"}`}
                >
                  <span className="truncate">{c.target_column ?? c.target_table}</span>
                  <span className="ml-auto text-[10px] uppercase text-muted-foreground">{c.status}</span>
                </button>
              </li>
            ))}
          </ol>
          <div className="space-y-3 rounded-lg border p-4">
            <div className="flex flex-wrap items-center gap-2">
              <h3>{current.check_type}</h3>
              <Badge variant={current.severity === "FAIL" ? "destructive" : "outline"}>{current.severity}</Badge>
              <Badge variant="outline">{current.origin}</Badge>
              <Badge variant={current.status === "APPROVED" ? "success" : current.status === "REJECTED" ? "destructive" : "warning"}>
                {current.status}
              </Badge>
            </div>
            <p className="text-sm">{current.client_requirement || "Derived from the STTM and SodaCL defaults."}</p>
            <p className="font-mono text-xs text-muted-foreground">
              {current.target_table}{current.target_column ? `.${current.target_column}` : ""}
            </p>
            {current.sodacl && (
              <pre className="overflow-auto rounded-md bg-muted/40 p-2 font-mono text-xs leading-relaxed">{current.sodacl}</pre>
            )}
            {canReview && current.status === "PROPOSED" && (
              <>
                <Label htmlFor="soda_need">Business need</Label>
                <Textarea id="soda_need" rows={2} value={requirement} onChange={(e) => setRequirement(e.target.value)} placeholder="What the client asked this check to protect" />
                <Label htmlFor="soda_why">Justification</Label>
                <Textarea id="soda_why" rows={2} value={justification} onChange={(e) => setJustification(e.target.value)} placeholder="Why this check stays, changes, or is dropped" />
                <div className="flex flex-wrap gap-2">
                  <Button type="button" disabled={pending} onClick={() => decide("APPROVED")}>Approve</Button>
                  <Button type="button" variant="outline" disabled={pending} onClick={() => decide("MODIFIED")}>Modify & approve</Button>
                  <Button type="button" variant="destructive" disabled={pending} onClick={() => decide("REJECTED")}>Reject</Button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {yaml && (
        <pre className="overflow-auto rounded-lg border bg-muted/30 p-3 text-xs leading-relaxed">{yaml}</pre>
      )}
    </div>
  );
}
