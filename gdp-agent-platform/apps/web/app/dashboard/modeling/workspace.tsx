"use client";

import Link from "next/link";
import { useMemo, useState, useTransition } from "react";
import {
  AlertTriangle, CheckCircle2, Copy, Eye, GitBranch, GitFork, Loader2, Map as MapIcon, Plus, Save, Sparkles, Trash2, Wand2,
} from "lucide-react";
import type { ModelAttribute, ModelContext, ModelEntity, ModelIssue, ModelRender, ModelSpec } from "@/lib/hub-types";
import { ErCanvas, icebergType } from "@/components/hub/er-canvas";
import { Modal } from "@/components/hub/overlay";
import { ErrorNote, StatusPill, explain } from "@/components/hub/states";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { finalize, propose, publish, renderSpec, saveDraft, startFromDomain, validateSpec } from "./actions";

type InitialDesign = { design_id: string; version: number; status: string; spec: ModelSpec; issues: ModelIssue[] | null; branch: string | null; pr_url: string | null; repo_id: string | null } | null;
const KINDS = ["DIMENSION", "FACT", "BRIDGE", "REFERENCE", "TABLE"];
const IDENT = (v: string) => v.toUpperCase().replace(/[^A-Z0-9_$]+/g, "_");

const emptySpec = (mode: ModelSpec["mode"], domain = "", parent: string | null = null): ModelSpec => ({
  domain, mode, parent_domain: parent, description: "", target_platform: "SNOWFLAKE", target_database: "", target_schema: "",
  external_volume: null, source_tables: [], design: { entities: [] }, relationships: [],
});

export function ModelingWorkspace({ ctx, initialTables, initialDesign }: { ctx: ModelContext; initialTables: string[]; initialDesign: InitialDesign }) {
  const [spec, setSpec] = useState<ModelSpec | null>(initialDesign?.spec ?? null);
  const [basePack, setBasePack] = useState<Record<string, unknown> | null>(null);
  const [designId, setDesignId] = useState<string | null>(initialDesign?.design_id ?? null);
  const [status, setStatus] = useState<{ version: number; status: string; branch?: string | null; pr?: string | null } | null>(
    initialDesign ? { version: initialDesign.version, status: initialDesign.status, branch: initialDesign.branch, pr: initialDesign.pr_url } : null);
  const [issues, setIssues] = useState<ModelIssue[]>(initialDesign?.issues ?? []);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [preview, setPreview] = useState<ModelRender | null>(null);
  const [repoId, setRepoId] = useState<string>(initialDesign?.repo_id ?? ctx.repos[0]?.repo_id ?? "");
  const [pending, start] = useTransition();

  if (!spec) {
    return <StartPanel ctx={ctx} initialTables={initialTables} pending={pending}
                       onStart={(next, pack, found) => { setSpec(next); setBasePack(pack); setIssues(found ?? []); }}
                       onError={setError} error={error} start={start} />;
  }

  const update = (fn: (s: ModelSpec) => ModelSpec) => { setSpec((s) => (s ? fn(s) : s)); setStatus((st) => (st && st.status !== "DRAFT" ? { ...st, status: "DRAFT" } : st)); };
  const entity = spec.design.entities.find((e) => e.entity_name === selected) ?? null;
  const setEntity = (name: string, fn: (e: ModelEntity) => ModelEntity) =>
    update((s) => ({ ...s, design: { ...s.design, entities: s.design.entities.map((e) => (e.entity_name === name ? fn(e) : e)) } }));

  const blocking = issues.filter((i) => i.severity === "ERROR");
  const run = (fn: () => Promise<void>) => start(async () => { setError(null); setNotice(null); await fn(); });

  const validate = () => run(async () => {
    const r = await validateSpec(spec);
    if (!r.ok) return setError(r.error);
    setIssues(r.data.issues);
    setNotice(r.data.blocking ? `${r.data.blocking} blocking issues` : `Valid: ${r.data.summary.entities} entities, ${r.data.summary.columns} columns, ${r.data.summary.relationships} relationships`);
  });
  const showPreview = () => run(async () => {
    const repo = ctx.repos.find((x) => x.repo_id === repoId);
    const r = await renderSpec(spec, basePack, repo?.dbt_project_dir ?? "");
    if (!r.ok) return setError(r.error);
    setIssues(r.data.issues);
    setPreview(r.data);
  });
  const save = () => run(async () => {
    const r = await saveDraft(spec, designId);
    if (!r.ok) return setError(r.error);
    setDesignId(r.data.design_id);
    setIssues(r.data.issues);
    setStatus({ version: r.data.version, status: "DRAFT" });
    setNotice(`Saved draft v${r.data.version}`);
  });
  const doFinalize = () => run(async () => {
    let id = designId;
    if (!id) {
      const s = await saveDraft(spec, null);
      if (!s.ok) return setError(s.error);
      id = s.data.design_id;
      setDesignId(id);
    }
    const r = await finalize(id, spec, basePack, repoId || null);
    if (!r.ok) {
      const e = explain(r.error);
      setError(e.list.length ? `${e.message}: ${e.list.slice(0, 4).join("; ")}` : e.message);
      return;
    }
    setStatus({ version: r.data.version, status: "FINALIZED", branch: r.data.branch });
    setNotice(`Finalized v${r.data.version}: ${r.data.files.length} files ready for ${r.data.branch}`);
  });
  const doPublish = () => run(async () => {
    if (!designId || !status || !repoId) return setError("Finalize the design and pick a repository first");
    const r = await publish(designId, status.version, repoId);
    if (!r.ok) return setError(r.error);
    if (r.data.status === "PUBLISHED" || r.data.status === "NO_CHANGES") {
      setStatus({ ...status, status: "PUBLISHED", branch: r.data.head_branch, pr: r.data.pull_request?.url ?? null });
      setNotice(`Pushed ${r.data.head_branch}${r.data.pull_request?.url ? " and opened a draft pull request" : ""}`);
    } else {
      setStatus({ ...status, status: "PUBLISH_FAILED" });
      setError(r.data.detail ?? r.data.status);
    }
  });

  const addEntity = () => {
    let n = 1;
    while (spec.design.entities.some((e) => e.entity_name === `NEW_ENTITY_${n}`)) n += 1;
    const name = `NEW_ENTITY_${n}`;
    update((s) => ({ ...s, design: { ...s.design, entities: [...s.design.entities, { entity_name: name, kind: "TABLE", purpose: "", grain: "", business_keys: [],
      attributes: [{ name: `${name}_ID`, datatype: "NUMBER(38,0)", nullable: false, is_pk: true, source_columns: [], derived: true }] }] } }));
    setSelected(name);
  };

  return (
    <div className="space-y-4">
      <div className="surface flex flex-wrap items-end gap-3 p-4">
        <div>
          <Label className="mt-0 text-xs">Domain</Label>
          <Input className="w-48 font-mono" value={spec.domain} disabled={spec.mode === "MAP"} onChange={(e) => update((s) => ({ ...s, domain: IDENT(e.target.value) }))} />
        </div>
        <div className="pb-1.5">
          <span className="rounded-full bg-accent px-2 py-1 text-xs font-medium text-accent-foreground">
            {spec.mode === "MAP" ? "Map to existing" : spec.mode === "FORK" ? `Fork of ${spec.parent_domain}` : "New domain"}
          </span>
        </div>
        <div>
          <Label className="mt-0 text-xs">Target platform</Label>
          <Select className="w-40" value={spec.target_platform} onChange={(e) => update((s) => ({ ...s, target_platform: e.target.value as ModelSpec["target_platform"] }))}>
            <option value="SNOWFLAKE">Snowflake tables</option><option value="ICEBERG">Iceberg tables</option>
          </Select>
        </div>
        <div>
          <Label className="mt-0 text-xs">Database</Label>
          <Input className="w-40 font-mono" value={spec.target_database} onChange={(e) => update((s) => ({ ...s, target_database: IDENT(e.target.value) }))} />
        </div>
        <div>
          <Label className="mt-0 text-xs">Schema</Label>
          <Input className="w-36 font-mono" value={spec.target_schema} placeholder={spec.domain} onChange={(e) => update((s) => ({ ...s, target_schema: IDENT(e.target.value) }))} />
        </div>
        {spec.target_platform === "ICEBERG" && (
          <div>
            <Label className="mt-0 text-xs">External volume</Label>
            <Input className="w-40 font-mono" value={spec.external_volume ?? ""} onChange={(e) => update((s) => ({ ...s, external_volume: e.target.value || null }))} />
          </div>
        )}
        <div className="ml-auto flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={validate} disabled={pending}><CheckCircle2 className="h-3.5 w-3.5" /> Validate</Button>
          <Button variant="outline" size="sm" onClick={showPreview} disabled={pending}><Eye className="h-3.5 w-3.5" /> Preview files</Button>
          <Button variant="outline" size="sm" onClick={save} disabled={pending}><Save className="h-3.5 w-3.5" /> Save draft</Button>
          <Button size="sm" onClick={doFinalize} disabled={pending}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wand2 className="h-3.5 w-3.5" />} Finalize</Button>
        </div>
      </div>

      {error && <ErrorNote>{error}</ErrorNote>}
      {notice && <p className="rounded-lg bg-accent px-3 py-2 text-sm text-accent-foreground">{notice}</p>}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="min-w-0 space-y-3">
          <ErCanvas entities={spec.design.entities} relationships={spec.relationships} platform={spec.target_platform}
                    selected={selected} onSelect={setSelected} className="h-[560px]" />
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={addEntity}><Plus className="h-3.5 w-3.5" /> Add entity</Button>
            <AddFromSources spec={spec} pending={pending} start={start} onError={setError}
                            onMerge={(more) => update((s) => mergeSpecs(s, more))} />
            <span className="ml-auto text-xs text-muted-foreground">Drag cards to arrange, drag the background to pan, Ctrl + wheel to zoom.</span>
          </div>
          {issues.length > 0 && (
            <div className="surface p-4">
              <h3 className="flex items-center gap-1.5 text-sm font-semibold"><AlertTriangle className="h-4 w-4 text-amber-600" /> {blocking.length} blocking, {issues.length - blocking.length} warnings</h3>
              <ul className="mt-2 max-h-48 space-y-1 overflow-y-auto text-xs">
                {issues.map((i, n) => (
                  <li key={n} className="flex gap-2">
                    <span className={cn("shrink-0 rounded px-1.5 font-medium", i.severity === "ERROR" ? "bg-rose-50 text-rose-700" : "bg-amber-50 text-amber-800")}>{i.severity.toLowerCase()}</span>
                    <span className="font-mono text-muted-foreground">{i.path}</span><span>{i.message}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <aside className="space-y-4">
          {entity ? (
            <EntityEditor key={entity.entity_name} entity={entity} spec={spec} platform={spec.target_platform}
                          onChange={(fn) => setEntity(entity.entity_name, fn)}
                          onRename={(name) => {
                            const clean = IDENT(name);
                            if (!clean || spec.design.entities.some((e) => e.entity_name === clean)) return;
                            update((s) => ({
                              ...s,
                              design: { ...s.design, entities: s.design.entities.map((e) => (e.entity_name === entity.entity_name ? { ...e, entity_name: clean } : e)) },
                              relationships: s.relationships.map((r) => ({ ...r, from_entity: r.from_entity === entity.entity_name ? clean : r.from_entity, to_entity: r.to_entity === entity.entity_name ? clean : r.to_entity })),
                            }));
                            setSelected(clean);
                          }}
                          onDelete={() => {
                            update((s) => ({ ...s, design: { ...s.design, entities: s.design.entities.filter((e) => e.entity_name !== entity.entity_name) },
                              relationships: s.relationships.filter((r) => r.from_entity !== entity.entity_name && r.to_entity !== entity.entity_name) }));
                            setSelected(null);
                          }}
                          onRelationships={(fn) => update((s) => ({ ...s, relationships: fn(s.relationships) }))} />
          ) : (
            <div className="surface p-4 text-sm text-muted-foreground">Select an entity on the canvas to edit its columns, keys and relationships.</div>
          )}

          <section className="surface space-y-3 p-4">
            <h3 className="flex items-center gap-1.5 text-sm font-semibold"><GitBranch className="h-4 w-4" /> Publish</h3>
            {status && <p className="text-xs">Design v{status.version} <StatusPill status={status.status} className="ml-1" /></p>}
            {ctx.repos.length ? (
              <Select aria-label="Repository" value={repoId} onChange={(e) => setRepoId(e.target.value)}>
                {ctx.repos.map((r) => <option key={r.repo_id} value={r.repo_id}>{r.name} ({r.branch})</option>)}
              </Select>
            ) : <p className="text-xs text-muted-foreground">No repository is connected for dbt. Connect one in <Link href="/admin" className="text-primary hover:underline">Admin, Integrations</Link>.</p>}
            {!ctx.publisher_ready && <p className="text-xs text-amber-800">GitHub publishing is not set up yet; finalized designs are stored and can be published later.</p>}
            <p className="text-xs text-muted-foreground">Finalize, then publish: a branch feature/model-{spec.domain.toLowerCase().replace(/_/g, "-") || "domain"}-&lt;timestamp&gt; with domain_pack.json, DDL and dbt models, and a draft pull request.</p>
            <Button className="w-full" onClick={doPublish} disabled={pending || !status || !["FINALIZED", "PUBLISH_FAILED"].includes(status.status) || !repoId || !ctx.publisher_ready}>
              <GitBranch className="h-4 w-4" /> Publish to a branch
            </Button>
            {status?.branch && <p className="break-all font-mono text-[11px] text-muted-foreground">{status.branch}</p>}
            {status?.pr && <a href={status.pr} target="_blank" rel="noreferrer" className="text-xs font-medium text-primary hover:underline">Open the pull request</a>}
          </section>

          {ctx.designs.length > 0 && (
            <section className="surface p-4">
              <h3 className="text-sm font-semibold">Recent designs</h3>
              <ul className="mt-2 space-y-1.5 text-xs">
                {ctx.designs.slice(0, 8).map((d) => (
                  <li key={d.design_id} className="flex items-center gap-2">
                    <Link href={`/dashboard/modeling?design=${d.design_id}`} className="flex-1 truncate font-mono text-primary hover:underline">{d.domain_name}</Link>
                    <span className="text-muted-foreground">{d.mode.toLowerCase()} v{d.version}</span><StatusPill status={d.status} />
                  </li>
                ))}
              </ul>
            </section>
          )}
        </aside>
      </div>

      <Modal open={!!preview} onClose={() => setPreview(null)} wide title="Rendered files" description={preview ? `Branch ${preview.branch}` : undefined}>
        {preview && <PreviewFiles render={preview} />}
      </Modal>
    </div>
  );
}

function mergeSpecs(base: ModelSpec, more: ModelSpec): ModelSpec {
  const have = new Set(base.design.entities.map((e) => e.entity_name));
  const key = (r: ModelSpec["relationships"][number]) => `${r.from_entity}.${r.from_column}>${r.to_entity}.${r.to_column}`;
  const rels = new Set(base.relationships.map(key));
  return {
    ...base,
    source_tables: Array.from(new Set([...base.source_tables, ...more.source_tables])),
    design: { ...base.design, entities: [...base.design.entities, ...more.design.entities.filter((e) => !have.has(e.entity_name))] },
    relationships: [...base.relationships, ...more.relationships.filter((r) => !rels.has(key(r)))],
  };
}

function StartPanel({ ctx, initialTables, pending, start, onStart, onError, error }: {
  ctx: ModelContext; initialTables: string[]; pending: boolean; start: (fn: () => Promise<void>) => void;
  onStart: (spec: ModelSpec, pack: Record<string, unknown> | null, issues?: ModelIssue[]) => void; onError: (e: string | null) => void; error: string | null;
}) {
  const [mode, setMode] = useState<ModelSpec["mode"]>(initialTables.length ? "NEW" : "MAP");
  const [domainId, setDomainId] = useState(ctx.domains[0]?.domain_id ?? "");
  const [name, setName] = useState("");
  const [tables, setTables] = useState(initialTables.join("\n"));
  const [useAi, setUseAi] = useState(false);
  const [instructions, setInstructions] = useState("");
  const parent = ctx.domains.find((d) => d.domain_id === domainId);
  const tableList = tables.split(/[\s,]+/).map((t) => t.trim()).filter(Boolean);

  const go = () => start(async () => {
    onError(null);
    let spec: ModelSpec;
    let pack: Record<string, unknown> | null = null;
    if (mode === "NEW") {
      spec = emptySpec("NEW", IDENT(name));
    } else {
      const r = await startFromDomain(domainId, mode, mode === "FORK" ? IDENT(name) : undefined);
      if (!r.ok) return onError(r.error);
      spec = r.data.spec;
      pack = r.data.base_pack;
    }
    if (tableList.length) {
      const r = await propose({ mode, domain: spec.domain, parent_domain: spec.parent_domain, source_tables: tableList, use_ai: useAi,
                                instructions: instructions || undefined, target_platform: spec.target_platform,
                                target_database: spec.target_database || undefined, target_schema: spec.target_schema || undefined });
      if (!r.ok) return onError(r.error);
      spec = mergeSpecs(spec, r.data.spec);
      if (r.data.grounding.missing_profiles.length) onError(`No profile yet for ${r.data.grounding.missing_profiles.join(", ")}; profile them in the Profiling Store to include them.`);
      return onStart(spec, pack, r.data.issues);
    }
    onStart(spec, pack);
  });

  const card = (key: ModelSpec["mode"], title: string, text: string, Icon: typeof MapIcon) => (
    <button type="button" onClick={() => setMode(key)} aria-pressed={mode === key}
            className={cn("surface flex flex-1 flex-col items-start gap-2 p-4 text-left transition", mode === key ? "border-primary/40 ring-2 ring-primary/15" : "hover:border-primary/25")}>
      <span className={cn("grid h-10 w-10 place-items-center rounded-xl", mode === key ? "bg-primary text-primary-foreground" : "bg-accent text-accent-foreground")}><Icon className="h-5 w-5" /></span>
      <span className="text-sm font-semibold">{title}</span>
      <span className="text-xs text-muted-foreground">{text}</span>
    </button>
  );
  const canGo = mode === "NEW" ? /^[A-Z][A-Z0-9_]{1,63}$/.test(IDENT(name)) : !!domainId && (mode === "MAP" || /^[A-Z][A-Z0-9_]{1,63}$/.test(IDENT(name)));

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 md:flex-row">
        {card("MAP", "Map to an existing domain", "Extend or adjust a registered domain in place: its targets, keys and knowledge.", MapIcon)}
        {card("FORK", "Fork an existing domain", "Start from a domain's model under a new name and evolve it independently.", GitFork)}
        {card("NEW", "Create a new domain model", "Design from your profiled source tables, with the modeling skills.", Plus)}
      </div>
      <div className="surface grid gap-x-6 p-5 md:grid-cols-2">
        <div>
          {mode !== "NEW" && (
            <>
              <Label htmlFor="dom">Domain</Label>
              <Select id="dom" value={domainId} onChange={(e) => setDomainId(e.target.value)}>
                {ctx.domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
              </Select>
              {parent?.description && <p className="mt-1 text-xs text-muted-foreground">{parent.description}</p>}
            </>
          )}
          {mode !== "MAP" && (
            <>
              <Label htmlFor="name">{mode === "FORK" ? "New domain name" : "Domain name"}</Label>
              <Input id="name" className="font-mono" value={name} placeholder={mode === "FORK" ? `${parent?.domain_name ?? "DOMAIN"}_EXT` : "SALES"}
                     onChange={(e) => setName(IDENT(e.target.value))} />
            </>
          )}
        </div>
        <div>
          <Label htmlFor="tables">Source tables (optional)</Label>
          <Textarea id="tables" className="font-mono text-xs" value={tables} placeholder="DB.SCHEMA.TABLE, one per line" onChange={(e) => setTables(e.target.value)} />
          <label className="mt-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} />
            <Sparkles className="h-3.5 w-3.5 text-primary" /> Design with AI (modeling skills, uses credits)</label>
          {useAi && <Textarea className="mt-2 text-xs" placeholder="Instructions for the designer (optional)" value={instructions} onChange={(e) => setInstructions(e.target.value)} />}
        </div>
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}
      <div className="flex justify-end">
        <Button onClick={go} disabled={!canGo || pending}>{pending ? <Loader2 className="h-4 w-4 animate-spin" /> : null} Open the canvas</Button>
      </div>
    </div>
  );
}

function AddFromSources({ spec, pending, start, onMerge, onError }: {
  spec: ModelSpec; pending: boolean; start: (fn: () => Promise<void>) => void; onMerge: (s: ModelSpec) => void; onError: (e: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [tables, setTables] = useState("");
  const add = () => start(async () => {
    const list = tables.split(/[\s,]+/).filter(Boolean);
    if (!list.length) return;
    const r = await propose({ mode: spec.mode, domain: spec.domain, parent_domain: spec.parent_domain, source_tables: list, use_ai: false,
                              target_platform: spec.target_platform });
    if (!r.ok) return onError(r.error);
    onMerge(r.data.spec);
    setOpen(false); setTables("");
  });
  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}><Copy className="h-3.5 w-3.5" /> Add from profiled tables</Button>
      <Modal open={open} onClose={() => setOpen(false)} title="Add entities from profiled tables"
             description="Each table becomes an entity with its profiled columns and keys; foreign keys are inferred from the profiles."
             footer={<Button onClick={add} disabled={pending}>{pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />} Add</Button>}>
        <Textarea className="font-mono text-xs" placeholder="DB.SCHEMA.TABLE, one per line" value={tables} onChange={(e) => setTables(e.target.value)} />
      </Modal>
    </>
  );
}

function EntityEditor({ entity, spec, platform, onChange, onRename, onDelete, onRelationships }: {
  entity: ModelEntity; spec: ModelSpec; platform: ModelSpec["target_platform"]; onChange: (fn: (e: ModelEntity) => ModelEntity) => void;
  onRename: (name: string) => void; onDelete: () => void; onRelationships: (fn: (r: ModelSpec["relationships"]) => ModelSpec["relationships"]) => void;
}) {
  const [name, setName] = useState(entity.entity_name);
  const [fkColumn, setFkColumn] = useState(entity.attributes[0]?.name ?? "");
  const [fkTarget, setFkTarget] = useState("");
  const others = spec.design.entities.filter((e) => e.entity_name !== entity.entity_name);
  const target = others.find((e) => e.entity_name === fkTarget);
  const targetKey = target ? (target.attributes.find((a) => a.is_pk)?.name ?? target.business_keys[0] ?? target.attributes[0]?.name ?? "") : "";
  const rels = useMemo(() => spec.relationships.filter((r) => r.from_entity === entity.entity_name), [spec.relationships, entity.entity_name]);
  const setAttr = (i: number, patch: Partial<ModelAttribute>) =>
    onChange((e) => ({ ...e, attributes: e.attributes.map((a, n) => (n === i ? { ...a, ...patch } : a)) }));

  return (
    <section className="surface space-y-3 p-4">
      <div className="flex items-end gap-2">
        <div className="flex-1">
          <Label className="mt-0 text-xs">Entity</Label>
          <Input className="font-mono" value={name} onChange={(e) => setName(IDENT(e.target.value))} onBlur={() => name !== entity.entity_name && onRename(name)} />
        </div>
        <Select aria-label="Kind" className="w-32" value={entity.kind} onChange={(e) => onChange((x) => ({ ...x, kind: e.target.value }))}>
          {KINDS.map((k) => <option key={k} value={k}>{k.toLowerCase()}</option>)}
        </Select>
        <Button variant="ghost" size="sm" aria-label="Delete entity" onClick={onDelete}><Trash2 className="h-4 w-4 text-rose-600" /></Button>
      </div>
      <Input aria-label="Grain" placeholder="Grain: one row per ..." value={entity.grain} onChange={(e) => onChange((x) => ({ ...x, grain: e.target.value }))} />
      <Input aria-label="Purpose" placeholder="Purpose" value={entity.purpose} onChange={(e) => onChange((x) => ({ ...x, purpose: e.target.value }))} />
      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="text-xs font-semibold">Columns ({entity.attributes.length})</span>
          <button className="text-xs text-primary hover:underline"
                  onClick={() => onChange((e) => ({ ...e, attributes: [...e.attributes, { name: `COLUMN_${e.attributes.length + 1}`, datatype: "VARCHAR", nullable: true, is_pk: false, source_columns: [], derived: true }] }))}>
            + column
          </button>
        </div>
        <div className="max-h-80 space-y-1 overflow-y-auto pr-1">
          {entity.attributes.map((a, i) => (
            <div key={i} className="rounded-lg border border-border/70 p-1.5">
              <div className="flex items-center gap-1">
                <Input aria-label="Column name" className="h-7 flex-1 px-2 font-mono text-[11px]" value={a.name} onChange={(e) => setAttr(i, { name: IDENT(e.target.value) })} />
                <Input aria-label="Data type" className="h-7 w-28 px-2 font-mono text-[11px]" value={a.datatype} onChange={(e) => setAttr(i, { datatype: e.target.value.toUpperCase() })} />
                <button aria-label="Remove column" onClick={() => onChange((e) => ({ ...e, attributes: e.attributes.filter((_, n) => n !== i) }))} className="rounded p-1 text-muted-foreground hover:bg-muted"><Trash2 className="h-3 w-3" /></button>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-3 px-1 text-[11px] text-muted-foreground">
                <label className="flex items-center gap-1"><input type="checkbox" checked={a.is_pk} onChange={(e) => setAttr(i, { is_pk: e.target.checked, nullable: e.target.checked ? false : a.nullable })} /> PK</label>
                <label className="flex items-center gap-1"><input type="checkbox" checked={!a.nullable} onChange={(e) => setAttr(i, { nullable: !e.target.checked })} /> not null</label>
                {platform === "ICEBERG" && <span className="font-mono">{icebergType(a.datatype)}</span>}
                <span className="truncate font-mono" title={a.source_columns.join(", ")}>{a.source_columns.length ? a.source_columns.join(", ") : "derived"}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div>
        <span className="text-xs font-semibold">Foreign keys</span>
        <ul className="mt-1 space-y-1 text-[11px]">
          {rels.map((r) => (
            <li key={`${r.from_column}>${r.to_entity}`} className="flex items-center gap-1.5 rounded bg-muted/50 px-2 py-1 font-mono">
              <span className="flex-1 truncate">{r.from_column} → {r.to_entity}.{r.to_column}</span>
              <button aria-label="Remove relationship" onClick={() => onRelationships((all) => all.filter((x) => x !== r))}><Trash2 className="h-3 w-3 text-muted-foreground" /></button>
            </li>
          ))}
          {!rels.length && <li className="text-muted-foreground">None</li>}
        </ul>
        {others.length > 0 && (
          <div className="mt-2 flex items-center gap-1">
            <Select aria-label="Column" className="h-7 flex-1 px-2 text-[11px]" value={fkColumn} onChange={(e) => setFkColumn(e.target.value)}>
              {entity.attributes.map((a) => <option key={a.name}>{a.name}</option>)}
            </Select>
            <Select aria-label="References" className="h-7 flex-1 px-2 text-[11px]" value={fkTarget} onChange={(e) => setFkTarget(e.target.value)}>
              <option value="">references...</option>
              {others.map((e) => <option key={e.entity_name}>{e.entity_name}</option>)}
            </Select>
            <Button size="sm" variant="outline" className="h-7 px-2" disabled={!fkColumn || !target || !targetKey}
                    onClick={() => onRelationships((all) => [...all, { from_entity: entity.entity_name, from_column: fkColumn, to_entity: fkTarget, to_column: targetKey, cardinality: "N:1" }])}>
              <Plus className="h-3 w-3" />
            </Button>
          </div>
        )}
      </div>
    </section>
  );
}

function PreviewFiles({ render }: { render: ModelRender }) {
  const files: Record<string, string> = { "model.sql": render.ddl, "domain_pack.json": JSON.stringify(render.domain_pack, null, 2), ...render.dbt };
  const names = Object.keys(files);
  const [file, setFile] = useState(names[0]);
  return (
    <div className="grid gap-3 md:grid-cols-[15rem_1fr]">
      <ul className="space-y-0.5 text-xs">
        {names.map((n) => (
          <li key={n}><button onClick={() => setFile(n)} className={cn("w-full truncate rounded px-2 py-1 text-left font-mono", n === file ? "bg-accent text-accent-foreground" : "hover:bg-muted")}>{n}</button></li>
        ))}
        {render.pack_problems.length > 0 && <li className="mt-2 rounded bg-rose-50 p-2 text-rose-700">Pack problems: {render.pack_problems.join("; ")}</li>}
      </ul>
      <pre className="max-h-[60vh] overflow-auto rounded-lg bg-slate-950 p-4 text-xs text-slate-100">{files[file]}</pre>
    </div>
  );
}
