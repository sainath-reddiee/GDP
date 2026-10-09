"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle, Boxes, Check, ChevronDown, GitCompare, History, Loader2, Plus, Save, Sparkles, Trash2, X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  approveDesign, checkDesign, diffDesigns, generateDesign, saveDesign,
  type Attribute, type Conventions, type Design, type DesignVersion, type DiffChange, type Entity, type Issue,
  type ModelPayload,
} from "./model-actions";

const PRESETS: { key: Conventions["preset"]; label: string; hint: string }[] = [
  { key: "NONE", label: "Free modeling", hint: "No conventions injected; shape the model as you like" },
  { key: "COMPANY", label: "Company standard", hint: "Your organisation's generic standard (Admin settings)" },
  { key: "GDP", label: "GDP standard", hint: "Hub/spoke, GDP keys and audit columns" },
  { key: "CUSTOM", label: "Custom", hint: "Pick audit columns, keys, naming and history yourself" },
];
const DECISION: Record<Design["decision"], { label: string; tone: string }> = {
  REUSE_EXISTING: { label: "Reuse an existing model", tone: "border-success/30 bg-success/5 text-success" },
  EXTEND_EXISTING: { label: "Extend an existing model", tone: "border-primary/30 bg-primary/5 text-primary" },
  NEW: { label: "New model", tone: "border-violet-300 bg-violet-50/60 text-violet-700 dark:bg-violet-950/30 dark:text-violet-300" },
};
const KINDS = ["DIMENSION", "FACT", "BRIDGE", "REFERENCE", "TABLE"];
const EMPTY_CUSTOM: Conventions = {
  preset: "CUSTOM", audit_columns: ["LOAD_TS"], surrogate_key: "none", key_pattern: "{TABLE}_SK", naming_case: "upper",
  table_prefix: "", scd_type: null, soft_delete: false,
};

function issuesFor(issues: Issue[], path: string) {
  return issues.filter((i) => i.path === path || i.path.startsWith(`${path}.`));
}

function ConventionsPicker({ value, onChange, standard }: { value: Conventions; onChange: (c: Conventions) => void; standard: string | null }) {
  const custom = value.preset === "CUSTOM";
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {PRESETS.filter((p) => p.key !== "GDP" || standard === "GDP" || value.preset === "GDP").map((p) => (
          <button key={p.key} type="button" title={p.hint} onClick={() => onChange(p.key === "CUSTOM" ? { ...EMPTY_CUSTOM } : { ...value, preset: p.key })}
                  aria-pressed={value.preset === p.key}
                  className={cn("rounded-full border px-3 py-1 text-xs", value.preset === p.key ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:border-primary/40")}>
            {p.label}
          </button>
        ))}
      </div>
      <p className="text-[11px] text-muted-foreground">{PRESETS.find((p) => p.key === value.preset)?.hint}</p>
      {custom && (
        <div className="grid gap-2 rounded-lg border bg-muted/20 p-3 sm:grid-cols-2 lg:grid-cols-3">
          <label className="space-y-1 text-xs">Audit columns (comma separated)
            <Input value={value.audit_columns.join(", ")} onChange={(e) => onChange({ ...value, audit_columns: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
          </label>
          <label className="space-y-1 text-xs">Surrogate key
            <select className="h-9 w-full rounded-lg border bg-card px-2 text-sm" value={value.surrogate_key} onChange={(e) => onChange({ ...value, surrogate_key: e.target.value })}>
              <option value="none">None (business key)</option><option value="sequence">Sequence</option><option value="hash">Hash</option>
            </select>
          </label>
          <label className="space-y-1 text-xs">Key name pattern
            <Input value={value.key_pattern ?? ""} onChange={(e) => onChange({ ...value, key_pattern: e.target.value })} placeholder="{TABLE}_SK" />
          </label>
          <label className="space-y-1 text-xs">Naming case
            <select className="h-9 w-full rounded-lg border bg-card px-2 text-sm" value={value.naming_case} onChange={(e) => onChange({ ...value, naming_case: e.target.value })}>
              <option value="upper">UPPER_CASE</option><option value="lower">lower_case</option><option value="as_is">As is</option>
            </select>
          </label>
          <label className="space-y-1 text-xs">Table prefix
            <Input value={value.table_prefix} onChange={(e) => onChange({ ...value, table_prefix: e.target.value })} placeholder="e.g. DIM_" />
          </label>
          <div className="flex items-end gap-4 text-xs">
            <label className="space-y-1">History
              <select className="h-9 rounded-lg border bg-card px-2 text-sm" value={value.scd_type ?? ""} onChange={(e) => onChange({ ...value, scd_type: e.target.value ? Number(e.target.value) : null })}>
                <option value="">None</option><option value="1">SCD 1</option><option value="2">SCD 2</option>
              </select>
            </label>
            <label className="flex items-center gap-1.5 pb-2"><input type="checkbox" checked={value.soft_delete} onChange={(e) => onChange({ ...value, soft_delete: e.target.checked })} /> Soft delete</label>
          </div>
        </div>
      )}
    </div>
  );
}

function EntityCard({ entity, index, issues, editable, onChange, onRemove }: {
  entity: Entity; index: number; issues: Issue[]; editable: boolean; onChange: (e: Entity) => void; onRemove: () => void;
}) {
  const [open, setOpen] = useState(true);
  const mine = issuesFor(issues, `entities[${index}]`);
  const errors = mine.filter((i) => i.severity === "ERROR").length;
  const setAttr = (j: number, patch: Partial<Attribute>) =>
    onChange({ ...entity, attributes: entity.attributes.map((a, k) => (k === j ? { ...a, ...patch } : a)) });
  return (
    <div className={cn("overflow-hidden rounded-xl border border-l-4 bg-card", errors ? "border-l-destructive" : "border-l-primary/40")}>
      <div className="flex flex-wrap items-center gap-2 px-4 py-3">
        <button type="button" onClick={() => setOpen((v) => !v)} aria-label="Toggle entity"><ChevronDown className={cn("h-4 w-4 transition", !open && "-rotate-90")} /></button>
        {editable ? (
          <Input className="h-8 w-56 font-mono text-sm font-semibold" value={entity.entity_name} onChange={(e) => onChange({ ...entity, entity_name: e.target.value })} aria-label="Entity name" />
        ) : <span className="font-mono text-sm font-semibold">{entity.entity_name}</span>}
        {editable ? (
          <select className="h-8 rounded-lg border bg-card px-2 text-xs" value={entity.kind} onChange={(e) => onChange({ ...entity, kind: e.target.value })} aria-label="Kind">
            {KINDS.map((k) => <option key={k}>{k}</option>)}
          </select>
        ) : <Badge variant="outline">{entity.kind.toLowerCase()}</Badge>}
        <span className="text-xs text-muted-foreground">{entity.attributes.length} columns</span>
        {errors > 0 && <Badge variant="destructive">{errors} to fix</Badge>}
        {editable && <Button size="sm" variant="ghost" className="ml-auto" onClick={onRemove} aria-label="Remove entity"><Trash2 className="h-3.5 w-3.5" /></Button>}
      </div>
      {open && (
        <div className="space-y-3 border-t px-4 pb-4 pt-3">
          <div className="grid gap-2 md:grid-cols-3">
            <label className="space-y-1 text-xs md:col-span-2">Purpose
              <Input disabled={!editable} value={entity.purpose} onChange={(e) => onChange({ ...entity, purpose: e.target.value })} />
            </label>
            <label className="space-y-1 text-xs">Grain
              <Input disabled={!editable} value={entity.grain} onChange={(e) => onChange({ ...entity, grain: e.target.value })} placeholder="one row per ..." />
            </label>
            <label className="space-y-1 text-xs md:col-span-3">Business keys (comma separated)
              <Input disabled={!editable} className="font-mono" value={entity.business_keys.join(", ")}
                     onChange={(e) => onChange({ ...entity, business_keys: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
            </label>
          </div>
          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full min-w-[820px] text-xs">
              <thead className="bg-muted/50 text-left text-[10px] uppercase tracking-wide text-muted-foreground">
                <tr><th className="px-2 py-1.5">Column</th><th className="px-2 py-1.5">Type</th><th className="px-2 py-1.5">Null</th><th className="px-2 py-1.5">PK</th>
                  <th className="px-2 py-1.5">Source columns</th><th className="px-2 py-1.5">Derived</th><th className="px-2 py-1.5">Why</th><th /></tr>
              </thead>
              <tbody>
                {entity.attributes.map((a, j) => {
                  const problems = issuesFor(issues, `entities[${index}].attributes[${j}]`);
                  return (
                    <tr key={j} className={cn("border-t align-top", problems.some((p) => p.severity === "ERROR") && "bg-destructive/5")}>
                      <td className="px-2 py-1"><Input disabled={!editable} className="h-7 w-44 font-mono text-xs" value={a.name} onChange={(e) => setAttr(j, { name: e.target.value })} aria-label="Column name" /></td>
                      <td className="px-2 py-1"><Input disabled={!editable} className="h-7 w-32 font-mono text-xs" value={a.datatype} onChange={(e) => setAttr(j, { datatype: e.target.value })} aria-label="Type" /></td>
                      <td className="px-2 py-1 text-center"><input type="checkbox" disabled={!editable} checked={a.nullable} onChange={(e) => setAttr(j, { nullable: e.target.checked })} aria-label="Nullable" /></td>
                      <td className="px-2 py-1 text-center"><input type="checkbox" disabled={!editable} checked={a.is_pk} onChange={(e) => setAttr(j, { is_pk: e.target.checked })} aria-label="Primary key" /></td>
                      <td className="px-2 py-1">
                        <Input disabled={!editable} className="h-7 w-56 font-mono text-[11px]" value={a.source_columns.join(", ")}
                               onChange={(e) => setAttr(j, { source_columns: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} placeholder="TABLE.COLUMN" aria-label="Source columns" />
                        {problems.map((p, k) => <p key={k} className={cn("mt-0.5 text-[10px]", p.severity === "ERROR" ? "text-destructive" : "text-warning")}>{p.message}</p>)}
                      </td>
                      <td className="px-2 py-1 text-center"><input type="checkbox" disabled={!editable} checked={a.derived} onChange={(e) => setAttr(j, { derived: e.target.checked })} aria-label="Derived" /></td>
                      <td className="max-w-[16rem] px-2 py-1 text-[11px] text-muted-foreground">{a.rationale}</td>
                      <td className="px-1 py-1">{editable && <button type="button" onClick={() => onChange({ ...entity, attributes: entity.attributes.filter((_, k) => k !== j) })} aria-label="Remove column" className="rounded p-1 hover:bg-muted"><X className="h-3.5 w-3.5" /></button>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {mine.filter((i) => i.path === `entities[${index}]`).map((i, k) => (
            <p key={k} className={cn("flex items-center gap-1 text-xs", i.severity === "ERROR" ? "text-destructive" : "text-warning")}><AlertTriangle className="h-3.5 w-3.5" />{i.message}</p>
          ))}
          {editable && (
            <Button size="sm" variant="outline" onClick={() => onChange({ ...entity, attributes: [...entity.attributes, { name: "NEW_COLUMN", datatype: "VARCHAR", nullable: true, is_pk: false, source_columns: [], derived: true, rationale: "" }] })}>
              <Plus className="h-3.5 w-3.5" />Add column
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

export function ModelDesign({ runId, data, canApply, state }: { runId: string; data: ModelPayload; canApply: boolean; state: string }) {
  const router = useRouter();
  const versions = data.versions;
  const [selected, setSelected] = useState<number>(data.current?.version ?? 0);
  const shown: DesignVersion | undefined = versions.find((v) => v.version === selected) ?? data.current ?? undefined;
  const [draft, setDraft] = useState<Design | null>(shown ? structuredClone(shown.design) : null);
  const [conv, setConv] = useState<Conventions>(shown?.conventions?.preset ? shown.conventions
    : { ...EMPTY_CUSTOM, preset: data.standard === "GDP" ? "GDP" : "NONE" });
  const [issues, setIssues] = useState<Issue[]>(shown?.issues ?? []);
  const [instructions, setInstructions] = useState("");
  const [dirty, setDirty] = useState(false);
  const [changes, setChanges] = useState<DiffChange[] | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, start] = useTransition();
  const [collapsed, setCollapsed] = useState(Boolean(data.approved) && !["DOMAIN_IDENTIFIED", "PROFILING_COMPLETE"].includes(state));

  // after a generate/save the page refreshes with the new version: load it unless the user is mid-edit
  useEffect(() => {
    const found = versions.find((x) => x.version === selected);
    if (found && !dirty) {
      setDraft(structuredClone(found.design));
      setIssues(found.issues ?? []);
      if (found.conventions?.preset) setConv(found.conventions);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versions, selected]);

  const pick = (v: number) => {
    const found = versions.find((x) => x.version === v);
    setSelected(v);
    setChanges(null);
    if (found) { setDraft(structuredClone(found.design)); setConv(found.conventions?.preset ? found.conventions : conv); setIssues(found.issues ?? []); setDirty(false); }
  };
  const edit = (d: Design) => { setDraft(d); setDirty(true); };
  const errors = issues.filter((i) => i.severity === "ERROR").length;
  const isApproved = shown?.status === "APPROVED" && !dirty;

  const generate = () => start(async () => {
    setMessage(null);
    const r = await generateDesign(runId, {
      preset: conv.preset, custom: conv.preset === "CUSTOM" ? conv : undefined, instructions,
      base_version: instructions && shown ? shown.version : undefined,
      target_database: draft?.target_database, target_schema: draft?.target_schema,
    });
    if (!r.ok) { setMessage({ ok: false, text: r.error }); return; }
    setMessage({ ok: true, text: `Version ${r.data.version} designed by AI${r.data.issues.length ? ` · ${r.data.issues.length} notes` : ""}.` });
    setInstructions("");
    router.refresh();
    setSelected(r.data.version);
  });
  const save = () => start(async () => {
    if (!draft) return;
    setMessage(null);
    const r = await saveDesign(runId, draft, conv);
    if (!r.ok) { setMessage({ ok: false, text: r.error }); return; }
    setIssues(r.data.issues);
    setDirty(false);
    setMessage({ ok: true, text: `Saved as version ${r.data.version}.` });
    router.refresh();
    setSelected(r.data.version);
  });
  const recheck = (c: Conventions) => {
    setConv(c);
    setDirty(true);
    if (!draft) return;
    start(async () => {
      const r = await checkDesign(runId, draft, c);
      if (r.ok) setIssues(r.data.issues);
    });
  };
  const approve = () => start(async () => {
    if (!shown) return;
    setMessage(null);
    const r = await approveDesign(runId, shown.version);
    if (!r.ok) { setMessage({ ok: false, text: r.error }); return; }
    setMessage({ ok: true, text: `Version ${shown.version} is the target: ${r.data.target_model}.${r.data.mapping_reset ? " Mapping was sent back so candidates are regenerated for it." : ""} Saved to knowledge.` });
    router.refresh();
  });
  const compare = () => start(async () => {
    const prev = versions.find((v) => v.version < (shown?.version ?? 0));
    if (!shown || !prev) return;
    const r = await diffDesigns(runId, prev.version, shown.version);
    if (r.ok) setChanges(r.data.changes);
  });

  const decision = draft ? DECISION[draft.decision] ?? DECISION.NEW : null;
  const evidence = useMemo(() => draft?.evidence ?? [], [draft]);

  return (
    <section className="space-y-4 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Boxes className="h-5 w-5 text-violet-600" />
        <h2 className="text-lg font-semibold">Data model</h2>
        {shown && <Badge variant={shown.status === "APPROVED" ? "success" : "outline"}>v{shown.version} · {shown.status.toLowerCase()}{dirty ? " · edited" : ""}</Badge>}
        {data.target_model && <span className="font-mono text-xs text-muted-foreground">target {data.target_model}</span>}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {versions.length > 0 && (
            <label className="flex items-center gap-1 text-xs text-muted-foreground"><History className="h-3.5 w-3.5" />
              <select className="h-8 rounded-lg border bg-card px-2 text-xs" value={selected} onChange={(e) => pick(Number(e.target.value))} aria-label="Version">
                {versions.map((v) => <option key={v.version} value={v.version}>v{v.version} · {v.origin.toLowerCase()} · {v.status.toLowerCase()}</option>)}
              </select>
            </label>
          )}
          {versions.length > 1 && <Button size="sm" variant="ghost" onClick={compare} disabled={busy}><GitCompare className="h-3.5 w-3.5" />Compare</Button>}
          <Button size="sm" variant="ghost" onClick={() => setCollapsed((v) => !v)} aria-label="Toggle data model"><ChevronDown className={cn("h-4 w-4 transition", collapsed && "-rotate-90")} /></Button>
        </div>
      </div>

      {!collapsed && (
        <>
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <div className="space-y-1">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Conventions</p>
              <ConventionsPicker value={conv} onChange={recheck} standard={data.standard} />
            </div>
            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Ask AI to design</p>
              <div className="flex gap-2">
                <Textarea rows={2} value={instructions} onChange={(e) => setInstructions(e.target.value)} className="flex-1"
                          placeholder={shown ? "Optional: what to change, e.g. split customer into its own dimension" : "Optional: anything the model must respect"} aria-label="Design instructions" />
                <Button className="self-stretch bg-gradient-to-r from-violet-600 to-primary text-white" disabled={busy} onClick={generate}>
                  {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}{instructions && shown ? "Revise" : shown ? "Redesign" : "Design"}
                </Button>
              </div>
              <p className="text-[11px] text-muted-foreground">Uses only this run&apos;s profiles, the domain&apos;s registered models and knowledge, and the modeling skills.</p>
            </div>
          </div>

          {draft && decision && (
            <div className={cn("flex flex-wrap items-start gap-3 rounded-lg border px-3 py-2 text-sm", decision.tone)}>
              <span className="font-semibold">{decision.label}</span>
              {draft.decision_target && <span className="font-mono text-xs">{draft.decision_target}</span>}
              <span className="basis-full text-xs text-foreground/80">{draft.reasons.join(" ")}</span>
              {evidence.length > 0 && (
                <span className="flex basis-full flex-wrap gap-1">
                  {evidence.slice(0, 12).map((e) => <span key={e} className="rounded-full border bg-card px-2 py-0.5 font-mono text-[10px] text-muted-foreground">{e}</span>)}
                </span>
              )}
            </div>
          )}

          {draft && (
            <div className="grid gap-2 sm:grid-cols-3">
              <label className="space-y-1 text-xs">Target database
                <Input className="font-mono" value={draft.target_database} onChange={(e) => edit({ ...draft, target_database: e.target.value })} />
              </label>
              <label className="space-y-1 text-xs">Target schema
                <Input className="font-mono" value={draft.target_schema} onChange={(e) => edit({ ...draft, target_schema: e.target.value })} />
              </label>
              <label className="space-y-1 text-xs">Entity the run maps to
                <select className="h-9 w-full rounded-lg border bg-card px-2 text-sm" value={draft.primary_entity} onChange={(e) => edit({ ...draft, primary_entity: e.target.value })}>
                  {draft.entities.map((e) => <option key={e.entity_name} value={e.entity_name}>{e.entity_name}</option>)}
                </select>
              </label>
            </div>
          )}

          {changes && (
            <div className="rounded-lg border bg-muted/20 p-3 text-xs">
              <p className="mb-1 font-semibold">Changes from the previous version</p>
              {changes.length === 0 ? <p className="text-muted-foreground">No column changes.</p> : (
                <ul className="space-y-0.5">
                  {changes.map((c, i) => (
                    <li key={i} className="font-mono"><span className={cn(c.change.includes("ADDED") ? "text-success" : c.change.includes("REMOVED") ? "text-destructive" : "text-warning")}>{c.change.toLowerCase()}</span> {c.entity}{c.column ? `.${c.column}` : ""}{c.fields ? ` (${c.fields.join(", ")})` : ""}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {draft ? (
            <div className="space-y-3">
              {draft.entities.map((e, i) => (
                <EntityCard key={i} entity={e} index={i} issues={issues} editable
                            onChange={(n) => edit({ ...draft, entities: draft.entities.map((x, k) => (k === i ? n : x)) })}
                            onRemove={() => edit({ ...draft, entities: draft.entities.filter((_, k) => k !== i) })} />
              ))}
              <Button size="sm" variant="outline" onClick={() => edit({ ...draft, entities: [...draft.entities, { entity_name: "NEW_ENTITY", kind: "TABLE", purpose: "", grain: "", business_keys: [], attributes: [] }] })}>
                <Plus className="h-3.5 w-3.5" />Add entity
              </Button>
            </div>
          ) : (
            <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">No design yet. Choose conventions and ask AI to design the model.</p>
          )}

          {message && <p role={message.ok ? "status" : "alert"} className={cn("text-sm", message.ok ? "text-success" : "text-destructive")}>{message.text}</p>}

          {draft && (
            <div className="flex flex-wrap items-center gap-2 border-t pt-3">
              <span className="text-xs text-muted-foreground">
                {errors ? `${errors} problem${errors === 1 ? "" : "s"} to fix before approving` : issues.length ? `${issues.length} note${issues.length === 1 ? "" : "s"}` : "Grounded in the profiled sources"}
              </span>
              <Button size="sm" variant="outline" className="ml-auto" disabled={busy || !dirty} onClick={save}><Save className="h-3.5 w-3.5" />Save as new version</Button>
              <Button size="sm" disabled={busy || dirty || !canApply || errors > 0 || isApproved} onClick={approve}
                      title={!canApply ? "The run is past mapping; reopen mapping to change the target" : dirty ? "Save your edits first" : undefined}>
                {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}{isApproved ? "In use" : "Approve and use for mapping"}
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
