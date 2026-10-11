"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, useTransition } from "react";
import {
  CalendarClock, ChevronRight, ClipboardCheck, Columns3, Loader2, MoreHorizontal, Pause, Pencil, Play, Plus, Search, Sparkles,
  Table2, Trash2, Wand2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { DqCheck, DqHistory, DqTable } from "@/lib/store-types";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { Modal } from "@/components/hub/overlay";
import { EmptyState, StatusPill, explain } from "@/components/hub/states";
import { CheckEditor, cleanDefinition, type Draft } from "@/components/quality/check-editor";
import { Spark } from "@/components/quality/quality-shared";
import { useDismiss } from "@/components/shell/use-dismiss";
import { useToast } from "@/components/ui/toast";
import { loadHistory, previewCheck, runTable, saveCheck, seedChecks, setCheckStatus, setSchedule, type Parsed } from "./actions";

const ORIGIN: Record<DqCheck["origin"], string> = { PROFILE: "profile", RUN: "run", USER: "user" };
const PRESETS = [
  { label: "Hourly", cron: "0 * * * * UTC" }, { label: "Daily 06:00", cron: "0 6 * * * UTC" },
  { label: "Weekdays 07:30", cron: "30 7 * * 1-5 UTC" }, { label: "Weekly Monday", cron: "0 6 * * 1 UTC" },
];

function describeCron(cron: string) {
  return PRESETS.find((p) => p.cron === cron.trim())?.label ?? cron;
}

// ---------------------------------------------------------------- add / edit a check

function CheckModal({ table, check, columns, onClose }: { table: string; check?: DqCheck; columns: string[]; onClose: () => void }) {
  const toast = useToast();
  const [tableName, setTableName] = useState(table);
  const [mode, setMode] = useState<"dsl" | "guided">(check ? "guided" : "dsl");
  const [dsl, setDsl] = useState("");
  const [draft, setDraft] = useState<Draft>(check
    ? { target_column: check.column_name ?? "", severity: check.severity, requirement: "", definition: { ...check.check_definition } }
    : { target_column: "", severity: "WARN", requirement: "", definition: { kind: "not_null" } });
  const [preview, setPreview] = useState<Parsed | null>(null);
  const [pending, start] = useTransition();

  const body = () => mode === "dsl"
    ? { check_id: check?.check_id ?? null, table: tableName.trim(), severity: draft.severity, dsl }
    : { check_id: check?.check_id ?? null, table: tableName.trim(), column: draft.target_column || null, severity: draft.severity,
        requirement: draft.requirement || null, definition: cleanDefinition(draft.definition) };

  // live preview of the rendered SodaCL
  useEffect(() => {
    if (mode === "dsl" && dsl.trim().length < 4) { setPreview(null); return; }
    const t = setTimeout(() => { void previewCheck(body()).then((r) => setPreview(r.ok ? r.data : { ok: false, problems: [explain(r.error).message] })); }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, dsl, draft, tableName]);

  const save = () => start(async () => {
    const r = await saveCheck(body());
    if (r.ok) { toast("ok", check ? "Check updated" : `Check added to ${tableName}`); onClose(); }
    else if (r.pending) { toast("info", r.error); onClose(); }
    else toast("error", explain(r.error).message);
  });

  return (
    <Modal open onClose={onClose} wide title={check ? "Edit check" : "Add a data quality check"}
           description="Type one line of SodaCL, or build the check with the guided editor. The neutral definition is stored and run; SodaCL is rendered from it."
           footer={<>
             <Button variant="ghost" onClick={onClose}>Cancel</Button>
             <Button onClick={save} disabled={pending || tableName.split(".").length !== 3 || (preview != null && !preview.ok)} data-testid="save-check">
               {pending && <Loader2 className="h-4 w-4 animate-spin" />} {check ? "Save check" : "Add check"}
             </Button>
           </>}>
      <div className="space-y-4">
        <div><Label htmlFor="ck-table">Table</Label>
          <Input id="ck-table" className="font-mono" value={tableName} disabled={!!check} placeholder="DATABASE.SCHEMA.TABLE"
                 onChange={(e) => setTableName(e.target.value.toUpperCase())} /></div>
        {!check && (
          <div role="radiogroup" aria-label="Editor" className="inline-flex rounded-xl bg-muted p-1">
            {([["dsl", "One line SodaCL", Wand2], ["guided", "Guided editor", Columns3]] as const).map(([id, label, Icon]) => (
              <button key={id} type="button" role="radio" aria-checked={mode === id} onClick={() => setMode(id)}
                      className={cn("flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition", mode === id ? "bg-card text-foreground shadow-xs" : "text-muted-foreground")}>
                <Icon className="h-3.5 w-3.5" /> {label}
              </button>
            ))}
          </div>
        )}
        {mode === "dsl" ? (
          <div className="space-y-2">
            <div className="grid gap-3 sm:grid-cols-[1fr_160px]">
              <div><Label htmlFor="ck-dsl">SodaCL</Label>
                <Input id="ck-dsl" autoFocus className="font-mono" value={dsl} onChange={(e) => setDsl(e.target.value)}
                       placeholder="missing_count(EMAIL) = 0" /></div>
              <div><Label htmlFor="ck-sev">Severity</Label>
                <Select id="ck-sev" value={draft.severity} onChange={(e) => setDraft({ ...draft, severity: e.target.value as Draft["severity"] })}>
                  <option value="WARN">Warn (alerts)</option><option value="FAIL">Fail (blocks)</option>
                </Select></div>
            </div>
            <div className="flex flex-wrap gap-1.5 text-xs">
              {["row_count > 0", "missing_count(EMAIL) = 0", "duplicate_count(CUSTOMER_ID) = 0", "missing_percent(PHONE) < 5%",
                "invalid_percent(EMAIL) < 1%", "freshness(UPDATED_AT) < 1d", "min(AMOUNT) >= 0"].map((ex) => (
                <button key={ex} type="button" onClick={() => setDsl(ex)} className="rounded-md border border-border bg-muted/50 px-2 py-0.5 font-mono text-muted-foreground transition hover:border-primary/40 hover:text-foreground">{ex}</button>
              ))}
            </div>
          </div>
        ) : (
          <CheckEditor draft={draft} onChange={setDraft} columns={columns} kindLocked={!!check} />
        )}
        <div className={cn("rounded-xl border p-3", preview && !preview.ok ? "border-destructive/30 bg-destructive/5" : "border-border/70 bg-slate-950")}>
          {!preview ? <p className="text-xs text-slate-400">The rendered SodaCL appears here.</p>
            : preview.ok ? <pre className="overflow-x-auto font-mono text-xs text-emerald-300">{preview.check.soda_dsl}</pre>
            : <ul className="list-disc pl-4 text-sm text-destructive">{preview.problems.map((p) => <li key={p}>{p}</li>)}</ul>}
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------- schedule, seed

function ScheduleModal({ table, current, onClose }: { table: string; current: string | null; onClose: () => void }) {
  const toast = useToast();
  const [cron, setCron] = useState(current ?? PRESETS[1].cron);
  const [pending, start] = useTransition();
  const apply = (value: string | null) => start(async () => {
    const r = await setSchedule(table, value);
    if (r.ok) { toast("ok", value ? `Scheduled ${describeCron(value)}` : "Schedule removed"); onClose(); }
    else if (r.pending) { toast("info", r.error); onClose(); }
    else toast("error", explain(r.error).message);
  });
  return (
    <Modal open onClose={onClose} title="Schedule scans" description={<span className="font-mono text-xs">{table}</span>}
           footer={<>
             {current && <Button variant="ghost" className="mr-auto text-destructive" disabled={pending} onClick={() => apply(null)}>Remove schedule</Button>}
             <Button variant="ghost" onClick={onClose}>Cancel</Button>
             <Button disabled={pending || cron.trim().split(/\s+/).length < 5} onClick={() => apply(cron)}>
               {pending && <Loader2 className="h-4 w-4 animate-spin" />} Save schedule
             </Button>
           </>}>
      <div className="grid grid-cols-2 gap-2">
        {PRESETS.map((p) => (
          <button key={p.cron} type="button" onClick={() => setCron(p.cron)}
                  className={cn("rounded-xl border p-3 text-left text-sm transition", cron === p.cron ? "border-primary/50 bg-primary/5 ring-2 ring-primary/20" : "border-border hover:border-primary/30")}>
            <span className="block font-medium">{p.label}</span>
            <span className="font-mono text-xs text-muted-foreground">{p.cron}</span>
          </button>
        ))}
      </div>
      <Label htmlFor="cron">Cron (five fields and an optional time zone)</Label>
      <Input id="cron" className="font-mono" value={cron} onChange={(e) => setCron(e.target.value)} />
      <p className="mt-2 text-xs text-muted-foreground">
        Creates a Snowflake task that calls QUALITY.RUN_TABLE_SCAN on this table. The task runs as the platform owner role, which needs SELECT on the table.
      </p>
    </Modal>
  );
}

function SeedModal({ onClose, tables }: { onClose: () => void; tables: string[] }) {
  const toast = useToast();
  const [mode, setMode] = useState<"table" | "run">("table");
  const [value, setValue] = useState("");
  const [pending, start] = useTransition();
  const seed = () => start(async () => {
    const r = await seedChecks(mode === "table" ? { table: value.trim() } : { run_id: value.trim() });
    if (r.ok) { toast("ok", `${r.data.added} check${r.data.added === 1 ? "" : "s"} added to ${r.data.table}${r.data.skipped ? `, ${r.data.skipped} already there` : ""}`); onClose(); }
    else if (r.pending) { toast("info", r.error); onClose(); }
    else toast("error", explain(r.error).message);
  });
  return (
    <Modal open onClose={onClose} title="Seed checks"
           description="Start a table's catalog from what the platform already knows about it."
           footer={<>
             <Button variant="ghost" onClick={onClose}>Cancel</Button>
             <Button disabled={pending || value.trim().length < 5} onClick={seed}>{pending && <Loader2 className="h-4 w-4 animate-spin" />} Seed checks</Button>
           </>}>
      <div className="grid grid-cols-2 gap-2">
        {([["table", "From a profile", "Not-null, uniqueness, valid values and ranges the latest profile suggests."],
           ["run", "From a run", "A run's approved checks, placed on its built target table."]] as const).map(([id, label, hint]) => (
          <button key={id} type="button" onClick={() => { setMode(id); setValue(""); }}
                  className={cn("rounded-xl border p-3 text-left transition", mode === id ? "border-primary/50 bg-primary/5 ring-2 ring-primary/20" : "border-border hover:border-primary/30")}>
            <span className="block text-sm font-medium">{label}</span>
            <span className="block text-xs text-muted-foreground">{hint}</span>
          </button>
        ))}
      </div>
      <Label htmlFor="seed-v">{mode === "table" ? "Profiled table" : "Run id"}</Label>
      <Input id="seed-v" className="font-mono" list={mode === "table" ? "seed-tables" : undefined} value={value}
             onChange={(e) => setValue(mode === "table" ? e.target.value.toUpperCase() : e.target.value)}
             placeholder={mode === "table" ? "DATABASE.SCHEMA.TABLE" : "00000000-0000-0000-0000-000000000000"} />
      <datalist id="seed-tables">{tables.map((t) => <option key={t} value={t} />)}</datalist>
    </Modal>
  );
}

// ---------------------------------------------------------------- tree

function CheckMenu({ check, onEdit, canEdit }: { check: DqCheck; onEdit: () => void; canEdit: boolean }) {
  const { open, setOpen, ref } = useDismiss();
  const toast = useToast();
  const [pending, start] = useTransition();
  const status = (s: "ACTIVE" | "PAUSED" | "RETIRED") => start(async () => {
    setOpen(false);
    const r = await setCheckStatus(check.check_id, s);
    if (r.ok) toast("ok", s === "ACTIVE" ? "Check resumed" : s === "PAUSED" ? "Check paused" : "Check retired");
    else toast(r.pending ? "info" : "error", r.pending ? r.error : explain(r.error).message);
  });
  if (!canEdit) return null;
  return (
    <div ref={ref} className="relative">
      <button type="button" aria-label="Check actions" onClick={() => setOpen(!open)} className="grid h-7 w-7 place-items-center rounded-lg text-muted-foreground hover:bg-muted">
        {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <MoreHorizontal className="h-4 w-4" />}
      </button>
      {open && (
        <div className="popover absolute right-0 top-8 z-30 w-40 animate-pop-in p-1 text-sm">
          <button type="button" onClick={() => { setOpen(false); onEdit(); }} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 hover:bg-muted"><Pencil className="h-3.5 w-3.5" /> Edit</button>
          {check.status === "ACTIVE"
            ? <button type="button" onClick={() => status("PAUSED")} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 hover:bg-muted"><Pause className="h-3.5 w-3.5" /> Pause</button>
            : <button type="button" onClick={() => status("ACTIVE")} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 hover:bg-muted"><Play className="h-3.5 w-3.5" /> Resume</button>}
          {check.status !== "RETIRED" && (
            <button type="button" onClick={() => status("RETIRED")} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-destructive hover:bg-destructive/10"><Trash2 className="h-3.5 w-3.5" /> Retire</button>
          )}
        </div>
      )}
    </div>
  );
}

function TableBlock({ table, defaultOpen, canEdit, canRun, onAdd, onEdit, onSchedule }: {
  table: DqTable; defaultOpen: boolean; canEdit: boolean; canRun: boolean;
  onAdd: () => void; onEdit: (c: DqCheck) => void; onSchedule: () => void;
}) {
  const toast = useToast();
  const router = useRouter();
  const [open, setOpen] = useState(defaultOpen);
  const [history, setHistory] = useState<DqHistory | null>(null);
  const [pending, start] = useTransition();
  useEffect(() => {
    if (open && !history) void loadHistory(table.table).then((r) => setHistory(r.ok ? r.data : { scans: [], checks: {} }));
  }, [open, history, table.table]);
  const run = () => start(async () => {
    const r = await runTable(table.table);
    if (r.ok) {
      const bad = r.data.failed + r.data.errors;
      toast(bad ? "error" : "ok", `${r.data.passed} of ${r.data.checks} passed${r.data.warned ? `, ${r.data.warned} warned` : ""}${bad ? `, ${bad} failed` : ""} on ${table.table}`);
      setHistory(null); setOpen(true); router.refresh();
    } else toast(r.pending ? "info" : "error", r.pending ? r.error : explain(r.error).message);
  });
  const c = table.counts;
  const failing = (c.FAIL ?? 0) + (c.ERROR ?? 0);
  const [db, schema, name] = table.table.split(".");
  return (
    <section className="surface overflow-hidden" data-testid={`dq-table-${name}`}>
      <div className="flex flex-wrap items-center gap-3 p-4">
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex min-w-0 flex-1 items-center gap-3 text-left">
          <ChevronRight className={cn("h-4 w-4 shrink-0 text-muted-foreground transition", open && "rotate-90")} />
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-sky-50 text-sky-600 ring-1 ring-inset ring-sky-100"><Table2 className="h-4 w-4" /></span>
          <span className="min-w-0">
            <span className="block truncate font-semibold">{name ?? table.table}</span>
            <span className="block truncate font-mono text-xs text-muted-foreground">{db}.{schema}</span>
          </span>
        </button>
        <div className="flex items-center gap-1.5 text-xs">
          <span className="rounded-full bg-muted px-2 py-0.5 tabular-nums">{table.checks} checks</span>
          {(c.PASS ?? 0) > 0 && <StatusPill status="PASS" className="tabular-nums" />}
          {(c.WARN ?? 0) > 0 && <span className="rounded-full bg-amber-50 px-2 py-0.5 text-amber-800 ring-1 ring-inset ring-amber-200">{c.WARN} warn</span>}
          {failing > 0 && <span className="rounded-full bg-rose-50 px-2 py-0.5 text-rose-700 ring-1 ring-inset ring-rose-200">{failing} failing</span>}
          {(c.NEVER_RUN ?? 0) > 0 && <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-600 ring-1 ring-inset ring-slate-200">{c.NEVER_RUN} never run</span>}
        </div>
        <div className="flex w-32 items-center gap-2" title={table.pass_rate == null ? "No results yet" : `${table.pass_rate}% passing`}>
          <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
            <span className={cn("block h-full rounded-full", table.pass_rate == null ? "" : table.pass_rate >= 95 ? "bg-emerald-500" : table.pass_rate >= 80 ? "bg-amber-500" : "bg-rose-500")}
                  style={{ width: `${table.pass_rate ?? 0}%` }} />
          </span>
          <span className="w-9 text-right text-xs tabular-nums text-muted-foreground">{table.pass_rate == null ? "-" : `${Math.round(table.pass_rate)}%`}</span>
        </div>
        <button type="button" onClick={onSchedule} disabled={!canEdit}
                className={cn("flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs transition", table.schedule ? "bg-violet-50 text-violet-700 ring-1 ring-inset ring-violet-100" : "text-muted-foreground hover:bg-muted", !canEdit && "cursor-default")}>
          <CalendarClock className="h-3.5 w-3.5" /> {table.schedule ? describeCron(table.schedule.cron) : "Not scheduled"}
        </button>
        <div className="flex items-center gap-1">
          {canRun && <Button size="sm" variant="outline" onClick={run} disabled={pending} data-testid="run-table">
            {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />} Run now
          </Button>}
          {canEdit && <Button size="sm" variant="ghost" onClick={onAdd} aria-label={`Add a check to ${table.table}`}><Plus className="h-4 w-4" /></Button>}
        </div>
      </div>
      {open && (
        <div className="border-t border-border/70">
          {table.columns.map((col) => (
            <div key={col.column ?? "__table"} className="border-b border-border/50 last:border-0">
              <div className="flex items-center gap-2 bg-muted/40 px-4 py-1.5 pl-12 text-xs font-semibold text-muted-foreground">
                {col.column ? <><Columns3 className="h-3.5 w-3.5" /> <span className="font-mono">{col.column}</span></> : <><Table2 className="h-3.5 w-3.5" /> Table level</>}
                <span className="font-normal">· {col.checks.length}</span>
              </div>
              {col.checks.map((ck) => (
                <div key={ck.check_id} className={cn("grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-4 py-2 pl-12 text-sm md:grid-cols-[minmax(0,1fr)_90px_80px_110px_90px_auto]",
                  ck.status !== "ACTIVE" && "opacity-60")}>
                  <div className="min-w-0">
                    <code className="block truncate font-mono text-[12.5px]" title={ck.soda_dsl}>{ck.headline || ck.soda_dsl}</code>
                    <span className="text-[11px] text-muted-foreground">{ck.check_type.toLowerCase().replace(/_/g, " ")} · from {ORIGIN[ck.origin]}{ck.status !== "ACTIVE" ? ` · ${ck.status.toLowerCase()}` : ""}</span>
                  </div>
                  <span className={cn("hidden text-xs font-medium md:block", ck.severity === "FAIL" ? "text-rose-600" : "text-amber-600")}>{ck.severity === "FAIL" ? "blocks" : "alerts"}</span>
                  <span className="hidden md:block">{history?.checks[ck.check_id] ? <Spark points={history.checks[ck.check_id]} /> : null}</span>
                  <span className="hidden md:block">{ck.last_run_status ? <StatusPill status={ck.last_run_status} /> : <StatusPill status="NEVER_RUN" />}</span>
                  <span className="hidden text-right text-xs tabular-nums text-muted-foreground md:block" title={ck.last_run_at ?? undefined}>
                    {ck.last_measured != null ? Number(ck.last_measured).toLocaleString() : ""}{ck.last_run_at ? <span className="block">{ck.last_run_at.slice(5, 16)}</span> : null}
                  </span>
                  <CheckMenu check={ck} canEdit={canEdit} onEdit={() => onEdit(ck)} />
                </div>
              ))}
            </div>
          ))}
          {history && history.scans.length > 0 && (
            <p className="bg-muted/30 px-4 py-2 pl-12 text-xs text-muted-foreground">
              Last scan {history.scans[0].started_at.slice(0, 16)} · health {history.scans[0].health ?? "-"} · {history.scans[0].rows_scanned?.toLocaleString() ?? "?"} rows · {history.scans.length} recent scans
            </p>
          )}
        </div>
      )}
    </section>
  );
}

/** Table, then column, then checks; each table can be run now or scheduled. */
export function DqStoreView({ tables, initialTable, status, openAdd, canEdit, canRun }: {
  tables: DqTable[]; initialTable: string; status: string; openAdd?: boolean; canEdit: boolean; canRun: boolean;
}) {
  const router = useRouter();
  const [q, setQ] = useState(initialTable);
  const [modal, setModal] = useState<{ kind: "check"; table: string; check?: DqCheck } | { kind: "schedule"; table: string; cron: string | null } | { kind: "seed" } | null>(
    openAdd && canEdit ? { kind: "check", table: initialTable.split(".").length === 3 ? initialTable.toUpperCase() : "" } : null);
  const term = q.trim().toLowerCase();
  const shown = useMemo(() => tables.filter((t) => !term || t.table.toLowerCase().includes(term)
    || t.columns.some((c) => (c.column ?? "").toLowerCase().includes(term) || c.checks.some((k) => k.soda_dsl.toLowerCase().includes(term)))), [tables, term]);
  const columnsOf = (table: string) => tables.find((t) => t.table === table)?.columns.map((c) => c.column).filter((c): c is string => !!c) ?? [];

  return (
    <div className="space-y-4">
      <div className="glass flex flex-wrap items-center gap-3 p-3">
        <div className="relative min-w-[220px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter tables, columns or SodaCL" className="pl-9" aria-label="Filter checks" />
        </div>
        <Select aria-label="Status" className="w-auto" value={status}
                onChange={(e) => router.push(`/dashboard/dq-store${e.target.value ? `?status=${e.target.value}` : ""}`)}>
          <option value="">Active and paused</option><option value="ACTIVE">Active</option><option value="PAUSED">Paused</option><option value="RETIRED">Retired</option>
        </Select>
        {canEdit && <>
          <Button variant="outline" onClick={() => setModal({ kind: "seed" })}><Sparkles className="h-4 w-4" /> Seed checks</Button>
          <Button onClick={() => setModal({ kind: "check", table: shown.length === 1 ? shown[0].table : "" })} data-testid="add-check"><Plus className="h-4 w-4" /> Add check</Button>
        </>}
      </div>

      {!tables.length ? (
        <EmptyState icon={ClipboardCheck} title="No data quality checks yet"
                    action={canEdit ? <Button onClick={() => setModal({ kind: "seed" })}><Sparkles className="h-4 w-4" /> Seed from a profile</Button> : undefined}>
          Seed a table&apos;s checks from its profile, promote the approved checks of a run, or add checks one at a time in SodaCL.
        </EmptyState>
      ) : !shown.length ? (
        <p className="py-10 text-center text-sm text-muted-foreground">No table, column or check matches &quot;{q}&quot;.</p>
      ) : (
        <div className="space-y-3">
          {shown.map((t, i) => (
            <TableBlock key={t.table} table={t} canEdit={canEdit} canRun={canRun}
                        defaultOpen={(!!initialTable && t.table.toLowerCase().includes(initialTable.toLowerCase())) || (!initialTable && i < 2)}
                        onAdd={() => setModal({ kind: "check", table: t.table })}
                        onEdit={(check) => setModal({ kind: "check", table: t.table, check })}
                        onSchedule={() => setModal({ kind: "schedule", table: t.table, cron: t.schedule?.cron ?? null })} />
          ))}
        </div>
      )}

      {modal?.kind === "check" && <CheckModal table={modal.table} check={modal.check} columns={columnsOf(modal.table)} onClose={() => setModal(null)} />}
      {modal?.kind === "schedule" && <ScheduleModal table={modal.table} current={modal.cron} onClose={() => setModal(null)} />}
      {modal?.kind === "seed" && <SeedModal tables={tables.map((t) => t.table)} onClose={() => setModal(null)} />}
    </div>
  );
}
