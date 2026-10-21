"use client";

import { useState } from "react";
import { ChevronDown, Database } from "lucide-react";
import { cn } from "@/lib/utils";
import type { SnapLogicPreview as Preview } from "@/lib/hub-types";

const show = (v: unknown) => (v === null || v === undefined || v === "" ? <span className="text-muted-foreground">NULL</span>
  : typeof v === "boolean" ? (v ? "TRUE" : "FALSE") : String(v));

function Columns({ row }: { row: Record<string, unknown> }) {
  return (
    <dl className="grid gap-x-4 gap-y-0.5 text-[11px] sm:grid-cols-[230px_1fr]">
      {Object.entries(row).map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="font-mono text-muted-foreground">{k}</dt>
          <dd className="min-w-0 truncate font-mono" title={v == null ? "" : String(v)}>{show(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

/** The exact rows an onboarding writes to SnapLogic's config tables: one master row for the source, one detail row
 *  per table. Ids are assigned by those tables when the rows are written. */
export function SnapLogicRows({ preview }: { preview: Preview }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <section className="space-y-3 rounded-xl border p-3" data-testid="snaplogic-preview">
      <header className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <Database className="h-4 w-4 self-center text-muted-foreground" />
        <h4 className="text-sm font-semibold">SnapLogic rows to be written</h4>
        <span className="text-xs text-muted-foreground">in the same transaction as the configuration; ids are assigned by the tables</span>
      </header>
      <div className="space-y-1.5">
        <p className="font-mono text-[11px] text-muted-foreground">{preview.tables.master} (1 row; reused if this source already has one)</p>
        <div className="rounded-lg bg-muted/30 p-2.5"><Columns row={preview.master} /></div>
      </div>
      <div className="space-y-1.5">
        <p className="font-mono text-[11px] text-muted-foreground">{preview.tables.detail} ({preview.details.length} row{preview.details.length === 1 ? "" : "s"})</p>
        <ul className="divide-y rounded-lg border">
          {preview.details.map((d, i) => (
            <li key={i}>
              <button type="button" onClick={() => setOpen(open === i ? null : i)} aria-expanded={open === i}
                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:bg-muted/30">
                <span className="font-mono font-medium">{String(d.LANDING_TABLE ?? `table ${i + 1}`)}</span>
                <span className="truncate text-muted-foreground">{String(d.SOURCE_TABLE_SQL ?? "")} · {String(d.CHILD_PIPELINE_NAME ?? "")}</span>
                <ChevronDown className={cn("ml-auto h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform", open === i && "rotate-180")} />
              </button>
              {open === i && <div className="bg-muted/20 px-3 py-2.5"><Columns row={d} /></div>}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
