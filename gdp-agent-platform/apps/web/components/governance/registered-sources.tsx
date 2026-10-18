"use client";

import { useEffect, useState } from "react";
import { Database } from "lucide-react";
import type { SourceOverviewItem } from "@/lib/types";
import { removedItems, type Removed } from "@/app/removal-actions";
import { RequestRemoval, RequestRestore } from "./request-removal";

const when = (v: string | null) => (v ? String(v).slice(0, 10) : "");

/** The source systems registered with the platform, each removable through an approval, and the removed ones with a
 *  restore. Landed data and run history are kept either way. */
export function RegisteredSources({ sources, canEdit }: { sources: SourceOverviewItem[]; canEdit: boolean }) {
  const [removed, setRemoved] = useState<Removed["sources"]>([]);
  useEffect(() => { void removedItems().then((r) => r.ok && setRemoved(r.data.sources)); }, []);
  return (
    <section className="surface space-y-3 p-4" data-testid="registered-sources">
      <header className="flex items-center gap-2">
        <Database className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-sm font-semibold">Registered sources</h2>
        <span className="text-xs text-muted-foreground">removing one needs another person&apos;s approval; landed data is kept</span>
      </header>
      {sources.length === 0 ? <p className="text-sm text-muted-foreground">No sources are registered yet.</p> : (
        <ul className="divide-y rounded-lg border">
          {sources.map((s) => (
            <li key={s.source_system_id} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2 text-sm">
              <span className="min-w-[8rem] flex-1 basis-0">
                <span className="block truncate font-medium">{s.source_system_name}</span>
                <span className="block truncate text-xs text-muted-foreground">
                  {s.source_type.toLowerCase().replace(/_/g, " ")}{s.database_name ? ` · ${s.database_name}.${s.schema_name}` : ""}{s.runs ? ` · ${s.runs} runs` : ""}
                </span>
              </span>
              {canEdit && <RequestRemoval kind="source" id={s.source_system_id} name={s.source_system_name} />}
            </li>
          ))}
        </ul>
      )}
      {removed.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-xs text-muted-foreground">Removed sources ({removed.length})</summary>
          <ul className="mt-2 divide-y rounded-lg border">
            {removed.map((s) => (
              <li key={s.source_system_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
                <span className="min-w-0 flex-1">
                  <span className="font-medium text-muted-foreground line-through">{s.source_system_name}</span>
                  <span className="ml-2 text-xs text-muted-foreground">removed by {s.retired_by} {when(s.retired_at)}{s.retire_reason ? `: ${s.retire_reason}` : ""}</span>
                </span>
                {canEdit && <RequestRestore kind="source" id={s.source_system_id} name={s.source_system_name} />}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
