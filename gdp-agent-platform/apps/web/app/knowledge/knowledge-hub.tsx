"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, useTransition, type ReactNode } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  ArrowLeftRight, BadgeCheck, Check, CheckCheck, Copy, Inbox, Loader2, Rss, Settings2, Sparkles, TriangleAlert, X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DiffView } from "@/components/diff-view";
import { useAccess } from "@/components/access";
import { useScrollLock } from "@/components/use-scroll-lock";
import { cn } from "@/lib/utils";
import { ago } from "../skills/types";
import {
  decideInbox, knowledgeDiff, loadKnowledgeItem, loadPolicy, savePolicy, verifyKnowledge,
  type FeedItem, type InboxItem, type KDiff, type KnowledgeItem, type Overview, type Policy,
} from "./actions";
import { HUB_TABS, type HubTab } from "./hub-tabs";
import { KnowledgeDrawer } from "./item-drawer";
import { ORIGINS, OriginChip, prettyType, StatusPill, TypeIcon } from "./knowledge-ui";


function useOpenItem() {
  const [open, setOpen] = useState<KnowledgeItem | null>(null);
  const [, start] = useTransition();
  const show = (id: string) => start(async () => { const r = await loadKnowledgeItem(id); if (r.ok) setOpen(r.data); });
  const drawer = open ? <KnowledgeDrawer item={open} onClose={() => setOpen(null)} onEdit={() => setOpen(null)} /> : null;
  return { show, drawer };
}

/** Header strip, tabs and policy button shared by every tab. */
export function HubShell({ tab, overview, children }: { tab: HubTab; overview: Overview | null; children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const { canAct } = useAccess();
  const [policy, setPolicy] = useState(false);
  const t = overview?.totals;
  const go = (id: HubTab) => {
    const next = new URLSearchParams(params.toString());
    next.set("tab", id); next.delete("offset");
    router.push(`${pathname}?${next}`, { scroll: false });
  };
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi label="In use" value={t?.active} hint="active items" />
        <Kpi label="Learned this week" value={t?.learned_7d} hint="from runs and AI" tone="primary" onClick={() => go("feed")} />
        <Kpi label="Waiting for review" value={t?.inbox} hint="in the inbox" tone={t?.inbox ? "warning" : undefined} onClick={() => go("inbox")} />
        <Kpi label="Verified" value={t?.verified} hint="checked by a person" tone="success" />
        <Kpi label="Stale" value={t?.stale} hint="due for review" tone={t?.stale ? "warning" : undefined} onClick={() => go("health")} />
        <Kpi label="Used, 30 days" value={t?.used_30d} hint={`${t?.uses_30d ?? 0} uses by stages`} />
      </div>
      <div className="flex flex-wrap items-center gap-1 border-b">
        {HUB_TABS.map((h) => (
          <button key={h.id} type="button" onClick={() => go(h.id)} aria-current={tab === h.id ? "page" : undefined}
                  className={cn("-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition-colors",
                                tab === h.id ? "border-primary font-medium text-primary" : "border-transparent text-muted-foreground hover:text-foreground")}>
            {h.label}
            {h.id === "inbox" && !!t?.inbox && <span className="rounded-full bg-amber-500 px-1.5 text-[10px] font-semibold text-white">{t.inbox}</span>}
          </button>
        ))}
        {canAct("KNOWLEDGE.EDIT") && (
          <button type="button" onClick={() => setPolicy(true)} className="ml-auto mb-1 flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground">
            <Settings2 className="h-3.5 w-3.5" />Learning policy
          </button>
        )}
      </div>
      {children}
      {policy && <PolicyDialog onClose={() => setPolicy(false)} />}
    </div>
  );
}

function Kpi({ label, value, hint, tone, onClick }: { label: string; value?: number; hint: string; tone?: "primary" | "success" | "warning"; onClick?: () => void }) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag type={onClick ? "button" : undefined} onClick={onClick}
         className={cn("rounded-xl border bg-card px-4 py-3 text-left shadow-sm transition", onClick && "hover:border-primary/40")}>
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={cn("mt-0.5 text-2xl font-semibold tabular-nums", tone === "primary" && "text-primary", tone === "success" && "text-success", tone === "warning" && "text-warning")}>{value ?? "–"}</p>
      <p className="text-[11px] text-muted-foreground">{hint}</p>
    </Tag>
  );
}

function Bars({ title, data, label }: { title: string; data: Record<string, number>; label?: (k: string) => ReactNode }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]).slice(0, 8);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  return (
    <section className="rounded-2xl border bg-card p-4 shadow-sm">
      <h3 className="mb-3 text-sm font-semibold">{title}</h3>
      <ul className="space-y-2">
        {entries.map(([k, v]) => (
          <li key={k} className="text-xs">
            <div className="mb-0.5 flex items-center justify-between gap-2"><span className="truncate">{label ? label(k) : k}</span><span className="tabular-nums text-muted-foreground">{v}</span></div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary/70" style={{ width: `${(v / max) * 100}%` }} /></div>
          </li>
        ))}
        {!entries.length && <li className="text-xs text-muted-foreground">Nothing yet.</li>}
      </ul>
    </section>
  );
}

export function OverviewPanel({ overview, ask }: { overview: Overview; ask: ReactNode }) {
  const { show, drawer } = useOpenItem();
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div className="space-y-4">
        {ask}
        <section className="rounded-2xl border bg-card p-4 shadow-sm">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="flex items-center gap-1.5 text-sm font-semibold"><Rss className="h-4 w-4 text-primary" />What the platform learned recently</h3>
            <Link href="?tab=feed" className="text-xs text-primary hover:underline">Full feed</Link>
          </div>
          <Timeline items={overview.feed} onOpen={show} />
        </section>
      </div>
      <aside className="space-y-4">
        <section className="rounded-2xl border bg-card p-4 shadow-sm">
          <h3 className="mb-3 flex items-center gap-1.5 text-sm font-semibold"><Sparkles className="h-4 w-4 text-primary" />Most used, 30 days</h3>
          <ul className="space-y-2">
            {overview.top_used.map((t) => (
              <li key={t.knowledge_id}>
                <button type="button" onClick={() => show(t.knowledge_id)} className="flex w-full items-center gap-2 text-left text-xs hover:text-primary">
                  <TypeIcon type={t.knowledge_type} className="h-6 w-6" />
                  <span className="min-w-0 flex-1 truncate">{t.title}</span>
                  <span className="tabular-nums text-muted-foreground">{t.uses} · {t.runs} runs</span>
                </button>
              </li>
            ))}
            {!overview.top_used.length && <li className="text-xs text-muted-foreground">No usage recorded yet. Stages record what they use from this release on.</li>}
          </ul>
        </section>
        <Bars title="By origin" data={overview.by.origin} label={(k) => <OriginChip origin={k} />} />
        <Bars title="By type" data={overview.by.type} label={(k) => prettyType(k)} />
        <Bars title="By domain" data={overview.by.domain} />
      </aside>
      {drawer}
    </div>
  );
}

function Timeline({ items, onOpen }: { items: FeedItem[]; onOpen: (id: string) => void }) {
  const days = useMemo(() => {
    const out: Record<string, FeedItem[]> = {};
    for (const i of items) (out[i.created_at.slice(0, 10)] ??= []).push(i);
    return Object.entries(out);
  }, [items]);
  if (!items.length) return <p className="text-sm text-muted-foreground">Nothing learned yet.</p>;
  return (
    <div className="space-y-4">
      {days.map(([day, list]) => (
        <div key={day}>
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{day}</p>
          <ol className="relative space-y-2 border-l pl-4">
            {list.map((i) => (
              <li key={i.knowledge_id} className="text-xs">
                <span className={cn("absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full border-2 border-card",
                                    i.status === "PROPOSED" ? "bg-amber-500" : i.is_current ? "bg-emerald-500" : "bg-muted-foreground/40")} />
                <button type="button" onClick={() => onOpen(i.knowledge_id)} className="flex w-full flex-wrap items-center gap-1.5 text-left hover:text-primary">
                  <OriginChip origin={i.origin} short />
                  <span className="font-medium">{i.title}</span>
                  <span className="text-muted-foreground">{prettyType(i.knowledge_type)} · v{i.version}</span>
                  {i.status !== "ACTIVE" && <StatusPill status={i.status} />}
                </button>
                <p className="mt-0.5 text-muted-foreground">
                  {i.domain_name} · {i.created_by} · {ago(i.created_at)}
                  {i.source_run_id && <> · run <Link href={`/runs/${i.source_run_id}`} className="text-primary hover:underline">{i.run_name ?? i.source_run_id.slice(0, 8)}</Link></>}
                  {i.change_note && <> · {i.change_note}</>}
                </p>
              </li>
            ))}
          </ol>
        </div>
      ))}
    </div>
  );
}

export function FeedPanel({ items }: { items: FeedItem[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const { show, drawer } = useOpenItem();
  const origin = params.get("origin") ?? "";
  const setOrigin = (o: string) => {
    const next = new URLSearchParams(params.toString());
    if (o) next.set("origin", o); else next.delete("origin");
    router.push(`${pathname}?${next}`, { scroll: false });
  };
  return (
    <section className="space-y-3 rounded-2xl border bg-card p-4 shadow-sm">
      <div className="flex flex-wrap gap-1.5">
        {["", ...Object.keys(ORIGINS)].map((o) => (
          <button key={o || "all"} type="button" onClick={() => setOrigin(o)} aria-pressed={origin === o}
                  className={cn("rounded-full border px-2.5 py-0.5 text-[11px]", origin === o ? "border-primary bg-primary/10 text-primary" : "text-muted-foreground hover:border-primary/40")}>
            {o ? ORIGINS[o].label : "Everything"}
          </button>
        ))}
      </div>
      <Timeline items={items} onOpen={show} />
      {drawer}
    </section>
  );
}

export function InboxPanel({ items }: { items: InboxItem[] }) {
  const router = useRouter();
  const { canAct } = useAccess();
  const may = canAct("KNOWLEDGE.EDIT");
  const [selected, setSelected] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [diffs, setDiffs] = useState<Record<string, KDiff>>({});
  const [msg, setMsg] = useState<{ tone: "ok" | "info" | "error"; text: string } | null>(null);
  const [pending, start] = useTransition();
  const decide = (ids: string[], decision: "approve" | "reject") => start(async () => {
    const r = await decideInbox(ids, decision, note);
    setMsg(r.ok ? { tone: "ok", text: `${r.data.decided} ${decision === "approve" ? "approved and now in use" : "rejected"}` }
      : { tone: /approval|request/i.test(r.error) ? "info" : "error", text: r.error });
    setSelected([]); router.refresh();
  });
  const diff = (p: InboxItem) => start(async () => {
    if (!p.current) return;
    const r = await knowledgeDiff(p.knowledge_id, p.current.knowledge_id, p.knowledge_id);
    if (r.ok) setDiffs({ ...diffs, [p.knowledge_id]: r.data });
  });
  if (!items.length) {
    return (
      <div className="rounded-2xl border border-dashed bg-card p-10 text-center">
        <CheckCheck className="mx-auto h-8 w-8 text-success" />
        <p className="mt-2 text-sm font-medium">Inbox zero</p>
        <p className="text-xs text-muted-foreground">Nothing learned is waiting for review. Choose what waits here in Learning policy.</p>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 rounded-xl border bg-card p-2 shadow-sm">
        <label className="flex items-center gap-1.5 px-1 text-xs">
          <input type="checkbox" checked={selected.length === items.length} onChange={() => setSelected(selected.length === items.length ? [] : items.map((i) => i.knowledge_id))} />
          Select all
        </label>
        <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Review note (optional)" className="h-8 min-w-[200px] flex-1 text-xs" />
        <Button size="sm" disabled={!may || pending || !selected.length} onClick={() => decide(selected, "approve")}><Check className="h-3.5 w-3.5" />Approve {selected.length || ""}</Button>
        <Button size="sm" variant="outline" disabled={!may || pending || !selected.length} onClick={() => decide(selected, "reject")}><X className="h-3.5 w-3.5" />Reject</Button>
        {pending && <Loader2 className="h-4 w-4 animate-spin" />}
      </div>
      {msg && <p role="status" className={cn("rounded-lg px-3 py-2 text-xs", msg.tone === "error" ? "bg-destructive/10 text-destructive" : msg.tone === "info" ? "bg-sky-50 text-sky-800 dark:bg-sky-950/40 dark:text-sky-200" : "bg-success/10 text-success")}>{msg.text}</p>}
      {items.map((p) => (
        <article key={p.knowledge_id} className={cn("rounded-2xl border bg-card shadow-sm", selected.includes(p.knowledge_id) && "border-primary ring-1 ring-primary/30")}>
          <div className="flex flex-wrap items-start gap-3 border-b px-4 py-3">
            <input type="checkbox" className="mt-1.5" aria-label={`Select ${p.title}`} checked={selected.includes(p.knowledge_id)}
                   onChange={() => setSelected(selected.includes(p.knowledge_id) ? selected.filter((x) => x !== p.knowledge_id) : [...selected, p.knowledge_id])} />
            <TypeIcon type={p.knowledge_type} />
            <div className="min-w-0 flex-1">
              <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">{p.title}<OriginChip origin={p.origin} />{p.current ? <span className="text-[11px] font-normal text-muted-foreground">update to v{p.current.version}</span> : <span className="text-[11px] font-normal text-muted-foreground">new item</span>}</p>
              <p className="text-xs text-muted-foreground">{prettyType(p.knowledge_type)} · {p.domain_name} · {p.created_by} · {ago(p.created_at)}
                {p.source_run_id && <> · from run <Link href={`/runs/${p.source_run_id}`} className="text-primary hover:underline">{p.run_name ?? p.source_run_id.slice(0, 8)}</Link></>}</p>
            </div>
            <div className="flex gap-1.5">
              {p.current && <Button size="sm" variant="ghost" disabled={pending} onClick={() => diff(p)}><ArrowLeftRight className="h-3.5 w-3.5" />Diff</Button>}
              <Button size="sm" disabled={!may || pending} onClick={() => decide([p.knowledge_id], "approve")}><Check className="h-3.5 w-3.5" />Approve</Button>
              <Button size="sm" variant="outline" disabled={!may || pending} onClick={() => decide([p.knowledge_id], "reject")}><X className="h-3.5 w-3.5" />Reject</Button>
            </div>
          </div>
          {diffs[p.knowledge_id] ? <div className="p-4"><DiffView files={diffs[p.knowledge_id].files} /></div> : (
            <div className={cn("grid gap-0", p.current && "md:grid-cols-2")}>
              {p.current && (
                <div className="border-b p-4 md:border-b-0 md:border-r">
                  <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">In use now · v{p.current.version}</p>
                  <p className="line-clamp-6 whitespace-pre-wrap text-xs">{p.current.content}</p>
                </div>
              )}
              <div className="p-4">
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-amber-600">Proposed · v{p.version}</p>
                <p className="line-clamp-6 whitespace-pre-wrap text-xs">{p.content}</p>
              </div>
            </div>
          )}
        </article>
      ))}
    </div>
  );
}

export function HealthPanel({ stale, pairs, staleDays }: {
  stale: KnowledgeItem[]; staleDays: number;
  pairs: { a_id: string; a_title: string; a_origin: string; b_id: string; b_title: string; b_origin: string; knowledge_type: string; domain_name: string; score: number }[];
}) {
  const router = useRouter();
  const { canAct } = useAccess();
  const { show, drawer } = useOpenItem();
  const [pending, start] = useTransition();
  const [msg, setMsg] = useState("");
  const verify = (id: string) => start(async () => { const r = await verifyKnowledge(id); setMsg(r.ok ? "Verified" : r.error); router.refresh(); });
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <section className="rounded-2xl border bg-card p-4 shadow-sm">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold"><TriangleAlert className="h-4 w-4 text-warning" />Stale ({stale.length})</h3>
        <p className="mb-3 text-xs text-muted-foreground">Past their review date, or never verified and unchanged for {staleDays} days. Verify what is still right; retire the rest.</p>
        {msg && <p role="status" className="mb-2 text-xs text-muted-foreground">{msg}</p>}
        <ul className="divide-y">
          {stale.map((i) => (
            <li key={i.knowledge_id} className="flex items-center gap-2 py-2 text-xs">
              <TypeIcon type={i.knowledge_type} className="h-6 w-6" />
              <button type="button" onClick={() => show(i.knowledge_id)} className="min-w-0 flex-1 truncate text-left font-medium hover:text-primary">{i.title}</button>
              <span className="text-muted-foreground">{i.review_due ? `due ${i.review_due}` : `updated ${ago(i.updated_at ?? i.created_at)}`}</span>
              {canAct("KNOWLEDGE.EDIT") && i.editable && <Button size="sm" variant="ghost" disabled={pending} onClick={() => verify(i.knowledge_id)}><BadgeCheck className="h-3.5 w-3.5" />Verify</Button>}
            </li>
          ))}
          {!stale.length && <li className="py-6 text-center text-xs text-muted-foreground">Nothing is stale.</li>}
        </ul>
      </section>
      <section className="rounded-2xl border bg-card p-4 shadow-sm">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold"><Copy className="h-4 w-4 text-primary" />Likely duplicates ({pairs.length})</h3>
        <p className="mb-3 text-xs text-muted-foreground">Items in use with the same domain and type and nearly the same title. Open both, keep one, retire the other.</p>
        <ul className="space-y-2">
          {pairs.map((p) => (
            <li key={`${p.a_id}-${p.b_id}`} className="rounded-xl border p-2.5 text-xs">
              <p className="mb-1 text-[11px] text-muted-foreground">{prettyType(p.knowledge_type)} · {p.domain_name} · {Math.round(p.score)}% similar</p>
              <div className="grid gap-1.5 sm:grid-cols-2">
                {[[p.a_id, p.a_title, p.a_origin], [p.b_id, p.b_title, p.b_origin]].map(([id, title, origin]) => (
                  <button key={id} type="button" onClick={() => show(id)} className="flex items-center gap-1.5 rounded-lg bg-muted/40 px-2 py-1.5 text-left hover:bg-muted">
                    <OriginChip origin={origin} short /><span className="truncate">{title}</span>
                  </button>
                ))}
              </div>
            </li>
          ))}
          {!pairs.length && <li className="py-6 text-center text-xs text-muted-foreground">No likely duplicates.</li>}
        </ul>
      </section>
      {drawer}
    </div>
  );
}

function PolicyDialog({ onClose }: { onClose: () => void }) {
  useScrollLock();
  const router = useRouter();
  const [data, setData] = useState<Policy | null>(null);
  const [draft, setDraft] = useState<Record<string, "auto" | "review">>({});
  const [copilot, setCopilot] = useState<"auto" | "review">("review");
  const [msg, setMsg] = useState<{ tone: "ok" | "info" | "error"; text: string } | null>(null);
  const [pending, start] = useTransition();
  useEffect(() => { loadPolicy().then((r) => { if (r.ok) { setData(r.data); setDraft(r.data.policy); setCopilot(r.data.copilot); } else setMsg({ tone: "error", text: r.error }); }); }, []);
  const save = () => start(async () => {
    const r = await savePolicy(draft, copilot);
    setMsg(r.ok ? { tone: "ok", text: "Saved. New learning follows this policy." } : { tone: /approval|request/i.test(r.error) ? "info" : "error", text: r.error });
    if (r.ok) router.refresh();
  });
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4" role="dialog" aria-modal="true" aria-label="Learning policy">
      <div className="flex max-h-[85vh] w-full max-w-xl flex-col rounded-2xl border bg-background shadow-2xl">
        <div className="flex items-start justify-between border-b px-5 py-3">
          <div>
            <h3 className="flex items-center gap-1.5 text-base font-semibold"><Inbox className="h-4 w-4" />Learning policy</h3>
            <p className="text-xs text-muted-foreground">Every approval, rejection and accepted AI suggestion teaches the platform. Choose per type whether what is learned is used at once, or waits in the Review inbox. Your own edits are never queued.</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-md p-1 hover:bg-muted"><X className="h-4 w-4" /></button>
        </div>
        <div className="flex-1 space-y-1 overflow-y-auto overscroll-contain px-5 py-3">
          {!data ? <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading…</p> : (
            <>
              {[...data.types.map((t) => ({ key: t, label: prettyType(t), value: draft[t] ?? "auto", set: (v: "auto" | "review") => setDraft({ ...draft, [t]: v }) })),
                { key: "COPILOT", label: "Answers saved from the copilot", value: copilot, set: setCopilot }].map((row) => (
                <div key={row.key} className="flex items-center gap-3 rounded-lg px-2 py-1.5 hover:bg-muted/40">
                  {row.key === "COPILOT" ? <OriginChip origin="COPILOT" short /> : <TypeIcon type={row.key} className="h-6 w-6" />}
                  <span className="flex-1 text-sm">{row.label}</span>
                  <div className="flex rounded-lg border p-0.5 text-xs">
                    {(["auto", "review"] as const).map((v) => (
                      <button key={v} type="button" onClick={() => row.set(v)} aria-pressed={row.value === v}
                              className={cn("rounded-md px-2.5 py-1", row.value === v ? (v === "review" ? "bg-amber-500 text-white" : "bg-primary text-primary-foreground") : "text-muted-foreground")}>
                        {v === "auto" ? "Use at once" : "Review first"}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
        <div className="flex items-center gap-2 border-t px-5 py-3">
          {msg && <p role="status" className={cn("text-xs", msg.tone === "error" ? "text-destructive" : msg.tone === "info" ? "text-sky-700 dark:text-sky-300" : "text-success")}>{msg.text}</p>}
          <span className="ml-auto" />
          <Button size="sm" variant="ghost" onClick={onClose}>Close</Button>
          <Button size="sm" disabled={pending || !data} onClick={save}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Save policy</Button>
        </div>
      </div>
    </div>
  );
}
