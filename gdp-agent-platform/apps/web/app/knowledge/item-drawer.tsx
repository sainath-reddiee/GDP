"use client";

import Link from "next/link";
import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeftRight, BadgeCheck, Loader2, Lock, Pencil, RotateCcw, Undo2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DiffView } from "@/components/diff-view";
import { TagEditor } from "@/components/tag-editor";
import { useAccess } from "@/components/access";
import { useScrollLock } from "@/components/use-scroll-lock";
import { cn } from "@/lib/utils";
import { ago } from "../skills/types";
import {
  knowledgeDiff, knowledgeUsage, knowledgeVersions, rollbackKnowledge, setKnowledgeStatus, verifyKnowledge,
  type KDiff, type KnowledgeItem, type KUsage,
} from "./actions";
import { OriginChip, prettyType, StatusPill, TypeIcon, Verified } from "./knowledge-ui";

const TABS = ["Content", "Versions", "Provenance", "Usage"] as const;
type Tab = (typeof TABS)[number];

/** One knowledge item: what it says, every version (diff, restore), where it came from and which runs used it. */
export function KnowledgeDrawer({ item: initial, onClose, onEdit, onChange }: {
  item: KnowledgeItem; onClose: () => void; onEdit: (i: KnowledgeItem) => void; onChange?: (i: KnowledgeItem) => void;
}) {
  useScrollLock();
  // the drawer keeps its own copy so restore, verify and rollback show the new state (and Edit gets the current version)
  const [item, setItem] = useState(initial);
  const router = useRouter();
  const { can, canAct } = useAccess();
  const [tab, setTab] = useState<Tab>("Content");
  const [versions, setVersions] = useState<KnowledgeItem[] | null>(null);
  const [usage, setUsage] = useState<KUsage | null>(null);
  const [versionsError, setVersionsError] = useState("");
  const [usageError, setUsageError] = useState("");
  const [diff, setDiff] = useState<KDiff | null>(null);
  const [msg, setMsg] = useState<{ tone: "ok" | "error" | "info"; text: string } | null>(null);
  const [pending, start] = useTransition();
  const editable = item.editable && canAct("KNOWLEDGE.EDIT");

  useEffect(() => {
    if (tab === "Versions" && !versions && !versionsError) knowledgeVersions(item.knowledge_id).then((r) => r.ok ? setVersions(r.data.versions) : setVersionsError(r.error));
    if (tab === "Usage" && !usage && !usageError) knowledgeUsage(item.knowledge_id).then((r) => r.ok ? setUsage(r.data) : setUsageError(r.error));
  }, [tab, versions, usage, versionsError, usageError, item.knowledge_id]);

  const act = (fn: () => Promise<{ ok: boolean; error?: string }>, ok: string, close = false) => start(async () => {
    const r = await fn();
    setMsg(r.ok ? { tone: "ok", text: ok } : { tone: /approval|request/i.test(r.error ?? "") ? "info" : "error", text: r.error ?? "Failed" });
    if (!r.ok) return;
    router.refresh();
    if (close) { onClose(); return; }
    // reload the lineage: rollback writes a new version, so follow the one now in use
    const v = await knowledgeVersions(item.knowledge_id);
    setUsage(null); setUsageError("");
    if (!v.ok) { setVersions(null); setVersionsError(v.error); return; }
    setVersions(v.data.versions); setVersionsError("");
    const fresh = v.data.versions.find((x) => x.is_current) ?? v.data.versions.find((x) => x.knowledge_id === item.knowledge_id);
    if (fresh) { setItem(fresh); onChange?.(fresh); }
  });
  const compare = (base: KnowledgeItem, head: KnowledgeItem) => start(async () => {
    const r = await knowledgeDiff(head.knowledge_id, base.knowledge_id, head.knowledge_id);
    if (r.ok) setDiff(r.data); else setMsg({ tone: "error", text: r.error });
  });

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={item.title}>
      <button type="button" aria-label="Close" className="absolute inset-0 bg-black/30" onClick={onClose} />
      <aside className="relative flex h-full w-[760px] max-w-full flex-col border-l bg-background shadow-2xl">
        <header className="border-b px-5 py-4">
          <div className="flex items-start gap-3">
            <TypeIcon type={item.knowledge_type} className="h-9 w-9" />
            <div className="min-w-0 flex-1">
              <h2 className="flex flex-wrap items-center gap-2 text-base font-semibold">{item.title}<Verified by={item.verified_by} at={item.verified_at} />
                {!item.editable && <span title={item.read_only_reason ?? ""}><Lock className="h-3.5 w-3.5 text-muted-foreground" /></span>}</h2>
              <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                {prettyType(item.knowledge_type)} · {item.domain_name} · v{item.version} <StatusPill status={item.status} /> <OriginChip origin={item.origin} />
              </p>
              {item.lineage_id && <div className="mt-2"><TagEditor entityType="KNOWLEDGE" entityKey={item.lineage_id} canEdit={can("TAG.MANAGE")} /></div>}
            </div>
            <button type="button" onClick={onClose} aria-label="Close" className="rounded-md p-1 hover:bg-muted"><X className="h-4 w-4" /></button>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {editable && item.is_current !== false && <Button size="sm" variant="outline" onClick={() => onEdit(item)}><Pencil className="h-3.5 w-3.5" />Edit</Button>}
            {editable && item.status === "ACTIVE" && (
              <Button size="sm" variant="outline" disabled={pending} onClick={() => act(() => verifyKnowledge(item.knowledge_id), "Verified. It is due for review again in 180 days.")}>
                <BadgeCheck className="h-3.5 w-3.5" />{item.verified_at ? "Verify again" : "Mark verified"}
              </Button>
            )}
            {editable && (item.status === "RETIRED"
              ? <Button size="sm" variant="ghost" disabled={pending} onClick={() => act(() => setKnowledgeStatus(item.knowledge_id, "restore"), "Restored")}><RotateCcw className="h-3.5 w-3.5" />Restore</Button>
              : item.status === "ACTIVE" && <Button size="sm" variant="ghost" disabled={pending} onClick={() => act(() => setKnowledgeStatus(item.knowledge_id, "retire"), "Retired: agents stop using it", true)}>Retire</Button>)}
          </div>
          {msg && <p role={msg.tone === "error" ? "alert" : "status"} className={cn("mt-2 rounded-lg px-3 py-1.5 text-xs",
            msg.tone === "error" ? "bg-destructive/10 text-destructive" : msg.tone === "info" ? "bg-sky-50 text-sky-800 dark:bg-sky-950/40 dark:text-sky-200" : "bg-success/10 text-success")}>{msg.text}</p>}
        </header>
        <nav className="flex gap-1 border-b px-5">
          {TABS.map((t) => (
            <button key={t} type="button" onClick={() => setTab(t)} aria-current={tab === t ? "page" : undefined}
                    className={cn("-mb-px border-b-2 px-3 py-2 text-sm", tab === t ? "border-primary font-medium text-primary" : "border-transparent text-muted-foreground hover:text-foreground")}>{t}</button>
          ))}
        </nav>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain px-5 py-4">
          {tab === "Content" && (
            <>
              <p className="whitespace-pre-wrap text-sm leading-relaxed">{item.content}</p>
              {item.content_json != null && (
                <div>
                  <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Structured (what mapping, STTM and quality read)</p>
                  <pre className="overflow-x-auto rounded-lg bg-muted/50 p-3 font-mono text-[11px]">{JSON.stringify(item.content_json, null, 2)}</pre>
                </div>
              )}
              {item.change_note && <p className="text-xs text-muted-foreground"><span className="font-medium text-foreground">Change note:</span> {item.change_note}</p>}
            </>
          )}

          {tab === "Versions" && (
            versionsError ? <TabError text={versionsError} onRetry={() => setVersionsError("")} />
            : !versions ? <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading versions…</p> : (
              <div className="space-y-3">
                {diff && (
                  <div className="space-y-2">
                    <div className="flex items-center justify-between text-xs">
                      <span>v{diff.base.version} → v{diff.head.version}: <span className="text-emerald-600">+{diff.added}</span> <span className="text-red-600">-{diff.removed}</span></span>
                      <button type="button" onClick={() => setDiff(null)} className="text-primary hover:underline">Close diff</button>
                    </div>
                    <DiffView files={diff.files} />
                  </div>
                )}
                <ol className="relative space-y-3 border-l pl-5">
                  {versions.map((v, i) => {
                    const prev = versions[i + 1];
                    return (
                      <li key={v.knowledge_id} className="text-xs">
                        <span className={cn("absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full border-2 border-background",
                                            v.is_current ? "bg-emerald-500" : v.status === "PROPOSED" ? "bg-amber-500" : "bg-muted-foreground/40")} />
                        <div className="rounded-xl border bg-card p-3 shadow-sm">
                          <p className="flex flex-wrap items-center gap-2 font-medium">v{v.version} <StatusPill status={v.status} />{v.is_current && <span className="text-emerald-600">in use</span>}
                            <OriginChip origin={v.origin} short /></p>
                          <p className="mt-0.5 text-muted-foreground">{v.created_by} · {ago(v.created_at)}{v.run_name ? <> · from run <Link className="text-primary hover:underline" href={`/runs/${v.source_run_id}`}>{v.run_name}</Link></> : ""}</p>
                          {v.change_note && <p className="mt-1">{v.change_note}</p>}
                          {v.review_note && <p className="mt-1 text-muted-foreground">Reviewer: {v.review_note}</p>}
                          <p className="mt-1 line-clamp-2 text-muted-foreground">{v.content}</p>
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {prev && <Button size="sm" variant="ghost" disabled={pending} onClick={() => compare(prev, v)}><ArrowLeftRight className="h-3 w-3" />Changes from v{prev.version}</Button>}
                            {editable && !v.is_current && ["SUPERSEDED", "ACTIVE"].includes(v.status) && (
                              <Button size="sm" variant="outline" disabled={pending}
                                      onClick={() => act(() => rollbackKnowledge(v.knowledge_id), `v${v.version} is in use again (saved as a new version)`)}>
                                <Undo2 className="h-3 w-3" />Restore this version
                              </Button>
                            )}
                          </div>
                        </div>
                      </li>
                    );
                  })}
                </ol>
              </div>
            )
          )}

          {tab === "Provenance" && (
            <dl className="grid gap-3 text-sm sm:grid-cols-2">
              <Fact label="Where it came from"><OriginChip origin={item.origin} /></Fact>
              <Fact label="Taught by run">{item.source_run_id ? <Link href={`/runs/${item.source_run_id}`} className="text-primary hover:underline">{item.source_run_id.slice(0, 8)}</Link> : "None"}</Fact>
              <Fact label="Written by">{item.created_by} · {ago(item.created_at)}</Fact>
              <Fact label="Confidence">{item.confidence != null ? `${Math.round(item.confidence * 100)}%` : "Not recorded"}</Fact>
              <Fact label="Verified">{item.verified_at ? `${item.verified_by} · ${ago(item.verified_at)}` : "Not yet"}</Fact>
              <Fact label="Review due">{item.review_due ?? "Not set"}</Fact>
              <Fact label="Reference key"><span className="font-mono text-xs">{item.source_reference ?? "None"}</span></Fact>
              <Fact label="Reviewed by">{item.reviewed_by ? `${item.reviewed_by}${item.review_note ? `: ${item.review_note}` : ""}` : "Not reviewed"}</Fact>
            </dl>
          )}

          {tab === "Usage" && (
            usageError ? <TabError text={usageError} onRetry={() => setUsageError("")} />
            : !usage ? <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading usage…</p> : (
              <div className="space-y-3">
                <div className="flex flex-wrap gap-2">
                  {Object.entries(usage.by_stage).map(([stage, n]) => (
                    <span key={stage} className="rounded-lg border bg-card px-2.5 py-1 text-xs"><span className="font-semibold">{n}</span> {stage.toLowerCase()}</span>
                  ))}
                  {!usage.total && <p className="text-sm text-muted-foreground">No stage has used this item yet (usage is recorded from this release on).</p>}
                </div>
                {usage.runs.length > 0 && (
                  <table className="w-full text-xs">
                    <thead className="text-left text-[10px] uppercase tracking-wide text-muted-foreground"><tr><th className="py-1">Run</th><th>Stage</th><th>Version</th><th className="text-right">Uses</th><th className="text-right">Last</th></tr></thead>
                    <tbody className="divide-y">
                      {usage.runs.map((r, i) => (
                        <tr key={i}>
                          <td className="py-1.5">{r.run_id ? <Link href={`/runs/${r.run_id}`} className="font-medium hover:text-primary">{r.run_name ?? r.run_id.slice(0, 8)}</Link> : "Outside a run"}</td>
                          <td>{r.stage.toLowerCase()}</td><td>v{r.version}</td><td className="text-right tabular-nums">{r.uses}</td><td className="text-right text-muted-foreground">{ago(r.last_used)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )
          )}
        </div>
      </aside>
    </div>
  );
}

function TabError({ text, onRetry }: { text: string; onRetry: () => void }) {
  return (
    <p role="alert" className="flex flex-wrap items-center gap-2 rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">
      {text}<button type="button" onClick={onRetry} className="text-xs font-medium underline">Try again</button>
    </p>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border bg-card p-3">
      <dt className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-1">{children}</dd>
    </div>
  );
}
