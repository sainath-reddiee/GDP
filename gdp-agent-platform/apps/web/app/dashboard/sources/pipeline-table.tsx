"use client";

import { useState, useTransition } from "react";
import { KeyRound } from "lucide-react";
import type { Pipeline } from "@/lib/hub-types";
import { StatusPill, ErrorNote } from "@/components/hub/states";
import { Select } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { setPipelineStatus } from "./actions";

const STATUSES: Pipeline["status"][] = ["REGISTERED", "ACTIVE", "PAUSED", "RETIRED"];

export function PipelineTable({ pipelines }: { pipelines: Pipeline[] }) {
  const [pending, start] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [showRetired, setShowRetired] = useState(false);
  const rows = pipelines.filter((p) => showRetired || p.status !== "RETIRED");
  if (!pipelines.length) {
    return <p className="text-sm text-muted-foreground">No external sources yet. Register one and the ingestion pipelines pick it up from the catalog.</p>;
  }
  return (
    <div className="space-y-3">
      {error && <ErrorNote>{error}</ErrorNote>}
      <Table>
        <THead><TR><TH>Source</TH><TH>System</TH><TH>Schedule</TH><TH>Landing table</TH><TH>Pipeline</TH><TH>Status</TH></TR></THead>
        <TBody>
          {rows.map((p) => (
            <TR key={p.source_id}>
              <TD>
                <div className="font-medium">{p.source_name}</div>
                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                  {String(p.connection_metadata?.host ?? p.connection_metadata?.base_url ?? p.connection_metadata?.bucket_path ?? "")}
                  {p.has_secret && <KeyRound className="h-3 w-3" aria-label="secret on record" />}
                </div>
              </TD>
              <TD className="text-xs">{p.source_type}{p.connection_type ? ` · ${p.connection_type}` : ""}</TD>
              <TD className="text-xs">{p.schedule?.frequency === "CRON" ? p.schedule.cron : p.schedule?.frequency?.toLowerCase() ?? "-"}</TD>
              <TD className="font-mono text-xs">{p.target_landing_table ?? "-"}</TD>
              <TD className="text-xs">{p.snaplogic_pipeline_name ?? <span className="text-muted-foreground">not assigned</span>}</TD>
              <TD>
                <div className="flex items-center gap-2">
                  <StatusPill status={p.status} />
                  <Select aria-label={`Status of ${p.source_name}`} className="h-7 w-28 py-0 text-xs" value={p.status} disabled={pending}
                          onChange={(e) => {
                            const next = e.target.value as Pipeline["status"];
                            start(async () => {
                              const r = await setPipelineStatus(p.source_id, next);
                              setError(r.ok ? null : r.error);
                            });
                          }}>
                    {STATUSES.map((s) => <option key={s} value={s}>{s.toLowerCase()}</option>)}
                  </Select>
                </div>
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
      {pipelines.some((p) => p.status === "RETIRED") && (
        <button className="text-xs text-primary hover:underline" onClick={() => setShowRetired((v) => !v)}>
          {showRetired ? "Hide retired" : "Show retired"}
        </button>
      )}
    </div>
  );
}
