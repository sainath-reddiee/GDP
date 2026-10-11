"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowRight, ExternalLink, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { requestPreview, type ChangeRequest, type PreviewRow, type RequestPreview } from "../governance-actions";

const MARK: Record<PreviewRow["change"], { label: string; tone: string }> = {
  added: { label: "added", tone: "bg-success/10 text-success" },
  removed: { label: "removed", tone: "bg-destructive/10 text-destructive" },
  changed: { label: "changed", tone: "bg-warning/10 text-warning" },
  set: { label: "", tone: "" },
};

function Value({ text, gone }: { text: string | null; gone?: boolean }) {
  if (text == null) return <span className="text-muted-foreground">(none)</span>;
  return <span className={cn("whitespace-pre-wrap break-words font-mono text-[11px]", gone && "line-through decoration-destructive/60")}>{text}</span>;
}

/** Rows as label, before and after; a "set" row (a requested value with nothing to compare) shows the value only. */
export function PreviewRows({ rows, testid }: { rows: PreviewRow[]; testid?: string }) {
  const compare = rows.some((r) => r.change !== "set");
  return (
    <div className="overflow-x-auto rounded-lg border bg-card" data-testid={testid}>
      <table className="w-full text-left text-xs">
        <thead className="bg-muted/40 text-[10px] uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-3 py-1.5 font-medium">Field</th>
            {compare && <th className="px-3 py-1.5 font-medium">Before</th>}
            <th className="px-3 py-1.5 font-medium">{compare ? "After" : "Value"}</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map((r, i) => (
            <tr key={`${r.label}-${i}`} className="align-top">
              <td className="w-1/4 px-3 py-1.5">
                <span className="font-medium">{r.label}</span>
                {MARK[r.change].label && (
                  <span className={cn("ml-1.5 rounded px-1 py-0.5 text-[9px] font-semibold uppercase", MARK[r.change].tone)}>
                    {MARK[r.change].label}
                  </span>
                )}
                {r.note && <span className="mt-0.5 block text-[11px] text-muted-foreground">{r.note}</span>}
              </td>
              {compare && <td className="px-3 py-1.5">{r.change === "set" || r.change === "added" ? <span className="text-muted-foreground">(none)</span>
                : <Value text={r.before} gone={r.change === "removed"} />}</td>}
              <td className="px-3 py-1.5">
                {r.change === "removed" ? <span className="text-muted-foreground">(removed)</span> : (
                  <span className="inline-flex items-start gap-1">
                    {compare && r.change === "changed" && <ArrowRight className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground" />}
                    <Value text={r.after} />
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** What a change request changes, loaded when its row opens; the stored call stays available, collapsed. */
export function RequestPreviewPanel({ r }: { r: ChangeRequest }) {
  const [preview, setPreview] = useState<RequestPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    requestPreview(r.request_id).then((res) => {
      if (!live) return;
      if (res.ok) setPreview(res.data); else setError(res.error);
    }).catch(() => live && setError("The preview could not be loaded."));
    return () => { live = false; };
  }, [r.request_id, r.status]);

  return (
    <div className="space-y-3" data-testid="request-preview">
      <div>
        <p className="mb-1 font-semibold">{r.status === "PENDING" ? "What will change" : "What was asked"}</p>
        {!preview && !error && (
          <p className="flex items-center gap-2 text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Reading the current state…</p>
        )}
        {error && <p role="alert" className="text-destructive">{error}</p>}
        {preview && (
          <div className="space-y-2">
            <p className="flex flex-wrap items-center gap-2">
              <span>{preview.headline}</span>
              {preview.link && (
                <Link href={preview.link.href} className="inline-flex items-center gap-1 text-primary hover:underline">
                  {preview.link.label}<ExternalLink className="h-3 w-3" />
                </Link>
              )}
            </p>
            {preview.warnings.map((w) => (
              <p key={w} role="alert" className="flex items-start gap-2 rounded-lg bg-warning/10 px-3 py-2 text-warning">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /><span>{w}</span>
              </p>
            ))}
            {preview.sections.map((s) => (
              <div key={s.title}>
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{s.title}</p>
                <PreviewRows rows={s.rows} testid={`preview-${s.title}`} />
              </div>
            ))}
          </div>
        )}
      </div>
      {r.result && (
        <div>
          <p className="mb-1 font-semibold">Result ({r.result.status_code})</p>
          {preview?.result.length ? <PreviewRows rows={preview.result} testid="preview-result" />
            : <p className="text-muted-foreground">{r.result.status_code && r.result.status_code < 400 ? "Applied." : "Applying it failed."}</p>}
        </div>
      )}
      <details className="text-[11px]">
        <summary className="cursor-pointer text-muted-foreground hover:text-foreground">Stored call</summary>
        <p className="mt-1 font-mono">{r.method} {r.path}</p>
        <pre className="mt-1 max-h-64 overflow-auto rounded-lg bg-slate-950 p-3 font-mono text-[11px] text-slate-100">
          {r.payload == null ? "(no body)" : JSON.stringify(r.payload, null, 2)}
        </pre>
        {r.result && (
          <pre className="mt-1 max-h-48 overflow-auto rounded-lg bg-muted p-3 font-mono text-[11px]">{JSON.stringify(r.result.body, null, 2)}</pre>
        )}
      </details>
    </div>
  );
}
