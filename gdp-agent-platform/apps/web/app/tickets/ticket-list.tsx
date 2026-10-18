"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlarmClock, Bot, ChevronDown, ExternalLink, Filter, Link2, Loader2, RefreshCw, Save, Search, Share2, X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { StatusPill } from "@/components/jira/jira-controls";
import { ConnectJira } from "../qa/qa-shared";
import {
  deleteTicketView, listTicketViews, listTickets, moveToSprint, saveTicketView, sprintChoices,
  type SprintChoice, type Ticket, type TicketFilters, type TicketList as TicketListData, type TicketView,
} from "./actions";

export const DEFAULT_FILTERS: TicketFilters = { source: "all", status: "open", assignee: "any" };
const KINDS = ["DATA_BUG", "CODE_BUG", "DATA_QUALITY", "PIPELINE", "QUESTION"];
const PRIORITY_TONE: Record<string, string> = {
  P1: "bg-rose-500/15 text-rose-600 dark:text-rose-400", P2: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  P3: "bg-sky-500/15 text-sky-700 dark:text-sky-400", P4: "bg-muted text-muted-foreground",
};
const select = "h-8 rounded-lg border border-border bg-background px-2 text-xs outline-none focus-visible:border-primary/50";

/** The filters as the page's own query string (shareable links; empty values left out). */
export function filtersToQuery(f: TicketFilters) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) {
    if (v === undefined || v === "" || v === false) continue;
    if (k in DEFAULT_FILTERS && DEFAULT_FILTERS[k as keyof TicketFilters] === v) continue;
    p.set(k, v === true ? "true" : String(v));
  }
  return p;
}

function Segmented<T extends string>({ label, value, options, onChange }: {
  label: string; value: T; options: [T, string][]; onChange: (v: T) => void;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg border border-border bg-background p-0.5">
      {options.map(([v, text]) => (
        <button key={v} type="button" role="radio" aria-checked={value === v} onClick={() => onChange(v)}
                className={cn("rounded-md px-2.5 py-1 text-xs", value === v ? "bg-primary/10 font-medium text-primary" : "text-muted-foreground hover:text-foreground")}>
          {text}
        </button>
      ))}
    </div>
  );
}

function Toggle({ label, on, onChange, icon: Icon }: { label: string; on: boolean; onChange: (v: boolean) => void; icon: typeof Filter }) {
  return (
    <button type="button" aria-pressed={on} onClick={() => onChange(!on)}
            className={cn("inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-xs",
              on ? "border-primary/40 bg-primary/10 font-medium text-primary" : "border-border text-muted-foreground hover:text-foreground")}>
      <Icon className="h-3.5 w-3.5" />{label}
    </button>
  );
}

/** Jira issues and platform cases in one list: filters (kept in the URL), saved views, and moving Jira issues to a sprint. */
export function TicketList({ initial, domains, canJiraWrite, openTicket }: {
  initial: TicketFilters; domains: { domain_id: string; name: string }[]; canJiraWrite: boolean;
  openTicket: (key: string) => void;
}) {
  const [f, setF] = useState<TicketFilters>({ ...DEFAULT_FILTERS, ...initial });
  const [text, setText] = useState(initial.q ?? "");
  const [more, setMore] = useState(Boolean(initial.project || initial.sprint || initial.type || initial.kind || initial.domain_id
    || initial.updated_from || initial.updated_to));
  const [data, setData] = useState<TicketListData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [views, setViews] = useState<TicketView[]>([]);
  const [viewsDeployed, setViewsDeployed] = useState(true);
  const [saving, setSaving] = useState<{ name: string; shared: boolean } | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sprints, setSprints] = useState<SprintChoice[] | null>(null);
  const [target, setTarget] = useState("");
  const [moving, setMoving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const seq = useRef(0);

  const set = (patch: Partial<TicketFilters>) => setF((x) => ({ ...x, ...patch }));
  const load = async (filters: TicketFilters) => {
    const n = ++seq.current;
    setLoading(true); setError(null);
    const r = await listTickets(filters).catch(() => null);
    if (n !== seq.current) return;   // a newer search replaced this one
    setLoading(false);
    if (!r) { setError("Tickets could not be loaded. Try again."); return; }
    if (!r.ok) { setError(r.error); return; }
    setData(r.data);
    setSelected(new Set());
  };

  useEffect(() => {
    const p = filtersToQuery(f);
    p.set("tab", "list");
    window.history.replaceState(window.history.state, "", `/tickets?${p}`);
    void load(f);
  }, [f]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {   // the search box applies after a short pause
    const t = setTimeout(() => setF((x) => (x.q ?? "") === text.trim() ? x : { ...x, q: text.trim() || undefined }), 350);
    return () => clearTimeout(t);
  }, [text]);

  const refreshViews = () => void listTicketViews().then((r) => {
    if (r.ok) { setViews(r.data.views); setViewsDeployed(r.data.deployed); }
  });
  useEffect(refreshViews, []);

  const needSprints = more || selected.size > 0;
  useEffect(() => {
    if (!needSprints || sprints || !data?.jira.connected) return;
    void sprintChoices(f.project ?? "").then((r) => setSprints(r.ok ? r.data.sprints : []));
  }, [needSprints, sprints, data?.jira.connected, f.project]);

  const rows = data?.tickets ?? [];
  const jiraKeys = useMemo(() => rows.filter((r) => r.sources.includes("JIRA") && selected.has(r.id)).map((r) => r.key), [rows, selected]);
  const allSelectable = rows.filter((r) => r.sources.includes("JIRA"));
  const allChecked = allSelectable.length > 0 && allSelectable.every((r) => selected.has(r.id));

  const move = async () => {
    if (!jiraKeys.length || !target) return;
    setMoving(true); setNotice(null);
    const r = await moveToSprint(jiraKeys, target === "backlog" ? null : Number(target)).catch(() => null);
    setMoving(false);
    if (!r?.ok) { setNotice(r ? r.error : "The tickets could not be moved."); return; }
    const where = target === "backlog" ? "the backlog" : sprints?.find((s) => String(s.id) === target)?.name ?? "the sprint";
    setNotice(`${r.data.moved.length} moved to ${where}` + (r.data.failed.length ? `; not moved: ${r.data.failed.map((x) => `${x.key} (${x.error})`).join(", ")}` : "."));
    void load(f);
  };

  const applyView = (v: TicketView) => { setText(v.filters.q ?? ""); setF({ ...DEFAULT_FILTERS, ...v.filters }); };
  const save = async () => {
    if (!saving?.name.trim()) return;
    const r = await saveTicketView(saving.name.trim(), f, saving.shared).catch(() => null);
    if (!r?.ok) { setNotice(r ? r.error : "The view could not be saved."); return; }
    setSaving(null); setNotice(`Saved the view ${r.data.name}.`); refreshViews();
  };
  const remove = async (v: TicketView) => {
    const r = await deleteTicketView(v.view_id).catch(() => null);
    if (r?.ok) refreshViews(); else setNotice(r ? r.error : "The view could not be deleted.");
  };

  const filtered = Object.keys(filtersToQuery(f)).length > 0;
  return (
    <div className="space-y-3">
      {/* saved views */}
      <div className="flex flex-wrap items-center gap-1.5" aria-label="Saved views">
        <button type="button" onClick={() => { setText(""); setF({ ...DEFAULT_FILTERS }); }}
                className={cn("h-7 rounded-full border px-3 text-xs", !filtered ? "border-primary/40 bg-primary/10 font-medium text-primary" : "border-border hover:bg-muted")}>
          All open
        </button>
        <button type="button" onClick={() => { setText(""); setF({ ...DEFAULT_FILTERS, assignee: "me" }); }}
                className={cn("h-7 rounded-full border px-3 text-xs", f.assignee === "me" && Object.keys(filtersToQuery(f)).length === 1 ? "border-primary/40 bg-primary/10 font-medium text-primary" : "border-border hover:bg-muted")}>
          Assigned to me
        </button>
        {views.map((v) => (
          <span key={v.view_id} className="group inline-flex h-7 items-center rounded-full border border-border text-xs">
            <button type="button" onClick={() => applyView(v)} className="flex items-center gap-1 pl-3 pr-1.5 hover:text-primary" title={v.mine ? undefined : `Shared by ${v.owner}`}>
              {v.shared && <Share2 className="h-3 w-3 text-muted-foreground" />}{v.name}
            </button>
            {v.mine && <button type="button" onClick={() => void remove(v)} aria-label={`Delete view ${v.name}`} className="pr-2 text-muted-foreground opacity-0 hover:text-destructive group-hover:opacity-100"><X className="h-3 w-3" /></button>}
          </span>
        ))}
        {saving ? (
          <span className="inline-flex items-center gap-1.5">
            <Input aria-label="View name" autoFocus value={saving.name} onChange={(e) => setSaving({ ...saving, name: e.target.value })}
                   onKeyDown={(e) => { if (e.key === "Enter") void save(); if (e.key === "Escape") setSaving(null); }}
                   placeholder="View name" className="h-7 w-40 text-xs" />
            <label className="flex items-center gap-1 text-xs text-muted-foreground"><input type="checkbox" checked={saving.shared} onChange={(e) => setSaving({ ...saving, shared: e.target.checked })} /> Share</label>
            <button type="button" onClick={() => void save()} disabled={!saving.name.trim()} className="h-7 rounded-lg bg-primary px-2.5 text-xs font-medium text-primary-foreground disabled:opacity-50">Save</button>
            <button type="button" onClick={() => setSaving(null)} className="h-7 rounded-lg px-2 text-xs hover:bg-muted">Cancel</button>
          </span>
        ) : (
          <button type="button" onClick={() => setSaving({ name: "", shared: false })} disabled={!viewsDeployed || !filtered}
                  title={!viewsDeployed ? "Saved views need migration V050" : !filtered ? "Set a filter first" : undefined}
                  className="inline-flex h-7 items-center gap-1 rounded-full px-2.5 text-xs text-muted-foreground hover:bg-muted disabled:opacity-40">
            <Save className="h-3 w-3" /> Save view
          </button>
        )}
      </div>

      {/* filters */}
      <div className="surface space-y-2 p-3" aria-label="Filters">
        <div className="flex flex-wrap items-center gap-2">
          <label className="relative min-w-[14rem] flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-muted-foreground" />
            <Input aria-label="Search tickets" value={text} onChange={(e) => setText(e.target.value)} placeholder="Search by key or text" className="h-8 pl-8 text-sm" />
          </label>
          <Segmented label="Source" value={f.source ?? "all"} onChange={(v) => set({ source: v })} options={[["all", "All"], ["jira", "Jira"], ["case", "Cases"]]} />
          <Segmented label="Status" value={f.status ?? "open"} onChange={(v) => set({ status: v })} options={[["open", "Open"], ["done", "Done"], ["any", "Any"]]} />
          <Segmented label="Assignee" value={f.assignee ?? "any"} onChange={(v) => set({ assignee: v })} options={[["any", "Anyone"], ["me", "Me"], ["unassigned", "Unassigned"]]} />
          <div role="group" aria-label="Priority" className="inline-flex gap-1">
            {["P1", "P2", "P3", "P4"].map((p) => {
              const on = (f.priority ?? "").split(",").includes(p);
              return (
                <button key={p} type="button" aria-pressed={on} onClick={() => {
                  const list = (f.priority ?? "").split(",").filter(Boolean);
                  set({ priority: (on ? list.filter((x) => x !== p) : [...list, p]).sort().join(",") || undefined });
                }} className={cn("h-8 rounded-lg border px-2 text-xs", on ? cn("border-transparent font-semibold", PRIORITY_TONE[p]) : "border-border text-muted-foreground hover:text-foreground")}>{p}</button>
              );
            })}
          </div>
          <button type="button" onClick={() => setMore(!more)} aria-expanded={more}
                  className="inline-flex h-8 items-center gap-1 rounded-lg px-2 text-xs text-muted-foreground hover:bg-muted">
            <Filter className="h-3.5 w-3.5" /> More filters <ChevronDown className={cn("h-3 w-3 transition", more && "rotate-180")} />
          </button>
        </div>
        {more && (
          <div className="flex flex-wrap items-center gap-2 border-t pt-2">
            <select aria-label="Sprint" value={f.sprint ?? ""} onChange={(e) => set({ sprint: e.target.value || undefined })} className={select}>
              <option value="">Any sprint</option><option value="active">Active sprint</option><option value="future">Future sprints</option>
              <option value="none">No sprint (backlog)</option>
              {sprints?.map((s) => <option key={s.id} value={String(s.id)}>{s.name} ({s.state})</option>)}
            </select>
            <Input aria-label="Jira project" value={f.project ?? ""} onChange={(e) => set({ project: e.target.value.toUpperCase() || undefined })} placeholder="Project key" className="h-8 w-28 text-xs" />
            <Input aria-label="Jira issue types" value={f.type ?? ""} onChange={(e) => set({ type: e.target.value || undefined })} placeholder="Types: Bug, Story" className="h-8 w-36 text-xs" />
            <select aria-label="Case kind" value={f.kind ?? ""} onChange={(e) => set({ kind: e.target.value || undefined })} className={select}>
              <option value="">Any case kind</option>
              {KINDS.map((k) => <option key={k} value={k}>{k.replace(/_/g, " ").toLowerCase()}</option>)}
            </select>
            <select aria-label="Domain" value={f.domain_id ?? ""} onChange={(e) => set({ domain_id: e.target.value || undefined })} className={select}>
              <option value="">Any domain</option>
              {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.name}</option>)}
            </select>
            <label className="flex items-center gap-1 text-xs text-muted-foreground">Updated
              <Input type="date" aria-label="Updated from" value={f.updated_from ?? ""} onChange={(e) => set({ updated_from: e.target.value || undefined })} className="h-8 w-36 text-xs" />
              to <Input type="date" aria-label="Updated to" value={f.updated_to ?? ""} onChange={(e) => set({ updated_to: e.target.value || undefined })} className="h-8 w-36 text-xs" />
            </label>
            <Toggle label="SLA breached" icon={AlarmClock} on={!!f.sla_breached} onChange={(v) => set({ sla_breached: v || undefined })} />
            <Toggle label="Linked to data" icon={Link2} on={!!f.linked} onChange={(v) => set({ linked: v || undefined })} />
            <Toggle label="In the Copilot" icon={Bot} on={!!f.copilot} onChange={(v) => set({ copilot: v || undefined })} />
            {filtered && <button type="button" onClick={() => { setText(""); setF({ ...DEFAULT_FILTERS }); }} className="ml-auto h-8 rounded-lg px-2 text-xs text-muted-foreground hover:bg-muted">Clear filters</button>}
          </div>
        )}
      </div>

      {/* where each source stands */}
      {data?.jira.connect && <ConnectJira returnTo="/tickets" text="Connect your Jira account to see your Jira tickets here with your cases." />}
      {data && !data.jira.connect && data.jira.reason && !data.jira.skipped && <p className="text-xs text-muted-foreground">Jira: {data.jira.reason}</p>}
      {data?.cases.deployed === false && <p className="text-xs text-muted-foreground">Cases are not deployed yet (migration V034).</p>}
      {notice && <p role="status" className="rounded-lg border bg-muted/40 px-3 py-2 text-xs">{notice}</p>}
      {error && <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">{error}</p>}

      {/* bulk: Jira issues to a sprint */}
      {jiraKeys.length > 0 && canJiraWrite && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-primary/30 bg-primary/5 px-3 py-2 text-xs" data-testid="sprint-bar">
          <span className="font-medium">{jiraKeys.length} Jira ticket{jiraKeys.length === 1 ? "" : "s"} selected</span>
          <select aria-label="Move to sprint" value={target} onChange={(e) => setTarget(e.target.value)} className={select}>
            <option value="">Move to sprint…</option>
            {sprints?.map((s) => <option key={s.id} value={String(s.id)}>{s.name} ({s.state}) · {s.board}</option>)}
            <option value="backlog">Backlog (no sprint)</option>
          </select>
          {sprints === null && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
          <button type="button" onClick={() => void move()} disabled={!target || moving}
                  className="inline-flex h-8 items-center gap-1 rounded-lg bg-primary px-3 font-medium text-primary-foreground disabled:opacity-50">
            {moving && <Loader2 className="h-3.5 w-3.5 animate-spin" />} Move
          </button>
          <button type="button" onClick={() => setSelected(new Set())} className="ml-auto h-8 rounded-lg px-2 hover:bg-muted">Clear</button>
        </div>
      )}

      <div className="overflow-x-auto rounded-xl border bg-card">
        <div className="flex items-center gap-2 border-b px-3 py-2 text-xs text-muted-foreground">
          {data && <span><b className="text-foreground">{data.counts.total}</b> tickets · {data.counts.jira} Jira · {data.counts.case} cases{data.jira.truncated ? " · Jira shows the first 100: narrow the filters" : ""}</span>}
          <button type="button" onClick={() => void load(f)} className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 hover:bg-muted">
            <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} /> Reload
          </button>
        </div>
        <table className="w-full text-sm" aria-label="Tickets">
          <thead className="bg-muted/50 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="w-8 px-3 py-2"><input type="checkbox" aria-label="Select all Jira tickets" checked={allChecked} disabled={!allSelectable.length}
                                                  onChange={() => setSelected(allChecked ? new Set() : new Set(allSelectable.map((r) => r.id)))} /></th>
              <th className="px-2 py-2">Ticket</th><th className="px-2 py-2">Title</th><th className="px-2 py-2">Status</th>
              <th className="px-2 py-2">Priority</th><th className="px-2 py-2">Assignee</th><th className="px-2 py-2">Sprint or domain</th>
              <th className="px-2 py-2">Updated</th><th className="px-2 py-2"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => <Row key={t.id} t={t} checked={selected.has(t.id)} openTicket={openTicket}
                                  toggle={() => setSelected((s) => { const n = new Set(s); if (n.has(t.id)) n.delete(t.id); else n.add(t.id); return n; })} />)}
            {loading && !data && <tr><td colSpan={9} className="px-3 py-8 text-center text-xs text-muted-foreground"><Loader2 className="mr-2 inline h-3.5 w-3.5 animate-spin" />Loading tickets…</td></tr>}
            {data && !rows.length && <tr><td colSpan={9} className="px-3 py-8 text-center text-xs text-muted-foreground">No tickets match these filters.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Row({ t, checked, toggle, openTicket }: { t: Ticket; checked: boolean; toggle: () => void; openTicket: (key: string) => void }) {
  const jira = t.sources.includes("JIRA");
  const caseHref = t.case ? `/qa/cases/${encodeURIComponent(t.case.case_id)}` : null;
  return (
    <tr className={cn("border-t align-top hover:bg-muted/30", checked && "bg-primary/5", t.done && "text-muted-foreground")}>
      <td className="px-3 py-2">{jira && <input type="checkbox" aria-label={`Select ${t.key}`} checked={checked} onChange={toggle} />}</td>
      <td className="whitespace-nowrap px-2 py-2">
        <div className="flex items-center gap-1.5">
          {jira ? <button type="button" onClick={() => openTicket(t.key)} className="font-mono text-xs font-semibold text-primary hover:underline">{t.key}</button>
            : <Link href={caseHref ?? "#"} className="font-mono text-xs font-semibold text-primary hover:underline">{t.key}</Link>}
        </div>
        <div className="mt-1 flex gap-1">
          {t.sources.map((s) => <span key={s} className={cn("rounded px-1.5 py-px text-[10px] font-medium", s === "JIRA" ? "bg-sky-500/10 text-sky-700 dark:text-sky-400" : "bg-blue-500/10 text-blue-700 dark:text-blue-400")}>{s === "JIRA" ? "Jira" : "Case"}</span>)}
          {jira && t.case && caseHref && <Link href={caseHref} className="text-[10px] text-muted-foreground hover:text-primary">{t.case.number}</Link>}
        </div>
      </td>
      <td className="max-w-[26rem] px-2 py-2">
        <span className="line-clamp-2">{t.title}</span>
        <span className="mt-0.5 block text-[11px] text-muted-foreground">{[t.type, t.project].filter(Boolean).join(" · ")}{t.linked ? ` · ${t.linked} linked` : ""}</span>
      </td>
      <td className="whitespace-nowrap px-2 py-2">{jira ? <StatusPill name={t.status} category={t.done ? "done" : null} /> : <span className="text-xs">{t.status}</span>}</td>
      <td className="px-2 py-2">{t.priority && <span className={cn("rounded px-1.5 py-0.5 text-[11px] font-semibold", PRIORITY_TONE[t.priority])} title={t.priority_name ?? undefined}>{t.priority}</span>}</td>
      <td className="whitespace-nowrap px-2 py-2 text-xs">{t.assignee ?? <span className="text-muted-foreground">unassigned</span>}{t.assignee_is_me && <span className="ml-1 text-[10px] text-primary">(you)</span>}</td>
      <td className="whitespace-nowrap px-2 py-2 text-xs">
        {t.sprint && <span className={cn(t.sprint.state === "active" && "font-medium")}>{t.sprint.name}</span>}
        {t.domain_name && <span className="block text-muted-foreground">{t.domain_name}</span>}
        {t.sla_breached && <span className="mt-0.5 inline-flex items-center gap-1 text-rose-600"><AlarmClock className="h-3 w-3" /> SLA breached</span>}
      </td>
      <td className="whitespace-nowrap px-2 py-2 text-xs text-muted-foreground">{t.updated ? new Date(t.updated).toLocaleDateString() : ""}</td>
      <td className="whitespace-nowrap px-2 py-2 text-right">
        {t.url && <a href={t.url} target="_blank" rel="noreferrer" aria-label={`Open ${t.key} in Jira`} className="mr-1 inline-grid h-7 w-7 place-items-center rounded-md text-muted-foreground hover:bg-muted"><ExternalLink className="h-3.5 w-3.5" /></a>}
        {jira && (
          <Link href={t.copilot ? `/dashboard/copilot/${t.copilot.session_id}` : `/dashboard/copilot?jira=${encodeURIComponent(t.key)}`}
                aria-label={`Work on ${t.key} in the Copilot`} title={t.copilot ? `Open the Copilot conversation${t.copilot.drafts ? ` (${t.copilot.drafts} drafts)` : ""}` : "Work on it in the Copilot"}
                className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-xs font-medium text-primary hover:bg-primary/10">
            <Bot className="h-3.5 w-3.5" /> {t.copilot ? `Copilot${t.copilot.drafts ? ` · ${t.copilot.drafts}` : ""}` : "Copilot"}
          </Link>
        )}
      </td>
    </tr>
  );
}

