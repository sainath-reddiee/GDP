"use client";

import Link from "next/link";
import { useEffect, useState, useTransition } from "react";
import { Check, CircleDashed, Eye, Loader2, Plus, X } from "lucide-react";
import { useAccess } from "@/components/access";
import { LAYER_META } from "@/lib/layers";
import { cn } from "@/lib/utils";
import { prettyType } from "../../knowledge/knowledge-ui";
import {
  domainPlaybook, playbookPreview, saveDomainConventions, saveDomainSkills,
  type DomainSkillRow, type Playbook,
} from "../actions";

type Pipeline = "SILVER" | "GOLD";
const STAGE_NAMES: Record<string, string> = { STTM: "STTM", DBT: "Code (dbt)", QA: "QA", SODA: "Data Quality" };
const STAGE_LABEL = (s: string) => STAGE_NAMES[s] ?? s.charAt(0) + s.slice(1).toLowerCase();
const EDITABLE_CONVENTIONS = ["model_dir", "layer_tag", "project_name", "branch_prefix", "pr_title", "commit_message", "codegen_prefix",
  "scd_type", "key_strategy", "prefix", "watermark_table", "skill", "convention"];

/** How this domain's runs of one layer model and code, all in one place, each part changeable for this layer only:
 *  the skills each stage loads, the conventions the code follows, the rules it changes, the knowledge each stage reads
 *  (with gaps), and a preview of exactly what a run is given. */
export function PlaybookTab({ domainId, domainName, layer }: { domainId: string; domainName: string; layer: Pipeline }) {
  const [data, setData] = useState<Playbook | null>(null);
  const [error, setError] = useState("");
  const load = () => void domainPlaybook(domainId, layer).then((r) => (r.ok ? setData(r.data) : setError(r.error)));
  useEffect(load, [domainId, layer]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (error) return <p role="alert" className="text-sm text-destructive">{error}</p>;
  if (!data) return <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-4 w-4 animate-spin" />Reading the playbook…</p>;
  const meta = LAYER_META[layer];
  return (
    <div className="space-y-4" data-testid="playbook">
      <p className="text-sm text-muted-foreground">
        <span className={cn("mr-1.5 rounded px-1.5 py-0.5 text-[11px] font-semibold", meta.chip)}>{meta.label}</span>
        How {domainName}&apos;s {layer === "GOLD" ? "Gold" : "Silver"} runs model and code. Everything here applies to this layer only (or to
        both, where you choose), and every change is a new domain version.</p>
      {!!data.readiness.length && <Readiness items={data.readiness} />}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-4">
          <Skills domainId={domainId} layer={layer} data={data} onSaved={load} />
          <Conventions domainId={domainId} layer={layer} data={data} onSaved={load} />
        </div>
        <aside className="space-y-4">
          <Coverage domainId={domainId} layer={layer} data={data} />
          <section className="surface p-4">
            <h3 className="text-sm font-semibold">Rules this domain changes</h3>
            {data.rules.changed.length ? (
              <ul className="mt-2 space-y-1 text-xs">{data.rules.changed.map((r) => (
                <li key={r.key} className="flex gap-2"><span className="font-mono">{r.key}</span><span className="ml-auto font-mono">{String(r.value)}</span>
                  <span className="text-muted-foreground">(was {String(r.base ?? "default")})</span></li>))}</ul>
            ) : <p className="mt-1 text-xs text-muted-foreground">None: this layer uses the {layer === "GOLD" ? "Silver values and the platform's Gold rules" : "platform rules"}.</p>}
            <Link href={`/domains/${domainId}?tab=rules&layer=${layer}`} className="mt-2 inline-block text-xs text-primary hover:underline">Change rules</Link>
          </section>
        </aside>
      </div>
      <Preview domainId={domainId} layer={layer} />
    </div>
  );
}

function Readiness({ items }: { items: Playbook["readiness"] }) {
  return (
    <section className="surface p-4">
      <h3 className="mb-2 text-sm font-semibold">Ready for Gold runs</h3>
      <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
        {items.map((i) => (
          <li key={i.id} className={cn("rounded-lg border px-3 py-2 text-xs", i.met ? "border-emerald-500/40" : "border-amber-500/40")}>
            <p className="flex items-center gap-1.5 font-medium">{i.met ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <CircleDashed className="h-3.5 w-3.5 text-amber-600" />}{i.label}</p>
            <p className="mt-0.5 text-muted-foreground">{i.detail}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Skills({ domainId, layer, data, onSaved }: { domainId: string; layer: Pipeline; data: Playbook; onSaved: () => void }) {
  const { canAct } = useAccess();
  const may = canAct("SKILL.RELEASE");
  const [adding, setAdding] = useState<{ stage: string; skill: string; replaces: string; scope: "ANY" | Pipeline } | null>(null);
  const [msg, setMsg] = useState("");
  const [pending, start] = useTransition();
  const rows: DomainSkillRow[] = data.skills.stages.flatMap((s) => s.domain_skills);
  const save = (next: DomainSkillRow[]) => start(async () => {
    setMsg("");
    const r = await saveDomainSkills(domainId, next.map(({ stage, skill_name, mode, replaces, position, enabled, note, pipeline }) =>
      ({ stage, skill_name, mode, replaces, position, enabled, note, pipeline: pipeline ?? "ANY" })), layer);
    if (!r.ok) { setMsg(r.error); return; }
    setAdding(null); onSaved();
  });
  return (
    <section className="surface space-y-2 p-4" data-testid="domain-skills">
      <div>
        <h3 className="text-sm font-semibold">Skills each stage loads</h3>
        <p className="text-xs text-muted-foreground">The {data.skills.standard} standard&apos;s skills for {layer === "GOLD" ? "Gold" : "Silver"} runs, then
          this domain&apos;s own (added, or in place of one), then what its MDM role adds. A domain skill can be for this layer only or for both. Changing them needs SKILL.RELEASE.
          {!!data.skills.other_layer && ` ${data.skills.other_layer} skill(s) are set for the other layer only.`}</p>
      </div>
      {msg && <p role="alert" className="text-xs text-destructive">{msg}</p>}
      <ul className="divide-y divide-border/60">
        {data.skills.stages.filter((s) => s.effective.length || s.domain_skills.length).map((s) => (
          <li key={s.stage} className="flex flex-wrap items-start gap-2 py-2 text-xs">
            <span className="w-28 shrink-0 pt-0.5 font-medium">{STAGE_LABEL(s.stage)}</span>
            <span className="flex min-w-0 flex-1 flex-wrap gap-1">
              {s.standard_skills.map((k) => {
                const replaced = s.domain_skills.find((m) => m.mode === "REPLACE" && (m.replaces ?? "").toUpperCase() === k.toUpperCase());
                return <Link key={k} href={`/skills/${encodeURIComponent(k)}`} title={replaced ? `Replaced here by ${replaced.skill_name}` : "From the platform"}
                             className={cn("rounded bg-muted px-1.5 py-0.5 font-mono hover:text-primary", replaced && "line-through opacity-60")}>{k}</Link>;
              })}
              {s.domain_skills.map((m) => (
                <span key={m.skill_name} className="inline-flex items-center gap-1 rounded bg-primary/10 px-1.5 py-0.5 font-mono text-primary"
                      title={m.mode === "REPLACE" ? `In place of ${m.replaces}` : "Added for this domain"}>
                  {m.skill_name}{m.mode === "REPLACE" ? ` (for ${m.replaces})` : ""}
                  <span className="rounded bg-background/70 px-1 font-sans text-[9px]">{(m.pipeline ?? "ANY") === "ANY" ? "both layers" : `${LAYER_META[layer].label} only`}</span>
                  {may && <button type="button" aria-label={`Remove ${m.skill_name}`} disabled={pending} onClick={() => save(rows.filter((r) => r !== m))}><X className="h-3 w-3" /></button>}
                </span>
              ))}
              {(s.mdm_skills ?? []).map((m) => (
                <span key={`mdm-${m.skill_name}`} title="Added because of the domain's MDM role (Reltio)" className={cn("rounded px-1.5 py-0.5 font-mono", LAYER_META.MDM.chip)}>{m.skill_name}</span>
              ))}
            </span>
            {may && <button type="button" onClick={() => setAdding({ stage: s.stage, skill: "", replaces: "", scope: layer })} className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-primary hover:bg-primary/10"><Plus className="h-3 w-3" /> Skill</button>}
            {adding?.stage === s.stage && (
              <div className="flex w-full flex-wrap items-center gap-2 pl-28">
                <select aria-label="Skill" value={adding.skill} onChange={(e) => setAdding({ ...adding, skill: e.target.value })} className="h-8 rounded-lg border bg-background px-2 text-xs">
                  <option value="">Choose a skill…</option>
                  {data.skills.available.map((a) => <option key={a.name} value={a.name} title={a.description}>{a.name}{a.version ? ` v${a.version}` : ""}</option>)}
                </select>
                <select aria-label="How" value={adding.replaces} onChange={(e) => setAdding({ ...adding, replaces: e.target.value })} className="h-8 rounded-lg border bg-background px-2 text-xs">
                  <option value="">Add to the stage</option>
                  {s.standard_skills.map((k) => <option key={k} value={k}>Instead of {k}</option>)}
                </select>
                <select aria-label="Layer" value={adding.scope} onChange={(e) => setAdding({ ...adding, scope: e.target.value as "ANY" | Pipeline })} className="h-8 rounded-lg border bg-background px-2 text-xs">
                  <option value={layer}>{LAYER_META[layer].label} runs only</option>
                  <option value="ANY">Both layers</option>
                </select>
                <button type="button" disabled={!adding.skill || pending} onClick={() => save([...rows, {
                  stage: s.stage, skill_name: adding.skill, mode: adding.replaces ? "REPLACE" : "ADD", replaces: adding.replaces || null,
                  position: 100 + s.domain_skills.length, enabled: true, note: null, pipeline: adding.scope }])}
                        className="h-8 rounded-lg bg-primary px-3 font-medium text-primary-foreground disabled:opacity-50">{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Attach"}</button>
                <button type="button" onClick={() => setAdding(null)} className="h-8 rounded-lg px-2 hover:bg-muted">Cancel</button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Conventions({ domainId, layer, data, onSaved }: { domainId: string; layer: Pipeline; data: Playbook; onSaved: () => void }) {
  const { canAct } = useAccess();
  const may = canAct("DOMAIN.EDIT");
  const c = data.conventions;
  const keys = EDITABLE_CONVENTIONS.filter((k) => k in c.platform);
  const initial = Object.fromEntries(keys.map((k) => [k, c.overrides[k] === undefined ? "" : String(c.overrides[k])]));
  const [text, setText] = useState<Record<string, string>>(initial);
  const [msg, setMsg] = useState("");
  const [pending, start] = useTransition();
  const dirty = JSON.stringify(text) !== JSON.stringify(initial);
  const save = () => start(async () => {
    setMsg("");
    const overrides: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(text)) {
      if (!v.trim()) continue;
      overrides[k] = typeof c.platform[k] === "number" ? Number(v) : v;
    }
    const r = await saveDomainConventions(domainId, layer, overrides, `${layer === "GOLD" ? "Gold" : "Silver"} conventions changed on the playbook`);
    if (!r.ok) { setMsg(r.error); return; }
    onSaved();
  });
  return (
    <section className="surface p-4">
      <h3 className="text-sm font-semibold">How the code is built</h3>
      <p className="text-xs text-muted-foreground">The {c.standard} {layer === "GOLD" ? "Gold" : "Silver"} conventions (Admin, Standards), and what this domain changes for its own runs.
        Empty means the platform value.</p>
      <table className="mt-2 w-full text-xs">
        <thead className="text-left text-[10px] uppercase tracking-wide text-muted-foreground"><tr><th className="py-1">Convention</th><th>Platform</th><th>This domain</th></tr></thead>
        <tbody className="divide-y">
          {keys.map((k) => (
            <tr key={k}>
              <td className="py-1 pr-2 font-mono">{k}</td>
              <td className="max-w-[260px] truncate pr-2 font-mono text-muted-foreground" title={String(c.platform[k])}>{String(c.platform[k])}</td>
              <td className="py-0.5"><input aria-label={k} value={text[k] ?? ""} disabled={!may} onChange={(e) => setText({ ...text, [k]: e.target.value })}
                                            placeholder="inherit" className={cn("h-7 w-full rounded-md border bg-background px-2 font-mono text-[11px]", text[k] && "border-primary/50")} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {msg && <p role="alert" className="mt-2 text-xs text-destructive">{msg}</p>}
      {dirty && may && (
        <div className="mt-2 flex gap-2">
          <button type="button" onClick={save} disabled={pending} className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-medium text-primary-foreground disabled:opacity-50">
            {pending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Save conventions</button>
          <button type="button" onClick={() => setText(initial)} className="h-8 rounded-lg px-2 text-xs hover:bg-muted">Discard</button>
        </div>
      )}
    </section>
  );
}

function Coverage({ domainId, layer, data }: { domainId: string; layer: Pipeline; data: Playbook }) {
  const gaps = data.knowledge.filter((k) => k.gap).length;
  return (
    <section className="surface p-4">
      <h3 className="text-sm font-semibold">Knowledge each stage reads</h3>
      <p className="text-xs text-muted-foreground">{gaps ? `${gaps} kind(s) have nothing yet: add them so the next ${layer === "GOLD" ? "Gold" : "Silver"} run uses them.` : "Every kind a stage reads has items."}</p>
      <ul className="mt-2 space-y-1.5">
        {data.knowledge.map((k) => (
          <li key={k.type} className="text-xs">
            <p className="flex items-center gap-1.5">
              {k.gap ? <CircleDashed className="h-3.5 w-3.5 text-amber-600" /> : <Check className="h-3.5 w-3.5 text-emerald-600" />}
              <span className="font-medium">{prettyType(k.type)}</span>
              <span className="ml-auto tabular-nums text-muted-foreground">{k.layer_items} {LAYER_META[layer].label.toLowerCase()} · {k.shared_items} shared</span>
            </p>
            <p className="pl-5 text-muted-foreground">{k.why} ({k.stages.map(STAGE_LABEL).join(", ")})
              {" "}<Link href={`/knowledge?tab=items&domain_id=${domainId}&type=${k.type}&layer=${layer}`} className="text-primary hover:underline">{k.gap ? "Add" : "Open"}</Link></p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Preview({ domainId, layer }: { domainId: string; layer: Pipeline }) {
  const [stage, setStage] = useState("MAPPING");
  const [target, setTarget] = useState("");
  const [out, setOut] = useState<{ skills: string[]; context: string } | null>(null);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const run = () => start(async () => {
    setError("");
    const r = await playbookPreview(domainId, layer, stage, target.trim() || undefined);
    if (!r.ok) { setError(r.error); return; }
    setOut(r.data);
  });
  return (
    <section className="surface p-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold"><Eye className="h-4 w-4 text-primary" />What a {layer === "GOLD" ? "Gold" : "Silver"} run is given</h3>
      <p className="text-xs text-muted-foreground">Built by the same functions a run uses: the skills a stage loads and the domain context in its prompt.</p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <select aria-label="Stage" value={stage} onChange={(e) => setStage(e.target.value)} className="h-8 rounded-lg border bg-background px-2 text-xs">
          {["PROFILING", "MAPPING", "STTM", "SODA", "DBT", "VALIDATION"].map((s) => <option key={s} value={s}>{STAGE_LABEL(s)}</option>)}
        </select>
        <input aria-label="Target table" value={target} onChange={(e) => setTarget(e.target.value)} placeholder={layer === "GOLD" ? "Gold table (optional), e.g. FACT_OPPORTUNITY" : "Target table (optional)"}
               className="h-8 w-72 rounded-lg border bg-background px-2 font-mono text-xs" />
        <button type="button" onClick={run} disabled={pending} className="inline-flex h-8 items-center gap-1.5 rounded-lg border px-3 text-xs font-medium hover:bg-muted disabled:opacity-50">
          {pending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Preview</button>
      </div>
      {error && <p role="alert" className="mt-2 text-xs text-destructive">{error}</p>}
      {out && (
        <div className="mt-3 space-y-2">
          <p className="text-xs"><span className="text-muted-foreground">Skills: </span>{out.skills.map((s) => <span key={s} className="mr-1 rounded bg-muted px-1.5 py-0.5 font-mono">{s}</span>)}</p>
          {out.context ? <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg border bg-muted/30 p-3 text-[11px]">{out.context}</pre>
            : <p className="text-xs text-muted-foreground">This stage reads no domain context from the playbook.</p>}
        </div>
      )}
    </section>
  );
}
