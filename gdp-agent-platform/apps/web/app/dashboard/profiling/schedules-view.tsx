"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { AlertTriangle, CalendarClock, Loader2, Play, Plus, Trash2 } from "lucide-react";
import type { ProfileSchedule } from "@/lib/hub-types";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { Modal } from "@/components/hub/overlay";
import { EmptyState, StatusPill } from "@/components/hub/states";
import { useToast } from "@/components/ui/toast";
import { cn } from "@/lib/utils";
import { deleteSchedule, runSchedule, saveSchedule } from "./actions";

const PRESETS = [
  { label: "Daily 05:00", cron: "0 5 * * * UTC" }, { label: "Weekdays 06:00", cron: "0 6 * * 1-5 UTC" },
  { label: "Weekly Sunday", cron: "0 4 * * 0 UTC" }, { label: "Every 6 hours", cron: "0 */6 * * * UTC" },
];
const describe = (cron: string) => PRESETS.find((p) => p.cron === cron.trim())?.label ?? cron;

function ScheduleModal({ schedule, onClose }: { schedule?: ProfileSchedule; onClose: () => void }) {
  const toast = useToast();
  const router = useRouter();
  const [database, setDatabase] = useState(schedule?.database_name ?? "");
  const [schema, setSchema] = useState(schedule?.schema_name ?? "");
  const [tables, setTables] = useState((schedule?.tables ?? []).join(", "));
  const [cron, setCron] = useState(schedule?.cron ?? PRESETS[0].cron);
  const [force, setForce] = useState(schedule?.force_refresh ?? false);
  const [pending, start] = useTransition();
  const save = () => start(async () => {
    const r = await saveSchedule({ schedule_id: schedule?.schedule_id, database: database.trim(), schema: schema.trim(),
      tables: tables.split(",").map((t) => t.trim()).filter(Boolean), cron: cron.trim(), force_refresh: force });
    if (r.ok) { toast("ok", `Profiling scheduled ${describe(cron)} for ${database}.${schema}`); onClose(); router.refresh(); }
    else toast("error", r.error);
  });
  return (
    <Modal open onClose={onClose} title={schedule ? "Change the profiling schedule" : "Schedule profiling"}
           description="A Snowflake task profiles the tables in place on the cron and records which tables drifted since their previous pass."
           footer={<>
             <Button variant="ghost" onClick={onClose}>Cancel</Button>
             <Button onClick={save} disabled={pending || !database.trim() || !schema.trim() || cron.trim().split(/\s+/).length < 5} data-testid="save-schedule">
               {pending && <Loader2 className="h-4 w-4 animate-spin" />} Save schedule
             </Button>
           </>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <div><Label htmlFor="ps-db">Database</Label><Input id="ps-db" className="font-mono" value={database} onChange={(e) => setDatabase(e.target.value.toUpperCase())} placeholder="RAW" /></div>
        <div><Label htmlFor="ps-schema">Schema</Label><Input id="ps-schema" className="font-mono" value={schema} onChange={(e) => setSchema(e.target.value.toUpperCase())} placeholder="CRM" /></div>
        <div className="sm:col-span-2"><Label htmlFor="ps-tables">Tables (comma separated; empty for every table)</Label>
          <Input id="ps-tables" className="font-mono" value={tables} onChange={(e) => setTables(e.target.value.toUpperCase())} placeholder="All tables of the schema" /></div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        {PRESETS.map((p) => (
          <button key={p.cron} type="button" onClick={() => setCron(p.cron)}
                  className={cn("rounded-xl border p-3 text-left text-sm transition", cron === p.cron ? "border-primary/50 bg-primary/5 ring-2 ring-primary/20" : "border-border hover:border-primary/30")}>
            <span className="block font-medium">{p.label}</span><span className="font-mono text-xs text-muted-foreground">{p.cron}</span>
          </button>
        ))}
      </div>
      <Label htmlFor="ps-cron">Cron (five fields and an optional time zone)</Label>
      <Input id="ps-cron" className="font-mono" value={cron} onChange={(e) => setCron(e.target.value)} />
      <label className="mt-3 flex items-start gap-2 text-sm">
        <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} className="mt-1" />
        <span>Profile every run, even when a table did not change<span className="block text-xs text-muted-foreground">Off: a table whose columns, row count and last change are the same as last time reuses its profile, which costs nothing.</span></span>
      </label>
      <p className="mt-2 text-xs text-muted-foreground">The task runs as the platform owner role, which needs SELECT on the tables and USAGE on the warehouse.</p>
    </Modal>
  );
}

/** Profiling on a cron: each schedule is a Snowflake task; the last run says what was profiled and what drifted. */
export function SchedulesView({ schedules, canRun }: { schedules: ProfileSchedule[]; canRun: boolean }) {
  const toast = useToast();
  const router = useRouter();
  const [modal, setModal] = useState<{ schedule?: ProfileSchedule } | null>(null);
  const [pending, start] = useTransition();
  const run = (s: ProfileSchedule) => start(async () => {
    const r = await runSchedule(s.schedule_id);
    if (r.ok) toast(r.data.status === "FAILED" ? "error" : "ok", r.data.status === "FAILED" ? `Profiling failed: ${r.data.error ?? ""}`
      : `${r.data.profiled ?? 0} profiled, ${r.data.cached ?? 0} unchanged, ${r.data.failed ?? 0} failed, ${r.data.drifted ?? 0} drifted`);
    else toast("error", r.error);
    router.refresh();
  });
  const remove = (s: ProfileSchedule) => start(async () => {
    const r = await deleteSchedule(s.schedule_id);
    if (r.ok) { toast("ok", "Schedule removed"); router.refresh(); } else toast("error", r.error);
  });
  return (
    <div className="space-y-3" data-testid="profile-schedules">
      {canRun && <div className="flex justify-end"><Button onClick={() => setModal({})} data-testid="add-schedule"><Plus className="h-4 w-4" /> Schedule profiling</Button></div>}
      {!schedules.length ? (
        <EmptyState icon={CalendarClock} title="No profiling schedules">Profile a schema every day or week, and see which tables drifted since their previous pass.</EmptyState>
      ) : schedules.map((s) => {
        const res = s.last_result;
        return (
          <section key={s.schedule_id} className="surface space-y-2 p-4" data-testid={`schedule-${s.schema_name}`}>
            <div className="flex flex-wrap items-center gap-2">
              <CalendarClock className="h-4 w-4 text-blue-600" />
              <span className="font-mono text-sm font-semibold">{s.database_name}.{s.schema_name}</span>
              <span className="text-xs text-muted-foreground">{s.tables.length ? `${s.tables.length} table${s.tables.length === 1 ? "" : "s"}` : "every table"} · {describe(s.cron)}{s.force_refresh ? " · always re-profile" : ""}</span>
              {s.last_status && <StatusPill status={s.last_status === "OK" ? "PASS" : s.last_status === "PARTIAL" ? "WARN" : "FAIL"} />}
              <span className="ml-auto flex items-center gap-1">
                {canRun && <>
                  <Button size="sm" variant="outline" onClick={() => run(s)} disabled={pending}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />} Run now</Button>
                  <Button size="sm" variant="ghost" onClick={() => setModal({ schedule: s })}>Edit</Button>
                  <Button size="sm" variant="ghost" className="text-destructive" onClick={() => remove(s)} aria-label="Remove schedule"><Trash2 className="h-3.5 w-3.5" /></Button>
                </>}
              </span>
            </div>
            <p className="text-xs text-muted-foreground">
              {s.last_run_at ? <>Last run {s.last_run_at.slice(0, 16)}: {res?.error ? res.error : `${res?.profiled?.length ?? 0} profiled, ${res?.cached?.length ?? 0} unchanged, ${res?.failed?.length ?? 0} failed`}</> : "Not run yet"}
            </p>
            {!!res?.drifted?.length && (
              <div className="rounded-lg border border-amber-200 bg-amber-50/70 p-2 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
                <p className="flex items-center gap-1.5 font-medium"><AlertTriangle className="h-3.5 w-3.5" /> {res.drifted.length} table{res.drifted.length === 1 ? "" : "s"} drifted since the previous pass</p>
                <ul className="mt-1 space-y-0.5">{res.drifted.slice(0, 8).map((d) => (
                  <li key={d.table}><span className="font-mono">{d.table}</span>: {Object.entries(d.columns).slice(0, 4).map(([c, f]) => `${c} (${f.join(", ")})`).join("; ")}</li>
                ))}</ul>
              </div>
            )}
            {!!res?.failed?.length && <p className="text-xs text-destructive">Failed: {res.failed.slice(0, 5).map((f) => `${f.table} (${f.error})`).join("; ")}</p>}
          </section>
        );
      })}
      {modal && <ScheduleModal schedule={modal.schedule} onClose={() => setModal(null)} />}
    </div>
  );
}
