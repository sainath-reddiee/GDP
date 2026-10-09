"use client";

import Link from "next/link";
import { useMemo, useState, useTransition } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  ArrowLeftRight, BadgeCheck, BookOpen, Boxes, Check, ChevronDown, ChevronRight, Clock, Inbox, KeyRound, Loader2, Pencil,
  Play, Plus, RotateCcw, Search, ShieldAlert, Trash2, TriangleAlert, Undo2, Users2, X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select, Textarea } from "@/components/ui/input";
import { DiffView } from "@/components/diff-view";
import { useAccess } from "@/components/access";
import { displayDomain } from "@/lib/catalog-display";
import { cn } from "@/lib/utils";
import { ago } from "../../skills/types";
import type { FeedItem } from "../../knowledge/actions";
import { OriginChip, prettyType, StatusPill } from "../../knowledge/knowledge-ui";
import {
  domainDiff, editDomain, rollbackDomain, saveDomainRules, saveMembers,
  type DomainDiff, type DomainMember, type DomainRules, type DomainVersion, type TargetModel,
} from "../actions";
import { DomainTools } from "../domain-tools";
import { Avatar, cleanDescription, DomainMark } from "../domain-ui";
import type { DomainCard } from "../page";

export type DomainDetail = {
  domain: { domain_id: string; domain_name: string; description: string | null; owner: string | null };
  targets: { target_table_id: string; target_table: string; column_count: number; role: "hub" | "spoke" | null; hub_fk: string | null;
             hkey_columns: string[]; lookups: string[]; casts: number }[];
  signals: { tables?: Record<string, number>; columns?: Record<string, number> };
  source_systems: { skey: number; name: string; bronze_schema: string }[];
  contract: string | null; silver: { database: string | null; schema: string | null };
  knowledge: { knowledge_type: string; n: number }[]; origin?: string;
  deletable?: boolean; not_deletable_reason?: string | null;
  active_runs?: { run_id: string; run_name: string; current_state: string }[]; deleted?: { at: string; by: string } | null;
};

const TABS = [
  { id: "overview", label: "Overview" }, { id: "models", label: "Target models" }, { id: "knowledge", label: "Knowledge" },
  { id: "rules", label: "Rules" }, { id: "versions", label: "Versions" },
] as const;
const KIND_TONE: Record<string, string> = {
  ROLLBACK: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300", DELETE: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
  RULES: "bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300", DEPLOY: "bg-muted text-muted-foreground",
  SUGGESTION: "bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300", IMPORT: "bg-teal-100 text-teal-700 dark:bg-teal-950 dark:text-teal-300",
};
type Msg = { tone: "ok" | "info" | "error"; text: string } | null;
const toneOf = (e: string): "info" | "error" => (/approval|request/i.test(e) ? "info" : "error");

export function DomainView({ detail, versions, unrecorded, members, rules, targets, feed, card, meta, tab }: {
  detail: DomainDetail; versions: DomainVersion[]; unrecorded: boolean; members: DomainMember[]; rules: DomainRules | null;
  targets: TargetModel[]; feed: FeedItem[]; card: DomainCard;
  meta: { version: number; standard: string | null; knowledge: number; targets: number; active: boolean }; tab: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const { canAct } = useAccess();
  const may = canAct("DOMAIN.EDIT");
  const [msg, setMsg] = useState<Msg>(null);
  const [editing, setEditing] = useState(false);
  const [description, setDescription] = useState(detail.domain.description ?? "");
  const [pending, start] = useTransition();
  const d = detail.domain;
  const name = displayDomain(d.domain_name) ?? d.domain_name;
  const active = TABS.some((t) => t.id === tab) ? tab : "overview";
  const go = (id: string) => {
    const next = new URLSearchParams(params.toString());
    next.set("tab", id);
    router.replace(`${pathname}?${next}`, { scroll: false });
  };
  const act = (fn: () => Promise<{ ok: boolean; error?: string }>, ok: string, after?: () => void) => start(async () => {
    const r = await fn();
    setMsg(r.ok ? { tone: "ok", text: ok } : { tone: toneOf(r.error ?? ""), text: r.error ?? "Failed" });
    if (r.ok) { after?.(); router.refresh(); }
  });
  const verifiedPct = meta.knowledge ? Math.round(((card.verified ?? 0) / meta.knowledge) * 100) : 0;

  return (
    <div className="space-y-4">
      <header className="rounded-2xl border bg-card p-5 shadow-sm">
        <div className="flex flex-wrap items-start gap-4">
          <DomainMark name={d.domain_name} size="lg" />
          <div className="min-w-0 flex-1">
            <h1 className="flex flex-wrap items-center gap-2 text-xl font-semibold">{name}
              {meta.standard && <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">{meta.standard}</span>}
              {!meta.active && <span className="rounded-full bg-red-100 px-2 py-0.5 text-[11px] text-red-700 dark:bg-red-950 dark:text-red-300">deleted</span>}
              <span className="rounded-full border px-2 py-0.5 text-[11px] font-medium text-muted-foreground">v{meta.version}</span>
            </h1>
            {editing ? (
              <div className="mt-2 space-y-2">
                <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} className="text-sm" aria-label="Description" />
                <div className="flex gap-2">
                  <Button size="sm" disabled={pending} onClick={() => act(() => editDomain(d.domain_id, { description, note: "Description edited" }), "Saved as a new version", () => setEditing(false))}>
                    {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Save</Button>
                  <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setDescription(d.description ?? ""); }}>Cancel</Button>
                </div>
              </div>
            ) : (
              <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{cleanDescription(d.description) ?? "No description yet."}
                {may && <button type="button" onClick={() => setEditing(true)} className="ml-2 inline-flex items-center gap-1 text-xs text-primary hover:underline"><Pencil className="h-3 w-3" />Edit</button>}</p>
            )}
            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              <Pill icon={<Boxes className="h-3.5 w-3.5" />}>{meta.targets} target models</Pill>
              <Pill icon={<BookOpen className="h-3.5 w-3.5" />}>{meta.knowledge} knowledge items</Pill>
              <Pill icon={<Play className="h-3.5 w-3.5" />}>{card.runs_30d ?? 0} runs in 30 days</Pill>
              <Pill icon={<BadgeCheck className="h-3.5 w-3.5 text-emerald-600" />}>{verifiedPct}% verified</Pill>
              {!!card.inbox && <Link href={`/knowledge?tab=inbox&domain_id=${d.domain_id}`}><Pill icon={<Inbox className="h-3.5 w-3.5 text-amber-600" />}>{card.inbox} to review</Pill></Link>}
              {!!card.stale && <Link href={`/knowledge?tab=health&domain_id=${d.domain_id}`}><Pill icon={<TriangleAlert className="h-3.5 w-3.5 text-amber-600" />}>{card.stale} stale</Pill></Link>}
            </div>
          </div>
          <div className="flex -space-x-1.5">{members.slice(0, 5).map((m) => <Avatar key={`${m.user_name}-${m.role}`} user={m.user_name} role={m.role} size="md" />)}</div>
        </div>
        {msg && <p role={msg.tone === "error" ? "alert" : "status"} className={cn("mt-3 rounded-lg px-3 py-2 text-xs",
          msg.tone === "error" ? "bg-destructive/10 text-destructive" : msg.tone === "info" ? "bg-sky-50 text-sky-800 dark:bg-sky-950/40 dark:text-sky-200" : "bg-success/10 text-success")}>{msg.text}</p>}
      </header>

      <nav className="flex gap-1 border-b" aria-label="Domain sections">
        {TABS.map((t) => (
          <button key={t.id} type="button" onClick={() => go(t.id)} aria-current={active === t.id ? "page" : undefined}
                  className={cn("-mb-px border-b-2 px-3 py-2 text-sm", active === t.id ? "border-primary font-medium text-primary" : "border-transparent text-muted-foreground hover:text-foreground")}>
            {t.label}{t.id === "versions" && <span className="ml-1.5 rounded-full bg-muted px-1.5 text-[10px]">{versions.length}</span>}
          </button>
        ))}
      </nav>

      {active === "overview" && (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="space-y-4">
            <Panel title="Detection signals" hint="Table and column names that tell the Sources page a dataset belongs to this domain.">
              <Signals signals={detail.signals} />
            </Panel>
            <div className="grid gap-4 md:grid-cols-2">
              <Panel title="Source systems">
                {detail.source_systems.length ? (
                  <ul className="space-y-1 text-sm">{detail.source_systems.slice(0, 12).map((s) => (
                    <li key={s.name} className="flex gap-2"><span className="font-medium">{s.name}</span><span className="ml-auto font-mono text-[11px] text-muted-foreground">{s.bronze_schema} · {s.skey}</span></li>
                  ))}</ul>
                ) : <p className="text-sm text-muted-foreground">None listed.</p>}
              </Panel>
              <Panel title="Contract and location">
                <dl className="space-y-1.5 text-sm">
                  <div className="flex gap-2"><dt className="text-muted-foreground">Silver</dt><dd className="ml-auto font-mono text-xs">{detail.silver.database ? `${detail.silver.database}.${detail.silver.schema}` : "Not set"}</dd></div>
                  <div className="flex gap-2"><dt className="text-muted-foreground">Contract</dt><dd className="ml-auto font-mono text-xs">{detail.contract?.split("/").pop() ?? "None"}</dd></div>
                  <div className="flex gap-2"><dt className="text-muted-foreground">Origin</dt><dd className="ml-auto text-xs">{detail.origin ?? "repository"}</dd></div>
                </dl>
              </Panel>
            </div>
            <Panel title="Tools" hint="Ask the domain, run an AI review of its pack, export it, or delete it.">
              <DomainTools domainId={d.domain_id} name={d.domain_name} deletable={!!detail.deletable} reason={detail.not_deletable_reason ?? null}
                           activeRuns={detail.active_runs ?? []} deleted={!meta.active} />
            </Panel>
          </div>
          <aside className="space-y-4">
            <People domainId={d.domain_id} members={members} may={may} onMsg={setMsg} />
            <Panel title="Recent changes" action={<button type="button" onClick={() => go("versions")} className="text-xs text-primary hover:underline">All versions</button>}>
              <ol className="space-y-2 text-xs">
                {versions.slice(0, 4).map((v) => (
                  <li key={v.version}>
                    <p className="flex items-center gap-1.5"><span className="font-medium">v{v.version}</span><Kind kind={v.change_kind} /><span className="text-muted-foreground">{v.created_by === "DEPLOY" ? "deploy" : v.created_by} · {ago(v.created_at)}</span></p>
                    <p className="text-muted-foreground">{v.summary.slice(0, 2).join("; ")}</p>
                  </li>
                ))}
                {!versions.length && <li className="text-muted-foreground">No versions recorded yet. The next deploy or change records the first one.</li>}
              </ol>
            </Panel>
          </aside>
        </div>
      )}

      {active === "models" && <Models targets={targets} roles={detail.targets} />}
      {active === "knowledge" && <KnowledgeTab domainId={d.domain_id} mix={detail.knowledge} feed={feed} card={card} />}
      {active === "rules" && <RulesTab domainId={d.domain_id} rules={rules} may={may} onMsg={setMsg} />}
      {active === "versions" && <Versions domainId={d.domain_id} versions={versions} unrecorded={unrecorded} may={may} onMsg={setMsg} />}
    </div>
  );
}

function Pill({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return <span className="inline-flex items-center gap-1.5 rounded-full border bg-background px-2.5 py-1">{icon}{children}</span>;
}

function Panel({ title, hint, action, children }: { title: string; hint?: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border bg-card p-4 shadow-sm">
      <div className="mb-2 flex items-start justify-between gap-2"><div><h3 className="text-sm font-semibold">{title}</h3>{hint && <p className="text-xs text-muted-foreground">{hint}</p>}</div>{action}</div>
      {children}
    </section>
  );
}

function Kind({ kind }: { kind: string | null }) {
  return <span className={cn("rounded-full px-1.5 py-0.5 text-[10px] font-medium lowercase", KIND_TONE[kind ?? ""] ?? "bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300")}>{(kind ?? "change").toLowerCase()}</span>;
}

function Signals({ signals }: { signals: DomainDetail["signals"] }) {
  const top = (w?: Record<string, number>, n = 16) => Object.entries(w ?? {}).sort((a, b) => b[1] - a[1]).slice(0, n);
  const tables = top(signals.tables, 12);
  const columns = top(signals.columns, 24);
  if (!tables.length && !columns.length) return <p className="text-sm text-muted-foreground">None configured. Accept AI review suggestions or import a pack to add signals.</p>;
  return (
    <div className="space-y-2">
      {!!tables.length && <div className="flex flex-wrap gap-1">{tables.map(([k, w]) => <span key={k} className="rounded-md bg-primary/10 px-2 py-0.5 text-xs text-primary" title={`weight ${w}`}>{k}</span>)}</div>}
      {!!columns.length && <div className="flex flex-wrap gap-1">{columns.map(([k, w]) => <span key={k} className="rounded-md border px-2 py-0.5 font-mono text-[11px] text-muted-foreground" title={`weight ${w}`}>{k}</span>)}</div>}
    </div>
  );
}

function People({ domainId, members, may, onMsg }: { domainId: string; members: DomainMember[]; may: boolean; onMsg: (m: Msg) => void }) {
  const router = useRouter();
  const [draft, setDraft] = useState(members.map((m) => ({ user: m.user_name, role: m.role as string })));
  const [user, setUser] = useState("");
  const [role, setRole] = useState("STEWARD");
  const [pending, start] = useTransition();
  const dirty = JSON.stringify(draft) !== JSON.stringify(members.map((m) => ({ user: m.user_name, role: m.role })));
  const save = () => start(async () => {
    const r = await saveMembers(domainId, draft);
    onMsg(r.ok ? { tone: "ok", text: "People saved" } : { tone: toneOf(r.error), text: r.error });
    if (r.ok) router.refresh();
  });
  const label: Record<string, string> = { OWNER: "Owner", STEWARD: "Steward", EXPERT: "Expert" };
  return (
    <Panel title="People" hint="Owners are accountable, stewards curate knowledge and rules, experts are asked for advice.">
      <ul className="space-y-1.5">
        {draft.map((m, i) => (
          <li key={`${m.user}-${m.role}`} className="flex items-center gap-2 text-sm">
            <Avatar user={m.user} role={m.role} />
            <span className="min-w-0 flex-1 truncate font-mono text-xs">{m.user}</span>
            <span className={cn("rounded-full px-1.5 py-0.5 text-[10px] font-medium", m.role === "OWNER" ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground")}>{label[m.role]}</span>
            {may && <button type="button" aria-label={`Remove ${m.user}`} onClick={() => setDraft(draft.filter((_, j) => j !== i))} className="rounded p-0.5 text-muted-foreground hover:text-destructive"><Trash2 className="h-3 w-3" /></button>}
          </li>
        ))}
        {!draft.length && <li className="flex items-center gap-1.5 text-xs text-muted-foreground"><Users2 className="h-3.5 w-3.5" />Nobody yet.</li>}
      </ul>
      {may && (
        <div className="mt-3 space-y-2 border-t pt-3">
          <div className="flex gap-1.5">
            <Input value={user} onChange={(e) => setUser(e.target.value.toUpperCase())} placeholder="SNOWFLAKE.USER" className="h-8 flex-1 font-mono text-xs" aria-label="User" />
            <Select value={role} onChange={(e) => setRole(e.target.value)} className="h-8 w-28 text-xs" aria-label="Role">
              <option value="OWNER">Owner</option><option value="STEWARD">Steward</option><option value="EXPERT">Expert</option>
            </Select>
            <Button size="sm" variant="outline" className="h-8" disabled={!user.trim() || draft.some((m) => m.user === user.trim() && m.role === role)}
                    onClick={() => { setDraft([...draft, { user: user.trim(), role }]); setUser(""); }}><Plus className="h-3 w-3" /></Button>
          </div>
          {dirty && <Button size="sm" className="w-full" disabled={pending || !draft.some((m) => m.role === "OWNER")} onClick={save}>
            {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}{draft.some((m) => m.role === "OWNER") ? "Save people" : "Add an owner first"}</Button>}
        </div>
      )}
    </Panel>
  );
}

function Models({ targets, roles }: { targets: TargetModel[]; roles: DomainDetail["targets"] }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [q, setQ] = useState("");
  const byName = useMemo(() => new Map(roles.map((r) => [r.target_table.toUpperCase(), r])), [roles]);
  const shown = targets.filter((t) => !q || `${t.table} ${t.columns.map((c) => c.name).join(" ")}`.toLowerCase().includes(q.toLowerCase()));
  if (!targets.length) return <p className="rounded-2xl border border-dashed p-8 text-center text-sm text-muted-foreground">No target models registered for this domain.</p>;
  return (
    <div className="space-y-3">
      <div className="relative max-w-sm">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a table or column" className="pl-8" aria-label="Find a table or column" />
      </div>
      {shown.map((t) => {
        const short = t.table.split(".").pop() ?? t.table;
        const meta = byName.get(short);
        const isOpen = open[t.table] || Boolean(q);
        return (
          <section key={t.table} className={cn("rounded-2xl border bg-card shadow-sm", !t.active && "opacity-60")}>
            <button type="button" onClick={() => setOpen({ ...open, [t.table]: !open[t.table] })} aria-expanded={isOpen} className="flex w-full items-center gap-3 px-4 py-3 text-left">
              {isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-2 text-sm font-semibold">{short}
                  {meta?.role && <span className={cn("rounded-full px-1.5 py-0.5 text-[10px]", meta.role === "hub" ? "bg-primary text-primary-foreground" : "border")}>{meta.role}</span>}
                  {!t.active && <span className="text-[10px] text-muted-foreground">inactive</span>}
                </span>
                <span className="block font-mono text-[11px] text-muted-foreground">{t.table}{meta?.hub_fk ? ` · joins hub on ${meta.hub_fk}` : ""}</span>
              </span>
              <span className="text-xs text-muted-foreground">{t.columns.length} columns · {t.columns.filter((c) => c.key).length} keys · {t.columns.filter((c) => c.pii).length} PII</span>
            </button>
            {isOpen && (
              <div className="overflow-x-auto border-t">
                <table className="w-full text-xs">
                  <thead className="bg-muted/40 text-left text-[10px] uppercase tracking-wide text-muted-foreground"><tr><th className="px-4 py-1.5">Column</th><th>Type</th><th>Flags</th><th className="px-4">Definition</th></tr></thead>
                  <tbody className="divide-y">
                    {t.columns.filter((c) => !q || c.name.toLowerCase().includes(q.toLowerCase()) || t.table.toLowerCase().includes(q.toLowerCase())).map((c) => (
                      <tr key={c.name}>
                        <td className="px-4 py-1.5 font-mono">{c.name}</td><td className="font-mono text-muted-foreground">{c.type}</td>
                        <td className="space-x-1">{c.key && <span className="inline-flex items-center gap-0.5 rounded bg-amber-100 px-1 text-[10px] text-amber-700 dark:bg-amber-950 dark:text-amber-300"><KeyRound className="h-2.5 w-2.5" />key</span>}
                          {c.pii && <span className="inline-flex items-center gap-0.5 rounded bg-red-100 px-1 text-[10px] text-red-700 dark:bg-red-950 dark:text-red-300"><ShieldAlert className="h-2.5 w-2.5" />PII</span>}
                          {!c.nullable && <span className="rounded bg-muted px-1 text-[10px] text-muted-foreground">not null</span>}</td>
                        <td className="max-w-md px-4 text-muted-foreground">{c.definition}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

function KnowledgeTab({ domainId, mix, feed, card }: { domainId: string; mix: DomainDetail["knowledge"]; feed: FeedItem[]; card: DomainCard }) {
  const max = Math.max(1, ...mix.map((m) => Number(m.n)));
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
      <Panel title="Recently learned" action={<Link href={`/knowledge?tab=feed&domain_id=${domainId}`} className="text-xs text-primary hover:underline">Full feed</Link>}>
        <ol className="space-y-2">
          {feed.map((f) => (
            <li key={f.knowledge_id} className="text-xs">
              <p className="flex flex-wrap items-center gap-1.5"><OriginChip origin={f.origin} short /><span className="font-medium">{f.title}</span>
                <span className="text-muted-foreground">{prettyType(f.knowledge_type)} · v{f.version}</span>{f.status !== "ACTIVE" && <StatusPill status={f.status} />}</p>
              <p className="text-muted-foreground">{f.created_by} · {ago(f.created_at)}{f.source_run_id && <> · run <Link className="text-primary hover:underline" href={`/runs/${f.source_run_id}`}>{f.run_name ?? f.source_run_id.slice(0, 8)}</Link></>}</p>
            </li>
          ))}
          {!feed.length && <li className="text-sm text-muted-foreground">Nothing learned for this domain yet.</li>}
        </ol>
      </Panel>
      <aside className="space-y-4">
        <Panel title="Open in the Knowledge hub">
          <div className="grid gap-2 text-sm">
            <Link href={`/knowledge?tab=items&domain_id=${domainId}`} className="flex items-center gap-2 rounded-lg border px-3 py-2 hover:border-primary hover:text-primary"><BookOpen className="h-4 w-4" />All items</Link>
            <Link href={`/knowledge?tab=inbox&domain_id=${domainId}`} className="flex items-center gap-2 rounded-lg border px-3 py-2 hover:border-primary hover:text-primary"><Inbox className="h-4 w-4" />Review inbox{card.inbox ? ` (${card.inbox})` : ""}</Link>
            <Link href={`/knowledge?tab=health&domain_id=${domainId}`} className="flex items-center gap-2 rounded-lg border px-3 py-2 hover:border-primary hover:text-primary"><TriangleAlert className="h-4 w-4" />Duplicates and stale{card.stale ? ` (${card.stale})` : ""}</Link>
          </div>
        </Panel>
        <Panel title="By type">
          <ul className="space-y-2">
            {mix.map((m) => (
              <li key={m.knowledge_type} className="text-xs">
                <div className="mb-0.5 flex justify-between"><span>{prettyType(m.knowledge_type)}</span><span className="tabular-nums text-muted-foreground">{m.n}</span></div>
                <div className="h-1.5 rounded-full bg-muted"><div className="h-full rounded-full bg-primary/70" style={{ width: `${(Number(m.n) / max) * 100}%` }} /></div>
              </li>
            ))}
          </ul>
        </Panel>
      </aside>
    </div>
  );
}

const GROUPS: [string, string][] = [
  ["quality.", "Data quality checks"], ["profile.", "Profiling"], ["relationships.", "Relationships"], ["joins.", "Joins"],
  ["mapping.", "Mapping"], ["domain.", "Domain detection"], ["ui.", "Confidence bands"], ["hints.", "Name hints"],
];
const toText = (v: unknown) => (v === undefined || v === null ? "" : Array.isArray(v) ? v.join(", ") : typeof v === "object" ? JSON.stringify(v) : String(v));
function parseLike(sample: unknown, text: string): unknown {
  if (typeof sample === "number") return Number(text);
  if (typeof sample === "boolean") return text.trim().toLowerCase() === "true";
  if (Array.isArray(sample)) return text.split(",").map((s) => s.trim()).filter(Boolean);
  if (typeof sample === "object" && sample !== null) return JSON.parse(text || "{}");
  return text;
}

function RulesTab({ domainId, rules, may, onMsg }: { domainId: string; rules: DomainRules | null; may: boolean; onMsg: (m: Msg) => void }) {
  const router = useRouter();
  const initial = useMemo(() => Object.fromEntries(Object.entries(rules?.overrides ?? {}).map(([k, v]) => [k, toText(v)])), [rules]);
  const [text, setText] = useState<Record<string, string>>(initial);
  const [note, setNote] = useState("");
  const [q, setQ] = useState("");
  const [onlyOverridden, setOnly] = useState(false);
  const [pending, start] = useTransition();
  if (!rules) return <p className="text-sm text-muted-foreground">Rules are not available from the API yet.</p>;
  const dirty = JSON.stringify(Object.fromEntries(Object.entries(text).filter(([, v]) => v.trim()))) !== JSON.stringify(Object.fromEntries(Object.entries(initial).filter(([, v]) => v.trim())));
  const save = () => start(async () => {
    try {
      const overrides = Object.fromEntries(Object.entries(text).filter(([, v]) => v.trim()).map(([k, v]) => [k, parseLike(rules.defaults[k], v)]));
      const r = await saveDomainRules(domainId, overrides, note);
      onMsg(r.ok ? { tone: "ok", text: "Rules saved as a new domain version" } : { tone: toneOf(r.error), text: r.error });
      if (r.ok) { setNote(""); router.refresh(); }
    } catch {
      onMsg({ tone: "error", text: "A value is not in the right format (JSON for the abbreviations map)." });
    }
  });
  const keys = Object.keys(rules.defaults).filter((k) => (!q || k.toLowerCase().includes(q.toLowerCase())) && (!onlyOverridden || (text[k] ?? "").trim()));
  return (
    <div className="space-y-3 pb-20">
      <div className="flex flex-wrap items-center gap-3 rounded-xl border bg-card p-2 shadow-sm">
        <div className="relative min-w-[220px] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search rules" className="border-0 pl-8 shadow-none focus-visible:ring-0" aria-label="Search rules" />
        </div>
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground"><input type="checkbox" checked={onlyOverridden} onChange={() => setOnly(!onlyOverridden)} />Only this domain&apos;s overrides</label>
        <p className="text-xs text-muted-foreground">Empty means the domain uses the platform value (Admin, Rules).</p>
      </div>
      {GROUPS.map(([prefix, title]) => {
        const items = keys.filter((k) => k.startsWith(prefix));
        if (!items.length) return null;
        return (
          <section key={prefix} className="overflow-hidden rounded-2xl border bg-card shadow-sm">
            <h3 className="border-b bg-muted/30 px-4 py-2 text-sm font-semibold">{title}</h3>
            <table className="w-full text-xs">
              <thead className="text-left text-[10px] uppercase tracking-wide text-muted-foreground"><tr><th className="px-4 py-1.5">Rule</th><th>Platform</th><th>This domain</th><th className="px-4" /></tr></thead>
              <tbody className="divide-y">
                {items.map((k) => {
                  const own = (text[k] ?? "").trim();
                  return (
                    <tr key={k} className={cn(own && "bg-primary/5")}>
                      <td className="px-4 py-1.5"><span className="capitalize">{k.split(".").slice(1).join(" ").replace(/_/g, " ")}</span><span className="block font-mono text-[10px] text-muted-foreground">{k}</span></td>
                      <td className="max-w-[220px] truncate font-mono text-muted-foreground" title={toText(rules.platform[k])}>{toText(rules.platform[k])}</td>
                      <td className="py-1"><Input value={text[k] ?? ""} disabled={!may} onChange={(e) => setText({ ...text, [k]: e.target.value })}
                                                  placeholder="inherit" className="h-7 w-56 font-mono text-[11px]" aria-label={k} /></td>
                      <td className="px-4 text-right">{own && may && <button type="button" title="Back to the platform value" onClick={() => setText({ ...text, [k]: "" })} className="text-primary"><RotateCcw className="h-3.5 w-3.5" /></button>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>
        );
      })}
      {dirty && may && (
        <div className="fixed inset-x-0 bottom-0 z-30 border-t bg-card/95 backdrop-blur">
          <div className="mx-auto flex max-w-6xl items-center gap-3 px-6 py-2.5">
            <span className="text-xs font-medium text-warning">Unsaved rule changes</span>
            <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why (shown in the version history)" className="h-8 max-w-md flex-1 text-xs" />
            <span className="ml-auto flex gap-2">
              <Button size="sm" variant="ghost" onClick={() => setText(initial)}>Discard</Button>
              <Button size="sm" disabled={pending} onClick={save}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Save rules</Button>
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

function Versions({ domainId, versions, unrecorded, may, onMsg }: {
  domainId: string; versions: DomainVersion[]; unrecorded: boolean; may: boolean; onMsg: (m: Msg) => void;
}) {
  const router = useRouter();
  const [base, setBase] = useState(versions[1]?.version ?? versions[0]?.version ?? 1);
  const [head, setHead] = useState(versions[0]?.version ?? 1);
  const [diff, setDiff] = useState<DomainDiff | null>(null);
  const [pending, start] = useTransition();
  const compare = (b: number, h: number) => {
    setBase(b); setHead(h);
    start(async () => { const r = await domainDiff(domainId, b, h); if (r.ok) setDiff(r.data); else onMsg({ tone: "error", text: r.error }); });
  };
  const rollback = (v: DomainVersion) => {
    const note = window.prompt(`Roll back to version ${v.version}? Description, owner, rules, signals, source systems and contract return to that version (as a new version). Target models and knowledge keep their own history. Note:`, `Rolled back to version ${v.version}`);
    if (note === null) return;
    start(async () => {
      const r = await rollbackDomain(domainId, v.version, note);
      onMsg(r.ok ? { tone: "ok", text: r.data.changed ? `Rolled back to version ${v.version}` : "Already the same as that version" } : { tone: toneOf(r.error), text: r.error });
      if (r.ok) router.refresh();
    });
  };
  if (!versions.length) return <p className="rounded-2xl border border-dashed p-8 text-center text-sm text-muted-foreground">No versions recorded yet. The next deploy or change records the first one.</p>;
  return (
    <div className="space-y-4">
      {unrecorded && <p className="flex items-center gap-1.5 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-200"><TriangleAlert className="h-3.5 w-3.5" />The domain changed outside the app since the last version (for example by a deploy that has not run yet). The next change or deploy records it.</p>}
      <section className="rounded-2xl border bg-card p-4 shadow-sm">
        <div className="flex flex-wrap items-end gap-2">
          <h3 className="mr-auto flex items-center gap-1.5 text-sm font-semibold"><ArrowLeftRight className="h-4 w-4" />Compare versions</h3>
          {(["Base", "Compare"] as const).map((label) => (
            <label key={label} className="text-xs text-muted-foreground">{label}
              <Select value={String(label === "Base" ? base : head)} onChange={(e) => (label === "Base" ? setBase : setHead)(Number(e.target.value))} className="ml-1 h-8 w-28 text-xs">
                {versions.map((v) => <option key={v.version} value={v.version}>v{v.version}</option>)}
              </Select>
            </label>
          ))}
          <Button size="sm" variant="outline" disabled={pending || base === head} onClick={() => compare(base, head)}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ArrowLeftRight className="h-3.5 w-3.5" />}Compare</Button>
        </div>
        {diff && (
          <div className="mt-4 space-y-3">
            <ul className="list-disc space-y-0.5 pl-5 text-xs">{diff.summary.map((s, i) => <li key={i}>{s}</li>)}</ul>
            <DiffView files={diff.files} />
          </div>
        )}
      </section>
      <ol className="relative space-y-3 border-l pl-5">
        {versions.map((v, i) => (
          <li key={v.version}>
            <span className={cn("absolute -left-[5px] mt-2 h-2.5 w-2.5 rounded-full border-2 border-background", i === 0 ? "bg-emerald-500" : "bg-muted-foreground/40")} />
            <div className="rounded-2xl border bg-card p-4 shadow-sm">
              <div className="flex flex-wrap items-start gap-3">
                <div className="min-w-[220px] flex-1">
                  <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">v{v.version} <Kind kind={v.change_kind} />{i === 0 && <span className="text-xs font-normal text-emerald-600">current</span>}</p>
                  <p className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground"><Clock className="h-3 w-3" />{v.created_by === "DEPLOY" ? "deploy" : v.created_by} · {ago(v.created_at)} · {v.targets} targets, {v.columns} columns</p>
                  {v.change_note && <p className="mt-1 text-xs">{v.change_note}</p>}
                  <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">{v.summary.slice(0, 6).map((s, j) => <li key={j}>{s}</li>)}{v.summary.length > 6 && <li>and {v.summary.length - 6} more</li>}</ul>
                </div>
                <div className="flex gap-1.5">
                  {versions[i + 1] && <Button size="sm" variant="ghost" disabled={pending} onClick={() => compare(versions[i + 1].version, v.version)}><ArrowLeftRight className="h-3.5 w-3.5" />Changes</Button>}
                  {may && i > 0 && <Button size="sm" variant="outline" disabled={pending} onClick={() => rollback(v)}><Undo2 className="h-3.5 w-3.5" />Roll back here</Button>}
                </div>
              </div>
            </div>
          </li>
        ))}
      </ol>
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><X className="h-3 w-3" />Rolling back never rewrites history: it records a new version with the earlier values.</p>
    </div>
  );
}
