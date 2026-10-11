"use client";

import { Fragment, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, KeyRound, Plus, RotateCcw, Search, ShieldAlert, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { DmAttribute, DmEntity, DmIssue, DmSpec } from "@/lib/model-types";
import {
  addAttribute, addEntity, addRelationship, dropAttribute, dropEntity, dropRelationship, moveAttribute, updateAttribute, updateEntity,
  updateRelationship,
} from "@/lib/model-edit";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";

const TABLE_TYPES = ["DIMENSION", "FACT", "BRIDGE", "REFERENCE", "TABLE", "HUB", "SPOKE", "LINK", "SAT"];
const TYPES = ["NUMBER(38,0)", "NUMBER(18,2)", "INTEGER", "BIGINT", "FLOAT", "BOOLEAN", "DATE", "TIMESTAMP_NTZ", "TIMESTAMP_TZ", "VARCHAR",
  "VARCHAR(255)", "VARCHAR(50)", "CHAR(2)", "VARIANT", "OBJECT", "ARRAY", "BINARY", "GEOGRAPHY"];
const PII = ["", "EMAIL", "PHONE", "NAME", "ADDRESS", "NATIONAL_ID", "FINANCIAL", "HEALTH", "OTHER"];
const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

function changedAttr(a: DmAttribute, old?: DmAttribute) {
  return old ? JSON.stringify(a) !== JSON.stringify(old) : false;
}

function ColumnRow({ a, e, old, isNew, editable, spec, onChange, approvedIds, first, last, issues }: {
  a: DmAttribute; e: DmEntity; old?: DmAttribute; isNew: boolean; editable: boolean; spec: DmSpec; onChange: (s: DmSpec) => void;
  approvedIds: Set<string>; first: boolean; last: boolean; issues: DmIssue[];
}) {
  const [open, setOpen] = useState(false);
  const set = (patch: Partial<DmAttribute>) => onChange(updateAttribute(spec, e.id, a.id, patch));
  const retired = a.status === "RETIRED";
  const problems = issues.filter((i) => i.path === `${e.entity_name}.${a.name}`);
  return (
    <Fragment>
      <tr className={cn("border-t border-border/60 align-top", retired && "opacity-50")} data-testid={`col-${a.name}`}>
        <td className="px-1 py-1.5">
          <button type="button" aria-label={open ? "Hide details" : "Show details"} onClick={() => setOpen(!open)} className="rounded p-0.5 text-muted-foreground hover:bg-muted">
            {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}</button>
        </td>
        <td className="px-1 py-1.5">
          <div className="flex items-center gap-1">
            {a.is_pk && <KeyRound className="h-3 w-3 shrink-0 text-warning" aria-label="primary key" />}
            <Input aria-label="Column name" className={cn("h-7 min-w-[170px] font-mono text-xs", problems.some((p) => p.severity === "ERROR") && "border-destructive")}
                   value={a.name} disabled={!editable || retired} onChange={(ev) => set({ name: ev.target.value })} />
          </div>
          <div className="mt-0.5 flex gap-1 text-[10px]">
            {isNew && <span className="rounded bg-emerald-100 px-1 font-semibold text-emerald-700">new</span>}
            {!isNew && changedAttr(a, old) && <span className="rounded bg-amber-100 px-1 font-semibold text-amber-800">changed</span>}
            {retired && <span className="rounded bg-slate-200 px-1 font-semibold text-slate-700">retired</span>}
            {problems.map((p, i) => <span key={i} className={p.severity === "ERROR" ? "text-destructive" : "text-amber-700"} title={p.message}>{p.severity === "ERROR" ? "error" : "warning"}</span>)}
          </div>
        </td>
        <td className="px-1 py-1.5">
          <Input aria-label="Data type" list="dm-types" className="h-7 w-36 font-mono text-xs" value={a.datatype} disabled={!editable || retired}
                 onChange={(ev) => set({ datatype: ev.target.value })} />
        </td>
        {(["nullable", "is_pk", "business_key", "is_unique"] as const).map((f) => (
          <td key={f} className="px-1 py-1.5 text-center">
            <input type="checkbox" aria-label={`${a.name} ${f}`} checked={!!a[f]} disabled={!editable || retired || (f === "nullable" && a.is_pk)}
                   onChange={(ev) => set({ [f]: ev.target.checked } as Partial<DmAttribute>)} />
          </td>
        ))}
        <td className="px-1 py-1.5">
          <Select aria-label="PII" className="h-7 w-28 py-0 text-xs" disabled={!editable || retired} value={a.pii.flag ? (a.pii.classification ?? "OTHER") : ""}
                  onChange={(ev) => set({ pii: { flag: !!ev.target.value, classification: ev.target.value || null } })}>
            {PII.map((p) => <option key={p} value={p}>{p ? p.toLowerCase().replace(/_/g, " ") : "not PII"}</option>)}
          </Select>
        </td>
        <td className="px-1 py-1.5">
          <Input aria-label="Description" className="h-7 min-w-[180px] text-xs" value={a.description} disabled={!editable || retired}
                 onChange={(ev) => set({ description: ev.target.value })} placeholder="What it means" />
        </td>
        <td className="whitespace-nowrap px-1 py-1.5">
          {editable && (
            <span className="flex items-center gap-0.5">
              <button type="button" aria-label="Move up" disabled={first} onClick={() => onChange(moveAttribute(spec, e.id, a.id, -1))} className="rounded p-1 text-muted-foreground hover:bg-muted disabled:opacity-30"><ArrowUp className="h-3 w-3" /></button>
              <button type="button" aria-label="Move down" disabled={last} onClick={() => onChange(moveAttribute(spec, e.id, a.id, 1))} className="rounded p-1 text-muted-foreground hover:bg-muted disabled:opacity-30"><ArrowDown className="h-3 w-3" /></button>
              <button type="button" aria-label={approvedIds.has(a.id) ? (retired ? "Restore column" : "Retire column") : "Remove column"} data-testid={`drop-${a.name}`}
                      onClick={() => onChange(dropAttribute(spec, e.id, a.id, approvedIds))} className="rounded p-1 text-muted-foreground hover:bg-muted">
                {retired ? <RotateCcw className="h-3 w-3" /> : <Trash2 className="h-3 w-3" />}</button>
            </span>
          )}
        </td>
      </tr>
      {open && (
        <tr className="bg-muted/30">
          <td />
          <td colSpan={9} className="px-1 pb-3 pt-1">
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <div className="lg:col-span-2"><Label htmlFor={`bd-${a.id}`}>Business definition</Label>
                <Textarea id={`bd-${a.id}`} rows={2} className="text-xs" value={a.business_definition} disabled={!editable || retired} onChange={(ev) => set({ business_definition: ev.target.value })} /></div>
              <div><Label htmlFor={`st-${a.id}`}>Semantic type</Label>
                <Input id={`st-${a.id}`} className="h-8 text-xs" value={a.semantic_type ?? ""} disabled={!editable || retired} onChange={(ev) => set({ semantic_type: ev.target.value.toUpperCase() || null })} placeholder="EMAIL, COUNTRY_CODE..." /></div>
              <div><Label htmlFor={`df-${a.id}`}>Default</Label>
                <Input id={`df-${a.id}`} className="h-8 font-mono text-xs" value={a.default ?? ""} disabled={!editable || retired} onChange={(ev) => set({ default: ev.target.value || null })} placeholder="CURRENT_TIMESTAMP()" /></div>
              <div className="lg:col-span-2"><Label htmlFor={`av-${a.id}`}>Accepted values (comma separated)</Label>
                <Input id={`av-${a.id}`} className="h-8 font-mono text-xs" value={a.accepted_values.join(", ")} disabled={!editable || retired} onChange={(ev) => set({ accepted_values: list(ev.target.value) })} /></div>
              <div><Label htmlFor={`ck-${a.id}`}>Check (SQL expression)</Label>
                <Input id={`ck-${a.id}`} className="h-8 font-mono text-xs" value={a.check ?? ""} disabled={!editable || retired} onChange={(ev) => set({ check: ev.target.value || null })} placeholder="AMOUNT >= 0" /></div>
              <div><Label htmlFor={`tg-${a.id}`}>Tags</Label>
                <Input id={`tg-${a.id}`} className="h-8 text-xs" value={a.tags.join(", ")} disabled={!editable || retired} onChange={(ev) => set({ tags: list(ev.target.value.toLowerCase()) })} /></div>
            </div>
            {a.source_columns.length > 0 && <p className="mt-1 text-[11px] text-muted-foreground">Sources (from runs): <span className="font-mono">{a.source_columns.join(", ")}</span></p>}
            {problems.map((p, i) => <p key={i} className={cn("mt-1 text-[11px]", p.severity === "ERROR" ? "text-destructive" : "text-amber-700")}>{p.message}</p>)}
          </td>
        </tr>
      )}
    </Fragment>
  );
}

function EntityEditor({ e, spec, onChange, editable, approvedIds, approved, issues }: {
  e: DmEntity; spec: DmSpec; onChange: (s: DmSpec) => void; editable: boolean; approvedIds: Set<string>; approved?: DmEntity; issues: DmIssue[];
}) {
  const set = (patch: Partial<DmEntity>) => onChange(updateEntity(spec, e.id, patch));
  const retired = e.status === "RETIRED";
  const oldCols = Object.fromEntries((approved?.attributes ?? []).map((a) => [a.id, a]));
  const entityIssues = issues.filter((i) => i.path === e.entity_name);
  const dis = !editable || retired;
  return (
    <section className="surface space-y-4 p-4" data-testid="entity-editor">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-[220px] flex-1"><Label htmlFor="en-name">Table name</Label>
          <Input id="en-name" className="font-mono" value={e.entity_name} disabled={dis} onChange={(ev) => set({ entity_name: ev.target.value })} /></div>
        <div><Label htmlFor="en-type">Table type</Label>
          <Select id="en-type" className="w-40" value={e.table_type} disabled={dis} onChange={(ev) => set({ table_type: ev.target.value })}>
            {Array.from(new Set([...TABLE_TYPES, e.table_type])).map((t) => <option key={t} value={t}>{t.toLowerCase()}</option>)}
          </Select></div>
        <div><Label htmlFor="en-scd">SCD</Label>
          <Input id="en-scd" className="w-24 font-mono" value={e.scd_type} disabled={dis} onChange={(ev) => set({ scd_type: ev.target.value.toUpperCase() })} /></div>
        <div><Label htmlFor="en-area">Subject area</Label>
          <Input id="en-area" className="w-40" list="dm-areas" value={e.subject_area ?? ""} disabled={dis} onChange={(ev) => set({ subject_area: ev.target.value || null })} /></div>
        {editable && (
          <Button variant="outline" className="mt-5" onClick={() => onChange(dropEntity(spec, e.id, approvedIds))} data-testid="drop-entity">
            {retired ? <><RotateCcw className="h-4 w-4" /> Restore</> : approvedIds.has(e.id) ? <><Trash2 className="h-4 w-4" /> Retire</> : <><Trash2 className="h-4 w-4" /> Remove</>}
          </Button>
        )}
      </div>
      {entityIssues.length > 0 && (
        <ul className="space-y-0.5 text-xs">{entityIssues.map((i, n) => <li key={n} className={i.severity === "ERROR" ? "text-destructive" : "text-amber-700"}><ShieldAlert className="mr-1 inline h-3 w-3" />{i.message}</li>)}</ul>
      )}
      <div className="grid gap-3 lg:grid-cols-2">
        <div><Label htmlFor="en-desc">Description</Label>
          <Textarea id="en-desc" rows={2} value={e.description} disabled={dis} onChange={(ev) => set({ description: ev.target.value })} /></div>
        <div><Label htmlFor="en-grain">Grain</Label>
          <Textarea id="en-grain" rows={2} value={e.grain} disabled={dis} onChange={(ev) => set({ grain: ev.target.value })} placeholder="One row per customer per day" /></div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div><Label htmlFor="en-bk">Business keys</Label>
          <Input id="en-bk" className="font-mono text-xs" value={e.business_keys.join(", ")} disabled={dis} onChange={(ev) => set({ business_keys: list(ev.target.value.toUpperCase()) })} /></div>
        <div><Label htmlFor="en-uq">Unique constraints (A, B; C)</Label>
          <Input id="en-uq" className="font-mono text-xs" value={e.unique_constraints.map((u) => u.join(", ")).join("; ")} disabled={dis}
                 onChange={(ev) => set({ unique_constraints: ev.target.value.split(";").map((u) => list(u.toUpperCase())).filter((u) => u.length) })} /></div>
        <div><Label htmlFor="en-db">Database / schema override</Label>
          <div className="flex gap-1">
            <Input id="en-db" aria-label="Database override" className="font-mono text-xs" value={e.database ?? ""} placeholder={spec.physical_defaults.database || "default"} disabled={dis} onChange={(ev) => set({ database: ev.target.value.toUpperCase() || null })} />
            <Input aria-label="Schema override" className="font-mono text-xs" value={e.schema ?? ""} placeholder={spec.physical_defaults.schema || "default"} disabled={dis} onChange={(ev) => set({ schema: ev.target.value.toUpperCase() || null })} />
          </div></div>
        <div><Label htmlFor="en-cl">Cluster by / options</Label>
          <div className="flex items-center gap-2">
            <Input id="en-cl" className="font-mono text-xs" value={e.physical.cluster_by.join(", ")} disabled={dis} onChange={(ev) => set({ physical: { ...e.physical, cluster_by: list(ev.target.value.toUpperCase()) } })} />
            <label className="flex items-center gap-1 whitespace-nowrap text-xs"><input type="checkbox" checked={e.physical.transient} disabled={dis} onChange={(ev) => set({ physical: { ...e.physical, transient: ev.target.checked } })} /> transient</label>
          </div></div>
      </div>
      <div>
        <Label>Checks</Label>
        <div className="space-y-1">
          {e.checks.map((c, i) => (
            <div key={i} className="flex gap-1">
              <Input aria-label="Check name" className="h-8 w-44 font-mono text-xs" value={c.name} disabled={dis} onChange={(ev) => set({ checks: e.checks.map((x, k) => (k === i ? { ...x, name: ev.target.value.toUpperCase() } : x)) })} />
              <Input aria-label="Check expression" className="h-8 font-mono text-xs" value={c.expression} disabled={dis} onChange={(ev) => set({ checks: e.checks.map((x, k) => (k === i ? { ...x, expression: ev.target.value } : x)) })} />
              {editable && <button type="button" aria-label="Remove check" className="rounded p-1 text-muted-foreground hover:bg-muted" onClick={() => set({ checks: e.checks.filter((_, k) => k !== i) })}><Trash2 className="h-3.5 w-3.5" /></button>}
            </div>
          ))}
          {editable && !retired && <Button size="sm" variant="ghost" onClick={() => set({ checks: [...e.checks, { name: `CK_${e.entity_name}_${e.checks.length + 1}`, expression: "" }] })}><Plus className="h-3.5 w-3.5" /> Add check</Button>}
        </div>
      </div>
      {e.model_spec && Object.keys(e.model_spec).length > 0 && (
        <details className="text-xs"><summary className="cursor-pointer text-muted-foreground">Hub and spoke settings (kept as they are)</summary>
          <pre className="mt-1 max-h-40 overflow-auto rounded bg-muted p-2 font-mono text-[11px]">{JSON.stringify(e.model_spec, null, 2)}</pre></details>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-sm" data-testid="columns-grid">
          <thead className="text-[10px] uppercase tracking-wide text-muted-foreground">
            <tr><th className="w-6" /><th className="px-1 py-1 text-left">Column</th><th className="px-1 py-1 text-left">Type</th>
              <th className="px-1 py-1">Null</th><th className="px-1 py-1">PK</th><th className="px-1 py-1">BK</th><th className="px-1 py-1">Unique</th>
              <th className="px-1 py-1 text-left">PII</th><th className="px-1 py-1 text-left">Description</th><th /></tr>
          </thead>
          <tbody>
            {e.attributes.map((a, i) => (
              <ColumnRow key={a.id} a={a} e={e} old={oldCols[a.id]} isNew={!approvedIds.has(a.id)} editable={editable} spec={spec} onChange={onChange}
                         approvedIds={approvedIds} first={i === 0} last={i === e.attributes.length - 1} issues={issues} />
            ))}
          </tbody>
        </table>
        {editable && !retired && <Button size="sm" variant="ghost" className="mt-1" onClick={() => onChange(addAttribute(spec, e.id))} data-testid="add-column"><Plus className="h-3.5 w-3.5" /> Add column</Button>}
      </div>
    </section>
  );
}

export function EntitiesPanel({ spec, onChange, editable, selected, onSelect, approvedIds, issues, approvedSpec }: {
  spec: DmSpec; onChange: (s: DmSpec) => void; editable: boolean; selected: string | null; onSelect: (id: string | null) => void;
  approvedIds: Set<string>; issues: DmIssue[]; approvedSpec: DmSpec | null;
}) {
  const [q, setQ] = useState("");
  const [newName, setNewName] = useState("");
  const shown = useMemo(() => spec.design.entities.filter((e) => !q || `${e.entity_name} ${e.subject_area ?? ""} ${e.attributes.map((a) => a.name).join(" ")}`.toLowerCase().includes(q.toLowerCase())), [spec, q]);
  const current = spec.design.entities.find((e) => e.id === selected) ?? spec.design.entities[0];
  const approvedById = Object.fromEntries((approvedSpec?.design.entities ?? []).map((e) => [e.id, e]));
  const areas = Array.from(new Set(spec.design.entities.map((e) => e.subject_area).filter(Boolean))) as string[];
  const add = () => {
    const r = addEntity(spec, newName || "NEW_ENTITY");
    onChange(r.spec);
    onSelect(r.id);
    setNewName("");
  };
  return (
    <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
      <datalist id="dm-types">{TYPES.map((t) => <option key={t} value={t} />)}</datalist>
      <datalist id="dm-areas">{areas.map((a) => <option key={a} value={a} />)}</datalist>
      <aside className="surface space-y-2 p-3" aria-label="Entities">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a table or column" className="h-8 pl-8 text-xs" aria-label="Find an entity" />
        </div>
        <ul className="max-h-[560px] space-y-0.5 overflow-auto">
          {shown.map((e) => {
            const isNew = !approvedIds.has(e.id);
            const changed = !isNew && approvedById[e.id] && JSON.stringify(approvedById[e.id]) !== JSON.stringify(e);
            const errs = issues.filter((i) => i.severity === "ERROR" && i.path.split(".")[0] === e.entity_name).length;
            return (
              <li key={e.id}>
                <button type="button" onClick={() => onSelect(e.id)} data-testid={`entity-${e.entity_name}`}
                        className={cn("flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-xs transition", current?.id === e.id ? "bg-primary/10" : "hover:bg-muted",
                          e.status === "RETIRED" && "opacity-50")}>
                  <span className="truncate font-mono font-medium">{e.entity_name}</span>
                  <span className="ml-auto flex items-center gap-1 text-[10px]">
                    {errs > 0 && <span className="rounded bg-rose-100 px-1 font-semibold text-rose-700">{errs}</span>}
                    {isNew && <span className="rounded bg-emerald-100 px-1 font-semibold text-emerald-700">new</span>}
                    {changed && <span className="h-1.5 w-1.5 rounded-full bg-warning" aria-label="changed" />}
                    <span className="text-muted-foreground">{e.table_type.toLowerCase()}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
        {editable && (
          <div className="flex gap-1 border-t pt-2">
            <Input value={newName} onChange={(e) => setNewName(e.target.value.toUpperCase())} placeholder="NEW_TABLE" className="h-8 font-mono text-xs" aria-label="New entity name"
                   onKeyDown={(e) => { if (e.key === "Enter") add(); }} />
            <Button size="sm" onClick={add} data-testid="add-entity"><Plus className="h-3.5 w-3.5" /> Add</Button>
          </div>
        )}
      </aside>
      {current ? <EntityEditor key={current.id} e={current} spec={spec} onChange={onChange} editable={editable} approvedIds={approvedIds}
                               approved={approvedById[current.id]} issues={issues} />
        : <p className="text-sm text-muted-foreground">No entities yet.</p>}
    </div>
  );
}

export function RelationshipsPanel({ spec, onChange, editable }: { spec: DmSpec; onChange: (s: DmSpec) => void; editable: boolean }) {
  const ents = spec.design.entities.filter((e) => e.status !== "RETIRED");
  const cols = (name: string) => ents.find((e) => e.entity_name === name)?.attributes.filter((a) => a.status !== "RETIRED").map((a) => a.name) ?? [];
  const add = () => {
    const from = ents[0], to = ents.find((e) => e.attributes.some((a) => a.is_pk)) ?? ents[0];
    if (!from || !to) return;
    const pk = to.attributes.find((a) => a.is_pk)?.name ?? to.attributes[0]?.name ?? "";
    onChange(addRelationship(spec, { from_entity: from.entity_name, from_column: from.attributes.find((a) => a.name === pk)?.name ?? from.attributes[0]?.name ?? "",
      to_entity: to.entity_name, to_column: pk, cardinality: "N:1", from_optional: true, to_optional: false, enforce: "NONE" }));
  };
  return (
    <section className="surface overflow-x-auto p-3" data-testid="relationships">
      <table className="w-full text-sm">
        <thead className="text-[10px] uppercase tracking-wide text-muted-foreground">
          <tr><th className="px-1 py-1 text-left">From</th><th className="px-1 py-1 text-left">To</th><th className="px-1 py-1 text-left">Cardinality</th>
            <th className="px-1 py-1">Optional</th><th className="px-1 py-1 text-left">Enforce</th><th className="px-1 py-1 text-left">Name</th><th /></tr>
        </thead>
        <tbody>
          {spec.relationships.map((r) => (
            <tr key={r.id} className="border-t border-border/60">
              <td className="px-1 py-1.5"><div className="flex gap-1">
                <Select aria-label="From table" className="h-8 w-44 font-mono text-xs" value={r.from_entity} disabled={!editable}
                        onChange={(e) => onChange(updateRelationship(spec, r.id, { from_entity: e.target.value, from_column: cols(e.target.value)[0] ?? "" }))}>
                  {ents.map((e) => <option key={e.id}>{e.entity_name}</option>)}</Select>
                <Select aria-label="From column" className="h-8 w-40 font-mono text-xs" value={r.from_column} disabled={!editable}
                        onChange={(e) => onChange(updateRelationship(spec, r.id, { from_column: e.target.value }))}>
                  {Array.from(new Set([r.from_column, ...cols(r.from_entity)])).map((c) => <option key={c}>{c}</option>)}</Select></div></td>
              <td className="px-1 py-1.5"><div className="flex gap-1">
                <Select aria-label="To table" className="h-8 w-44 font-mono text-xs" value={r.to_entity} disabled={!editable}
                        onChange={(e) => onChange(updateRelationship(spec, r.id, { to_entity: e.target.value, to_column: ents.find((x) => x.entity_name === e.target.value)?.attributes.find((a) => a.is_pk)?.name ?? cols(e.target.value)[0] ?? "" }))}>
                  {ents.map((e) => <option key={e.id}>{e.entity_name}</option>)}</Select>
                <Select aria-label="To column" className="h-8 w-40 font-mono text-xs" value={r.to_column} disabled={!editable}
                        onChange={(e) => onChange(updateRelationship(spec, r.id, { to_column: e.target.value }))}>
                  {Array.from(new Set([r.to_column, ...cols(r.to_entity)])).map((c) => <option key={c}>{c}</option>)}</Select></div></td>
              <td className="px-1 py-1.5"><Select aria-label="Cardinality" className="h-8 w-24 text-xs" value={r.cardinality} disabled={!editable}
                        onChange={(e) => onChange(updateRelationship(spec, r.id, { cardinality: e.target.value as typeof r.cardinality }))}>
                {["N:1", "1:1", "1:N"].map((c) => <option key={c}>{c}</option>)}</Select></td>
              <td className="px-1 py-1.5 text-center"><input type="checkbox" aria-label="Optional" checked={r.from_optional} disabled={!editable}
                        onChange={(e) => onChange(updateRelationship(spec, r.id, { from_optional: e.target.checked }))} /></td>
              <td className="px-1 py-1.5"><Select aria-label="Enforce" className="h-8 w-24 text-xs" value={r.enforce} disabled={!editable}
                        onChange={(e) => onChange(updateRelationship(spec, r.id, { enforce: e.target.value as typeof r.enforce }))}>
                <option value="NONE">none</option><option value="RELY">rely</option></Select></td>
              <td className="px-1 py-1.5"><Input aria-label="Relationship name" className="h-8 font-mono text-xs" value={r.name} disabled={!editable}
                        onChange={(e) => onChange(updateRelationship(spec, r.id, { name: e.target.value.toUpperCase() }))} /></td>
              <td className="px-1 py-1.5">{editable && <button type="button" aria-label="Remove relationship" className="rounded p-1 text-muted-foreground hover:bg-muted" onClick={() => onChange(dropRelationship(spec, r.id))}><Trash2 className="h-3.5 w-3.5" /></button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!spec.relationships.length && <p className="p-3 text-sm text-muted-foreground">No relationships yet.</p>}
      {editable && ents.length > 0 && <Button size="sm" variant="ghost" className="mt-1" onClick={add} data-testid="add-relationship"><Plus className="h-3.5 w-3.5" /> Add relationship</Button>}
      <p className="mt-2 text-xs text-muted-foreground">Relationships render as foreign keys (informational in Snowflake; RELY lets the optimizer use them) and as dbt relationships tests.</p>
    </section>
  );
}
