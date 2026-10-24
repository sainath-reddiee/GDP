"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { ArrowRight, Check, Loader2, Radar, Rocket, Search, Settings2, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { moveLabel } from "../actions";
import {
  dismissProposal, saveScoutSettings, scoutDraft, scoutPlan, type ProposalState, type ScoutPlan, type ScoutSettings, type SkillProposal,
} from "./actions";

const STATE: Record<ProposalState, { label: string; tone: string }> = {
  PROPOSED: { label: "Proposed", tone: "bg-agent/10 text-agent" },
  CANDIDATE: { label: "Candidate", tone: "bg-sky-100 text-sky-800" },
  PROMOTED: { label: "In production", tone: "bg-success/10 text-success" },
  DISMISSED: { label: "Dismissed", tone: "bg-muted text-muted-foreground" },
  DISCARDED: { label: "Discarded by its checks", tone: "bg-muted text-muted-foreground" },
};
const TRANSFER_TONE: Record<string, string> = {
  "transfers well": "bg-emerald-100 text-emerald-800", "transfers with care": "bg-amber-100 text-amber-800",
  "client specific": "bg-rose-100 text-rose-800",
};
const pct = (v: number | null | undefined) => (v == null ? "-" : `${Math.round(v * 100)}%`);

type Result<T> = { ok: true; data: T } | { ok: false; error: string };

/** The skill scout's proposals and the controls: look for themes now (dry run, no AI), draft one, make a proposal the
 *  candidate or promote it, dismiss it, and the scout's settings. */
export function ProposedView({ items, canEdit, canRelease, canAi, canConfig, settings }: {
  items: SkillProposal[]; canEdit: boolean; canRelease: boolean; canAi: boolean; canConfig: boolean; settings: ScoutSettings | null;
}) {
  const router = useRouter();
  const [plan, setPlan] = useState<ScoutPlan | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState("");
  const [pending, start] = useTransition();
  const run = <T,>(tag: string, fn: () => Promise<Result<T>>, after: (d: T) => string) => start(async () => {
    setError(""); setDone(""); setBusy(tag);
    const r = await fn();
    setBusy("");
    if (!r.ok) { setError(r.error); return; }
    setDone(after(r.data));
    router.refresh();
  });
  const spin = <Loader2 className="h-3.5 w-3.5 animate-spin" />;

  return (
    <div className="space-y-5">
      {canEdit && (
        <section className="surface space-y-3 p-4" data-testid="scout-plan">
          <div className="flex flex-wrap items-center gap-2">
            <Radar className="h-4 w-4 text-agent" />
            <h2 className="text-sm font-semibold">What the scout sees</h2>
            <span className="text-xs text-muted-foreground">themes of approved knowledge used again and again; it scans {settings?.enabled === false ? "never (switched off)" : `every ${settings?.cadence_days ?? 7} days`}</span>
            <Button size="sm" variant="outline" className="ml-auto" disabled={pending}
                    onClick={() => run("plan", scoutPlan, (p) => { setPlan(p); return ""; })}>
              {busy === "plan" ? spin : <Search className="h-3.5 w-3.5" />}Look now
            </Button>
          </div>
          {plan && (plan.clusters.length === 0
            ? <p className="text-xs text-muted-foreground">No theme is strong enough yet: it needs {plan.settings.min_items} approved items used in {plan.settings.min_domains} domains or {plan.settings.min_runs} runs.</p>
            : (
              <ul className="divide-y rounded-lg border text-xs">
                {plan.clusters.slice(0, 12).map((c) => (
                  <li key={c.key} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
                    <span className="rounded-full bg-muted px-2 py-0.5 font-semibold">{c.stage}</span>
                    <span className="min-w-0 flex-1 truncate font-medium" title={c.titles.join("\n")}>{c.theme || c.key} · {c.titles[0]}</span>
                    <span className="tabular-nums text-muted-foreground">{c.items} items · {c.domains} domain{c.domains === 1 ? "" : "s"} · {c.runs} runs · helped {pct(c.helpfulness)}</span>
                    {c.skip ? <span className="text-muted-foreground">{c.skip}</span> : canAi && (
                      <Button size="sm" disabled={pending} onClick={() => run(c.key, () => scoutDraft(c.key), (r) =>
                        r.proposed.length ? `Proposed ${r.proposed[0].skill_name}.` : r.discarded.length ? `Discarded: ${r.discarded[0].reason}` : (r.errors[0] ?? "Nothing drafted."))}>
                        {busy === c.key ? spin : <Sparkles className="h-3.5 w-3.5" />}{busy === c.key ? "Drafting and testing" : "Draft this one"}
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            ))}
        </section>
      )}
      {done && <p role="status" className="text-sm text-success">{done}</p>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}

      {items.length === 0 ? (
        <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
          No proposals yet. The scout proposes a skill when the same kind of approved knowledge keeps being used across domains or runs.
        </p>
      ) : (
        <div className="space-y-3">
          {items.map((p) => <ProposalCard key={p.proposal_id} p={p} canEdit={canEdit} canRelease={canRelease} pending={pending}
            busy={busy} run={run} />)}
        </div>
      )}

      {canConfig && settings && <ScoutSettingsForm settings={settings} />}
    </div>
  );
}

function ProposalCard({ p, canEdit, canRelease, pending, busy, run }: {
  p: SkillProposal; canEdit: boolean; canRelease: boolean; pending: boolean; busy: string;
  run: <T>(tag: string, fn: () => Promise<Result<T>>, after: (d: T) => string) => void;
}) {
  const [note, setNote] = useState("");
  const [dismissing, setDismissing] = useState(false);
  const s = STATE[p.state] ?? STATE.PROPOSED;
  const live = p.state === "PROPOSED" || p.state === "CANDIDATE";
  return (
    <article className="surface space-y-2 p-4" data-testid="skill-proposal">
      <header className="flex flex-wrap items-center gap-2">
        <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-semibold", s.tone)}>{s.label}</span>
        <h3 className="min-w-0 flex-1 truncate text-sm font-semibold">{p.title ?? p.skill_name ?? p.cluster_key}</h3>
        {p.transfer_label && <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-semibold", TRANSFER_TONE[p.transfer_label] ?? "bg-muted")}
                                   title="How well it would carry to another client">{p.transfer_label} · {p.transfer_score}</span>}
      </header>
      <p className="text-xs text-muted-foreground">
        {p.stage ?? "Any stage"} · from {p.evidence_count} approved knowledge item{p.evidence_count === 1 ? "" : "s"} in {p.domain_count} domain{p.domain_count === 1 ? "" : "s"}
        {p.skill_name ? <> · <span className="font-mono">{p.skill_name}</span></> : null} · {String(p.created_at).slice(0, 10)}
      </p>
      {p.eval && (
        <p className="text-xs">Tested on {p.eval.total} cases: passed {p.eval.candidate_passed}, against {p.eval.baseline_passed} for {p.eval.baseline_label}
          <span className={cn("ml-1 font-semibold", p.eval.verdict === "better" ? "text-success" : p.eval.verdict === "worse" ? "text-destructive" : "text-muted-foreground")}>({p.eval.verdict})</span>
        </p>
      )}
      {p.replacements?.length > 0 && (
        <p className="text-xs text-muted-foreground">Generalised: {p.replacements.map((r) => `${r.count} ${r.from_kind} name${r.count === 1 ? "" : "s"} to ${r.placeholder}`).join(", ")}</p>
      )}
      {p.discard_reason && <p className="text-xs text-muted-foreground">Why it was discarded: {p.discard_reason}</p>}
      {p.dismiss_note && <p className="text-xs text-muted-foreground">Dismissed by {p.dismissed_by}: {p.dismiss_note}</p>}
      <details className="text-xs">
        <summary className="cursor-pointer text-muted-foreground">Evidence</summary>
        <ul className="mt-1 space-y-0.5">
          {p.evidence.slice(0, 12).map((e) => (
            <li key={e.knowledge_id}>{e.title} <span className="text-muted-foreground">({e.domain ?? "no domain"}{e.runs ? `, ${e.runs} runs` : ""}{e.helpfulness != null ? `, helped ${pct(e.helpfulness)}` : ""})</span></li>
          ))}
        </ul>
      </details>
      {live && p.skill_id && p.skill_name && (
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <Link href={`/os/skills/registry/${encodeURIComponent(p.skill_name)}?tab=versions`} className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">Open <ArrowRight className="h-3 w-3" /></Link>
          {canEdit && p.state === "PROPOSED" && (
            <Button size="sm" variant="outline" disabled={pending} onClick={() => run(`c${p.proposal_id}`, () => moveLabel(p.skill_name!, "candidate", p.skill_id!, "From the skill scout"), () => "It is the candidate: try it on a run from the skill's page.")}>
              {busy === `c${p.proposal_id}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Make candidate
            </Button>
          )}
          {canRelease && (
            <Button size="sm" variant="outline" disabled={pending} onClick={() => run(`p${p.proposal_id}`, () => moveLabel(p.skill_name!, "production", p.skill_id!, "Promoted from the skill scout"), () => "Promoted. Bind it to a stage under Skills per stage so runs load it.")}>
              <Rocket className="h-3.5 w-3.5" />Promote
            </Button>
          )}
          {canEdit && p.state === "PROPOSED" && (dismissing ? (
            <span className="flex flex-1 items-center gap-2">
              <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why not?" className="h-8 text-xs" aria-label="Why dismiss" />
              <Button size="sm" variant="outline" disabled={pending || note.trim().length < 3}
                      onClick={() => run(`d${p.proposal_id}`, () => dismissProposal(p.proposal_id, note.trim()), () => "Dismissed. The scout proposes this theme again only once its evidence doubles.")}>Dismiss</Button>
            </span>
          ) : (
            <Button size="sm" variant="ghost" disabled={pending} onClick={() => setDismissing(true)}><X className="h-3.5 w-3.5" />Dismiss</Button>
          ))}
        </div>
      )}
    </article>
  );
}

const FIELDS: { key: keyof ScoutSettings; label: string; step?: string }[] = [
  { key: "cadence_days", label: "Scan every (days)" }, { key: "max_per_run", label: "Drafts per scan" },
  { key: "min_items", label: "Items in a theme" }, { key: "min_domains", label: "Domains, or" }, { key: "min_runs", label: "runs that used it" },
  { key: "min_helpfulness", label: "Observed helpfulness", step: "0.05" },
];

function ScoutSettingsForm({ settings }: { settings: ScoutSettings }) {
  const router = useRouter();
  const [v, setV] = useState<ScoutSettings>(settings);
  const [msg, setMsg] = useState("");
  const [pending, start] = useTransition();
  const save = (reset: boolean) => start(async () => {
    const r = await saveScoutSettings(v, reset);
    setMsg(r.ok ? (reset ? "Back to the defaults." : "Saved.") : r.error);
    router.refresh();
  });
  return (
    <section className="surface space-y-3 p-4" data-testid="scout-settings">
      <h2 className="flex items-center gap-2 text-sm font-semibold"><Settings2 className="h-4 w-4" />Skill scout settings</h2>
      <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={v.enabled} onChange={(e) => setV({ ...v, enabled: e.target.checked })} />Scan on a schedule</label>
      <div className="grid gap-2 sm:grid-cols-3">
        {FIELDS.map((f) => (
          <label key={f.key} className="space-y-1 text-xs font-medium">{f.label}
            <Input type="number" step={f.step ?? "1"} value={String(v[f.key])} onChange={(e) => setV({ ...v, [f.key]: Number(e.target.value) })} />
          </label>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={pending} onClick={() => save(false)}>Save</Button>
        <Button size="sm" variant="outline" disabled={pending} onClick={() => save(true)}>Reset to defaults</Button>
        {msg && <span className="text-xs text-muted-foreground">{msg}</span>}
      </div>
    </section>
  );
}
