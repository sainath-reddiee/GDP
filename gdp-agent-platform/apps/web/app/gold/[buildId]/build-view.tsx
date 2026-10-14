"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Check, Code2, Crown, Database, ExternalLink, GitMerge, Loader2, Lock, Network, TriangleAlert } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAccess } from "@/components/access";
import { ErCanvas } from "@/components/hub/er-canvas";
import { cancelGold, generateGold, goldCandidates, goldFiles, goldToWorkbench, proposeGold, setGoldInputs,
  type GoldBuild, type GoldCandidate, type GoldInput, type GoldLine } from "../actions";

const KIND: Record<GoldLine["kind"], string> = {
  KEY: "bg-slate-500/10 text-slate-600", COLUMN: "bg-sky-500/10 text-sky-700 dark:text-sky-300",
  RESOLVE: "bg-violet-500/10 text-violet-700 dark:text-violet-300", DERIVED: "bg-amber-500/10 text-amber-700 dark:text-amber-300",
  AUDIT: "bg-muted text-muted-foreground",
};
const STEPS = [
  { id: "INPUTS", n: 1, title: "Inputs", hint: "Silver tables to build from", icon: Database },
  { id: "MODELING", n: 2, title: "Gold modeling", hint: "Facts, dimensions, bridges", icon: Network },
  { id: "REVIEW", n: 3, title: "Review", hint: "Model Workbench, four eyes", icon: GitMerge },
  { id: "CODE", n: 4, title: "Gold code generation", hint: "dbt models", icon: Code2 },
] as const;
const ORDER = ["INPUTS", "MODELING", "REVIEW", "CODE", "DONE"];

/** One gold build: choose silver inputs, propose the gold model, review it in the Model Workbench, generate dbt. */
export function BuildView({ build }: { build: GoldBuild }) {
  const router = useRouter();
  const { canAct } = useAccess();
  const may = canAct("MODEL.EDIT") && build.status !== "CANCELLED";
  const [cands, setCands] = useState<GoldCandidate[] | null>(null);
  const [picked, setPicked] = useState<string[]>((build.inputs ?? []).map((t) => `${t.database}.${t.schema}.${t.table}`));
  const [msg, setMsg] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [table, setTable] = useState<string>("ALL");
  const [pending, start] = useTransition();
  useEffect(() => { void goldCandidates(build.build_id).then((r) => r.ok ? setCands(r.data.tables) : setMsg({ tone: "error", text: r.error })); }, [build.build_id]);

  const spec = build.proposal?.spec;
  const at = ORDER.indexOf(build.step);
  const approved = !!build.model?.approved_version && build.model.approved_version >= (build.model_version ?? 0) && !build.model?.open_version;
  const run = (fn: () => Promise<{ ok: true; data: unknown } | { ok: false; error: string }>, done: string) => start(async () => {
    setMsg(null);
    const r = await fn();
    if (!r.ok) { setMsg({ tone: "error", text: r.error }); return; }
    setMsg({ tone: "ok", text: done });
    router.refresh();
  });
  const inputs: GoldInput[] = (cands ?? []).filter((c) => picked.includes(`${c.database}.${c.schema}.${c.table}`))
    .map(({ database, schema, table: t, layer }) => ({ database, schema, table: t, layer }));
  const entities = useMemo(() => (spec?.design.entities ?? []).map((e) => ({
    entity_name: e.entity_name, kind: e.conformed_from ? `${e.table_type} (from ${e.conformed_from})` : e.table_type,
    purpose: e.description ?? "", grain: e.grain ?? "", business_keys: e.business_keys,
    attributes: e.attributes.map((a) => ({ name: a.name, datatype: a.datatype, nullable: a.nullable, is_pk: a.is_pk, source_columns: [], derived: false })),
  })), [spec]);
  const conformed = useMemo(() => new Set((spec?.design.entities ?? []).filter((e) => e.conformed_from).map((e) => e.entity_name)), [spec]);
  const lines = build.lines.filter((l) => table === "ALL" || l.gold_table === table);
  const entity = spec?.design.entities.find((e) => e.entity_name === selected);

  return (
    <div className="space-y-4">
      <section className="surface p-4">
        <div className="flex flex-wrap items-start gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-amber-500/10 text-amber-600"><Crown className="h-5 w-5" /></span>
          <div className="min-w-0 flex-1">
            <h1 className="text-lg font-semibold">{build.name}</h1>
            <p className="text-xs text-muted-foreground">
              <Link href={`/domains/${build.domain_id}?tab=lineage`} className="text-primary hover:underline">{build.domain_name}</Link>
              {build.role === "CONSUMER" ? <> · consumes {build.consumes.map((c) => `${c.domain} (${c.silver_column} to ${c.gold_column})`).join(", ")}</>
                : build.role === "MASTERED" ? <> · mastered by Reltio</> : <> · not linked to MDM</>}
              {" "}· {build.status.toLowerCase().replace(/_/g, " ")} · started by {build.created_by} {build.created_at}
            </p>
          </div>
          {may && build.status !== "GENERATED" && (
            <button type="button" disabled={pending} onClick={() => run(() => cancelGold(build.build_id), "Cancelled.")}
                    className="h-8 rounded-lg px-2 text-xs text-muted-foreground hover:bg-muted">Cancel build</button>
          )}
        </div>
        <ol className="mt-3 grid gap-2 sm:grid-cols-4">
          {STEPS.map((s) => {
            const i = ORDER.indexOf(s.id);
            const done = at > i || (s.id === "REVIEW" && approved);
            const current = at === i;
            return (
              <li key={s.id} className={cn("flex items-center gap-2 rounded-xl border px-3 py-2", current && "border-primary/50 bg-primary/5", done && "border-emerald-500/30")}>
                <span className={cn("grid h-7 w-7 place-items-center rounded-full text-xs font-semibold", done ? "bg-emerald-500 text-white" : current ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>
                  {done ? <Check className="h-3.5 w-3.5" /> : s.n}</span>
                <span><span className="block text-sm font-semibold">{s.title}</span><span className="block text-[11px] text-muted-foreground">{s.hint}</span></span>
              </li>
            );
          })}
        </ol>
        {msg && <p role={msg.tone === "error" ? "alert" : "status"} className={cn("mt-3 rounded-lg px-3 py-2 text-xs", msg.tone === "error" ? "bg-destructive/10 text-destructive" : "bg-emerald-500/10 text-emerald-700")}>{msg.text}</p>}
      </section>

      <section className="surface space-y-3 p-4" aria-label="Inputs">
        <h2 className="text-sm font-semibold">1. Inputs</h2>
        <p className="text-xs text-muted-foreground">{build.role === "MASTERED"
          ? "A mastered domain's gold already exists in gdp-dbt; its post-MDM tables are listed for reference."
          : "The domain's silver hubs (pre-MDM, one row per source record). Their pre-MDM company and property keys are resolved to the mastered gold keys."}</p>
        {!cands ? <p className="text-xs text-muted-foreground"><Loader2 className="mr-1 inline h-3 w-3 animate-spin" />Reading the catalog…</p> : !cands.length ? (
          <p className="text-xs text-muted-foreground">No silver tables are registered for {build.domain_name} yet. Run pipeline 1 first.</p>
        ) : (
          <ul className="divide-y rounded-xl border text-sm">
            {cands.map((c) => {
              const id = `${c.database}.${c.schema}.${c.table}`;
              return (
                <li key={id} className="flex flex-wrap items-center gap-3 px-3 py-2">
                  <input type="checkbox" aria-label={`Use ${c.table}`} disabled={!may || !c.exists || build.status === "IN_REVIEW"} checked={picked.includes(id)}
                         onChange={(e) => setPicked(e.target.checked ? [...picked, id] : picked.filter((x) => x !== id))} />
                  <span className="font-mono text-xs">{id}</span>
                  <span className="rounded bg-muted px-1.5 py-0.5 text-[10px]">{c.layer === "SILVER_POST_MDM" ? "silver post-MDM" : "silver pre-MDM"}</span>
                  <span className="ml-auto text-xs text-muted-foreground">{c.error ? <span className="text-destructive">{c.error}</span> : !c.exists ? "not found"
                    : `${Number(c.row_count ?? 0).toLocaleString()} rows${c.last_altered ? ` · changed ${c.last_altered.slice(0, 10)}` : ""}`}</span>
                </li>
              );
            })}
          </ul>
        )}
        {may && build.status !== "IN_REVIEW" && (
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={pending || !inputs.length} onClick={() => run(() => setGoldInputs(build.build_id, inputs), `${inputs.length} input table(s) saved.`)}
                    className="inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm hover:bg-muted disabled:opacity-50">Save inputs</button>
            <button type="button" disabled={pending || !(build.inputs ?? []).length || build.role !== "CONSUMER"}
                    onClick={() => run(() => proposeGold(build.build_id), "Gold model proposed. Review it below, then send it to the Model Workbench.")}
                    className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground disabled:opacity-50">
              {pending && <Loader2 className="h-4 w-4 animate-spin" />}Propose the gold model<ArrowRight className="h-4 w-4" /></button>
          </div>
        )}
      </section>

      <section className="surface space-y-3 p-4" aria-label="Gold modeling">
        <h2 className="text-sm font-semibold">2. Gold modeling</h2>
        {!spec ? <p className="text-xs text-muted-foreground">Propose the gold model from the chosen inputs.</p> : (
          <>
            <p className="text-xs text-muted-foreground">
              {spec.design.entities.filter((e) => !e.conformed_from).length} gold tables in <span className="font-mono">{spec.physical_defaults.database}.{spec.physical_defaults.schema}</span>
              {" "}({spec.physical_defaults.platform.toLowerCase()}), plus {conformed.size} conformed dimension(s) from the mastered domains (shown dimmed, referenced not generated).
              Click a table for its columns.</p>
            {!!build.proposal?.notes.length && (
              <ul className="space-y-0.5 text-xs text-amber-700 dark:text-amber-300">{build.proposal.notes.map((n) => <li key={n} className="flex gap-1"><TriangleAlert className="mt-0.5 h-3 w-3 shrink-0" />{n}</li>)}</ul>
            )}
            <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_320px]">
              <ErCanvas className="h-[520px]" platform={spec.physical_defaults.platform === "ICEBERG" ? "ICEBERG" : "SNOWFLAKE"} entities={entities}
                        relationships={spec.relationships} selected={selected} onSelect={setSelected} muted={conformed} />
              <aside className="max-h-[520px] overflow-auto rounded-xl border p-3 text-xs">
                {!entity ? <p className="text-muted-foreground">Click a table.</p> : (
                  <div className="space-y-2">
                    <p className="font-mono text-sm font-semibold">{entity.entity_name}</p>
                    <p className="text-muted-foreground">{entity.table_type.toLowerCase()}{entity.conformed_from ? ` · owned by ${entity.conformed_from}` : ""}{entity.grain ? ` · ${entity.grain}` : ""}</p>
                    {entity.description && <p>{entity.description}</p>}
                    <p><span className="text-muted-foreground">Business keys:</span> <span className="font-mono">{entity.business_keys.join(", ") || "none"}</span></p>
                    <ul className="space-y-0.5">{entity.attributes.map((a) => (
                      <li key={a.name} className="flex gap-2"><span className={cn("font-mono", a.is_pk && "font-semibold")}>{a.name}</span>
                        <span className="ml-auto font-mono text-muted-foreground">{a.datatype}{a.nullable ? "" : " NN"}</span></li>
                    ))}</ul>
                  </div>
                )}
              </aside>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm font-medium">Where every gold column comes from</p>
              <select aria-label="Gold table" value={table} onChange={(e) => setTable(e.target.value)} className="h-8 rounded-lg border bg-background px-2 text-xs">
                <option value="ALL">All tables ({build.lines.length} columns)</option>
                {Array.from(new Set(build.lines.map((l) => l.gold_table))).map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div className="max-h-[420px] overflow-auto rounded-xl border">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-muted text-left text-[10px] uppercase tracking-wide text-muted-foreground">
                  <tr><th className="px-2 py-1.5">Gold column</th><th className="px-2 py-1.5">Kind</th><th className="px-2 py-1.5">From</th><th className="px-2 py-1.5">How</th></tr>
                </thead>
                <tbody>
                  {lines.map((l) => (
                    <tr key={`${l.gold_table}.${l.gold_column}`} className="border-t align-top">
                      <td className="px-2 py-1.5 font-mono">{table === "ALL" && <span className="text-muted-foreground">{l.gold_table}.</span>}{l.gold_column}</td>
                      <td className="px-2 py-1.5"><span className={cn("rounded px-1.5 py-0.5 text-[10px] font-medium", KIND[l.kind])}>{l.kind.toLowerCase()}</span></td>
                      <td className="px-2 py-1.5 font-mono">{l.source_table ? `${l.source_table.split(".").slice(-1)[0]}.${l.source_column}` : "—"}</td>
                      <td className="px-2 py-1.5">{l.expression ?? "as is"}{l.note && <span className="block text-muted-foreground">{l.note}</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      <section className="surface space-y-2 p-4" aria-label="Review">
        <h2 className="text-sm font-semibold">3. Review</h2>
        <p className="text-xs text-muted-foreground">The proposal becomes a draft of {build.domain_name}&apos;s gold model in the Model Workbench: edit it there, submit it, and another model approver approves it.
          Approving a gold model never changes the silver targets runs map into.</p>
        <div className="flex flex-wrap items-center gap-2">
          {may && spec && (build.status === "PROPOSED" || build.status === "IN_REVIEW") && (
            <button type="button" disabled={pending} onClick={() => run(() => goldToWorkbench(build.build_id), "The proposal is a draft of the gold model in the Model Workbench.")}
                    className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground disabled:opacity-50">
              {pending && <Loader2 className="h-4 w-4 animate-spin" />}{build.model_id ? "Send the proposal again" : "Send to the Model Workbench"}</button>
          )}
          {build.model_id && (
            <Link href={`/dashboard/modeling/${build.model_id}`} className="inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm hover:bg-muted">
              <ExternalLink className="h-4 w-4" />Open the gold model{build.model?.approved_version ? ` (approved v${build.model.approved_version})` : build.model?.open_version ? ` (draft v${build.model.open_version})` : ""}</Link>
          )}
        </div>
      </section>

      <GoldCode build={build} approved={approved} may={canAct("DBT.EDIT") && build.status !== "CANCELLED"} />
    </div>
  );
}

/** Step 4: the approved gold model as dbt models, Iceberg DDL and a report of what to confirm in review. */
function GoldCode({ build, approved, may }: { build: GoldBuild; approved: boolean; may: boolean }) {
  const router = useRouter();
  const [files, setFiles] = useState<Record<string, string> | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const gen = build.generation;
  useEffect(() => {
    if (!gen) return;
    void goldFiles(build.build_id).then((r) => { if (r.ok) { setFiles(r.data.files); setOpen((o) => o ?? Object.keys(r.data.files ?? {}).find((p) => p.startsWith("models/")) ?? null); } });
  }, [build.build_id, gen?.version]);   // eslint-disable-line react-hooks/exhaustive-deps
  const run = () => start(async () => {
    setError("");
    const r = await generateGold(build.build_id);
    if (!r.ok) { setError(r.error); return; }
    router.refresh();
  });
  return (
    <section className="surface space-y-3 p-4" aria-label="Gold code generation">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">4. Gold code generation {!approved && <Lock className="h-3.5 w-3.5 text-muted-foreground" />}</h2>
        {gen && <span className="text-xs text-muted-foreground">v{gen.version} from approved gold model v{gen.model_version} · {gen.report.engine}</span>}
        <span className="ml-auto flex gap-2">
          {may && approved && (
            <button type="button" disabled={pending} onClick={run} className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-medium text-primary-foreground disabled:opacity-50">
              {pending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}{gen ? "Generate again" : "Generate the gold dbt models"}</button>
          )}
          {gen && <a href={`/bff/gold-generation/${encodeURIComponent(build.build_id)}`} className="inline-flex h-8 items-center gap-1.5 rounded-lg border px-3 text-xs hover:bg-muted">Download (.zip)</a>}
          {gen && build.proposal?.spec && (
            <Link href={`/lineage?object=${encodeURIComponent(`${build.proposal.spec.physical_defaults.database}.${build.proposal.spec.physical_defaults.schema}.${gen.report.tables[0] ?? ""}`)}&direction=up`}
                  className="inline-flex h-8 items-center gap-1.5 rounded-lg border px-3 text-xs hover:bg-muted">Trace lineage</Link>
          )}
        </span>
      </div>
      {!approved && !gen && <p className="text-xs text-muted-foreground">Unlocks once the gold model is approved in the Model Workbench.</p>}
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
      {gen && (
        <>
          <p className="text-xs text-muted-foreground">{gen.report.tables.length} gold table(s): {gen.report.tables.join(", ")}. Conformed dimensions referenced, not generated: {gen.report.conformed.join(", ") || "none"}.
            Each table has an Iceberg DDL file (run first) and an incremental dbt model that keeps existing skeys and writes only new or changed rows.</p>
          {!!(gen.report.assumptions.length || gen.report.notes?.length) && (
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-3 text-xs">
              <p className="mb-1 font-medium text-amber-800 dark:text-amber-200">Confirm in review</p>
              <ul className="space-y-0.5">{[...gen.report.assumptions, ...(gen.report.notes ?? [])].map((a) => <li key={a} className="flex gap-1"><TriangleAlert className="mt-0.5 h-3 w-3 shrink-0 text-amber-600" />{a}</li>)}</ul>
            </div>
          )}
          <div className="grid gap-3 lg:grid-cols-[260px_minmax(0,1fr)]">
            <ul className="max-h-[480px] overflow-auto rounded-xl border p-1 text-xs">
              {gen.paths.map((p) => (
                <li key={p}><button type="button" onClick={() => setOpen(p)} className={cn("w-full truncate rounded px-2 py-1 text-left font-mono", open === p ? "bg-primary/10 text-primary" : "hover:bg-muted")} title={p}>{p}</button></li>
              ))}
            </ul>
            <pre className="max-h-[480px] overflow-auto rounded-xl border bg-muted/40 p-3 font-mono text-[11px] leading-relaxed">{open && files ? files[open] : "Choose a file."}</pre>
          </div>
        </>
      )}
    </section>
  );
}
