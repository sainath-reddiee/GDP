"use client";

import Link from "next/link";
import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { ArrowDown, ArrowUp, Check, Loader2, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/input";
import { useAccess } from "@/components/access";
import { cn } from "@/lib/utils";
import { saveBindings } from "../skills/actions";
import { pretty, STAGE_LABELS, type SkillBinding } from "../skills/types";

const HINTS: Record<string, string> = {
  LANDING: "Before landing source data and extracting DDL", PROFILING: "Column profiling and AI descriptions",
  DOMAIN: "Detecting the business domain", MODELING: "AI model design in Mapping", MAPPING: "Mapping candidates",
  STTM: "Transformation specs", SODA: "Data quality checks", DBT: "dbt generation", VALIDATION: "Validation and readiness",
};

/** Which skills each stage loads, in order. Replaces the built-in map once saved. */
export function SkillsSection({ stages, bindings, skills }: { stages: string[]; bindings: SkillBinding[]; skills: string[] }) {
  const router = useRouter();
  const { canAct } = useAccess();
  const editable = canAct("SKILL.RELEASE");
  const initial = useMemo(() => {
    const out: Record<string, SkillBinding[]> = {};
    for (const s of stages) out[s] = bindings.filter((b) => b.stage === s).sort((a, b) => a.position - b.position);
    return out;
  }, [stages, bindings]);
  const [draft, setDraft] = useState(initial);
  const [adding, setAdding] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<{ tone: "ok" | "info" | "error"; text: string } | null>(null);
  const [pending, start] = useTransition();
  const dirtyStages = stages.filter((s) => JSON.stringify(draft[s]) !== JSON.stringify(initial[s]));

  const update = (stage: string, list: SkillBinding[]) =>
    setDraft({ ...draft, [stage]: list.map((b, i) => ({ ...b, position: (i + 1) * 10 })) });
  const move = (stage: string, i: number, d: -1 | 1) => {
    const list = [...draft[stage]];
    const j = i + d;
    if (j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    update(stage, list);
  };
  const save = () => start(async () => {
    const r = await saveBindings(dirtyStages.flatMap((s) => draft[s]), dirtyStages);
    if (r.ok) { setMsg({ tone: "ok", text: `Saved ${dirtyStages.length} stage${dirtyStages.length === 1 ? "" : "s"}. New runs load these skills.` }); router.refresh(); }
    else setMsg({ tone: /approval|request/i.test(r.error) ? "info" : "error", text: r.error });
  });

  return (
    <div className="space-y-4 pb-16">
      <p className="text-sm text-muted-foreground">
        Every stage loads its enabled skills, in this order, before it acts. A skill always loads its production version
        (or the version a run is trying). Versions and releases live on <Link href="/skills" className="text-primary hover:underline">Skills</Link>.
        &ldquo;GDP runs&rdquo; skills load only when the run follows the GDP standard.
      </p>
      <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
        {stages.map((stage) => {
          const list = draft[stage] ?? [];
          const free = skills.filter((n) => !list.some((b) => b.skill_name === n));
          return (
            <section key={stage} className={cn("rounded-xl border bg-card p-4 shadow-sm", dirtyStages.includes(stage) && "border-amber-300 dark:border-amber-800")}>
              <div className="mb-2 flex items-baseline justify-between">
                <h3 className="text-sm font-semibold">{STAGE_LABELS[stage] ?? stage}</h3>
                <span className="text-[11px] text-muted-foreground">{list.filter((b) => b.enabled).length} of {list.length} on</span>
              </div>
              <p className="mb-3 text-[11px] text-muted-foreground">{HINTS[stage]}</p>
              <ol className="space-y-1.5">
                {list.map((b, i) => (
                  <li key={b.skill_name} className={cn("flex items-center gap-2 rounded-lg border px-2 py-1.5", !b.enabled && "bg-muted/50")}>
                    <span className="w-4 text-center text-[10px] tabular-nums text-muted-foreground">{i + 1}</span>
                    <label className="relative inline-flex cursor-pointer items-center" title={b.enabled ? "Loaded" : "Skipped"}>
                      <input type="checkbox" className="peer sr-only" checked={b.enabled} disabled={!editable}
                             onChange={() => update(stage, list.map((x) => (x.skill_name === b.skill_name ? { ...x, enabled: !x.enabled } : x)))} />
                      <span className="h-4 w-7 rounded-full bg-muted transition peer-checked:bg-primary" />
                      <span className="absolute left-0.5 h-3 w-3 rounded-full bg-white shadow transition peer-checked:translate-x-3" />
                    </label>
                    <Link href={`/skills/${encodeURIComponent(b.skill_name)}`}
                          className={cn("min-w-0 flex-1 truncate text-xs hover:text-primary", !b.enabled && "text-muted-foreground line-through")}>
                      {pretty(b.skill_name)}
                    </Link>
                    <button type="button" disabled={!editable} onClick={() => update(stage, list.map((x) => (x.skill_name === b.skill_name ? { ...x, standard: x.standard === "GDP" ? "ANY" : "GDP" } : x)))}
                            className={cn("rounded px-1.5 py-0.5 text-[9px] font-medium", b.standard === "GDP" ? "bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300" : "bg-muted text-muted-foreground")}
                            title="Load for every run, or only for runs that follow the GDP standard">
                      {b.standard === "GDP" ? "GDP runs" : "all runs"}
                    </button>
                    {editable && (
                      <span className="flex">
                        <button type="button" aria-label="Move up" disabled={i === 0} onClick={() => move(stage, i, -1)} className="rounded p-0.5 hover:bg-muted disabled:opacity-30"><ArrowUp className="h-3 w-3" /></button>
                        <button type="button" aria-label="Move down" disabled={i === list.length - 1} onClick={() => move(stage, i, 1)} className="rounded p-0.5 hover:bg-muted disabled:opacity-30"><ArrowDown className="h-3 w-3" /></button>
                        <button type="button" aria-label={`Remove ${b.skill_name}`} onClick={() => update(stage, list.filter((x) => x.skill_name !== b.skill_name))}
                                className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-destructive"><Trash2 className="h-3 w-3" /></button>
                      </span>
                    )}
                  </li>
                ))}
                {list.length === 0 && <li className="rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground">No skills bound.</li>}
              </ol>
              {editable && free.length > 0 && (
                <div className="mt-2 flex gap-1.5">
                  <Select value={adding[stage] ?? ""} onChange={(e) => setAdding({ ...adding, [stage]: e.target.value })} className="h-7 flex-1 text-[11px]" aria-label={`Add a skill to ${stage}`}>
                    <option value="">Add a skill…</option>
                    {free.map((n) => <option key={n} value={n}>{pretty(n)}</option>)}
                  </Select>
                  <Button size="sm" variant="outline" className="h-7" disabled={!adding[stage]}
                          onClick={() => { update(stage, [...list, { stage, skill_name: adding[stage], enabled: true, position: 0, standard: "ANY" }]); setAdding({ ...adding, [stage]: "" }); }}>
                    <Plus className="h-3 w-3" />
                  </Button>
                </div>
              )}
            </section>
          );
        })}
      </div>
      {(dirtyStages.length > 0 || msg) && (
        <div className="fixed inset-x-0 bottom-0 z-30 border-t bg-card/95 backdrop-blur">
          <div className="mx-auto flex max-w-6xl items-center gap-3 px-6 py-2.5">
            {dirtyStages.length > 0
              ? <span className="text-xs font-medium text-warning">Unsaved changes: {dirtyStages.map((s) => STAGE_LABELS[s] ?? s).join(", ")}</span>
              : msg && <span role="status" className={cn("text-xs", msg.tone === "error" ? "text-destructive" : msg.tone === "info" ? "text-sky-700 dark:text-sky-300" : "text-success")}>{msg.text}</span>}
            <span className="ml-auto flex gap-2">
              {dirtyStages.length > 0 && <Button size="sm" variant="ghost" disabled={pending} onClick={() => { setDraft(initial); setMsg(null); }}>Discard</Button>}
              {dirtyStages.length > 0 && (
                <Button size="sm" disabled={pending} onClick={save}>
                  {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Save
                </Button>
              )}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
