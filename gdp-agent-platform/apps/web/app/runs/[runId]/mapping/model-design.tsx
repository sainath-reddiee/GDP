"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertTriangle, Boxes, Check, ChevronDown, GitCompare, GitPullRequestArrow, History, Link2, Loader2, Plus, Save, Sparkles, Trash2, X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { TagEditor } from "@/components/tag-editor";
import { useToast } from "@/components/ui/toast";
import {
  applyDomainModel, approveDesign, checkDesign, diffDesigns, generateDesign, proposeDesign, saveDesign,
  type Attribute, type Conventions, type Design, type DesignVersion, type DiffChange, type DomainModelSummary, type Entity,
  type Issue, type ModelPayload, type StoredTable, type WorkbenchState,
} from "./model-actions";

const PRESETS: { key: Conventions["preset"]; label: string; hint: string }[] = [
  { key: "NONE", label: "Free modeling", hint: "No conventions injected; shape the model as you like" },
  // COMPANY is the stored key; the name avoids any confusion with the COMPANY domain
  { key: "COMPANY", label: "Organisation standard", hint: "Your organisation's generic standard (Admin settings, Standards); the same for every domain" },
  { key: "GDP", label: "GDP standard", hint: "Hub/spoke, GDP keys and audit columns" },
  { key: "CUSTOM", label: "Custom", hint: "Pick audit columns, keys, naming and history yourself" },
];
const DECISION: Record<Design["decision"], { label: string; tone: string }> = {
  REUSE_EXISTING: { label: "Reuse an existing model", tone: "border-success/30 bg-success/5 text-success" },
  EXTEND_EXISTING: { label: "Extend an existing model", tone: "border-primary/30 bg-primary/5 text-primary" },
  NEW: { label: "New model", tone: "border-blue-300 bg-blue-50/60 text-blue-700 dark:bg-blue-950/30 dark:text-blue-300" },
};
const KINDS = ["DIMENSION", "FACT", "BRIDGE", "REFERENCE", "TABLE"];
const EMPTY_CUSTOM: Conventions = {
  preset: "CUSTOM", audit_columns: ["LOAD_TS"], surrogate_key: "none", key_pattern: "{TABLE}_SK", naming_case: "upper",
  table_prefix: "", scd_type: null, soft_delete: false,
};

/** What a save did to the Model Store, for the confirmation line. */
function stored(tables?: StoredTable[]): string {
  const failed = (tables ?? []).find((t) => t.status === "FAILED");
  if (failed) return ` The Model Store was not updated (${failed.error ?? "unknown error"}); save again to retry.`;
  const changed = (tables ?? []).filter((t) => t.status !== "UNCHANGED");
  return changed.length ? ` ${changed.length} table model(s) updated in the Model Store.` : "";
}

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

/** Use the domain's approved model as it is: no AI design and no proposal; the run maps to its hub and every
 * spoke with columns. */
function UseDomainModel({ dm, inUse, canApply, busy, onUse }: {
  dm: DomainModelSummary; inUse: boolean; canApply: boolean; busy: boolean; onUse: () => void;
}) {
  const usable = dm.entities.filter((e) => e.columns > 0);
  const empty = dm.entities.filter((e) => e.columns === 0);
  return (
    <div className="space-y-2 rounded-lg border border-emerald-300/60 bg-emerald-50/40 p-3 dark:border-emerald-900 dark:bg-emerald-950/20" data-testid="use-domain-model">
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">Use the {dm.domain_name ?? "domain"} model as it is</p>
          <p className="text-xs text-muted-foreground">
            The source maps to the approved model v{dm.version}: its hub and every table below. No design and no proposal;
            anything the source has that the model does not is left unmapped.
          </p>
        </div>
        {inUse ? <Badge variant="success">In use</Badge> : (
          <Button size="sm" onClick={onUse} disabled={busy || !canApply || !dm.hub} data-testid="use-domain-model-button"
                  title={!canApply ? "The run is past mapping, or you lack MODEL.EDIT" : !dm.hub ? "No table with columns" : undefined}>
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Use v{dm.version} as it is
          </Button>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {usable.map((e) => (
          <span key={e.entity} className="inline-flex items-center gap-1 rounded-md border bg-card px-2 py-0.5 text-[11px]" title={e.fqn}>
            <span className="font-mono font-medium">{e.entity}</span>
            <span className="text-muted-foreground">{e.role.toLowerCase()} · {e.columns} col{e.columns === 1 ? "" : "s"}</span>
          </span>
        ))}
      </div>
      {empty.length > 0 && (
        <p className="text-[11px] text-amber-700">
          {empty.map((e) => e.entity).join(", ")} {empty.length === 1 ? "has" : "have"} no columns in v{dm.version}, so nothing maps to {empty.length === 1 ? "it" : "them"} until a modeler adds them.
        </p>
      )}
    </div>
  );
}

function WorkbenchBanner({ wb }: { wb: WorkbenchState }) {
  const p = wb.proposal;
  const model = wb.model_id ?? p?.model_id ?? null;
  const href = (tab: string) => (model ? `/os/design/modeling/${model}?tab=${tab}` : "/os/design/modeling");
  return (
    <div className="space-y-1.5 rounded-lg border border-blue-300/60 bg-blue-50/50 px-3 py-2 text-xs dark:border-blue-800 dark:bg-blue-950/20" data-testid="run-workbench">
      <p className="flex flex-wrap items-center gap-2">
        <GitPullRequestArrow className="h-3.5 w-3.5 text-blue-600" />
        <span className="font-semibold">The domain model is governed in the Model Workbench.</span>
        <span className="text-muted-foreground">Design here, then propose it. A design that reuses or extends an existing model maps to it at once; anything it adds, or a new model, waits for modelers to merge, review and approve it.</span>
      </p>
      {wb.model_id && wb.model_version != null && (
        <p className="flex flex-wrap items-center gap-2">
          <Link2 className="h-3.5 w-3.5 text-success" />
          <span>Maps to <span className="font-mono font-semibold">{wb.model_entity ?? "?"}</span> of domain model <Badge variant="success">v{wb.model_version}</Badge></span>
          <Link className="text-primary underline-offset-2 hover:underline" href={href("diagram")}>Open model</Link>
        </p>
      )}
      {p && (
        <p className="flex flex-wrap items-center gap-2" role="status">
          <Badge variant={p.status === "STALE" ? "warning" : "outline"}>{({ PROPOSED: "proposal pending", MERGED: "merged into a draft", STALE: "proposal out of date" } as const)[p.status]}</Badge>
          <span className="text-muted-foreground">
            {p.status === "PROPOSED" && (p.maps_existing ? "Waiting for a modeler to merge it; mapping uses the approved model meanwhile."
              : "Waiting for a modeler to merge it into a draft.")}
            {p.status === "MERGED" && (p.maps_existing ? `Merged into draft v${p.merged_into_version ?? "?"}; mapping uses the approved model until that version is approved.`
              : `Merged into draft v${p.merged_into_version ?? "?"}; mapping starts once that version is approved.`)}
            {p.status === "STALE" && "The domain model was approved without it. Propose again, or ask a modeler to merge it into the new draft."}
          </span>
          <Link className="text-primary underline-offset-2 hover:underline" href={href(p.status === "MERGED" ? "review" : "changes")}>
            {p.status === "MERGED" ? "Open review" : "Open proposals"}
          </Link>
        </p>
      )}
    </div>
  );
}

export function ModelDesign({ runId, data, canApply, state, canEdit = true, canAI = true }: {
  runId: string; data: ModelPayload; canApply: boolean; state: string; canEdit?: boolean; canAI?: boolean;
}) {
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
  const toast = useToast();
  // approving or proposing refreshes the run layout, which can remount this section: the toast outlives it
  const say = (ok: boolean, text: string) => { setMessage({ ok, text }); toast(ok ? "ok" : "error", text); };
  const [collapsed, setCollapsed] = useState(
    Boolean(data.approved || data.workbench?.model_id || data.target_model),
  );

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
    setMessage({ ok: true, text: `Version ${r.data.version} designed by AI${r.data.issues.length ? ` · ${r.data.issues.length} notes` : ""}.${stored(r.data.table_models)}` });
    setInstructions("");
    setDirty(false);  // the new AI version replaces the local draft, otherwise Save would store the old draft over it
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
    setMessage({ ok: true, text: `Saved as version ${r.data.version}.${stored(r.data.table_models)}` });
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
    if (!r.ok) { say(false, r.error); return; }
    say(true, `Version ${shown.version} is the target: ${r.data.target_model}.${r.data.mapping_reset ? " Mapping was sent back so candidates are regenerated for it." : ""} Saved to knowledge.`);
    router.refresh();
  });
  const propose = () => start(async () => {
    if (!shown) return;
    setMessage(null);
    const r = await proposeDesign(runId, shown.version);
    if (!r.ok) { say(false, r.error); return; }
    const d = r.data;
    if (d.pinned) {
      const pin = d.pin;
      if (pin && !pin.pinned) say(false, `It matches domain model v${d.version}, but the run could not use it: ${pin.detail ?? "reopen mapping"}.`);
      else say(true, `Nothing new for the domain model: the run maps to ${d.entity} of v${d.version}.${pin?.mapping_reset ? " Mapping was sent back so candidates are regenerated for it." : ""}`);
    } else if (d.status === "MERGED") {
      say(true, "The domain had no model yet: this design is now its first draft (v1). Submit it for review in the Model Workbench.");
    } else {
      const n = d.counts ? Object.values(d.counts).reduce((a, b) => a + b, 0) : 0;
      const changes = `${n} change${n === 1 ? "" : "s"}${d.breaking ? ", including breaking ones," : ""}`;
      if (d.use_existing && d.pin?.pinned) {
        say(true, `Mapping can start now: the run maps to ${d.use_existing.entity} of domain model v${d.use_existing.version}. `
          + `The design's ${changes} went to the modelers as a proposal; once approved, the run moves to the new version.`
          + (d.pin.mapping_reset ? " Mapping was sent back so candidates are regenerated for it." : ""));
      } else if (d.use_existing) {
        say(false, `Proposed ${changes} to the domain model, but the run could not map to ${d.use_existing.entity} yet: ${d.pin?.detail ?? "reopen mapping"}.`);
      } else {
        say(true, `Proposed ${changes} to the domain model. A modeler merges and approves it; the run maps once it is approved.`);
      }
    }
    router.refresh();
  });
  const useAsIs = () => start(async () => {
    setMessage(null);
    const r = await applyDomainModel(runId);
    if (!r.ok) { say(false, r.error); return; }
    const tables = r.data.entities.filter((e) => e.columns > 0).length;
    say(true, `The run maps to domain model v${r.data.version} as it is: ${r.data.entity}${tables > 1 ? ` and ${tables - 1} more table${tables === 2 ? "" : "s"}` : ""}. Generate mapping candidates next.`
      + (r.data.withdrawn ? ` ${r.data.withdrawn} pending proposal${r.data.withdrawn === 1 ? " was" : "s were"} withdrawn.` : "")
      + (r.data.mapping_reset ? " Mapping was sent back so candidates are regenerated for it." : ""));
    router.refresh();
  });
  const compare = () => start(async () => {
    const prev = versions.find((v) => v.version < (shown?.version ?? 0));
    if (!shown || !prev) return;
    const r = await diffDesigns(runId, prev.version, shown.version);
    if (r.ok) setChanges(r.data.changes);
  });

  const wb = data.workbench;
  const decision = draft ? DECISION[draft.decision] ?? DECISION.NEW : null;
  const evidence = useMemo(() => draft?.evidence ?? [], [draft]);

  return (
    <section className="space-y-4 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Boxes className="h-5 w-5 text-blue-600" />
        <h2 className="text-lg font-semibold">Data model</h2>
        {shown && <Badge variant={shown.status === "APPROVED" ? "success" : "outline"}>v{shown.version} · {shown.status.toLowerCase()}{dirty ? " · edited" : ""}</Badge>}
        {data.target_model && <span className="font-mono text-xs text-muted-foreground">target {data.target_model}</span>}
        {data.target_model && data.target_model.split(".").length === 3 && <TagEditor entityType="MODEL" entityKey={data.target_model} compact />}
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
          {wb?.enforced && <WorkbenchBanner wb={wb} />}
          {wb?.domain_model && (
            <UseDomainModel dm={wb.domain_model} busy={busy} onUse={useAsIs}
              canApply={canEdit && ["PROFILING_COMPLETE", "DOMAIN_IDENTIFIED", "MAPPING_PENDING", "MAPPING_REVIEW"].includes(state)}
              inUse={wb.model_id === wb.domain_model.model_id && wb.model_version === wb.domain_model.version
                     && wb.model_entity === wb.domain_model.hub && !wb.proposal} />
          )}
          {versions.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Every saved version keeps its tables in the Model Store, where they can be reopened and edited after this run moves on.{" "}
              <Link href={`/os/design/modeling?view=tables&run=${encodeURIComponent(runId)}`} className="font-medium text-primary hover:underline" data-testid="run-table-models">Open this run&apos;s table models</Link>
            </p>
          )}
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
                <Button className="self-stretch bg-primary text-white" disabled={busy || !canAI} onClick={generate}
                        title={canAI ? undefined : "Needs the AI.USE and MODEL.EDIT privileges"}>
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
                <EntityCard key={i} entity={e} index={i} issues={issues} editable={canEdit}
                            onChange={(n) => edit({ ...draft, entities: draft.entities.map((x, k) => (k === i ? n : x)) })}
                            onRemove={() => edit({ ...draft, entities: draft.entities.filter((_, k) => k !== i) })} />
              ))}
              <Button size="sm" variant="outline" disabled={!canEdit} onClick={() => edit({ ...draft, entities: [...draft.entities, { entity_name: "NEW_ENTITY", kind: "TABLE", purpose: "", grain: "", business_keys: [], attributes: [] }] })}>
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
                {errors ? `${errors} problem${errors === 1 ? "" : "s"} to fix before ${wb?.enforced ? "proposing" : "approving"}` : issues.length ? `${issues.length} note${issues.length === 1 ? "" : "s"}` : "Grounded in the profiled sources"}
              </span>
              <Button size="sm" variant="outline" className="ml-auto" disabled={busy || !dirty || !canEdit} onClick={save}><Save className="h-3.5 w-3.5" />Save as new version</Button>
              {wb?.enforced ? (
                <Button size="sm" disabled={busy || dirty || !canApply || errors > 0 || !canEdit} onClick={propose}
                        title={!canApply ? "The run is past mapping; reopen mapping to change the target" : dirty ? "Save your edits first"
                          : wb.proposal && wb.proposal.status !== "STALE" ? "Replaces the open proposal of this run" : undefined}>
                  {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitPullRequestArrow className="h-3.5 w-3.5" />}
                  {wb.proposal && wb.proposal.status === "PROPOSED" ? "Propose again" : "Propose to domain model"}
                </Button>
              ) : (
                <Button size="sm" disabled={busy || dirty || !canApply || errors > 0 || isApproved} onClick={approve}
                        title={!canApply ? "The run is past mapping; reopen mapping to change the target" : dirty ? "Save your edits first" : undefined}>
                  {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}{isApproved ? "In use" : "Approve and use for mapping"}
                </Button>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
