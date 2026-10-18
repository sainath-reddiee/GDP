"use client";

import { useCallback, useEffect, useState } from "react";
import { Table2 } from "lucide-react";
import { domainTables, removedItems, type RegisteredTable, type Removed } from "@/app/removal-actions";
import { AddTable } from "./add-table";
import { RequestRemoval, RequestRestore } from "./request-removal";

const when = (v: string | null) => (v ? String(v).slice(0, 10) : "");

/** Every table registered for a domain (whether or not a model owns it). Tables can be added from Snowflake and
 *  removed again through an approval; removed ones are listed with who removed them, why, and a restore. */
export function RegisteredTables({ domainId, canEdit }: { domainId: string; canEdit: boolean }) {
  const [tables, setTables] = useState<RegisteredTable[] | null>(null);
  const [removed, setRemoved] = useState<Removed["targets"]>([]);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    void domainTables(domainId).then((r) => (r.ok ? setTables(r.data.targets) : setError(r.error)));
    void removedItems().then((r) => r.ok && setRemoved(r.data.targets));
  }, [domainId]);
  useEffect(load, [load]);
  const names = new Set((tables ?? []).map((t) => t.domain_name));
  const gone = removed.filter((t) => !t.domain_name || names.size === 0 || names.has(t.domain_name));

  return (
    <section className="surface space-y-3 p-4" data-testid="registered-tables">
      <header className="flex flex-wrap items-center gap-2">
        <Table2 className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-sm font-semibold">Registered tables</h2>
        {tables && <span className="text-xs tabular-nums text-muted-foreground">{tables.length}</span>}
        {canEdit && <AddTable domainId={domainId} onAdded={load}
                              registered={new Set((tables ?? []).map((t) => `${t.target_database}.${t.target_schema}.${t.target_table}`))} />}
      </header>
      <p className="text-xs text-muted-foreground">The tables this domain maps into. Removing one needs another person&apos;s approval and can be undone.</p>
      {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : tables === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : tables.length === 0 ? (
        <p className="text-sm text-muted-foreground">No tables are registered for this domain yet.</p>
      ) : (
        <ul className="divide-y rounded-lg border">
          {tables.map((t) => (
            <li key={t.target_table_id} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2 text-sm">
              <span className="min-w-[8rem] flex-1 basis-0">
                <span className="block truncate font-medium">{t.target_table}</span>
                <span className="block truncate text-xs text-muted-foreground" title={`${t.target_database}.${t.target_schema}`}>
                  {t.target_database}.{t.target_schema} · {t.columns} columns{t.table_type ? ` · ${t.table_type.toLowerCase()}` : ""}
                </span>
              </span>
              {canEdit && <RequestRemoval kind="target" id={t.target_table_id} name={t.target_table} label="Remove from domain" />}
            </li>
          ))}
        </ul>
      )}
      {gone.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-xs text-muted-foreground">Removed tables ({gone.length})</summary>
          <ul className="mt-2 divide-y rounded-lg border">
            {gone.map((t) => (
              <li key={t.target_table_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
                <span className="min-w-0 flex-1">
                  <span className="font-medium text-muted-foreground line-through">{t.target_table}</span>
                  <span className="ml-2 text-xs text-muted-foreground">removed by {t.retired_by} {when(t.retired_at)}{t.retire_reason ? `: ${t.retire_reason}` : ""}</span>
                </span>
                {canEdit && <RequestRestore kind="target" id={t.target_table_id} name={t.target_table} />}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
