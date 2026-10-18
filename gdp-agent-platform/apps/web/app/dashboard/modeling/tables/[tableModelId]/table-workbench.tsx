"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import {
  AlertTriangle, ArrowDown, ArrowUp, Code2, Download, ExternalLink, GitPullRequest, History, KeyRound, ListChecks, Loader2, Network, Plus,
  RefreshCw, RotateCcw, Save, Send, Sparkles, Trash2, Undo2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  TM_CLASSIFICATIONS, type DmAttribute, type DmDiff, type DmIssue, type DmSpec, type TmDetail, type TmLayer, type TmRelated, type TmRendered,
  type TmSuggestion, type TmTableType,
} from "@/lib/model-types";
import { addAttribute, dropAttribute, moveAttribute, sameSpec, updateAttribute, updateEntity } from "@/lib/model-edit";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { StatusPill, explain } from "@/components/hub/states";
import { ErCanvas } from "@/components/hub/er-canvas";
import { SqlEditor } from "@/components/quality/sql-editor";
import { useToast } from "@/components/ui/toast";
import { DiffList } from "../../[modelId]/review-panel";
import { loadPublishing, type Publishing } from "../../model-actions";
import { GitState } from "../../table-models-list";
import {
  loadRelated, loadTableDiff, proposeTableModel, publishTableModel, renderTableModel, saveTableLayout, saveTableModel, setTableDomain,
  suggestColumns, syncTablePrs,
} from "../../table-model-actions";

type Tab = "diagram" | "columns" | "code" | "versions" | "publish";
const TABS: [Tab, string, typeof Network][] = [
  ["diagram", "Diagram", Network], ["columns", "Columns", ListChecks], ["code", "DDL & dbt", Code2], ["versions", "Versions", History],
  ["publish", "Publish", GitPullRequest],
];
const LAYERS: TmLayer[] = ["LANDING", "BRONZE", "SILVER", "GOLD"];
const TABLE_TYPES: [TmTableType, string][] = [["STANDARD", "Snowflake table"], ["ICEBERG", "Iceberg table"], ["DYNAMIC_TABLE", "Dynamic table"]];
const TYPES = ["VARCHAR", "VARCHAR(255)", "NUMBER(38,0)", "NUMBER(18,2)", "INTEGER", "FLOAT", "BOOLEAN", "DATE", "TIMESTAMP_NTZ", "TIMESTAMP_TZ",
  "ARRAY", "VARIANT", "OBJECT", "BINARY"];
const CLASS_LABEL: Record<string, string> = {
  BUSINESS_KEY: "Business key", SURROGATE_KEY: "Surrogate key", ATTRIBUTE: "Attribute", AUDIT_TIMESTAMP: "Audit timestamp", MEASURE: "Measure",
  RECORD_SOURCE: "Record source",
};

function tabFromUrl(): string | null {
  try { return typeof window === "undefined" ? null : new URL(window.location.href).searchParams.get("tab"); } catch { return null; }
}

/** One table model: diagram, column editor, live DDL and dbt, versions with diffs, and Git publishing. Every save is a
 *  new version; the governed domain model only changes through a proposal reviewed in the Model Workbench. */
export function TableWorkbench({ detail, initialTab, canEdit, canAI, domains }: {
  detail: TmDetail; initialTab: string | null; canEdit: boolean; canAI: boolean; domains: { domain_id: string; domain_name: string }[];
}) {
  const router = useRouter();
  const toast = useToast();
  const m = detail.model;
  const current = detail.current;
  const id = m.table_model_id;
  const latest = !!current && current.version === m.current_version;
  const editable = canEdit && latest;
  const [spec, setSpec] = useState<DmSpec | null>(current?.spec ?? null);
  const [layer, setLayer] = useState<TmLayer>(m.model_layer);
  const [tableType, setTableType] = useState<TmTableType>(m.table_type);
  const [note, setNote] = useState("");
  const [conflict, setConflict] = useState("");
  const [tab, setTab] = useState<Tab>(() => (TABS.find(([t]) => t === (tabFromUrl() ?? initialTab))?.[0]) ?? "columns");
  const [preview, setPreview] = useState<(TmRendered & { issues: DmIssue[] }) | null>(null);
  const [rendering, setRendering] = useState(false);
  const [pending, start] = useTransition();

  useEffect(() => {
    try { const u = new URL(window.location.href); u.searchParams.set("tab", tab); window.history.replaceState(null, "", u.toString()); } catch { /* no-op */ }
  }, [tab]);
  // a refresh after save brings the new version
  useEffect(() => { setSpec(current?.spec ?? null); setLayer(m.model_layer); setTableType(m.table_type); setPreview(null); }, [m.etag, current?.version]); // eslint-disable-line react-hooks/exhaustive-deps

  const dirty = editable && (!sameSpec(spec, current?.spec ?? null) || layer !== m.model_layer || tableType !== m.table_type);
  // the DDL and dbt follow the editor as it changes (rendered on the server, nothing saved)
  useEffect(() => {
    if (!dirty || !spec) { setPreview(null); return; }
    setRendering(true);
    const t = setTimeout(async () => {
      const r = await renderTableModel(id, spec, tableType, m.domain_name ?? m.source_name);
      if (r.ok) setPreview(r.data);
      setRendering(false);
    }, 500);
    return () => clearTimeout(t);
  }, [spec, tableType, dirty]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!current || !spec) return <p className="text-sm text-muted-foreground">This table model has no version yet.</p>;
  const entity = spec.design.entities[0];
  const issues = preview?.issues ?? current.issues;
  const ddl = preview?.ddl ?? current.ddl;
  const dbt = preview?.dbt ?? current.dbt;
  const errors = issues.filter((i) => i.severity === "ERROR");

  const save = () => start(async () => {
    const r = await saveTableModel(id, { spec, etag: m.etag ?? "", change_note: note, model_layer: layer, table_type: tableType });
    if (!r.ok) {
      const msg = explain(r.error).message;
      if (msg.includes("CONFLICT")) setConflict(msg.replace(/^CONFLICT:\s*/, "")); else toast("error", msg);
      return;
    }
    if (r.data.unchanged) toast("ok", "Nothing changed");
    else toast(r.data.issues.some((i) => i.severity === "ERROR") ? "error" : "ok",
      `Saved as v${r.data.version}${r.data.issues.some((i) => i.severity === "ERROR") ? " (has errors to fix before publishing)" : ""}`);
    setNote("");
    router.replace(`/dashboard/modeling/tables/${id}?tab=${tab}`);
    router.refresh();
  });

  return (
    <div className="space-y-4" data-testid="table-workbench">
      {!latest && (
        <p className="flex items-center gap-2 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-100">
          <History className="h-4 w-4" /> You are looking at v{current.version}. <Link href={`/dashboard/modeling/tables/${id}?tab=${tab}`} className="font-medium underline">Open the latest (v{m.current_version})</Link> to edit.
        </p>
      )}
      {conflict && (
        <div role="alert" className="flex flex-wrap items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-100">
          <AlertTriangle className="h-4 w-4" /> {conflict}
          <Button size="sm" variant="outline" className="ml-auto" onClick={() => { setConflict(""); router.refresh(); }}><RotateCcw className="h-3.5 w-3.5" /> Reload (your unsaved edits are lost)</Button>
        </div>
      )}

      <nav className="flex flex-wrap gap-1 border-b border-border/70" aria-label="Table model sections">
        {TABS.map(([t, label, Icon]) => (
          <button key={t} type="button" onClick={() => setTab(t)} aria-current={tab === t ? "page" : undefined} data-testid={`tm-tab-${t}`}
                  className={cn("relative flex items-center gap-1.5 px-3 pb-2 pt-1 text-sm transition", tab === t ? "font-medium text-foreground" : "text-muted-foreground hover:text-foreground")}>
            <Icon className="h-4 w-4" /> {label}
            {t === "columns" && errors.length > 0 && <span className="rounded-full bg-rose-100 px-1.5 text-[10px] font-semibold text-rose-700">{errors.length}</span>}
            {tab === t && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-primary" />}
          </button>
        ))}
        <span className="ml-auto flex items-center gap-3 pb-2 text-xs text-muted-foreground">
          <span className="tabular-nums">v{current.version} · {entity?.attributes.filter((a) => a.status !== "RETIRED").length ?? 0} columns</span>
        </span>
      </nav>

      {tab === "diagram" && entity && <DiagramTab id={id} spec={spec} layout={m.diagram_layout} />}
      {tab === "columns" && entity && (
        <ColumnsTab id={id} spec={spec} onChange={setSpec} editable={editable} issues={issues} canAI={canAI && editable}
                    layer={layer} setLayer={setLayer} tableType={tableType} setTableType={setTableType} sourceTables={m.source_tables} />
      )}
      {tab === "code" && <CodeTab ddl={ddl} dbt={dbt} live={!!preview} rendering={rendering} id={id} version={current.version} dirty={dirty} />}
      {tab === "versions" && <VersionsTab id={id} detail={detail} />}
      {tab === "publish" && <PublishTab id={id} detail={detail} canEdit={canEdit} domains={domains} errors={errors} dirty={dirty} />}

      {editable && (
        <div className="sticky bottom-0 z-30 rounded-xl border bg-background/95 px-3 py-2 shadow-sm backdrop-blur">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm">{dirty ? <span className="font-medium text-amber-700 dark:text-amber-300">Unsaved changes</span> : <span className="text-muted-foreground">All changes saved as v{current.version}</span>}</span>
            {errors.length > 0 && <span className="text-xs text-destructive">{errors.length} error(s)</span>}
            <Input aria-label="Change note" className="ml-auto h-8 max-w-xs text-xs" value={note} onChange={(e) => setNote(e.target.value)} placeholder="What changed (optional)" disabled={!dirty} />
            <Button size="sm" variant="ghost" disabled={!dirty || pending} onClick={() => { setSpec(current.spec); setLayer(m.model_layer); setTableType(m.table_type); }}><Undo2 className="h-3.5 w-3.5" /> Discard</Button>
            <Button size="sm" onClick={save} disabled={!dirty || pending} data-testid="tm-save">{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Save draft</Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- diagram

function DiagramTab({ id, spec, layout }: { id: string; spec: DmSpec; layout: Record<string, { x: number; y: number }> }) {
  const [related, setRelated] = useState<TmRelated | null>(null);
  const [selected, setSelected] = useState<string | null>(spec.design.entities[0]?.entity_name ?? null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => { (async () => { const r = await loadRelated(id); setRelated(r.ok ? r.data : { model_id: null, entities: [], relationships: [] }); })(); }, [id]);
  const entity = spec.design.entities[0];
  const others = (related?.entities ?? []).filter((e) => e.entity_name !== entity.entity_name);
  const names = new Set([entity.entity_name, ...others.map((e) => e.entity_name)]);
  const toCanvas = (e: typeof entity) => ({ entity_name: e.entity_name, kind: e.table_type, purpose: e.description, grain: e.grain,
    business_keys: e.business_keys, attributes: e.attributes.filter((a) => a.status !== "RETIRED") });
  const cols = new Set(entity.attributes.map((a) => a.name));
  // relationships of the domain model that still fit this table's columns
  const rels = (related?.relationships ?? []).filter((r) => names.has(r.from_entity) && names.has(r.to_entity)
    && (r.from_entity !== entity.entity_name || cols.has(r.from_column)) && (r.to_entity !== entity.entity_name || cols.has(r.to_column)));
  const moveEnd = (positions: Record<string, { x: number; y: number }>) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => { void saveTableLayout(id, positions); }, 600);
  };
  return (
    <div className="space-y-2">
      <ErCanvas className="h-[560px]" platform={spec.physical_defaults.platform} selected={selected} onSelect={setSelected}
                entities={[toCanvas(entity), ...others.map(toCanvas)]} relationships={rels}
                muted={new Set(others.map((e) => e.entity_name))} positions={layout} onMoveEnd={moveEnd} />
      <p className="text-xs text-muted-foreground">
        {related === null ? "Loading related tables of the domain model..."
          : others.length ? <>Faded tables are the related entities of the approved domain model (v{related.version}); relationships are governed there.{" "}
            {related.model_id && <Link href={`/dashboard/modeling/${related.model_id}?tab=relationships`} className="text-primary hover:underline">Open the domain model</Link>}</>
          : related.model_id ? "The approved domain model has no relationships to this table yet." : "Assign a domain in Publish to see the table among its related domain entities."}
        {" "}Drag tables to arrange them; the layout is saved without a new version.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------- columns

function ColumnsTab({ id, spec, onChange, editable, issues, canAI, layer, setLayer, tableType, setTableType, sourceTables }: {
  id: string; spec: DmSpec; onChange: (s: DmSpec) => void; editable: boolean; issues: DmIssue[]; canAI: boolean;
  layer: TmLayer; setLayer: (l: TmLayer) => void; tableType: TmTableType; setTableType: (t: TmTableType) => void; sourceTables: string[];
}) {
  const toast = useToast();
  const e = spec.design.entities[0];
  const [suggestions, setSuggestions] = useState<TmSuggestion[] | null>(null);
  const [asking, startAsk] = useTransition();
  const byColumn = useMemo(() => new Map((suggestions ?? []).map((s) => [s.column, s])), [suggestions]);
  const set = (a: DmAttribute, patch: Partial<DmAttribute>) => onChange(updateAttribute(spec, e.id, a.id, patch));
  const setPhys = (patch: Partial<DmSpec["physical_defaults"]>) => onChange({ ...spec, physical_defaults: { ...spec.physical_defaults, ...patch } });
  const modelIssues = issues.filter((i) => i.path === "model" || i.path === e.entity_name);
  const live = e.attributes;

  const ask = () => startAsk(async () => {
    const r = await suggestColumns(id, spec);
    if (!r.ok) { toast("error", explain(r.error).message); return; }
    if (r.data.error) toast("error", r.data.error);
    setSuggestions(r.data.items);
    if (!r.data.items.length && !r.data.error) toast("ok", "No suggestions: the columns look right");
  });
  const accept = (a: DmAttribute, s: TmSuggestion) => {
    set(a, { ...(s.classification ? { semantic_type: s.classification } : {}), ...(s.description && !a.description ? { description: s.description } : {}) });
    setSuggestions((cur) => (cur ?? []).filter((x) => x !== s));
  };
  const acceptAll = () => {
    let next = spec;
    for (const a of live) {
      const s = byColumn.get(a.name);
      if (s) next = updateAttribute(next, e.id, a.id, { ...(s.classification ? { semantic_type: s.classification } : {}), ...(s.description && !a.description ? { description: s.description } : {}) });
    }
    onChange(next);
    setSuggestions([]);
  };

  return (
    <div className="space-y-4">
      <section className="surface grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="sm:col-span-2"><Label htmlFor="tm-desc">Description</Label>
          <Textarea id="tm-desc" rows={2} value={e.description} disabled={!editable} onChange={(ev) => onChange(updateEntity(spec, e.id, { description: ev.target.value, purpose: ev.target.value.slice(0, 500) }))} placeholder="What one row of this table is" /></div>
        <div className="sm:col-span-2"><Label htmlFor="tm-grain">Grain</Label>
          <Textarea id="tm-grain" rows={2} value={e.grain} disabled={!editable} onChange={(ev) => onChange(updateEntity(spec, e.id, { grain: ev.target.value }))} placeholder="One row per ..." /></div>
        <div><Label htmlFor="tm-layer">Layer</Label>
          <Select id="tm-layer" value={layer} disabled={!editable} onChange={(ev) => setLayer(ev.target.value as TmLayer)}>{LAYERS.map((l) => <option key={l} value={l}>{l}</option>)}</Select></div>
        <div><Label htmlFor="tm-type">Table type</Label>
          <Select id="tm-type" value={tableType} disabled={!editable} onChange={(ev) => setTableType(ev.target.value as TmTableType)}>{TABLE_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></div>
        <div><Label htmlFor="tm-db">Target database</Label>
          <Input id="tm-db" className="font-mono" value={spec.physical_defaults.database} disabled={!editable} onChange={(ev) => setPhys({ database: ev.target.value.toUpperCase() })} placeholder="SILVER_DB" /></div>
        <div><Label htmlFor="tm-schema">Target schema</Label>
          <Input id="tm-schema" className="font-mono" value={spec.physical_defaults.schema} disabled={!editable} onChange={(ev) => setPhys({ schema: ev.target.value.toUpperCase() })} placeholder="SALES" /></div>
        {tableType === "ICEBERG" && (
          <div className="sm:col-span-2"><Label htmlFor="tm-vol">External volume (Iceberg)</Label>
            <Input id="tm-vol" className="font-mono" value={spec.physical_defaults.iceberg.external_volume ?? ""} disabled={!editable}
                   onChange={(ev) => setPhys({ iceberg: { ...spec.physical_defaults.iceberg, external_volume: ev.target.value || null } })} placeholder="ICEBERG_VOLUME" /></div>
        )}
        {tableType === "DYNAMIC_TABLE" && <p className="self-end text-xs text-muted-foreground sm:col-span-2">Dynamic tables are generated by the dbt model (materialized as a dynamic table); the DDL shows the column contract.</p>}
        {sourceTables.length > 0 && <p className="text-xs text-muted-foreground sm:col-span-2 lg:col-span-4">Sources: <span className="font-mono">{sourceTables.join(", ")}</span></p>}
        {modelIssues.map((i, n) => <p key={n} className={cn("text-xs sm:col-span-2 lg:col-span-4", i.severity === "ERROR" ? "text-destructive" : "text-amber-700 dark:text-amber-300")}>{i.message}</p>)}
      </section>

      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">Columns</h3>
        {canAI && <Button size="sm" variant="outline" onClick={ask} disabled={asking} data-testid="tm-suggest">{asking ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />} Suggest classifications and descriptions</Button>}
        {suggestions && suggestions.length > 0 && <Button size="sm" variant="ghost" onClick={acceptAll}>Accept all {suggestions.length}</Button>}
        {editable && <Button size="sm" variant="outline" className="ml-auto" onClick={() => onChange(addAttribute(spec, e.id))}><Plus className="h-3.5 w-3.5" /> Add column</Button>}
      </div>
      <datalist id="tm-types">{TYPES.map((t) => <option key={t} value={t} />)}</datalist>
      <section className="surface overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-[11px] uppercase tracking-wide text-muted-foreground">
            <tr><th className="px-2 py-2">Column</th><th className="px-2 py-2">Type</th><th className="px-2 py-2">Classification</th>
              <th className="px-1 py-2 text-center">PK</th><th className="px-1 py-2 text-center">Null</th><th className="px-1 py-2 text-center">Unique</th>
              <th className="px-2 py-2">Expression</th><th className="px-2 py-2">Description</th><th /></tr>
          </thead>
          <tbody>
            {live.map((a, i) => {
              const problems = issues.filter((x) => x.path === `${e.entity_name}.${a.name}`);
              const s = byColumn.get(a.name);
              return (
                <tr key={a.id} className={cn("border-t border-border/60 align-top", a.status === "RETIRED" && "opacity-50")} data-testid={`tm-col-${a.name}`}>
                  <td className="px-2 py-1.5">
                    <div className="flex items-center gap-1">
                      {a.is_pk && <KeyRound className="h-3 w-3 shrink-0 text-warning" aria-label="primary key" />}
                      <Input aria-label="Column name" className={cn("h-7 min-w-[160px] font-mono text-xs", problems.some((p) => p.severity === "ERROR") && "border-destructive")}
                             value={a.name} disabled={!editable} onChange={(ev) => set(a, { name: ev.target.value.toUpperCase() })} />
                    </div>
                    {a.source_columns.length > 0 && <p className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground" title={a.source_columns.join(", ")}>from {a.source_columns.join(", ")}</p>}
                    {problems.map((p, n) => <p key={n} className={cn("text-[10px]", p.severity === "ERROR" ? "text-destructive" : "text-amber-700 dark:text-amber-300")}>{p.message}</p>)}
                  </td>
                  <td className="px-2 py-1.5"><Input aria-label="Data type" list="tm-types" className="h-7 w-36 font-mono text-xs" value={a.datatype} disabled={!editable}
                                                     onChange={(ev) => set(a, { datatype: ev.target.value.toUpperCase() })} /></td>
                  <td className="px-2 py-1.5">
                    <Select aria-label="Classification" className="h-7 w-36 py-0 text-xs" value={a.semantic_type ?? ""} disabled={!editable}
                            onChange={(ev) => set(a, { semantic_type: ev.target.value || null, ...(ev.target.value === "BUSINESS_KEY" ? { business_key: true } : {}) })}>
                      <option value="">Not set</option>
                      {TM_CLASSIFICATIONS.map((c) => <option key={c} value={c}>{CLASS_LABEL[c]}</option>)}
                      {a.semantic_type && !(TM_CLASSIFICATIONS as readonly string[]).includes(a.semantic_type) && <option value={a.semantic_type}>{a.semantic_type}</option>}
                    </Select>
                  </td>
                  {(["is_pk", "nullable", "is_unique"] as const).map((f) => (
                    <td key={f} className="px-1 py-1.5 text-center">
                      <input type="checkbox" aria-label={`${a.name} ${f}`} checked={!!a[f]} disabled={!editable || (f === "nullable" && a.is_pk)}
                             onChange={(ev) => set(a, { [f]: ev.target.checked, ...(f === "is_pk" && ev.target.checked ? { nullable: false } : {}) } as Partial<DmAttribute>)} />
                    </td>
                  ))}
                  <td className="px-2 py-1.5"><Input aria-label="Expression" className="h-7 min-w-[170px] font-mono text-xs" value={a.transformation_hint} disabled={!editable}
                                                     placeholder={a.source_columns[0]?.split(".").pop()?.toLowerCase() ?? "SQL expression"}
                                                     onChange={(ev) => set(a, { transformation_hint: ev.target.value, derived: !!ev.target.value.trim() || !a.source_columns.length })} /></td>
                  <td className="px-2 py-1.5">
                    <Input aria-label="Description" className="h-7 min-w-[200px] text-xs" value={a.description} disabled={!editable}
                           onChange={(ev) => set(a, { description: ev.target.value })} placeholder="What it means" />
                    {s && (
                      <div className="mt-1 rounded-md border border-blue-200 bg-blue-50 px-2 py-1 text-[11px] text-blue-900 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-100">
                        <Sparkles className="mr-1 inline h-3 w-3" />
                        {s.classification && <b>{CLASS_LABEL[s.classification] ?? s.classification}. </b>}{s.description && <>{s.description} </>}
                        <span className="text-blue-700/80 dark:text-blue-200/70">{s.reason}</span>
                        <span className="ml-2 inline-flex gap-2"><button type="button" className="font-semibold underline" onClick={() => accept(a, s)}>Accept</button>
                          <button type="button" className="underline" onClick={() => setSuggestions((cur) => (cur ?? []).filter((x) => x !== s))}>Dismiss</button></span>
                      </div>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-1 py-1.5">
                    {editable && (
                      <span className="flex items-center gap-0.5">
                        <button type="button" aria-label="Move up" disabled={i === 0} onClick={() => onChange(moveAttribute(spec, e.id, a.id, -1))} className="rounded p-1 text-muted-foreground hover:bg-muted disabled:opacity-30"><ArrowUp className="h-3 w-3" /></button>
                        <button type="button" aria-label="Move down" disabled={i === live.length - 1} onClick={() => onChange(moveAttribute(spec, e.id, a.id, 1))} className="rounded p-1 text-muted-foreground hover:bg-muted disabled:opacity-30"><ArrowDown className="h-3 w-3" /></button>
                        <button type="button" aria-label="Remove column" onClick={() => onChange(dropAttribute(spec, e.id, a.id, new Set()))} className="rounded p-1 text-muted-foreground hover:bg-muted"><Trash2 className="h-3 w-3" /></button>
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
      <p className="text-xs text-muted-foreground">An expression replaces the straight copy of the source column in the generated dbt model. Removing a column only affects new versions; earlier versions keep it.</p>
    </div>
  );
}

// ---------------------------------------------------------------- DDL and dbt

function CodeTab({ ddl, dbt, live, rendering, id, version, dirty }: { ddl: string; dbt: Record<string, string>; live: boolean; rendering: boolean; id: string; version: number; dirty: boolean }) {
  const files = Object.keys(dbt).sort();
  const [file, setFile] = useState<string>("__ddl__");
  const shown = file === "__ddl__" ? ddl : dbt[file] ?? "";
  return (
    <div className="grid gap-3 lg:grid-cols-[260px_1fr]">
      <aside className="surface space-y-1 p-2 text-xs">
        <button type="button" onClick={() => setFile("__ddl__")} className={cn("block w-full truncate rounded px-2 py-1 text-left font-mono", file === "__ddl__" ? "bg-primary/10 text-primary" : "hover:bg-muted")}>DDL ({live ? "live" : `v${version}`})</button>
        <p className="px-2 pt-2 text-[10px] uppercase tracking-wide text-muted-foreground">dbt files</p>
        {files.map((f) => (
          <button key={f} type="button" title={f} onClick={() => setFile(f)} className={cn("block w-full truncate rounded px-2 py-1 text-left font-mono", file === f ? "bg-primary/10 text-primary" : "hover:bg-muted")}>{f.split("/").slice(-2).join("/")}</button>
        ))}
        <div className="flex gap-2 border-t px-2 pt-2">
          <a href={`/bff/table-model-export/${id}?format=ddl&version=${version}`} className="inline-flex items-center gap-1 text-primary hover:underline"><Download className="h-3 w-3" /> DDL</a>
          <a href={`/bff/table-model-export/${id}?format=dbt&version=${version}`} className="inline-flex items-center gap-1 text-primary hover:underline"><Download className="h-3 w-3" /> dbt zip</a>
        </div>
        {dirty && <p className="px-2 text-[10px] text-muted-foreground">Downloads are the saved v{version}; the viewer shows your unsaved edits.</p>}
      </aside>
      <div className="space-y-1">
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="font-mono">{file === "__ddl__" ? "DDL" : file}</span>
          {rendering ? <><Loader2 className="h-3 w-3 animate-spin" /> updating</> : live ? <span className="rounded bg-amber-100 px-1.5 text-[10px] font-semibold text-amber-800">unsaved preview</span> : null}
        </p>
        <SqlEditor value={shown} readOnly rows={24} label={file === "__ddl__" ? "DDL" : file} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- versions

function VersionsTab({ id, detail }: { id: string; detail: TmDetail }) {
  const toast = useToast();
  const [diff, setDiff] = useState<DmDiff | null>(null);
  const [pair, setPair] = useState<[number | null, number] | null>(null);
  const [pending, start] = useTransition();
  const compare = (to: number, from: number | null) => start(async () => {
    const r = await loadTableDiff(id, to, from);
    if (r.ok) { setDiff(r.data); setPair([from, to]); } else toast("error", explain(r.error).message);
  });
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,420px)_1fr]">
      <ol className="space-y-2">
        {detail.versions.map((v) => (
          <li key={v.version} className={cn("surface space-y-1 p-3 text-sm", detail.current?.version === v.version && "ring-1 ring-primary/40")}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold">v{v.version}</span><StatusPill status={v.status} />
              <span className="text-xs text-muted-foreground">{v.origin?.toLowerCase()}{v.run_design_version ? ` · run design v${v.run_design_version}` : ""}</span>
              <span className="ml-auto text-[11px] text-muted-foreground">{v.created_at?.slice(0, 16)} · {v.created_by}</span>
            </div>
            {v.change_note && <p className="text-xs">{v.change_note}</p>}
            {v.change_summary && v.version > 1 && <p className="text-[11px] text-muted-foreground">{v.change_summary.counts.BREAKING ?? 0} breaking · {v.change_summary.counts.ADDITIVE ?? 0} additive · {v.change_summary.counts.NON_FUNCTIONAL ?? 0} documentation</p>}
            <div className="flex flex-wrap gap-3 text-xs">
              <Link href={`/dashboard/modeling/tables/${id}?version=${v.version}&tab=columns`} className="text-primary hover:underline">Open</Link>
              {v.version > 1 && <button type="button" className="text-primary hover:underline" onClick={() => compare(v.version, v.version - 1)} disabled={pending}>Compare with v{v.version - 1}</button>}
              {detail.model.current_version && v.version !== detail.model.current_version &&
                <button type="button" className="text-primary hover:underline" onClick={() => compare(detail.model.current_version as number, v.version)} disabled={pending}>Compare with latest</button>}
              {v.pr_url && <a href={v.pr_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">PR {v.pr_state?.toLowerCase()} <ExternalLink className="h-3 w-3" /></a>}
            </div>
          </li>
        ))}
      </ol>
      <section className="surface p-4">
        {pending ? <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" /> Comparing...</p>
          : diff && pair ? <><h3 className="mb-2 text-sm font-semibold">v{pair[0] ?? "none"} → v{pair[1]}</h3><DiffList diff={diff} /></>
          : <p className="text-sm text-muted-foreground">Pick a version to compare. Every save is a version; nothing is overwritten, so you can always reopen an older one and save it again as the latest.</p>}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- publish and propose

function PublishTab({ id, detail, canEdit, domains, errors, dirty }: {
  id: string; detail: TmDetail; canEdit: boolean; domains: { domain_id: string; domain_name: string }[]; errors: DmIssue[]; dirty: boolean;
}) {
  const router = useRouter();
  const toast = useToast();
  const m = detail.model;
  const v = detail.current;
  const [pub, setPub] = useState<Publishing | null>(null);
  const [repo, setRepo] = useState("");
  const [domain, setDomain] = useState(m.domain_id ?? "");
  const [pending, start] = useTransition();
  useEffect(() => {
    (async () => { const r = await loadPublishing(); if (r.ok) { setPub(r.data); setRepo(r.data.repos[0]?.repo_id ?? ""); } })();
  }, []);
  if (!v) return null;
  const blocked = errors.length > 0 || dirty;

  const assign = () => start(async () => {
    const r = await setTableDomain(id, domain);
    if (r.ok) { toast("ok", `Domain set to ${r.data.domain_name}`); router.refresh(); } else toast("error", explain(r.error).message);
  });
  const propose = () => start(async () => {
    const r = await proposeTableModel(id, v.version);
    if (!r.ok) { toast("error", explain(r.error).message); return; }
    toast("ok", r.data.pinned ? "The approved domain model already matches this table" : r.data.status === "MERGED"
      ? "Became the first draft of the domain model" : "Proposed to the domain model; a modeler merges and approves it");
    router.refresh();
  });
  const publish = () => start(async () => {
    const r = await publishTableModel(id, v.version, { repo_id: repo });
    if (!r.ok) { toast("error", explain(r.error).message); return; }
    if (r.data.status === "PUBLISHED") toast("ok", `Pull request opened: ${r.data.head_branch}`);
    else if (r.data.status === "NO_CHANGES") toast("ok", "The repository already has these files; marked published");
    else toast("error", r.data.detail || `Publishing failed (${r.data.status})`);
    router.refresh();
  });
  const sync = () => start(async () => {
    const r = await syncTablePrs();
    if (r.ok) { toast("ok", r.data.detail || `${r.data.checked} pull request(s) checked`); router.refresh(); } else toast("error", explain(r.error).message);
  });

  return (
    <div className="grid gap-4 lg:grid-cols-2" data-testid="tm-publish">
      <section className="surface space-y-3 p-4">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold"><Send className="h-4 w-4" /> Propose to the domain model</h3>
        <p className="text-sm text-muted-foreground">The domain&apos;s governed model changes only through review: this sends v{v.version} of <span className="font-mono">{m.table_name}</span> as a proposal that a modeler merges and an approver approves (four eyes).</p>
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-[200px] flex-1"><Label htmlFor="tm-domain">Domain</Label>
            <Select id="tm-domain" value={domain} disabled={!canEdit} onChange={(e) => setDomain(e.target.value)}>
              <option value="">Choose a domain</option>
              {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
            </Select></div>
          {canEdit && domain !== (m.domain_id ?? "") && <Button variant="outline" onClick={assign} disabled={pending || !domain}>Set domain</Button>}
        </div>
        {canEdit && <Button onClick={propose} disabled={pending || blocked || !m.domain_id || domain !== (m.domain_id ?? "")} data-testid="tm-propose">
          {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />} Propose v{v.version}</Button>}
        {m.linked_model_id && <p className="text-xs">Last proposed as <span className="font-mono">{m.linked_entity}</span>. <Link href={`/dashboard/modeling/${m.linked_model_id}?tab=changes`} className="text-primary hover:underline">See it in the domain model&apos;s proposals</Link></p>}
      </section>

      <section className="surface space-y-3 p-4">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold"><GitPullRequest className="h-4 w-4" /> Push to a Git branch</h3>
        <p className="text-sm text-muted-foreground">The version&apos;s DDL and dbt files go to <span className="font-mono">feature/model-{m.table_name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-v{v.version}</span> with a pull request. It becomes published when the pull request is merged.</p>
        <p className="flex items-center gap-2 text-xs">Current state: <GitState row={{ status: v.status, branch: v.branch, pr_url: v.pr_url, pr_state: v.pr_state }} /></p>
        {!pub ? <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" /> Loading repositories...</p>
          : !pub.repos.length ? <p className="text-sm text-muted-foreground">Connect a GitHub repository in Admin, Integrations first.</p>
          : (
            <div className="flex flex-wrap items-end gap-2">
              <div className="min-w-[200px] flex-1"><Label htmlFor="tm-repo">Repository</Label>
                <Select id="tm-repo" value={repo} onChange={(e) => setRepo(e.target.value)}>{pub.repos.map((r) => <option key={r.repo_id} value={r.repo_id}>{r.name} ({r.branch})</option>)}</Select></div>
              {canEdit && <Button onClick={publish} disabled={pending || blocked || !repo || !pub.publisher_ready} data-testid="tm-push">
                {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitPullRequest className="h-4 w-4" />} Push v{v.version}</Button>}
              <Button variant="ghost" onClick={sync} disabled={pending}><RefreshCw className="h-4 w-4" /> Check pull requests</Button>
            </div>
          )}
        {pub && !pub.publisher_ready && <p className="text-xs text-amber-700 dark:text-amber-300">GitHub publishing is not set up. An admin can set it up in the GitHub publishing card.</p>}
      </section>
      {blocked && <p className="text-xs text-muted-foreground lg:col-span-2">{dirty ? "Save your changes first; only a saved version is proposed or pushed." : `Fix ${errors.length} error(s) in Columns first.`}</p>}
    </div>
  );
}
