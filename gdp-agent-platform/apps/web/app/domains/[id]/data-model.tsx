"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, BookOpen, Check, FileInput, GitBranch, KeyRound, Loader2, Network, Sparkles, TableProperties, Upload, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAccess } from "@/components/access";
import { ErCanvas } from "@/components/hub/er-canvas";
import { AiEvolutionStudio } from "@/components/modeling/ai-evolution-studio";
import { domainDataModel, domainExamples, uploadExamples, type DomainDataModel, type DomainExamples, type TableExamples } from "../actions";

const MARK = { added: "text-emerald-600", changed: "text-amber-600", removed: "text-red-600 line-through" } as const;

function Chip({ ok, label, title }: { ok: boolean; label: string; title: string }) {
  return (
    <span title={title} className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium",
      ok ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "bg-muted text-muted-foreground")}>
      {ok ? <Check className="h-3 w-3" /> : <X className="h-3 w-3" />}{label}
    </span>
  );
}

function Preview({ ex }: { ex: TableExamples }) {
  return (
    <div className="overflow-auto rounded-lg border">
      <table className="w-full text-[10px]">
        <thead className="bg-muted/60"><tr>{ex.columns.map((c) => <th key={c} className="whitespace-nowrap px-1.5 py-1 text-left font-mono font-medium">{c}</th>)}</tr></thead>
        <tbody>{ex.rows.slice(0, 5).map((r, i) => (
          <tr key={i} className="border-t">{r.map((v, j) => <td key={j} className="max-w-[10rem] truncate whitespace-nowrap px-1.5 py-0.5 font-mono" title={v}>{v}</td>)}</tr>
        ))}</tbody>
      </table>
    </div>
  );
}

/** A table's correct-output examples: the accepted rows, the ones waiting for review, and an upload. */
function Examples({ domainId, table, data, may, onChange }: {
  domainId: string; table: string; data: DomainExamples["tables"][string] | undefined; may: boolean; onChange: () => void;
}) {
  const file = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const upload = async (files: FileList | null) => {
    const f = files?.[0];
    if (!f) return;
    const form = new FormData();
    form.append("file", f);
    setBusy(true); setMsg(null);
    const r = await uploadExamples(domainId, table, form).catch(() => null);
    setBusy(false);
    if (file.current) file.current.value = "";
    if (!r?.ok) { setMsg({ tone: "error", text: r && !r.ok ? r.error : "Could not upload the examples." }); return; }
    const u = r.data;
    setMsg({ tone: "ok", text: u.unchanged ? "These examples are already the ones on file."
      : `${u.row_count} row${u.row_count === 1 ? "" : "s"} of ${u.columns.length} columns proposed; a steward accepts them in the review inbox.`
        + (u.warnings.length ? ` ${u.warnings.join("; ")}.` : "") });
    onChange();
  };
  const active = data?.active;
  const proposed = data?.proposed;
  return (
    <div className="space-y-1.5" data-testid="table-examples">
      <p className="font-medium">Correct-output examples</p>
      {active ? (
        <>
          <p className="text-muted-foreground">{active.row_count} accepted row{active.row_count === 1 ? "" : "s"}{active.file ? ` from ${active.file}` : ""}{active.verified_by ? ` · verified by ${active.verified_by}` : ""}</p>
          <Preview ex={active} />
        </>
      ) : !proposed && <p className="text-muted-foreground">None yet. A few rows exactly as this table should hold them are the strongest context a run gets: Data Mapping and code generation follow their formats.</p>}
      {proposed && (
        <p className="text-amber-600">{proposed.row_count} row{proposed.row_count === 1 ? "" : "s"} waiting for review{proposed.file ? ` (${proposed.file})` : ""}:{" "}
          <Link href={`/knowledge?tab=inbox&domain_id=${encodeURIComponent(domainId)}`} className="underline">review inbox</Link></p>
      )}
      {msg && <p role={msg.tone === "error" ? "alert" : "status"} className={msg.tone === "error" ? "text-destructive" : "text-emerald-700 dark:text-emerald-300"}>{msg.text}</p>}
      {may && (
        <>
          <input ref={file} type="file" accept=".csv,.tsv,.txt,.xlsx,.xlsm" hidden onChange={(e) => void upload(e.target.files)} />
          <button type="button" disabled={busy} onClick={() => file.current?.click()}
                  className="inline-flex items-center gap-1 rounded-md border px-2 py-1 font-medium hover:bg-muted disabled:opacity-50">
            {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Upload className="h-3 w-3" />}{active || proposed ? "Replace examples" : "Add examples"} (.csv or .xlsx)
          </button>
          <p className="text-[10px] text-muted-foreground">First row: the table&apos;s column names. Up to 50 rows.</p>
        </>
      )}
    </div>
  );
}

/** The domain's data model: the tables every run maps into, drawn from the approved model (or the open draft, with its
 *  changes marked), and what each table already has: knowledge, STTM, a table model. */
export function DataModel({ domainId, domainName }: { domainId: string; domainName: string }) {
  const { canAct } = useAccess();
  const [view, setView] = useState<"approved" | "draft">("approved");
  const [data, setData] = useState<DomainDataModel | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [area, setArea] = useState("");
  const [showAiStudio, setShowAiStudio] = useState(false);
  const [examples, setExamples] = useState<DomainExamples["tables"]>({});
  const loadExamples = () => void domainExamples(domainId).then((r) => { if (r.ok) setExamples(r.data.tables); });
  useEffect(loadExamples, [domainId]);   // eslint-disable-line react-hooks/exhaustive-deps
  const reloadModel = (targetView: "approved" | "draft" = view) => {
    void domainDataModel(domainId, targetView).then((r) => { if (r.ok) setData(r.data); else setError(r.error); });
  };
  useEffect(() => {
    let live = true;
    setData(null); setError("");
    void domainDataModel(domainId, view).then((r) => { if (live) { if (r.ok) setData(r.data); else setError(r.error); } });
    return () => { live = false; };
  }, [domainId, view]);
  const d = data?.diagram;
  const shown = useMemo(() => (d?.entities ?? []).filter((e) => !area || e.subject_area === area), [d, area]);
  const names = useMemo(() => new Set(shown.map((e) => e.entity_name)), [shown]);
  const entity = d?.entities.find((e) => e.entity_name === selected) ?? null;

  if (error) return <section className="surface p-4 text-sm text-destructive" role="alert">The data model could not be read: {error}</section>;
  if (!data) return <section className="surface p-4 text-xs text-muted-foreground"><Loader2 className="mr-1 inline h-3 w-3 animate-spin" />Drawing the data model…</section>;
  const m = data.model;
  const workbench = m ? `/os/design/modeling/${encodeURIComponent(m.model_id)}` : "/os/design/modeling";
  const mayImport = canAct("MODEL.EDIT");
  const mayExamples = canAct("KNOWLEDGE.EDIT");
  const draft = view === "draft";
  const changes = d?.highlight.changes ?? [];

  return (
    <section className="surface space-y-3 p-4" data-testid="domain-data-model">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="flex items-center gap-2 text-sm font-semibold"><Network className="h-4 w-4 text-primary" />{domainName} data model</h3>
          <p className="text-xs text-muted-foreground">
            The tables every run of this domain maps into, with their keys and relationships. Click a table for its definition and what it already has.
          </p>
        </div>
        {m?.open_version && m.approved_version && (
          <div className="flex rounded-lg border p-0.5 text-xs" role="group" aria-label="Version">
            <button type="button" aria-pressed={!draft} onClick={() => setView("approved")} className={cn("rounded-md px-2 py-1", !draft ? "bg-muted font-medium" : "text-muted-foreground")}>Approved v{m.approved_version}</button>
            <button type="button" aria-pressed={draft} onClick={() => setView("draft")} className={cn("rounded-md px-2 py-1", draft ? "bg-muted font-medium" : "text-muted-foreground")}>Draft v{m.open_version}</button>
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          {mayImport && m && (
            <button
              type="button"
              onClick={() => setShowAiStudio((v) => !v)}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-primary/40 bg-primary/10 px-2.5 text-xs font-medium text-primary hover:bg-primary/15"
              data-testid="evolve-domain-model-ai"
            >
              <Sparkles className="h-3.5 w-3.5" />{showAiStudio ? "Hide AI Studio" : "Evolve with AI"}
            </button>
          )}
          {mayImport && (
            <Link href={`${workbench}?import=file`} className="inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-medium hover:bg-muted" data-testid="import-model-file">
              <FileInput className="h-3.5 w-3.5" />Import a model file
            </Link>
          )}
          <Link href={m?.approved_version ? `${workbench}?version=${m.approved_version}` : workbench} className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-2.5 text-xs font-medium text-primary-foreground">
            Model Workbench<ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </div>
      </div>

      {mayImport && m && showAiStudio && (
        <AiEvolutionStudio
          modelId={m.model_id}
          domainName={domainName}
          defaultExpanded={true}
          onWorkflowApplied={() => {
            setView("draft");
            reloadModel("draft");
          }}
        />
      )}

      {!m || !d ? (
        <div className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
          {!m ? <>No data model for {domainName} yet. Create it in the Model Workbench, or import an Erwin model, DDL or a data dictionary into it.</>
              : <>The model has no version to show yet. Open it in the Model Workbench.</>}
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">{d.status === "APPROVED" ? `Approved v${d.version}` : `v${d.version} · ${d.status.toLowerCase().replace(/_/g, " ")}`}</span>
            {d.approved_by && <span>by {d.approved_by}{d.approved_at ? ` · ${d.approved_at}` : ""}</span>}
            <span>{d.summary.tables} table{d.summary.tables === 1 ? "" : "s"} · {d.summary.columns} columns · {d.summary.relationships} relationship{d.summary.relationships === 1 ? "" : "s"}</span>
            {d.location.database && <span className="font-mono">{d.location.database}.{d.location.schema}</span>}
            {draft && changes.length > 0 && (
              <span className="flex items-center gap-2">
                <span className="text-emerald-600">added</span><span className="text-amber-600">changed</span><span className="text-red-600">removed</span>
                <span>against approved v{m.approved_version}</span>
              </span>
            )}
          </div>
          {d.subject_areas.length > 1 && (
            <div className="flex flex-wrap gap-1 text-xs">
              {["", ...d.subject_areas].map((a) => (
                <button key={a || "all"} type="button" onClick={() => setArea(a)}
                        className={cn("rounded-full border px-2.5 py-0.5", area === a ? "border-primary bg-primary/10 text-primary" : "text-muted-foreground")}>{a || "All areas"}</button>
              ))}
            </div>
          )}
          <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_300px]">
            <ErCanvas className="h-[540px]" platform={d.platform} selected={selected} onSelect={setSelected}
                      entities={shown} positions={d.positions}
                      relationships={d.relationships.filter((r) => names.has(r.from_entity) && names.has(r.to_entity))}
                      highlight={draft ? { entities: d.highlight.entities, columns: d.highlight.columns } : undefined} />
            <aside className="max-h-[540px] space-y-3 overflow-auto rounded-xl border p-3 text-xs">
              {entity ? (
                <>
                  <div>
                    <p className="flex items-center gap-1.5 font-mono text-sm font-semibold">
                      <TableProperties className="h-4 w-4 text-primary" />
                      <span className={cn(draft && d.highlight.entities[entity.entity_name] && MARK[d.highlight.entities[entity.entity_name]])}>{entity.entity_name}</span>
                    </p>
                    <p className="mt-0.5 text-muted-foreground">{entity.kind.toLowerCase()}{entity.scd_type ? ` · SCD ${entity.scd_type}` : ""}{entity.subject_area ? ` · ${entity.subject_area}` : ""}</p>
                  </div>
                  {entity.purpose && <p>{entity.purpose}</p>}
                  {entity.grain && <p><span className="text-muted-foreground">Grain:</span> {entity.grain}</p>}
                  {!!entity.business_keys.length && <p><span className="text-muted-foreground">Business keys:</span> <span className="font-mono">{entity.business_keys.join(", ")}</span></p>}
                  <div className="flex flex-wrap gap-1">
                    <Chip ok={(d.coverage[entity.entity_name]?.knowledge ?? 0) > 0} label={`${d.coverage[entity.entity_name]?.knowledge ?? 0} knowledge`} title="Active knowledge items about this table" />
                    <Chip ok={!!d.coverage[entity.entity_name]?.sttm} label="STTM" title="A source-to-target mapping exists for this table" />
                    <Chip ok={!!d.coverage[entity.entity_name]?.table_model} label="Table model" title="A table model exists in the Model Workbench" />
                    <Chip ok={!!examples[entity.entity_name]?.active} label="Examples" title="Accepted correct-output examples" />
                  </div>
                  {!draft && <Examples domainId={domainId} table={entity.entity_name} data={examples[entity.entity_name]} may={mayExamples} onChange={loadExamples} />}
                  <div>
                    <p className="mb-1 font-medium">Columns ({entity.attributes.length})</p>
                    <ul className="space-y-0.5">
                      {entity.attributes.map((a) => {
                        const mark = draft ? d.highlight.columns[`${entity.entity_name}.${a.name}`] : undefined;
                        return (
                          <li key={a.name} className="flex items-center gap-1.5" title={a.description ?? undefined}>
                            {a.is_pk ? <KeyRound className="h-3 w-3 shrink-0 text-amber-500" /> : <span className="w-3 shrink-0" />}
                            <span className={cn("font-mono", mark && MARK[mark])}>{a.name}</span>
                            <span className="ml-auto font-mono text-[10px] text-muted-foreground">{a.datatype}{a.nullable ? "" : " NN"}</span>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                  {(() => {
                    const rels = d.relationships.filter((r) => r.from_entity === entity.entity_name || r.to_entity === entity.entity_name);
                    return rels.length ? (
                      <div>
                        <p className="mb-1 font-medium">Relationships</p>
                        <ul className="space-y-0.5 font-mono text-[11px]">
                          {rels.map((r, i) => (
                            <li key={i}><GitBranch className="mr-1 inline h-3 w-3 text-muted-foreground" />
                              <button type="button" className="hover:text-primary" onClick={() => setSelected(r.from_entity === entity.entity_name ? r.to_entity : r.from_entity)}>
                                {r.from_entity}.{r.from_column} → {r.to_entity}.{r.to_column}</button></li>
                          ))}
                        </ul>
                      </div>
                    ) : null;
                  })()}
                  <Link href={`/knowledge?tab=items&domain_id=${encodeURIComponent(domainId)}&q=${encodeURIComponent(entity.entity_name)}`} className="inline-flex items-center gap-1 text-primary hover:underline">
                    <BookOpen className="h-3.5 w-3.5" />Knowledge about {entity.entity_name}</Link>
                </>
              ) : (
                <>
                  <p className="font-medium">Tables</p>
                  <ul className="space-y-1">
                    {shown.map((e) => {
                      const c = d.coverage[e.entity_name];
                      const mark = draft ? d.highlight.entities[e.entity_name] : undefined;
                      return (
                        <li key={e.entity_name}>
                          <button type="button" onClick={() => setSelected(e.entity_name)} className="flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left hover:bg-muted">
                            <span className={cn("min-w-0 flex-1 truncate font-mono", mark && MARK[mark])}>{e.entity_name}</span>
                            <span title="knowledge items" className={cn("tabular-nums", c?.knowledge ? "text-foreground" : "text-muted-foreground")}>{c?.knowledge ?? 0}</span>
                            <span title={c?.sttm ? "Data Mapping exists" : "no Data Mapping yet"} className={cn("h-2 w-2 rounded-full", c?.sttm ? "bg-emerald-500" : "bg-muted-foreground/30")} />
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                  <p className="text-[11px] text-muted-foreground">Number: active knowledge items about the table. Dot: green when a Data Mapping exists.</p>
                  {draft && (changes.length > 0 || d.highlight.removed_entities.length > 0) && (
                    <div>
                      <p className="mb-1 font-medium">What the draft changes</p>
                      <ul className="space-y-0.5 text-muted-foreground">
                        {d.highlight.removed_entities.map((n) => <li key={n} className="text-red-600">{n} removed</li>)}
                        {changes.slice(0, 12).map((c, i) => <li key={i}>{c.message}</li>)}
                        {changes.length > 12 && <li>+{changes.length - 12} more in the Model Workbench</li>}
                      </ul>
                    </div>
                  )}
                </>
              )}
            </aside>
          </div>
        </>
      )}
    </section>
  );
}
