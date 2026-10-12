"use client";

import { useState, useTransition } from "react";
import { Database, KeyRound, Trash2 } from "lucide-react";
import { Modal } from "@/components/hub/overlay";
import { DiffView } from "@/components/diff-view";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Change, ColumnRow, SttmRow, Workbench } from "@/lib/copilot-types";
import { discardCopilotChange } from "../actions";

const TABS = [
  { key: "model", label: "Model" },
  { key: "sttm", label: "STTM" },
  { key: "dbt", label: "dbt and DDL" },
  { key: "quality", label: "QA and DQ" },
] as const;
type Tab = (typeof TABS)[number]["key"];

const STATE: Record<string, string> = {
  added: "bg-emerald-50 dark:bg-emerald-950/40", changed: "bg-amber-50 dark:bg-amber-950/40",
  removed: "bg-red-50 line-through dark:bg-red-950/40", same: "",
};

/** Everything drafted in a conversation, by kind: the table model, mapping lines, rendered files and checks. */
export function DiffWorkbench({ open, onClose, sessionId, workbench, changes, onChanged }: {
  open: boolean; onClose: () => void; sessionId: string; workbench: Workbench; changes: Change[]; onChanged: () => void;
}) {
  const first = TABS.find((t) => workbench[t.key].length)?.key ?? "model";
  const [tab, setTab] = useState<Tab>(first);
  const [pending, start] = useTransition();
  const [error, setError] = useState("");
  const discard = (changeId: string) => start(async () => {
    setError("");
    const r = await discardCopilotChange(sessionId, changeId);
    if (!r.ok) setError(r.error); else onChanged();
  });
  const live = new Set(changes.map((c) => c.change_id));
  return (
    <Modal open={open} onClose={onClose} wide title="Review drafted changes"
           description="What would change if you publish. Nothing has been saved yet.">
      <div role="tablist" aria-label="Change kind" className="mb-4 flex flex-wrap gap-1 border-b">
        {TABS.map((t) => (
          <button key={t.key} role="tab" type="button" aria-selected={tab === t.key} onClick={() => setTab(t.key)}
                  className={cn("-mb-px border-b-2 px-3 py-2 text-sm", tab === t.key ? "border-primary font-medium text-primary" : "border-transparent text-muted-foreground")}>
            {t.label} <span className="text-xs text-muted-foreground">{workbench[t.key].length || ""}</span>
          </button>
        ))}
      </div>
      {error && <p role="alert" className="mb-3 text-xs text-destructive">{error}</p>}
      <div className="space-y-5">
        {!workbench[tab].length && <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">Nothing drafted here.</p>}
        {workbench[tab].map((entry) => (
          <section key={`${tab}-${entry.change_id}`} className="space-y-2">
            <header className="flex items-center gap-2">
              <h3 className="min-w-0 flex-1 truncate text-sm font-semibold">{entry.title}</h3>
              {live.has(entry.change_id) && (
                <Button variant="ghost" size="sm" disabled={pending} onClick={() => discard(entry.change_id)}>
                  <Trash2 className="h-3.5 w-3.5" /> Discard
                </Button>
              )}
            </header>
            {tab === "model" && "columns" in entry && <ModelCard entity={entry.entity} columns={entry.columns} />}
            {tab === "sttm" && "lines" in entry && <SttmGrid lines={entry.lines} />}
            {(tab === "dbt" || tab === "quality") && "files" in entry && <DiffView files={entry.files} />}
          </section>
        ))}
      </div>
    </Modal>
  );
}

function ModelCard({ entity, columns }: { entity: string; columns: ColumnRow[] }) {
  return (
    <div className="overflow-hidden rounded-xl border">
      <div className="flex items-center gap-2 border-b bg-muted/40 px-3 py-2 text-xs font-semibold">
        <Database className="h-3.5 w-3.5" /> <span className="font-mono">{entity}</span>
      </div>
      <table className="w-full text-xs">
        <thead className="text-left text-muted-foreground"><tr><th className="px-3 py-1.5">Column</th><th className="px-3 py-1.5">Type</th><th className="px-3 py-1.5">Null</th><th className="px-3 py-1.5">Change</th></tr></thead>
        <tbody>
          {columns.map((c) => (
            <tr key={`${c.name}-${c.state}`} className={cn("border-t", STATE[c.state])}>
              <td className="px-3 py-1.5 font-mono">{c.is_pk && <KeyRound className="mr-1 inline h-3 w-3 text-amber-600" />}{c.name}</td>
              <td className="px-3 py-1.5 font-mono">{c.datatype}</td>
              <td className="px-3 py-1.5">{c.nullable === false ? "not null" : ""}</td>
              <td className="px-3 py-1.5 text-muted-foreground">{c.state === "same" ? "" : c.state}{c.changed.length ? `: ${c.changed.join(", ")}` : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SttmGrid({ lines }: { lines: SttmRow[] }) {
  const cell = (v: Record<string, string | null> | null) => v
    ? <span className="font-mono">{[v.source_table, v.source_column].filter(Boolean).join(".") || "derived"}{v.transformation ? ` → ${v.transformation}` : ""}</span>
    : <span className="text-muted-foreground">none</span>;
  return (
    <div className="overflow-x-auto rounded-xl border">
      <table className="w-full text-xs">
        <thead className="text-left text-muted-foreground"><tr><th className="px-3 py-1.5">Target column</th><th className="px-3 py-1.5">Before</th><th className="px-3 py-1.5">After</th></tr></thead>
        <tbody>
          {lines.map((l) => (
            <tr key={l.target_column} className={cn("border-t align-top", STATE[l.state])}>
              <td className="px-3 py-1.5 font-mono">{l.target_column}</td>
              <td className="px-3 py-1.5">{cell(l.before)}</td>
              <td className="px-3 py-1.5">{cell(l.after)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
