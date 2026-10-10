"use client";

import Link from "next/link";
import { useMemo, useState, useTransition } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ChevronDown, ChevronRight, FlaskConical, LayoutGrid, List, Loader2, Plus, Rocket, Search, Settings2, Sparkles, Trash2, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { useAccess } from "@/components/access";
import { useScrollLock } from "@/components/use-scroll-lock";
import { cn } from "@/lib/utils";
import { CATEGORY_COLORS, CategoryIcon, ICON_NAMES } from "./category-icon";
import { deleteCategory, moveCategory, saveCategory } from "./actions";
import { SkillBuilder } from "./skill-builder";
import { ago, pretty, STAGE_LABELS, versionLabel, type SkillCard, type SkillCategory, type SkillsResponse } from "./types";

type Status = "all" | "production" | "candidate" | "unused";
const ORIGINS = ["REPOSITORY", "USER", "AI"] as const;
const ORIGIN_LABEL: Record<string, string> = { REPOSITORY: "Repository", USER: "Edited", AI: "AI" };

export function Sparkline({ values, className }: { values: number[]; className?: string }) {
  const max = Math.max(1, ...values);
  const pts = values.length ? values : [0];
  const w = 64, h = 18, step = pts.length > 1 ? w / (pts.length - 1) : w;
  const d = pts.map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${(h - 2 - (v / max) * (h - 4)).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className={cn("h-[18px] w-16", className)} aria-hidden>
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

export function OriginBadge({ origin }: { origin: string | null | undefined }) {
  const o = (origin || "REPOSITORY").toUpperCase();
  return (
    <span className={cn("rounded-full px-1.5 py-0.5 text-[10px] font-medium",
                        o === "AI" ? "bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300"
                          : o === "USER" ? "bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300"
                          : "bg-muted text-muted-foreground")}>
      {ORIGIN_LABEL[o] ?? o}
    </span>
  );
}

export function LabelPill({ label, version }: { label: "production" | "candidate" | "draft"; version?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium",
                        label === "production" ? "border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-300"
                          : label === "candidate" ? "border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/50 dark:text-amber-300"
                          : "border-border bg-muted text-muted-foreground")}>
      {label === "production" ? <Rocket className="h-3 w-3" /> : label === "candidate" ? <FlaskConical className="h-3 w-3" /> : null}
      {label}{version ? ` ${version}` : ""}
    </span>
  );
}

export function SkillsCatalog({ data }: { data: SkillsResponse }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const { can } = useAccess();
  const editable = can("SKILL.EDIT");
  const category = params.get("category") ?? "all";
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [stage, setStage] = useState("");
  const [origin, setOrigin] = useState<string>("");
  const [status, setStatus] = useState<Status>("all");
  const [view, setView] = useState<"grid" | "list">("grid");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [managing, setManaging] = useState(false);
  const [building, setBuilding] = useState(false);

  const cats = data.categories.length ? data.categories
    : [{ category_id: "general", name: "All skills", description: null, icon: "sparkles", color: "#64748b", position: 0, is_system: true }];
  const catById = useMemo(() => new Map(cats.map((c) => [c.category_id, c])), [cats]);
  const counts = useMemo(() => {
    const out: Record<string, number> = {};
    for (const s of data.skills) {
      const key = catById.has(s.category_id) ? s.category_id : "general";
      out[key] = (out[key] ?? 0) + 1;
    }
    return out;
  }, [data.skills, catById]);

  const matches = (s: SkillCard) => {
    const q = query.trim().toLowerCase();
    if (q && !`${s.skill_name} ${pretty(s.skill_name)} ${s.description ?? ""} ${s.skill_type}`.toLowerCase().includes(q)) return false;
    if (stage && !s.stages.some((b) => b.stage === stage && b.enabled)) return false;
    if (origin && (s.origin || "REPOSITORY") !== origin) return false;
    if (status === "production" && !s.production) return false;
    if (status === "candidate" && !s.candidate) return false;
    if (status === "unused" && s.loads_30d > 0) return false;
    return true;
  };
  const shown = data.skills.filter(matches);
  const filtering = Boolean(query || stage || origin || status !== "all");
  const visibleCats = cats.filter((c) => (category === "all" || c.category_id === category) && shown.some((s) => catOf(s) === c.category_id));
  function catOf(s: SkillCard) { return catById.has(s.category_id) ? s.category_id : "general"; }

  const pickCategory = (id: string) => {
    const next = new URLSearchParams(params.toString());
    if (id === "all") next.delete("category"); else next.set("category", id);
    router.replace(`${pathname}${next.toString() ? `?${next}` : ""}`, { scroll: false });
  };

  const st = data.stats;
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Kpi label="Skills" value={st.skills ?? data.skills.length} hint={`${cats.length} categories`} />
        <Kpi label="In production" value={st.in_production ?? 0} hint="loaded by every run" tone="success" />
        <Kpi label="Candidates" value={st.candidates ?? 0} hint="waiting for review" tone={st.candidates ? "warning" : undefined}
             onClick={() => setStatus(status === "candidate" ? "all" : "candidate")} active={status === "candidate"} />
        <Kpi label="Loads, 30 days" value={st.loads_30d ?? 0} hint="across all runs" />
        <Kpi label="Unused, 30 days" value={st.unused_30d ?? 0} hint="candidates for clean-up"
             onClick={() => setStatus(status === "unused" ? "all" : "unused")} active={status === "unused"} />
      </div>

      {data.upgraded === false && (
        <p role="status" className="rounded-lg border border-dashed px-4 py-2 text-sm text-muted-foreground">
          Versions, labels and categories need the latest deploy (migration V021). Showing the current skills only.
        </p>
      )}

      <div className="grid gap-5 lg:grid-cols-[230px_minmax(0,1fr)]">
        <aside className="space-y-1 lg:sticky lg:top-4 lg:self-start" aria-label="Skill categories">
          <RailItem active={category === "all"} onClick={() => pickCategory("all")} label="All skills" count={data.skills.length}
                    icon={<CategoryIcon icon="sparkles" color="#64748b" size="sm" />} />
          <div className="my-2 border-t" />
          {cats.map((c) => (
            <RailItem key={c.category_id} active={category === c.category_id} onClick={() => pickCategory(c.category_id)}
                      label={c.name} count={counts[c.category_id] ?? 0} icon={<CategoryIcon icon={c.icon} color={c.color} size="sm" />} />
          ))}
          {editable && (
            <button type="button" onClick={() => setManaging(true)}
                    className="mt-2 flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground">
              <Settings2 className="h-3.5 w-3.5" />Manage categories
            </button>
          )}
        </aside>

        <div className="min-w-0 space-y-4">
          <div className="flex flex-wrap items-center gap-2 rounded-xl border bg-card p-2 shadow-sm">
            <div className="relative min-w-[220px] flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search skills by name, purpose or type"
                     className="h-9 border-0 bg-transparent pl-8 shadow-none focus-visible:ring-0" aria-label="Search skills" />
            </div>
            <Select value={stage} onChange={(e) => setStage(e.target.value)} className="h-9 w-40 text-xs" aria-label="Stage">
              <option value="">Any stage</option>
              {(data.stages ?? Object.keys(STAGE_LABELS)).map((s) => <option key={s} value={s}>{STAGE_LABELS[s] ?? s}</option>)}
            </Select>
            <div className="flex rounded-lg border p-0.5 text-xs" role="group" aria-label="Origin">
              {["", ...ORIGINS].map((o) => (
                <button key={o || "any"} type="button" onClick={() => setOrigin(o)} aria-pressed={origin === o}
                        className={cn("rounded-md px-2 py-1", origin === o ? "bg-muted font-medium" : "text-muted-foreground hover:text-foreground")}>
                  {o ? ORIGIN_LABEL[o] : "Any origin"}
                </button>
              ))}
            </div>
            <div className="flex rounded-lg border p-0.5" role="group" aria-label="Layout">
              <button type="button" aria-label="Grid" aria-pressed={view === "grid"} onClick={() => setView("grid")}
                      className={cn("rounded-md p-1.5", view === "grid" ? "bg-muted" : "text-muted-foreground")}><LayoutGrid className="h-3.5 w-3.5" /></button>
              <button type="button" aria-label="List" aria-pressed={view === "list"} onClick={() => setView("list")}
                      className={cn("rounded-md p-1.5", view === "list" ? "bg-muted" : "text-muted-foreground")}><List className="h-3.5 w-3.5" /></button>
            </div>
            {editable && can("AI.USE") && (
              <Button size="sm" onClick={() => setBuilding(true)} className="bg-gradient-to-r from-violet-500 to-primary text-white">
                <Sparkles className="h-3.5 w-3.5" />New skill with AI
              </Button>
            )}
            {filtering && (
              <button type="button" onClick={() => { setQuery(""); setStage(""); setOrigin(""); setStatus("all"); }}
                      className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:text-foreground">
                <X className="h-3 w-3" />Clear
              </button>
            )}
          </div>

          {visibleCats.length === 0 && (
            <div className="rounded-xl border border-dashed p-10 text-center text-sm text-muted-foreground">
              No skills match these filters.
            </div>
          )}

          {visibleCats.map((c) => {
            const inCat = shown.filter((s) => catOf(s) === c.category_id);
            const names = new Set(inCat.map((s) => s.skill_name));
            // sub-skills sit under their umbrella when both are in this category
            const top = inCat.filter((s) => !s.parent_skill || !names.has(s.parent_skill));
            const kids = (parent: string) => inCat.filter((s) => s.parent_skill === parent);
            const open = !collapsed[c.category_id];
            return (
              <section key={c.category_id} className="rounded-2xl border bg-card/60 shadow-sm">
                <button type="button" onClick={() => setCollapsed({ ...collapsed, [c.category_id]: open })} aria-expanded={open}
                        className="flex w-full items-center gap-3 px-4 py-3 text-left">
                  <CategoryIcon icon={c.icon} color={c.color} />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2 text-sm font-semibold">{c.name}
                      <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">{inCat.length}</span>
                      {inCat.some((s) => s.candidate) && <LabelPill label="candidate" version={`× ${inCat.filter((s) => s.candidate).length}`} />}
                    </span>
                    {c.description && <span className="block truncate text-xs text-muted-foreground">{c.description}</span>}
                  </span>
                  {open ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
                </button>
                {open && (view === "grid" ? (
                  <div className="grid gap-3 border-t p-4 sm:grid-cols-2 2xl:grid-cols-3">
                    {top.map((s) => <Card key={s.skill_name} skill={s} kids={kids(s.skill_name)} cats={cats} editable={editable} />)}
                  </div>
                ) : (
                  <ListView skills={[...top, ...top.flatMap((s) => kids(s.skill_name))]} />
                ))}
              </section>
            );
          })}
        </div>
      </div>
      {building && <SkillBuilder categories={cats} skills={data.skills.map((s) => s.skill_name)} onClose={() => setBuilding(false)} />}
      {managing && <ManageCategories cats={cats} counts={counts} onClose={() => setManaging(false)} />}
    </div>
  );
}

function Kpi({ label, value, hint, tone, onClick, active }: {
  label: string; value: number; hint: string; tone?: "success" | "warning"; onClick?: () => void; active?: boolean;
}) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag type={onClick ? "button" : undefined} onClick={onClick} aria-pressed={onClick ? active : undefined}
         className={cn("rounded-xl border bg-card px-4 py-3 text-left shadow-sm transition", onClick && "hover:border-primary/40",
                       active && "border-primary ring-1 ring-primary/30")}>
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={cn("mt-0.5 text-2xl font-semibold tabular-nums", tone === "success" && "text-success", tone === "warning" && "text-warning")}>{value}</p>
      <p className="text-[11px] text-muted-foreground">{hint}</p>
    </Tag>
  );
}

function RailItem({ active, onClick, label, count, icon }: { active: boolean; onClick: () => void; label: string; count: number; icon: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick} aria-current={active ? "true" : undefined}
            className={cn("flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm transition",
                          active ? "bg-primary/10 font-medium text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground")}>
      {icon}
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <span className="text-[11px] tabular-nums">{count}</span>
    </button>
  );
}

function Card({ skill: s, kids, cats, editable }: { skill: SkillCard; kids: SkillCard[]; cats: SkillCategory[]; editable: boolean }) {
  const [open, setOpen] = useState(false);
  const [pending, start] = useTransition();
  const [error, setError] = useState("");
  const href = `/skills/${encodeURIComponent(s.skill_name)}`;
  const stages = s.stages.filter((b) => b.enabled);
  return (
    <div className="group flex flex-col rounded-xl border bg-card p-4 shadow-sm transition hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md">
      <div className="flex items-start gap-2">
        <Link href={href} className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold group-hover:text-primary">{pretty(s.skill_name)}</p>
          <p className="truncate font-mono text-[10px] text-muted-foreground">{s.skill_name}</p>
        </Link>
        <OriginBadge origin={s.origin} />
      </div>
      <Link href={href} className="mt-2 line-clamp-2 min-h-[2.5rem] text-xs text-muted-foreground">{s.description || "No description yet."}</Link>
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {s.production && <LabelPill label="production" version={versionLabel(s.production)} />}
        {s.candidate && <LabelPill label="candidate" version={versionLabel(s.candidate)} />}
        {s.versions > 1 && <span className="text-[10px] text-muted-foreground">{s.versions} versions</span>}
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        {stages.length ? stages.map((b) => (
          <span key={b.stage} className="rounded-md bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">{STAGE_LABELS[b.stage] ?? b.stage}</span>
        )) : <span className="text-[10px] text-muted-foreground">Not bound to a stage (reference only)</span>}
      </div>
      <div className="mt-auto flex items-center gap-2 border-t pt-2.5 text-[11px] text-muted-foreground" style={{ marginTop: "0.75rem" }}>
        <Sparkline values={s.daily} className={s.loads_30d ? "text-primary" : "text-muted-foreground/40"} />
        <span>{s.loads_30d ? `${s.loads_30d} loads · ${s.runs_30d} runs` : "Unused for 30 days"}</span>
        <span className="ml-auto" title={s.last_used ?? undefined}>{s.last_used ? ago(s.last_used) : ""}</span>
      </div>
      {editable && (
        <div className="mt-2 flex items-center gap-1.5 text-[11px]">
          <label className="text-muted-foreground" htmlFor={`cat-${s.skill_name}`}>Category</label>
          <select id={`cat-${s.skill_name}`} value={s.category_id} disabled={pending}
                  onChange={(e) => { const v = e.target.value; start(async () => { const r = await moveCategory(s.skill_name, v); setError(r.ok ? "" : r.error); }); }}
                  className="h-6 rounded-md border bg-background px-1 text-[11px]">
            {cats.map((c) => <option key={c.category_id} value={c.category_id}>{c.name}</option>)}
          </select>
          {pending && <Loader2 className="h-3 w-3 animate-spin" />}
          {error && <span role="alert" className="text-destructive">{error}</span>}
        </div>
      )}
      {kids.length > 0 && (
        <div className="mt-3 rounded-lg border bg-muted/30">
          <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
                  className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground">
            {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}{kids.length} sub-skills
          </button>
          {open && (
            <ul className="space-y-0.5 border-t px-1.5 py-1.5">
              {kids.map((k) => (
                <li key={k.skill_name}>
                  <Link href={`/skills/${encodeURIComponent(k.skill_name)}`}
                        className="flex items-center gap-2 rounded-md px-1.5 py-1 text-[11px] hover:bg-card">
                    <span className="min-w-0 flex-1 truncate">{pretty(k.skill_name)}</span>
                    {k.candidate && <FlaskConical className="h-3 w-3 text-amber-600" />}
                    <span className="text-muted-foreground">{k.loads_30d || ""}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function ListView({ skills }: { skills: SkillCard[] }) {
  return (
    <div className="overflow-x-auto border-t">
      <table className="w-full text-xs">
        <thead className="bg-muted/40 text-left text-[10px] uppercase tracking-wide text-muted-foreground">
          <tr><th className="px-4 py-2">Skill</th><th className="px-2 py-2">Production</th><th className="px-2 py-2">Candidate</th>
            <th className="px-2 py-2">Stages</th><th className="px-2 py-2">Origin</th><th className="px-2 py-2 text-right">Loads 30 d</th><th className="px-4 py-2">Last used</th></tr>
        </thead>
        <tbody className="divide-y">
          {skills.map((s) => (
            <tr key={s.skill_name} className="hover:bg-muted/30">
              <td className="px-4 py-2">
                <Link href={`/skills/${encodeURIComponent(s.skill_name)}`} className="font-medium hover:text-primary">
                  {s.parent_skill && <span className="mr-1 text-muted-foreground">↳</span>}{pretty(s.skill_name)}
                </Link>
                <p className="line-clamp-1 max-w-md text-[11px] text-muted-foreground">{s.description}</p>
              </td>
              <td className="px-2 py-2">{s.production && <LabelPill label="production" version={versionLabel(s.production)} />}</td>
              <td className="px-2 py-2">{s.candidate && <LabelPill label="candidate" version={versionLabel(s.candidate)} />}</td>
              <td className="px-2 py-2 text-muted-foreground">{s.stages.filter((b) => b.enabled).map((b) => STAGE_LABELS[b.stage] ?? b.stage).join(", ") || "None"}</td>
              <td className="px-2 py-2"><OriginBadge origin={s.origin} /></td>
              <td className="px-2 py-2 text-right tabular-nums">{s.loads_30d}</td>
              <td className="px-4 py-2 text-muted-foreground">{ago(s.last_used)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ManageCategories({ cats, counts, onClose }: { cats: SkillCategory[]; counts: Record<string, number>; onClose: () => void }) {
  useScrollLock();
  const [pending, start] = useTransition();
  const [error, setError] = useState("");
  const [draft, setDraft] = useState({ name: "", description: "", color: CATEGORY_COLORS[3], icon: "folder" });
  const run = (fn: () => Promise<{ ok: boolean; error?: string }>) => start(async () => {
    const r = await fn();
    setError(r.ok ? "" : r.error ?? "Failed");
  });
  const move = (c: SkillCategory, dir: -1 | 1) => {
    const i = cats.findIndex((x) => x.category_id === c.category_id);
    const other = cats[i + dir];
    if (!other) return;
    run(async () => {
      const a = await saveCategory({ name: c.name, description: c.description, icon: c.icon, color: c.color, position: other.position }, c.category_id);
      if (!a.ok) return a;
      return saveCategory({ name: other.name, description: other.description, icon: other.icon, color: other.color, position: c.position }, other.category_id);
    });
  };
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4" role="dialog" aria-modal="true" aria-label="Manage categories">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-2xl border bg-background shadow-2xl">
        <div className="flex items-center justify-between border-b px-5 py-3">
          <div>
            <h3 className="text-base font-semibold">Manage categories</h3>
            <p className="text-xs text-muted-foreground">Rename, recolour and reorder. Built-in categories cannot be deleted.</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-md p-1 hover:bg-muted"><X className="h-4 w-4" /></button>
        </div>
        <div className="flex-1 space-y-2 overflow-y-auto overscroll-contain px-5 py-4">
          {cats.map((c, i) => <CategoryRow key={c.category_id} c={c} count={counts[c.category_id] ?? 0} first={i === 0} last={i === cats.length - 1}
                                           busy={pending} onMove={(d) => move(c, d)} run={run} />)}
          <div className="rounded-xl border border-dashed p-3">
            <p className="mb-2 text-xs font-semibold">New category</p>
            <div className="flex flex-wrap items-center gap-2">
              <ColorIconPicker color={draft.color} icon={draft.icon} onChange={(color, icon) => setDraft({ ...draft, color, icon })} />
              <Input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Name" className="h-8 w-44 text-xs" />
              <Input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} placeholder="What belongs here"
                     className="h-8 min-w-[160px] flex-1 text-xs" />
              <Button size="sm" disabled={pending || draft.name.trim().length < 2}
                      onClick={() => run(async () => {
                        const r = await saveCategory({ name: draft.name.trim(), description: draft.description || null, color: draft.color, icon: draft.icon });
                        if (r.ok) setDraft({ ...draft, name: "", description: "" });
                        return r;
                      })}>
                <Plus className="h-3.5 w-3.5" />Add
              </Button>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 border-t px-5 py-3">
          {pending && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
          {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
          <Button size="sm" variant="outline" className="ml-auto" onClick={onClose}>Done</Button>
        </div>
      </div>
    </div>
  );
}

function CategoryRow({ c, count, first, last, busy, onMove, run }: {
  c: SkillCategory; count: number; first: boolean; last: boolean; busy: boolean; onMove: (d: -1 | 1) => void;
  run: (fn: () => Promise<{ ok: boolean; error?: string }>) => void;
}) {
  const [name, setName] = useState(c.name);
  const [description, setDescription] = useState(c.description ?? "");
  const [color, setColor] = useState(c.color ?? "#64748b");
  const [icon, setIcon] = useState(c.icon ?? "folder");
  const dirty = name !== c.name || description !== (c.description ?? "") || color !== (c.color ?? "#64748b") || icon !== (c.icon ?? "folder");
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border bg-card p-2.5">
      <ColorIconPicker color={color} icon={icon} onChange={(co, ic) => { setColor(co); setIcon(ic); }} />
      <Input value={name} onChange={(e) => setName(e.target.value)} className="h-8 w-40 text-xs" aria-label="Name" />
      <Input value={description} onChange={(e) => setDescription(e.target.value)} className="h-8 min-w-[140px] flex-1 text-xs" aria-label="Description" />
      <span className="w-14 text-right text-[11px] text-muted-foreground">{count} skills</span>
      <div className="flex">
        <button type="button" disabled={first || busy} onClick={() => onMove(-1)} aria-label="Move up" className="rounded p-1 hover:bg-muted disabled:opacity-30">
          <ChevronDown className="h-3.5 w-3.5 rotate-180" /></button>
        <button type="button" disabled={last || busy} onClick={() => onMove(1)} aria-label="Move down" className="rounded p-1 hover:bg-muted disabled:opacity-30">
          <ChevronDown className="h-3.5 w-3.5" /></button>
      </div>
      {dirty && <Button size="sm" disabled={busy || name.trim().length < 2}
                        onClick={() => run(() => saveCategory({ name: name.trim(), description: description || null, color, icon }, c.category_id))}>Save</Button>}
      {!c.is_system && (
        <button type="button" disabled={busy} aria-label={`Delete ${c.name}`} title="Delete (its skills move to General)"
                onClick={() => run(() => deleteCategory(c.category_id))} className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-destructive">
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      )}
      {c.is_system && <Badge variant="outline" className="px-1.5 py-0 text-[9px]">built-in</Badge>}
    </div>
  );
}

function ColorIconPicker({ color, icon, onChange }: { color: string; icon: string; onChange: (color: string, icon: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button type="button" onClick={() => setOpen(!open)} aria-label="Colour and icon"><CategoryIcon icon={icon} color={color} /></button>
      {open && (
        <div className="absolute left-0 top-10 z-10 w-56 space-y-2 rounded-xl border bg-popover bg-card p-2 shadow-lg">
          <div className="flex flex-wrap gap-1">
            {CATEGORY_COLORS.map((co) => (
              <button key={co} type="button" onClick={() => onChange(co, icon)} aria-label={co}
                      className={cn("h-5 w-5 rounded-full border-2", co === color ? "border-foreground" : "border-transparent")} style={{ backgroundColor: co }} />
            ))}
          </div>
          <div className="flex flex-wrap gap-1">
            {ICON_NAMES.map((ic) => (
              <button key={ic} type="button" onClick={() => { onChange(color, ic); setOpen(false); }} aria-label={ic}
                      className={cn("rounded-lg", ic === icon && "ring-2 ring-primary")}><CategoryIcon icon={ic} color={color} size="sm" /></button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
