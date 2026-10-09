"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import { CheckCheck, Download, FileUp, Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Label, Textarea } from "@/components/ui/input";
import { approveStage } from "../actions";
import { importSoda, saveSodaDecisions } from "../pipeline-actions";

function download(content: string, name: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/** Client quality brief upload, kept behind a button so the checks stay the focus. */
export function BriefImport({ runId, brief }: { runId: string; brief: { title: string; content: string } | null }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [filename, setFilename] = useState("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const submit = () => start(async () => {
    setError("");
    if (!text.trim()) { setError("Upload a file or paste the brief first."); return; }
    const r = await importSoda(runId, { brief: text, filename });
    if (!r.ok) { setError(r.error); return; }
    setOpen(false);
    setText("");
  });

  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)} title={brief ? `Current brief: ${brief.title}` : undefined}>
        <FileUp className="h-3.5 w-3.5" />Import client brief
      </Button>
      {open && (
        <div className="fixed inset-0 z-50 grid place-items-center p-4">
          <button type="button" aria-label="Close" className="absolute inset-0 bg-black/30" onClick={() => setOpen(false)} />
          <div role="dialog" aria-label="Import client brief" className="relative w-full max-w-xl space-y-3 rounded-2xl border bg-background p-5 shadow-2xl">
            <div className="flex items-center justify-between">
              <h3 className="text-base font-semibold">Import client quality brief</h3>
              <button type="button" aria-label="Close" onClick={() => setOpen(false)} className="rounded p-1 hover:bg-muted"><X className="h-4 w-4" /></button>
            </div>
            <p className="text-xs text-muted-foreground">CSV, JSON, Markdown or pasted notes. Cortex turns them into proposed checks against the STTM.</p>
            {brief && <p className="rounded-md bg-muted/40 px-2 py-1 text-xs">Current: <span className="font-medium">{brief.title}</span></p>}
            <Label htmlFor="brief_file">File</Label>
            <input id="brief_file" type="file" accept=".csv,.json,.txt,.md,.yml,.yaml" className="block text-sm"
                   onChange={(e) => {
                     const file = e.target.files?.[0];
                     if (!file) return;
                     setFilename(file.name);
                     const reader = new FileReader();
                     reader.onload = () => setText(String(reader.result || ""));
                     reader.readAsText(file);
                   }} />
            <Textarea rows={6} value={text} onChange={(e) => setText(e.target.value)}
                      placeholder="EMAIL must be a valid email; CUSTOMER_STATUS in ACTIVE, INACTIVE; load fresher than 1 day." />
            {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
              <Button disabled={pending} onClick={submit}>{pending && <Loader2 className="h-4 w-4 animate-spin" />}{pending ? "Extracting…" : "Extract checks"}</Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export function DownloadMenu({ yaml, gxSuite }: { yaml: string; gxSuite?: Record<string, unknown> | null }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  if (!yaml) return null;
  return (
    <div ref={ref} className="relative">
      <Button size="sm" variant="ghost" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-label="Downloads">
        <Download className="h-3.5 w-3.5" />
      </Button>
      {open && (
        <div className="absolute right-0 z-40 mt-1 w-56 rounded-lg border bg-card p-1 text-sm shadow-lg">
          <button type="button" className="w-full rounded px-2 py-1.5 text-left hover:bg-muted"
                  onClick={() => { download(yaml, "data-quality.soda.yml", "text/yaml"); setOpen(false); }}>SodaCL (checks.yml)</button>
          {gxSuite && (
            <button type="button" className="w-full rounded px-2 py-1.5 text-left hover:bg-muted"
                    onClick={() => { download(JSON.stringify(gxSuite, null, 2), "data-quality.gx-suite.json", "application/json"); setOpen(false); }}>
              Great Expectations suite
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export function ApproveRemaining({ runId, ids }: { runId: string; ids: string[] }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("Checks match the approved STTM and the client quality need.");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  if (!ids.length) return null;
  return (
    <div className="relative">
      <Button size="sm" variant="outline" onClick={() => setOpen((v) => !v)}><CheckCheck className="h-3.5 w-3.5" />Approve all {ids.length}</Button>
      {open && (
        <div className="absolute right-0 z-40 mt-1 w-80 space-y-2 rounded-lg border bg-card p-3 shadow-lg">
          <Label htmlFor="mass_note">Justification for {ids.length} checks</Label>
          <Input id="mass_note" value={note} onChange={(e) => setNote(e.target.value)} />
          {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
          <Button size="sm" disabled={pending || note.trim().length < 8} onClick={() => start(async () => {
            setError("");
            const r = await saveSodaDecisions(runId, ids.map((id) => ({ expectation_id: id, decision: "APPROVED", justification: note.trim() })));
            if (!r.ok) setError(r.error); else setOpen(false);
          })}>{pending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Approve</Button>
        </div>
      )}
    </div>
  );
}

/** One slim line for the pack gate: approved, or ready to approve. Nothing while checks are still open. */
export function PackGate({ runId, currentState, complete }: { runId: string; currentState: string; complete: boolean }) {
  const [note, setNote] = useState("Data Quality checks match the STTM and the client quality need.");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  // the approved state is a pill in the page header; this only asks for the approval itself
  if (currentState !== "SODA_REVIEW" || !complete) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2">
      <span className="text-sm font-medium">Every check is decided.</span>
      <Input className="min-w-[16rem] flex-1" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Approval note" />
      <Button size="sm" disabled={pending || !note.trim()} onClick={() => start(async () => {
        setError("");
        const r = await approveStage(runId, "SODA_APPROVED", note.trim());
        if (!r.ok) setError(r.error);
      })}>{pending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Approve pack</Button>
      {error && <p role="alert" className="basis-full text-xs text-destructive">{error}</p>}
    </div>
  );
}
