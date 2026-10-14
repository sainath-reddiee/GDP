"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import { Check, CircleAlert, Database, Loader2, Upload } from "lucide-react";
import { useAccess } from "@/components/access";
import { LAYER_META, type Layer } from "@/lib/layers";
import { cn } from "@/lib/utils";
import {
  addSchemaReference, domainReferences, setReferenceKind, uploadReferences, withdrawReference,
  type ReferenceKind, type ReferenceRow, type References,
} from "../actions";

const KIND: Record<ReferenceKind, { label: string; use: string }> = {
  DATA_MODEL: { label: "Data model", use: "The layer's reference model: Gold proposals align to it and output is scored against it" },
  MDM_SCHEMA: { label: "Post-MDM model", use: "Reltio's output: the MDM contract and resolution come from it" },
  STTM: { label: "STTM sheet", use: "Mapping ranks its lines first; the STTM pre-fills its transformations" },
  DBT_CODE: { label: "dbt code", use: "Generated code follows its folders, naming, materializations and macros" },
  SAMPLE_ROWS: { label: "Sample rows", use: "A table's correct output examples (after review)" },
  DOCUMENT: { label: "Document", use: "Read for knowledge, proposed for this layer (after review)" },
};
const STATUS: Record<ReferenceRow["status"], string> = {
  QUEUED: "bg-sky-500/10 text-sky-700 dark:text-sky-300", READING: "bg-sky-500/10 text-sky-700 dark:text-sky-300",
  READY: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300", FAILED: "bg-destructive/10 text-destructive",
  WITHDRAWN: "bg-muted text-muted-foreground",
};

/** What a person gives the platform for this domain and layer, so its decisions get better: any file (a data model,
 *  a post-MDM model, an STTM sheet, dbt code, sample rows, documents) or a live schema. Each is read for what it is and
 *  used where it helps; what changes a run goes through review, the Model Workbench or the run's own gates. */
export function ReferencesTab({ domainId, layer }: { domainId: string; layer: Layer }) {
  const { canAct } = useAccess();
  const may = canAct("KNOWLEDGE.EDIT");
  const [data, setData] = useState<References | null>(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [kind, setKind] = useState<"" | ReferenceKind>("");
  const [table, setTable] = useState("");
  const [schema, setSchema] = useState({ database: "", schema: "", kind: (layer === "MDM" ? "MDM_SCHEMA" : "DATA_MODEL") as "DATA_MODEL" | "MDM_SCHEMA" });
  const [pending, start] = useTransition();
  const input = useRef<HTMLInputElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = () => void domainReferences(domainId, layer).then((r) => {
    if (!r.ok) { setError(r.error); return; }
    setData(r.data);
    if (r.data.references.some((x) => x.status === "QUEUED" || x.status === "READING")) timer.current = setTimeout(load, 4000);
  });
  useEffect(() => { load(); return () => { if (timer.current) clearTimeout(timer.current); }; }, [domainId, layer]);   // eslint-disable-line react-hooks/exhaustive-deps

  const upload = (files: FileList | null) => start(async () => {
    if (!files?.length) return;
    setMsg("");
    const form = new FormData();
    Array.from(files).forEach((f) => form.append("files", f));
    form.append("layer", layer);
    if (kind) form.append("kind", kind);
    if (table.trim()) form.append("table", table.trim());
    const r = await uploadReferences(domainId, form);
    if (!r.ok) { setMsg(r.error); return; }
    setMsg(r.data.references.map((x) => x.error ? `${x.file_name}: ${x.error}` : x.duplicate ? `${x.file_name}: already given`
      : `${x.file_name}: ${KIND[x.kind as ReferenceKind]?.label ?? x.kind}`).join("; "));
    if (input.current) input.current.value = "";
    load();
  });
  const fromSchema = () => start(async () => {
    setMsg("");
    const r = await addSchemaReference(domainId, { ...schema, layer });
    if (!r.ok) { setMsg(r.error); return; }
    setMsg(`Read ${r.data.title}: ${r.data.note}`);
    load();
  });
  const act = (fn: () => Promise<{ ok: boolean; error?: string }>) => start(async () => { const r = await fn(); if (!r.ok) setMsg(r.error ?? "Failed"); load(); });

  if (error) return <p role="alert" className="text-sm text-destructive">{error}</p>;
  if (!data) return <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-4 w-4 animate-spin" />Reading the references…</p>;
  if (!data.deployed) return <p className="text-sm text-muted-foreground">Reference inputs need migration V057 (Admin, Deploy).</p>;
  const live = data.references.filter((r) => r.status !== "WITHDRAWN");
  return (
    <div className="space-y-4" data-testid="references">
      <p className="text-sm text-muted-foreground">
        <span className={cn("mr-1.5 rounded px-1.5 py-0.5 text-[11px] font-semibold", LAYER_META[layer].chip)}>{LAYER_META[layer].label}</span>
        Give the platform anything you have for this layer: it is read for what it is and used where it makes decisions better.
        Nothing changes a run without a person: models go to the Model Workbench, knowledge to the review inbox, mappings and STTMs through their gates.</p>

      {may && (
        <div className="grid gap-4 lg:grid-cols-2">
          <section className="surface space-y-2 p-4">
            <h3 className="flex items-center gap-2 text-sm font-semibold"><Upload className="h-4 w-4 text-primary" />Files</h3>
            <p className="text-xs text-muted-foreground">Any type: Erwin XML, DDL, an Excel or CSV dictionary, an STTM sheet, a zip of a dbt project, sample rows, PDFs, Word, images. Up to 10 files of 50 MB.</p>
            <div className="flex flex-wrap items-center gap-2">
              <select aria-label="Kind" value={kind} onChange={(e) => setKind(e.target.value as "" | ReferenceKind)} className="h-8 rounded-lg border bg-background px-2 text-xs">
                <option value="">Detect what it is</option>
                {(Object.keys(KIND) as ReferenceKind[]).map((k) => <option key={k} value={k}>{KIND[k].label}</option>)}
              </select>
              <input aria-label="Table (sample rows or STTM)" value={table} onChange={(e) => setTable(e.target.value)} placeholder="Table (for rows or an STTM sheet)"
                     className="h-8 w-56 rounded-lg border bg-background px-2 font-mono text-xs" />
              <input ref={input} type="file" multiple aria-label="Files" onChange={(e) => upload(e.target.files)} disabled={pending}
                     className="text-xs file:mr-2 file:rounded-md file:border-0 file:bg-primary file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-primary-foreground" />
            </div>
          </section>
          <section className="surface space-y-2 p-4">
            <h3 className="flex items-center gap-2 text-sm font-semibold"><Database className="h-4 w-4 text-primary" />A live schema</h3>
            <p className="text-xs text-muted-foreground">Read only (columns, comments, keys): for example the post-MDM schema COMPANY_MDM, or an existing gold database.</p>
            <div className="flex flex-wrap items-center gap-2">
              <input aria-label="Database" value={schema.database} onChange={(e) => setSchema({ ...schema, database: e.target.value.toUpperCase() })} placeholder="DATABASE" className="h-8 w-48 rounded-lg border bg-background px-2 font-mono text-xs" />
              <input aria-label="Schema" value={schema.schema} onChange={(e) => setSchema({ ...schema, schema: e.target.value.toUpperCase() })} placeholder="SCHEMA" className="h-8 w-40 rounded-lg border bg-background px-2 font-mono text-xs" />
              <select aria-label="Read as" value={schema.kind} onChange={(e) => setSchema({ ...schema, kind: e.target.value as "DATA_MODEL" | "MDM_SCHEMA" })} className="h-8 rounded-lg border bg-background px-2 text-xs">
                <option value="DATA_MODEL">Data model</option><option value="MDM_SCHEMA">Post-MDM model</option>
              </select>
              <button type="button" onClick={fromSchema} disabled={pending || !schema.database || !schema.schema}
                      className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-medium text-primary-foreground disabled:opacity-50">
                {pending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Read schema</button>
            </div>
          </section>
        </div>
      )}
      {msg && <p role="status" className="rounded-lg bg-muted px-3 py-2 text-xs">{msg}</p>}

      <section className="surface p-0">
        <table className="w-full text-xs">
          <thead className="bg-muted/50 text-left text-[10px] uppercase tracking-wide text-muted-foreground">
            <tr><th className="px-3 py-2">Input</th><th className="px-3 py-2">What it is</th><th className="px-3 py-2">How it is used</th><th className="px-3 py-2">Status</th><th className="px-3 py-2" /></tr>
          </thead>
          <tbody>
            {live.map((r) => (
              <tr key={r.reference_id} className="border-t align-top">
                <td className="px-3 py-2"><p className="font-medium">{r.title}</p>
                  <p className="text-[10px] text-muted-foreground">{r.origin.toLowerCase()} · {r.created_by} · {r.created_at}{r.layer === "ANY" ? " · both layers" : ""}</p></td>
                <td className="px-3 py-2">
                  {may && r.origin === "UPLOAD" ? (
                    <select aria-label={`Kind of ${r.title}`} value={r.kind} disabled={pending}
                            onChange={(e) => act(() => setReferenceKind(domainId, r.reference_id, { kind: e.target.value as ReferenceKind, layer: r.layer }))}
                            className="h-7 rounded-md border bg-background px-1.5 text-xs">
                      {(Object.keys(KIND) as ReferenceKind[]).map((k) => <option key={k} value={k}>{KIND[k].label}</option>)}
                    </select>
                  ) : <span className="font-medium">{KIND[r.kind]?.label ?? r.kind}</span>}
                  {r.detected_why && <p className="mt-0.5 text-[10px] text-muted-foreground">{r.detected_why}</p>}
                </td>
                <td className="max-w-md px-3 py-2 text-muted-foreground">{KIND[r.kind]?.use}<p className="mt-0.5 text-foreground">{r.note}</p></td>
                <td className="px-3 py-2"><span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium", STATUS[r.status])}>
                  {r.status === "READY" ? <Check className="h-3 w-3" /> : r.status === "FAILED" ? <CircleAlert className="h-3 w-3" /> : (r.status === "QUEUED" || r.status === "READING") ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
                  {r.status.toLowerCase()}</span></td>
                <td className="px-3 py-2 text-right">{may && <button type="button" disabled={pending} onClick={() => act(() => withdrawReference(domainId, r.reference_id))} className="text-[11px] text-muted-foreground hover:text-destructive">Withdraw</button>}</td>
              </tr>
            ))}
            {!live.length && <tr><td colSpan={5} className="px-3 py-6 text-center text-muted-foreground">Nothing given for this layer yet.</td></tr>}
          </tbody>
        </table>
      </section>

      <section className="surface p-4">
        <h3 className="text-sm font-semibold">How close the platform comes to your references</h3>
        {data.scores.length ? (
          <ul className="mt-2 space-y-1 text-xs">{data.scores.filter((s) => s.layer === layer).slice(0, 12).map((s, i) => (
            <li key={i} className="flex flex-wrap items-center gap-2"><span className="w-28 font-medium">{KIND[s.kind as ReferenceKind]?.label ?? s.kind}</span>
              <span className="w-14 tabular-nums font-semibold">{s.score.pct}%</span>
              <span className="text-muted-foreground">{s.score.matched} matched{s.score.missing?.length ? `, missing ${s.score.missing.slice(0, 4).join(", ")}` : ""}{s.stage ? ` · ${s.stage.toLowerCase()}` : ""} · {s.scored_at}</span></li>
          ))}</ul>
        ) : <p className="mt-1 text-xs text-muted-foreground">Scores appear once this layer has a reference model or STTM and a proposal, mapping or code generation has run.</p>}
      </section>
    </div>
  );
}
