"use client";

import { useEffect, useMemo, useState } from "react";
import { Database, KeyRound, Loader2, ShieldCheck, Trash2 } from "lucide-react";
import { Drawer } from "@/components/hub/overlay";
import { DiffView, type DiffFile } from "@/components/diff-view";
import { cn } from "@/lib/utils";
import { KIND_LABEL, type Change, type ColumnRow, type SttmRow, type Workbench } from "@/lib/copilot-types";
import { StatusPill } from "./thread";

type Preview = { model?: { entity: string; columns: ColumnRow[]; counts: Record<string, number> }; sttm?: { lines: SttmRow[] }; files: DiffFile[] };

function previews(workbench: Workbench): Record<string, Preview> {
  const out: Record<string, Preview> = {};
  const at = (id: string) => (out[id] ??= { files: [] });
  workbench.model.forEach((m) => { at(m.change_id).model = m; });
  workbench.sttm.forEach((s) => { at(s.change_id).sttm = s; });
  [...workbench.dbt, ...workbench.quality].forEach((f) => { at(f.change_id).files.push(...f.files); });
  return out;
}

const ROW: Record<string, string> = {
  added: "bg-emerald-500/[0.07]", changed: "bg-amber-500/[0.08]", removed: "bg-red-500/[0.07] line-through", same: "",
};

/** Review every drafted change, choose what to publish, then approve. */
export function ReviewDrawer({ open, onClose, changes, workbench, focus, onDiscard, onPublish, publishing, progress, busy }: {
  open: boolean; busy: boolean; onClose: () => void; changes: Change[]; workbench: Workbench; focus?: string | null;
  onDiscard: (id: string) => void; onPublish: (ids: string[]) => void; publishing: boolean; progress: Record<string, string>;
}) {
  const shown = changes.filter((c) => c.status !== "DISCARDED");
  const drafts = shown.filter((c) => c.status === "PROPOSED");
  const [selected, setSelected] = useState<string | null>(null);
  const [include, setInclude] = useState<Set<string>>(new Set());
  const [confirm, setConfirm] = useState(false);
  const data = useMemo(() => previews(workbench), [workbench]);

  useEffect(() => {
    if (!open) return;
    setSelected(focus ?? shown[0]?.change_id ?? null);
    setInclude(new Set(drafts.map((c) => c.change_id)));
    setConfirm(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, focus]);

  const current = shown.find((c) => c.change_id === selected) ?? shown[0];
  const p = current ? data[current.change_id] : undefined;
  const chosen = drafts.filter((c) => include.has(c.change_id));
  const calls = chosen.reduce((n, c) => n + (c.operations?.length ?? 1), 0);

  const footer = (
    confirm ? (
          <>
            <p className="min-w-0 flex-1 text-xs text-muted-foreground">
              Publishing runs {calls} API call{calls === 1 ? "" : "s"} as you. Anything your approval rules route to an approver waits for them in Approvals.
            </p>
            <button type="button" onClick={() => setConfirm(false)} disabled={publishing} className="rounded-lg px-3 py-2 text-sm hover:bg-muted">Back</button>
            <button type="button" disabled={publishing || busy || !chosen.length} onClick={() => onPublish(chosen.map((c) => c.change_id))}
                    className="inline-flex h-9 items-center gap-2 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50">
              {publishing ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />} Approve and publish
            </button>
          </>
        ) : (
          <>
            <p className="min-w-0 flex-1 text-xs text-muted-foreground">
              {busy ? "The copilot is still working. Publish when it has finished." : `${chosen.length} of ${drafts.length} drafted change${drafts.length === 1 ? "" : "s"} selected`}
            </p>
            <button type="button" onClick={onClose} className="rounded-lg px-3 py-2 text-sm hover:bg-muted">Close</button>
            <button type="button" disabled={!chosen.length || publishing || busy} onClick={() => setConfirm(true)}
                    className="inline-flex h-9 items-center gap-2 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50">
              <ShieldCheck className="h-4 w-4" /> Publish {chosen.length || ""}
            </button>
          </>
        )
  );

  return (
    <Drawer open={open} onClose={onClose} wide title="Review changes" footer={<div className="flex w-full flex-wrap items-center gap-3">{footer}</div>}
            subtitle="Everything the copilot drafted. Nothing has been saved. Choose what to publish, then approve.">
      <div className="-m-5 grid min-h-[calc(100%+2.5rem)] gap-0 md:grid-cols-[280px_minmax(0,1fr)]">
        <ul className="space-y-1.5 border-b p-3 md:border-b-0 md:border-r">
          {shown.map((c) => {
            const state = progress[c.change_id];
            return (
              <li key={c.change_id}>
                <div className={cn("flex items-start gap-2 rounded-xl border p-2.5 transition", current?.change_id === c.change_id ? "border-primary/40 bg-primary/[0.04]" : "border-transparent hover:bg-muted/60")}>
                  {c.status === "PROPOSED" ? (
                    <input type="checkbox" aria-label={`Publish ${c.title}`} checked={include.has(c.change_id)} disabled={publishing}
                           onChange={(e) => setInclude((s) => { const n = new Set(s); if (e.target.checked) n.add(c.change_id); else n.delete(c.change_id); return n; })}
                           className="mt-1 h-3.5 w-3.5 accent-[hsl(var(--primary))]" />
                  ) : <span className="w-3.5" />}
                  <button type="button" onClick={() => setSelected(c.change_id)} className="min-w-0 flex-1 text-left">
                    <span className="block text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{KIND_LABEL[c.kind] ?? c.kind}</span>
                    <span className="block text-sm leading-snug">{c.title}</span>
                    <span className="mt-1 flex items-center gap-1.5">
                      {state === "running" ? <span className="inline-flex items-center gap-1 text-[11px] text-primary"><Loader2 className="h-3 w-3 animate-spin" /> Publishing</span> : <StatusPill status={c.status} />}
                    </span>
                  </button>
                </div>
              </li>
            );
          })}
        </ul>

        <div className="min-w-0 space-y-4 p-5">
          {!current && <p className="text-sm text-muted-foreground">Nothing drafted.</p>}
          {current && (
            <>
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <h3 className="text-base font-semibold">{current.title}</h3>
                  {current.result?.detail && <p className="mt-1 text-xs text-muted-foreground">{current.result.detail}</p>}
                </div>
                {current.status === "PROPOSED" && !publishing && !busy && (
                  <button type="button" onClick={() => onDiscard(current.change_id)}
                          className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-muted-foreground hover:bg-destructive/10 hover:text-destructive">
                    <Trash2 className="h-3.5 w-3.5" /> Discard
                  </button>
                )}
              </div>
              {p?.model && <ModelPreview entity={p.model.entity} columns={p.model.columns} />}
              {p?.sttm && <SttmPreview lines={p.sttm.lines} />}
              {!!p?.files.length && <DiffView files={p.files} />}
              {!p && <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">No preview for this change.</p>}
              {!!current.operations?.length && (
                <details className="rounded-xl border border-border text-xs">
                  <summary className="cursor-pointer px-3 py-2 text-muted-foreground">What publishing does ({current.operations.length} call{current.operations.length === 1 ? "" : "s"})</summary>
                  <ul className="space-y-1 border-t px-3 py-2 font-mono">
                    {current.operations.map((o, i) => <li key={i}><span className="text-primary">{o.method}</span> {o.path}</li>)}
                  </ul>
                </details>
              )}
            </>
          )}
        </div>
      </div>

    </Drawer>
  );
}

function ModelPreview({ entity, columns }: { entity: string; columns: ColumnRow[] }) {
  const counts = ["added", "changed", "removed"].map((s) => [s, columns.filter((c) => c.state === s).length] as const).filter(([, n]) => n);
  return (
    <div className="overflow-hidden rounded-xl border border-border">
      <div className="flex items-center gap-2 border-b bg-muted/40 px-3 py-2">
        <Database className="h-4 w-4 text-primary" /><span className="font-mono text-sm font-semibold">{entity}</span>
        <span className="ml-auto flex gap-1.5 text-[11px]">{counts.map(([s, n]) => <span key={s} className={cn("rounded-full px-2 py-0.5", ROW[s])}>{n} {s}</span>)}</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-[11px] uppercase tracking-wide text-muted-foreground">
            <tr><th className="px-3 py-2 font-medium">Column</th><th className="px-3 py-2 font-medium">Type</th><th className="px-3 py-2 font-medium">Nullable</th><th className="px-3 py-2 font-medium">Change</th></tr>
          </thead>
          <tbody>
            {columns.map((c) => (
              <tr key={`${c.name}-${c.state}`} className={cn("border-t border-border/60", ROW[c.state])}>
                <td className="px-3 py-1.5 font-mono">{c.is_pk && <KeyRound className="mr-1 inline h-3 w-3 text-amber-500" />}{c.name}</td>
                <td className="px-3 py-1.5 font-mono text-muted-foreground">{c.datatype}</td>
                <td className="px-3 py-1.5">{c.nullable === false ? "no" : "yes"}</td>
                <td className="px-3 py-1.5 text-muted-foreground">{c.state === "same" ? "" : c.state}{c.changed.length ? ` (${c.changed.join(", ")})` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SttmPreview({ lines }: { lines: SttmRow[] }) {
  const show = (v: Record<string, string | null> | null) => v
    ? <><span className="font-mono">{[v.source_table, v.source_column].filter(Boolean).join(".") || "derived"}</span>
        {v.transformation && <code className="mt-0.5 block whitespace-pre-wrap rounded bg-muted px-1.5 py-1 font-mono text-[11px]">{v.transformation}</code>}</>
    : <span className="text-muted-foreground">none</span>;
  return (
    <div className="overflow-x-auto rounded-xl border border-border">
      <table className="w-full text-xs">
        <thead className="text-left text-[11px] uppercase tracking-wide text-muted-foreground">
          <tr><th className="px-3 py-2 font-medium">Target column</th><th className="px-3 py-2 font-medium">Before</th><th className="px-3 py-2 font-medium">After</th></tr>
        </thead>
        <tbody>
          {lines.map((l) => (
            <tr key={l.target_column} className={cn("border-t border-border/60 align-top", ROW[l.state])}>
              <td className="px-3 py-2 font-mono font-medium">{l.target_column}</td>
              <td className="px-3 py-2">{show(l.before)}</td>
              <td className="px-3 py-2">{show(l.after)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
