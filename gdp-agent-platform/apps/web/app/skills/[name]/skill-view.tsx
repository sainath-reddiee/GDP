"use client";

import Link from "next/link";
import { useMemo, useState, useTransition } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  ArrowLeftRight, Check, FileCode2, FileText, FlaskConical, History, Loader2, Pencil, Plus, Rocket, Undo2, X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select, Textarea } from "@/components/ui/input";
import { Markdown } from "@/components/copilot/markdown";
import { DiffView } from "@/components/diff-view";
import { useAccess } from "@/components/access";
import { useScrollLock } from "@/components/use-scroll-lock";
import { cn } from "@/lib/utils";
import { CategoryIcon } from "../category-icon";
import { clearCandidate, loadDiff, moveCategory, moveLabel, saveVersion, setVersionStatus, tryOnRun } from "../actions";
import { LabelPill, OriginBadge, Sparkline } from "../skills-catalog";
import {
  ago, pretty, STAGE_LABELS, versionLabel, type SkillCard, type SkillCategory, type SkillDetail, type SkillDiff, type SkillVersion,
} from "../types";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "content", label: "Content" },
  { id: "versions", label: "Versions" },
  { id: "usage", label: "Usage" },
] as const;

type Msg = { tone: "ok" | "error" | "info"; text: string } | null;
const pendingApproval = (text: string) => /approval|request/i.test(text);

export function SkillView({ detail, card, categories, tab }: {
  detail: SkillDetail; card: SkillCard | null; categories: SkillCategory[]; tab: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const { can, canAct } = useAccess();
  const [msg, setMsg] = useState<Msg>(null);
  const [pending, start] = useTransition();
  const [editing, setEditing] = useState(false);
  const versions = detail.versions;
  const byId = useMemo(() => new Map(versions.map((v) => [v.skill_id, v])), [versions]);
  const prod = byId.get(detail.labels.production?.skill_id ?? "");
  const cand = byId.get(detail.labels.candidate?.skill_id ?? "");
  const selected = byId.get(detail.selected.skill_id) ?? versions[0];
  const category = categories.find((c) => c.category_id === (detail.category_id ?? "general"));
  const active = TABS.some((t) => t.id === tab) ? tab : "overview";

  const go = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(patch)) if (v === null) next.delete(k); else next.set(k, v);
    router.replace(`${pathname}?${next}`, { scroll: false });
  };
  const act = (fn: () => Promise<{ ok: boolean; error?: string }>, ok: string) => start(async () => {
    const r = await fn();
    if (r.ok) setMsg({ tone: "ok", text: ok });
    else setMsg({ tone: pendingApproval(r.error ?? "") ? "info" : "error", text: r.error ?? "Failed" });
    router.refresh();
  });
  const promote = (v: SkillVersion) => {
    const note = window.prompt(`Promote ${versionLabel(v)} to production? Every new run will load it. Add a release note:`, v.change_note ?? "");
    if (note === null) return;
    act(() => moveLabel(detail.skill_name, "production", v.skill_id, note), `${versionLabel(v)} is now in production`);
  };

  return (
    <div className="space-y-4">
      <header className="rounded-2xl border bg-card p-5 shadow-sm">
        <div className="flex flex-wrap items-start gap-4">
          <CategoryIcon icon={category?.icon} color={category?.color} size="lg" />
          <div className="min-w-0 flex-1">
            <h1 className="flex flex-wrap items-center gap-2 text-xl font-semibold">
              {pretty(detail.skill_name)}
              <OriginBadge origin={selected?.origin} />
            </h1>
            <p className="font-mono text-xs text-muted-foreground">{detail.skill_name}{category ? ` · ${category.name}` : ""}</p>
            <p className="mt-2 max-w-3xl text-sm text-muted-foreground">{selected?.description || card?.description || "No description."}</p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {prod && <LabelPill label="production" version={versionLabel(prod)} />}
              {cand && <LabelPill label="candidate" version={versionLabel(cand)} />}
              <span className="text-xs text-muted-foreground">{versions.length} version{versions.length === 1 ? "" : "s"}</span>
            </div>
          </div>
          <div className="flex flex-col items-end gap-2">
            <label className="flex items-center gap-2 text-xs text-muted-foreground">Viewing
              <Select value={selected?.skill_id} onChange={(e) => go({ version: e.target.value })} className="h-8 w-56 text-xs" aria-label="Version">
                {versions.map((v) => (
                  <option key={v.skill_id} value={v.skill_id}>
                    {versionLabel(v)}{v.labels.length ? ` (${v.labels.join(", ")})` : ""}{v.status !== "ACTIVE" ? ` · ${v.status.toLowerCase()}` : ""}
                  </option>
                ))}
              </Select>
            </label>
            <div className="flex flex-wrap justify-end gap-2">
              {can("SKILL.EDIT") && (
                <Button size="sm" variant="outline" onClick={() => setEditing(true)}><Pencil className="h-3.5 w-3.5" />Edit as new version</Button>
              )}
              {cand && canAct("SKILL.RELEASE") && (
                <Button size="sm" disabled={pending} onClick={() => promote(cand)}><Rocket className="h-3.5 w-3.5" />Promote candidate</Button>
              )}
            </div>
          </div>
        </div>
        {msg && (
          <p role={msg.tone === "error" ? "alert" : "status"}
             className={cn("mt-3 rounded-lg px-3 py-2 text-xs", msg.tone === "error" ? "bg-destructive/10 text-destructive"
               : msg.tone === "info" ? "bg-sky-50 text-sky-800 dark:bg-sky-950/40 dark:text-sky-200" : "bg-success/10 text-success")}>
            {msg.text}
          </p>
        )}
      </header>

      <nav className="flex gap-1 border-b" aria-label="Skill sections">
        {TABS.map((t) => (
          <button key={t.id} type="button" onClick={() => go({ tab: t.id })} aria-current={active === t.id ? "page" : undefined}
                  className={cn("-mb-px border-b-2 px-3 py-2 text-sm transition-colors",
                                active === t.id ? "border-primary font-medium text-primary" : "border-transparent text-muted-foreground hover:text-foreground")}>
            {t.label}
            {t.id === "versions" && <span className="ml-1.5 rounded-full bg-muted px-1.5 text-[10px]">{versions.length}</span>}
          </button>
        ))}
      </nav>

      {active === "overview" && (
        <Overview detail={detail} card={card} prod={prod} cand={cand} categories={categories} byId={byId}
                  canEdit={can("SKILL.EDIT")} onMoveCategory={(id) => act(() => moveCategory(detail.skill_name, id), "Category changed")} />
      )}
      {active === "content" && <Content files={detail.selected.files} config={detail.selected.config} version={selected} />}
      {active === "versions" && (
        <Versions detail={detail} prod={prod} cand={cand} busy={pending}
                  canEdit={can("SKILL.EDIT")} canRelease={canAct("SKILL.RELEASE")}
                  onView={(v) => go({ version: v.skill_id, tab: "content" })}
                  onPromote={promote}
                  onCandidate={(v) => act(() => moveLabel(detail.skill_name, "candidate", v.skill_id), `${versionLabel(v)} is the candidate`)}
                  onClearCandidate={() => act(() => clearCandidate(detail.skill_name), "Candidate cleared")}
                  onStatus={(v, a) => act(() => setVersionStatus(detail.skill_name, v.skill_id, a), a === "retire" ? "Version retired" : "Version restored")} />
      )}
      {active === "usage" && (
        <Usage detail={detail} byId={byId} canEdit={can("SKILL.EDIT")}
               onClear={(runId) => act(() => tryOnRun(runId, detail.skill_name, null), "The run is back on production")} />
      )}

      {editing && selected && (
        <Editor detail={detail} base={selected} onClose={() => setEditing(false)}
                onSaved={(text) => { setEditing(false); setMsg({ tone: "ok", text }); go({ version: null, tab: "versions" }); router.refresh(); }} />
      )}
    </div>
  );
}

function Tile({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded-xl border bg-card px-4 py-3 shadow-sm">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <div className="mt-1 text-sm font-semibold">{children}</div>
      {hint && <p className="mt-0.5 text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

function Overview({ detail, card, prod, cand, categories, byId, canEdit, onMoveCategory }: {
  detail: SkillDetail; card: SkillCard | null; prod?: SkillVersion; cand?: SkillVersion; categories: SkillCategory[];
  byId: Map<string, SkillVersion>; canEdit: boolean; onMoveCategory: (id: string) => void;
}) {
  const stages = detail.bindings;
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Tile label="Production" hint={detail.labels.production ? `by ${detail.labels.production.moved_by === "SEED" ? "deploy" : detail.labels.production.moved_by}, ${ago(detail.labels.production.moved_at)}` : undefined}>
            {prod ? versionLabel(prod) : "None"}
          </Tile>
          <Tile label="Candidate" hint={cand ? `by ${cand.created_by}` : "nothing waiting"}>{cand ? versionLabel(cand) : "None"}</Tile>
          <Tile label="Loads, 30 days" hint={card ? `${card.runs_30d} runs` : undefined}>
            <span className="flex items-center gap-2">{card?.loads_30d ?? 0}<Sparkline values={card?.daily ?? []} className="text-primary" /></span>
          </Tile>
          <Tile label="Last used">{ago(card?.last_used)}</Tile>
        </div>

        <section className="rounded-xl border bg-card p-4 shadow-sm">
          <h3 className="text-sm font-semibold">Where it is used</h3>
          <p className="mb-3 text-xs text-muted-foreground">Stages load their bound skills, in order, before they act.
            Change the bindings in <Link href="/admin?section=skills" className="text-primary hover:underline">Admin, Skills per stage</Link>.</p>
          {stages.length ? (
            <div className="flex flex-wrap gap-2">
              {stages.map((b) => (
                <span key={b.stage} className={cn("inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs",
                                                  b.enabled ? "bg-card" : "bg-muted text-muted-foreground line-through")}>
                  {STAGE_LABELS[b.stage] ?? b.stage}
                  {b.standard === "GDP" && <span className="rounded bg-violet-100 px-1 text-[9px] text-violet-700 dark:bg-violet-950 dark:text-violet-300">GDP runs</span>}
                </span>
              ))}
            </div>
          ) : <p className="text-xs text-muted-foreground">Not bound to any stage. Agents can still load it by name, and other skills may reference it.</p>}
          {detail.children.length > 0 && (
            <>
              <h4 className="mb-1.5 mt-4 text-xs font-semibold">Sub-skills</h4>
              <div className="flex flex-wrap gap-1.5">
                {detail.children.map((c) => (
                  <Link key={c} href={`/skills/${encodeURIComponent(c)}`} className="rounded-md border px-2 py-0.5 text-xs hover:border-primary hover:text-primary">{pretty(c)}</Link>
                ))}
              </div>
            </>
          )}
        </section>

        <section className="rounded-xl border bg-card p-4 shadow-sm">
          <h3 className="mb-3 text-sm font-semibold">Recent runs</h3>
          <RunsTable runs={detail.runs.slice(0, 8)} byId={byId} />
        </section>
      </div>

      <aside className="space-y-4">
        <section className="rounded-xl border bg-card p-4 shadow-sm">
          <h3 className="mb-2 text-sm font-semibold">Category</h3>
          <Select value={detail.category_id ?? "general"} disabled={!canEdit} onChange={(e) => onMoveCategory(e.target.value)} className="h-9 w-full text-sm">
            {categories.map((c) => <option key={c.category_id} value={c.category_id}>{c.name}</option>)}
          </Select>
        </section>
        <section className="rounded-xl border bg-card p-4 shadow-sm">
          <h3 className="mb-3 flex items-center gap-1.5 text-sm font-semibold"><History className="h-4 w-4" />Release history</h3>
          {detail.label_history.length ? (
            <ol className="relative space-y-3 border-l pl-4">
              {detail.label_history.slice(0, 10).map((h, i) => {
                const to = h.to_skill_id ? byId.get(h.to_skill_id) : undefined;
                return (
                  <li key={i} className="text-xs">
                    <span className={cn("absolute -left-[5px] mt-1 h-2.5 w-2.5 rounded-full border-2 border-card",
                                        h.label === "production" ? "bg-emerald-500" : "bg-amber-500")} />
                    <p><span className="font-medium capitalize">{h.label}</span> {to ? `→ ${versionLabel(to)}` : "cleared"}</p>
                    <p className="text-muted-foreground">{h.moved_by === "SEED" ? "deploy" : h.moved_by} · {ago(h.moved_at)}</p>
                    {h.note && <p className="mt-0.5 text-muted-foreground">“{h.note}”</p>}
                  </li>
                );
              })}
            </ol>
          ) : <p className="text-xs text-muted-foreground">No releases recorded yet.</p>}
        </section>
      </aside>
    </div>
  );
}

function Content({ files, config, version }: { files: { path: string; content: string }[]; config: unknown; version?: SkillVersion }) {
  const all = config ? [...files, { path: "config.json", content: JSON.stringify(config, null, 2) }] : files;
  const [path, setPath] = useState(all[0]?.path ?? "SKILL.md");
  const file = all.find((f) => f.path === path) ?? all[0];
  const md = file?.path.endsWith(".md");
  return (
    <div className="grid gap-4 lg:grid-cols-[240px_minmax(0,1fr)]">
      <aside className="space-y-0.5 rounded-xl border bg-card p-2 shadow-sm lg:sticky lg:top-4 lg:self-start">
        <p className="px-2 pb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{all.length} files · {versionLabel(version)}</p>
        {all.map((f) => (
          <button key={f.path} type="button" onClick={() => setPath(f.path)} aria-current={f.path === file?.path ? "true" : undefined}
                  className={cn("flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs",
                                f.path === file?.path ? "bg-primary/10 font-medium text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground")}>
            {f.path.endsWith(".md") ? <FileText className="h-3.5 w-3.5 shrink-0" /> : <FileCode2 className="h-3.5 w-3.5 shrink-0" />}
            <span className="truncate font-mono">{f.path}</span>
          </button>
        ))}
      </aside>
      <article className="min-w-0 rounded-xl border bg-card p-5 shadow-sm">
        <p className="mb-3 font-mono text-xs text-muted-foreground">{file?.path}</p>
        {file && (md ? <div className="prose-sm max-w-none text-sm"><Markdown text={file.content} /></div>
          : <pre className="overflow-x-auto rounded-lg bg-muted/50 p-3 font-mono text-[11px] leading-relaxed">{file.content}</pre>)}
      </article>
    </div>
  );
}

function Versions({ detail, prod, cand, busy, canEdit, canRelease, onView, onPromote, onCandidate, onClearCandidate, onStatus }: {
  detail: SkillDetail; prod?: SkillVersion; cand?: SkillVersion; busy: boolean; canEdit: boolean; canRelease: boolean;
  onView: (v: SkillVersion) => void; onPromote: (v: SkillVersion) => void; onCandidate: (v: SkillVersion) => void;
  onClearCandidate: () => void; onStatus: (v: SkillVersion, a: "retire" | "restore") => void;
}) {
  const versions = detail.versions;
  const [base, setBase] = useState(prod?.skill_id ?? versions[1]?.skill_id ?? versions[0]?.skill_id ?? "");
  const [head, setHead] = useState(cand?.skill_id ?? versions[0]?.skill_id ?? "");
  const [diff, setDiff] = useState<SkillDiff | null>(null);
  const [error, setError] = useState("");
  const [loading, start] = useTransition();
  const compare = (b: string, h: string) => {
    setBase(b); setHead(h);
    start(async () => {
      const r = await loadDiff(detail.skill_name, b, h);
      if (r.ok) { setDiff(r.data); setError(""); } else setError(r.error);
    });
  };
  return (
    <div className="space-y-4">
      <section className="rounded-xl border bg-card p-4 shadow-sm">
        <div className="flex flex-wrap items-end gap-2">
          <h3 className="mr-auto flex items-center gap-1.5 text-sm font-semibold"><ArrowLeftRight className="h-4 w-4" />Compare versions</h3>
          <label className="text-xs text-muted-foreground">Base
            <Select value={base} onChange={(e) => setBase(e.target.value)} className="ml-1 h-8 w-48 text-xs">
              {versions.map((v) => <option key={v.skill_id} value={v.skill_id}>{versionLabel(v)}{v.labels.length ? ` (${v.labels.join(", ")})` : ""}</option>)}
            </Select>
          </label>
          <label className="text-xs text-muted-foreground">Compare
            <Select value={head} onChange={(e) => setHead(e.target.value)} className="ml-1 h-8 w-48 text-xs">
              {versions.map((v) => <option key={v.skill_id} value={v.skill_id}>{versionLabel(v)}{v.labels.length ? ` (${v.labels.join(", ")})` : ""}</option>)}
            </Select>
          </label>
          <Button size="sm" variant="outline" disabled={loading || !base || !head || base === head} onClick={() => compare(base, head)}>
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ArrowLeftRight className="h-3.5 w-3.5" />}Compare
          </Button>
        </div>
        {error && <p role="alert" className="mt-2 text-xs text-destructive">{error}</p>}
        {diff && (
          <div className="mt-4 space-y-2">
            <p className="text-xs text-muted-foreground">
              {versionLabel(diff.base)} → {versionLabel(diff.head)}: <span className="text-emerald-600">+{diff.added}</span>{" "}
              <span className="text-red-600">-{diff.removed}</span> lines
            </p>
            <DiffView files={diff.files} />
          </div>
        )}
      </section>

      <ol className="space-y-2">
        {versions.map((v, i) => {
          const isProd = v.skill_id === prod?.skill_id;
          const isCand = v.skill_id === cand?.skill_id;
          const prev = versions[i + 1];
          return (
            <li key={v.skill_id} className={cn("rounded-xl border bg-card p-4 shadow-sm", isProd && "border-emerald-300 dark:border-emerald-900",
                                               v.status === "RETIRED" && "opacity-60")}>
              <div className="flex flex-wrap items-start gap-3">
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">
                    {versionLabel(v)}
                    {isProd && <LabelPill label="production" />}
                    {isCand && <LabelPill label="candidate" />}
                    {v.status === "DRAFT" && <LabelPill label="draft" />}
                    {v.status === "RETIRED" && <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">retired</span>}
                    <OriginBadge origin={v.origin} />
                  </p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {v.created_by === "SEED" ? "Repository deploy" : v.created_by} · {ago(v.created_at)} · {v.loads_30d} loads in 30 days
                    <span className="ml-1 font-mono">#{v.checksum.slice(0, 8)}</span>
                  </p>
                  {v.change_note && <p className="mt-1.5 text-xs">{v.change_note}</p>}
                </div>
                <div className="flex flex-wrap justify-end gap-1.5">
                  <Button size="sm" variant="ghost" onClick={() => onView(v)}><FileText className="h-3.5 w-3.5" />View</Button>
                  {prev && <Button size="sm" variant="ghost" disabled={busy} onClick={() => compare(prev.skill_id, v.skill_id)}>
                    <ArrowLeftRight className="h-3.5 w-3.5" />Changes</Button>}
                  {canEdit && !isProd && !isCand && v.status !== "RETIRED" && (
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => onCandidate(v)}><FlaskConical className="h-3.5 w-3.5" />Set as candidate</Button>
                  )}
                  {canEdit && isCand && <Button size="sm" variant="ghost" disabled={busy} onClick={onClearCandidate}><X className="h-3.5 w-3.5" />Clear candidate</Button>}
                  {canRelease && !isProd && v.status !== "RETIRED" && (
                    <Button size="sm" disabled={busy} onClick={() => onPromote(v)}>
                      {(prod && (v.revision ?? 0) < (prod.revision ?? 0)) ? <><Undo2 className="h-3.5 w-3.5" />Roll back to this</> : <><Rocket className="h-3.5 w-3.5" />Promote</>}
                    </Button>
                  )}
                  {canEdit && !isProd && (
                    <Button size="sm" variant="ghost" disabled={busy} onClick={() => onStatus(v, v.status === "RETIRED" ? "restore" : "retire")}>
                      {v.status === "RETIRED" ? "Restore" : "Retire"}
                    </Button>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function RunsTable({ runs, byId }: { runs: SkillDetail["runs"]; byId: Map<string, SkillVersion> }) {
  if (!runs.length) return <p className="text-xs text-muted-foreground">No run has loaded this skill yet (usage is recorded per run from this release on).</p>;
  return (
    <table className="w-full text-xs">
      <thead className="text-left text-[10px] uppercase tracking-wide text-muted-foreground">
        <tr><th className="py-1.5">Run</th><th>Version</th><th>Chosen by</th><th className="text-right">Loads</th><th className="text-right">When</th></tr>
      </thead>
      <tbody className="divide-y">
        {runs.map((r) => {
          const v = r.skill_id ? byId.get(r.skill_id) : undefined;
          return (
            <tr key={`${r.run_id}-${r.skill_id}`}>
              <td className="py-1.5"><Link href={`/runs/${r.run_id}`} className="font-medium hover:text-primary">{r.run_name ?? r.run_id.slice(0, 8)}</Link>
                {r.current_state && <span className="ml-1.5 text-[10px] text-muted-foreground">{r.current_state}</span>}</td>
              <td>{v ? versionLabel(v) : r.version ? `v${r.version.split("+")[0]}` : ""}</td>
              <td>{r.picked_by === "override" ? <LabelPill label="candidate" version="trial" /> : <span className="text-muted-foreground">{r.picked_by ?? "production"}</span>}</td>
              <td className="text-right tabular-nums">{r.loads}</td>
              <td className="text-right text-muted-foreground">{ago(r.loaded_at)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Usage({ detail, byId, canEdit, onClear }: {
  detail: SkillDetail; byId: Map<string, SkillVersion>; canEdit: boolean; onClear: (runId: string) => void;
}) {
  return (
    <div className="space-y-4">
      <section className="rounded-xl border bg-card p-4 shadow-sm">
        <h3 className="text-sm font-semibold">Runs trying a specific version</h3>
        <p className="mb-3 text-xs text-muted-foreground">Set from a run page (Skills used, Try a version). These runs load the chosen version instead of production.</p>
        {detail.overrides.length ? (
          <ul className="divide-y text-xs">
            {detail.overrides.map((o) => (
              <li key={o.run_id} className="flex items-center gap-3 py-2">
                <Link href={`/runs/${o.run_id}`} className="font-medium hover:text-primary">{o.run_name ?? o.run_id.slice(0, 8)}</Link>
                <LabelPill label="candidate" version={versionLabel(byId.get(o.skill_id))} />
                <span className="text-muted-foreground">by {o.set_by} · {ago(o.set_at)}</span>
                {canEdit && <Button size="sm" variant="ghost" className="ml-auto" onClick={() => onClear(o.run_id)}>Back to production</Button>}
              </li>
            ))}
          </ul>
        ) : <p className="text-xs text-muted-foreground">No run is trying another version.</p>}
      </section>
      <section className="rounded-xl border bg-card p-4 shadow-sm">
        <h3 className="mb-3 text-sm font-semibold">Every run that loaded it</h3>
        <RunsTable runs={detail.runs} byId={byId} />
      </section>
    </div>
  );
}

function Editor({ detail, base, onClose, onSaved }: {
  detail: SkillDetail; base: SkillVersion; onClose: () => void; onSaved: (text: string) => void;
}) {
  useScrollLock();
  const [files, setFiles] = useState(detail.selected.files.map((f) => ({ ...f })));
  const [current, setCurrent] = useState(0);
  const [note, setNote] = useState("");
  const [description, setDescription] = useState(base.description ?? "");
  const [newPath, setNewPath] = useState("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const original = detail.selected.files;
  const changed = files.some((f, i) => f.content !== original[i]?.content) || files.length !== original.length || description !== (base.description ?? "");
  const assemble = () => files[0].content + files.slice(1).map((f) => `\n\n# ${f.path}\n${f.content}`).join("");
  const save = (status: "DRAFT" | "ACTIVE") => start(async () => {
    const r = await saveVersion(detail.skill_name, {
      content: assemble(), base_skill_id: base.skill_id, description: description || null, change_note: note.trim(),
      status, set_candidate: true,
    });
    if (r.ok) onSaved(`Saved v${r.data.version} as the candidate. Try it on a run, compare it, then promote it.`);
    else setError(r.error);
  });
  const addFile = () => {
    const p = newPath.trim().replace(/^\/+/, "");
    if (!/^[\w./-]+\.(md|sql|ya?ml|html)$/.test(p) || files.some((f) => f.path === p)) { setError("Use a new path ending in .md, .sql, .yml or .html"); return; }
    setFiles([...files, { path: p, content: "" }]);
    setCurrent(files.length);
    setNewPath("");
    setError("");
  };
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Edit skill">
      <button type="button" aria-label="Close" className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="relative flex h-full w-[920px] max-w-full flex-col border-l bg-background shadow-2xl">
        <div className="flex items-center gap-3 border-b px-5 py-3">
          <Pencil className="h-4 w-4 text-primary" />
          <div className="min-w-0 flex-1">
            <h3 className="text-base font-semibold">New version of {pretty(detail.skill_name)}</h3>
            <p className="text-xs text-muted-foreground">Starting from {versionLabel(base)}. Saving never changes production: it creates a candidate.</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-md p-1 hover:bg-muted"><X className="h-4 w-4" /></button>
        </div>
        <div className="grid min-h-0 flex-1 grid-cols-[200px_minmax(0,1fr)]">
          <aside className="space-y-0.5 overflow-y-auto overscroll-contain border-r p-2">
            {files.map((f, i) => (
              <button key={f.path} type="button" onClick={() => setCurrent(i)}
                      className={cn("flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-xs",
                                    i === current ? "bg-primary/10 font-medium text-primary" : "text-muted-foreground hover:bg-muted")}>
                <span className="min-w-0 flex-1 truncate font-mono">{f.path}</span>
                {f.content !== original[i]?.content && <span className="h-1.5 w-1.5 rounded-full bg-amber-500" title="changed" />}
              </button>
            ))}
            <div className="mt-2 space-y-1 border-t pt-2">
              <Input value={newPath} onChange={(e) => setNewPath(e.target.value)} placeholder="references/new.md" className="h-7 text-[11px]" />
              <Button size="sm" variant="ghost" className="w-full" onClick={addFile}><Plus className="h-3 w-3" />Add file</Button>
            </div>
          </aside>
          <div className="flex min-h-0 flex-col">
            <Textarea value={files[current]?.content ?? ""} spellCheck={false}
                      onChange={(e) => setFiles(files.map((f, i) => (i === current ? { ...f, content: e.target.value } : f)))}
                      className="min-h-0 flex-1 resize-none rounded-none border-0 font-mono text-[12px] leading-relaxed focus-visible:ring-0"
                      aria-label={`Content of ${files[current]?.path}`} />
          </div>
        </div>
        <div className="space-y-2 border-t px-5 py-3">
          <div className="grid gap-2 md:grid-cols-2">
            <label className="space-y-1 text-xs">What changed and why (required)
              <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Tighten freshness checks for daily feeds" />
            </label>
            <label className="space-y-1 text-xs">Description
              <Input value={description} onChange={(e) => setDescription(e.target.value)} />
            </label>
          </div>
          <div className="flex items-center gap-2">
            {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
            <span className="ml-auto" />
            <Button size="sm" variant="ghost" onClick={onClose}>Cancel</Button>
            <Button size="sm" variant="outline" disabled={pending || !changed || note.trim().length < 3} onClick={() => save("DRAFT")}>Save as draft</Button>
            <Button size="sm" disabled={pending || !changed || note.trim().length < 3} onClick={() => save("ACTIVE")}>
              {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Save as candidate
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
