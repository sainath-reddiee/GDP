"use client";

import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import Link from "next/link";
import { Archive, ArchiveRestore, Trash2 } from "lucide-react";
import type { CleanupResult, Lifecycle, RunStatusFilter, RunSummary } from "@/lib/types";
import { cleanupRuns, setRunsArchived } from "@/app/runs/actions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { STATUS_FILTERS } from "@/lib/run-filters";


export function lifecycleVariant(lifecycle: Lifecycle | string) {
  if (lifecycle === "FAILED") return "destructive" as const;
  if (lifecycle === "COMPLETED") return "success" as const;
  if (lifecycle === "RUNNING") return "warning" as const;
  if (lifecycle === "ARCHIVED") return "outline" as const;
  return "secondary" as const;
}

export function formatAge(minutes: number | null | undefined, createdAt: string): string {
  if (minutes == null) return createdAt.slice(0, 16).replace("T", " ");
  if (minutes < 60) return `${Math.max(minutes, 0)}m`;
  if (minutes < 60 * 24) return `${Math.floor(minutes / 60)}h`;
  return `${Math.floor(minutes / (60 * 24))}d`;
}

function sourceLabel(r: RunSummary) {
  if (r.source_system_name) return r.source_system_name;
  if (r.source_database) return `${r.source_database}.${r.source_schema ?? ""}`;
  return "—";
}

function cleanupMessage(result: CleanupResult): string {
  const parts = [`Deleted ${result.deleted.length} run${result.deleted.length === 1 ? "" : "s"}.`];
  if (result.landing.dropped.length) parts.push(`Dropped ${result.landing.dropped.length} landing table(s).`);
  if (result.landing.kept.length) parts.push(`Kept ${result.landing.kept.length} landing table(s) still used by other runs.`);
  const files = result.workspaces.reduce((n, w) => n + w.files_removed, 0);
  if (files) parts.push(`Removed ${files} workspace file(s).`);
  if (result.skipped.length) parts.push(`Skipped ${result.skipped.length}: ${result.skipped[0].reason}.`);
  const errors = result.landing.errors.length + result.workspaces.reduce((n, w) => n + w.errors.length, 0);
  if (errors) parts.push(`${errors} object(s) could not be removed; see the run audit trail.`);
  parts.push("Staged table profiles were kept.");
  return parts.join(" ");
}

export function RunTable({
  runs, filter = "all", selectable = true,
}: { runs: RunSummary[]; filter?: RunStatusFilter; selectable?: boolean }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [confirming, setConfirming] = useState(false);
  const [dropLanding, setDropLanding] = useState(true);
  const [deleteWorkspaces, setDeleteWorkspaces] = useState(true);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [pending, start] = useTransition();
  const headerBox = useRef<HTMLInputElement>(null);

  const ids = useMemo(() => runs.map((r) => r.run_id), [runs]);
  useEffect(() => {
    setSelected((prev) => prev.filter((id) => ids.includes(id)));
  }, [ids]);

  const allChecked = runs.length > 0 && selected.length === runs.length;
  useEffect(() => {
    if (headerBox.current) headerBox.current.indeterminate = selected.length > 0 && !allChecked;
  }, [selected.length, allChecked]);

  const chosen = runs.filter((r) => selected.includes(r.run_id));
  const anyArchived = chosen.some((r) => r.is_archived);
  const anyLive = chosen.some((r) => !r.is_archived);

  const toggle = (id: string) =>
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  const archive = (archived: boolean) =>
    start(async () => {
      setNotice(null);
      const targets = chosen.filter((r) => r.is_archived !== archived).map((r) => r.run_id);
      const result = await setRunsArchived(targets, archived);
      if (!result.ok) {
        setNotice({ tone: "error", text: result.error });
        return;
      }
      const verb = archived ? "Archived" : "Restored";
      const skipped = result.data.skipped;
      setNotice({
        tone: "ok",
        text: `${verb} ${result.data.changed.length} run(s).${skipped.length ? ` Skipped ${skipped.length}: ${skipped[0].reason}.` : ""}`,
      });
      setSelected([]);
    });

  const remove = () =>
    start(async () => {
      setNotice(null);
      const result = await cleanupRuns(selected, {
        drop_landing_tables: dropLanding, delete_workspaces: deleteWorkspaces,
      });
      setConfirming(false);
      if (!result.ok) {
        setNotice({ tone: "error", text: result.error });
        return;
      }
      setNotice({ tone: "ok", text: cleanupMessage(result.data) });
      setSelected([]);
    });

  return (
    <Card>
      {selectable && (
        <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
          <nav aria-label="Filter by status" className="flex flex-wrap gap-1">
            {STATUS_FILTERS.map((f) => (
              <Link
                key={f.value}
                href={f.value === "all" ? "/runs" : `/runs?status=${f.value}`}
                aria-current={filter === f.value ? "page" : undefined}
                className={cn(
                  "rounded-md px-2.5 py-1 text-xs font-medium",
                  filter === f.value ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted",
                )}
              >
                {f.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground" aria-live="polite">{selected.length} selected</span>
            <Button size="sm" variant="outline" disabled={pending || !anyLive} onClick={() => archive(true)}>
              <Archive className="h-3.5 w-3.5" /> Archive selected
            </Button>
            {anyArchived && (
              <Button size="sm" variant="outline" disabled={pending} onClick={() => archive(false)}>
                <ArchiveRestore className="h-3.5 w-3.5" /> Restore selected
              </Button>
            )}
            <Button size="sm" variant="destructive" disabled={pending || selected.length === 0}
                    onClick={() => setConfirming(true)}>
              <Trash2 className="h-3.5 w-3.5" /> Delete selected
            </Button>
          </div>
        </div>
      )}

      {confirming && (
        <div role="dialog" aria-label="Confirm delete" className="space-y-3 border-b bg-destructive/5 px-4 py-3 text-sm">
          <p className="font-medium">Delete {selected.length} run{selected.length === 1 ? "" : "s"}?</p>
          <p className="text-muted-foreground">
            Runs disappear from every list and can no longer move. Their audit history and the staged table
            profiles stay, so later runs of the same source still reuse them.
          </p>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={dropLanding} onChange={(e) => setDropLanding(e.target.checked)} />
            Drop landing tables (tables another run still uses are kept)
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={deleteWorkspaces} onChange={(e) => setDeleteWorkspaces(e.target.checked)} />
            Delete dbt workspaces (staged files and compile-only projects)
          </label>
          <div className="flex gap-2">
            <Button size="sm" variant="destructive" disabled={pending} onClick={remove}>
              {pending ? "Deleting…" : `Delete ${selected.length} run${selected.length === 1 ? "" : "s"}`}
            </Button>
            <Button size="sm" variant="outline" disabled={pending} onClick={() => setConfirming(false)}>Cancel</Button>
          </div>
        </div>
      )}

      {notice && (
        <p role={notice.tone === "error" ? "alert" : "status"}
           className={cn("border-b px-4 py-2 text-sm", notice.tone === "error" ? "text-destructive" : "text-muted-foreground")}>
          {notice.text}
        </p>
      )}

      <Table>
        <THead>
          <TR>
            {selectable && (
              <TH className="w-10">
                <input
                  ref={headerBox}
                  type="checkbox"
                  aria-label="Select all runs"
                  className="h-4 w-4"
                  checked={allChecked}
                  onChange={() => setSelected(allChecked ? [] : ids)}
                />
              </TH>
            )}
            <TH>Run</TH><TH>Domain</TH><TH>Source</TH><TH>Tables</TH><TH>Current stage</TH><TH>Status</TH><TH>Age</TH>
          </TR>
        </THead>
        <TBody>
          {runs.map((r) => (
            <TR key={r.run_id} className={cn(selected.includes(r.run_id) && "bg-primary/5")}>
              {selectable && (
                <TD>
                  <input type="checkbox" className="h-4 w-4" aria-label={`select ${r.run_name}`}
                         checked={selected.includes(r.run_id)} onChange={() => toggle(r.run_id)} />
                </TD>
              )}
              <TD>
                <Link href={`/runs/${r.run_id}`} className="font-medium text-primary hover:underline">{r.run_name}</Link>
                <div className="font-mono text-[11px] text-muted-foreground">{r.run_id.slice(0, 8)}</div>
              </TD>
              <TD>{r.domain_name ?? "—"}</TD>
              <TD className="font-mono text-xs">{sourceLabel(r)}</TD>
              <TD>{r.table_count || "—"}</TD>
              <TD>
                <div>{r.current_stage ?? "—"}</div>
                <div className="font-mono text-[11px] text-muted-foreground">{r.current_state}</div>
              </TD>
              <TD><Badge variant={lifecycleVariant(r.lifecycle)}>{r.lifecycle}</Badge></TD>
              <TD className="text-muted-foreground" title={r.created_at}>{formatAge(r.age_minutes, r.created_at)}</TD>
            </TR>
          ))}
          {runs.length === 0 && (
            <TR>
              <TD colSpan={selectable ? 8 : 7} className="text-muted-foreground">
                {filter === "archived" ? "No archived runs." : filter === "all" ? "No runs yet." : "No runs match this filter."}
              </TD>
            </TR>
          )}
        </TBody>
      </Table>
    </Card>
  );
}
