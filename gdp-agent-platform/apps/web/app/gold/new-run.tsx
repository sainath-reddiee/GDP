"use client";

import { useEffect, useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2, Plus } from "lucide-react";
import { useAccess } from "@/components/access";
import { Modal } from "@/components/hub/overlay";
import { PIPELINES } from "@/lib/stages";
import { cn } from "@/lib/utils";
import { goldInputs, startGoldRun, type GoldInputs } from "./actions";

/** Start a Gold run, through the same steps as a Silver run: a consumer domain's Silver tables (read in place) become
 *  its facts, bridges and dimensions; a domain Reltio masters rebuilds its conformed dimension from the post-MDM golden
 *  record and related tables (a new source Reltio now masters reaches gold this way). */
export function NewGoldRun({ domains }: { domains: { domain_id: string; domain_name: string }[] }) {
  const { canAct } = useAccess();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [domain, setDomain] = useState("");
  const [inputs, setInputs] = useState<GoldInputs | null>(null);
  const [loading, setLoading] = useState(false);
  const [schemaKey, setSchemaKey] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();

  useEffect(() => {
    setInputs(null); setPicked([]); setSchemaKey(""); setError("");
    if (!domain) return;
    setLoading(true);
    void goldInputs(domain).then((r) => {
      setLoading(false);
      if (!r.ok) { setError(r.error); return; }
      setInputs(r.data);
      const first = r.data.schemas.find((s) => s.tables.some((t) => t.suggested)) ?? r.data.schemas[0];
      if (first) {
        setSchemaKey(`${first.database}.${first.schema}`);
        setPicked(first.tables.filter((t) => t.suggested).map((t) => t.table));
      }
    });
  }, [domain]);

  if (!canAct("RUN.CREATE")) return null;
  const schema = inputs?.schemas.find((s) => `${s.database}.${s.schema}` === schemaKey);
  const mastered = inputs?.role === "MASTERED";
  const ready = inputs?.role === "CONSUMER" || mastered;
  const golden = inputs?.mastered?.golden?.toUpperCase();
  const go = () => start(async () => {
    if (!schema) return;
    setError("");
    const r = await startGoldRun({ domain_id: domain, database: schema.database, schema: schema.schema, tables: picked,
                                   ...(name.trim() ? { run_name: name.trim() } : {}) });
    if (!r.ok) { setError(r.error); return; }
    router.push(r.data.stage === "MODELING" ? `/runs/${r.data.run_id}/modeling` : `/runs/${r.data.run_id}`);
  });

  return (
    <>
      <button type="button" onClick={() => setOpen(true)} data-testid="new-gold-run"
              className={cn("inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm font-medium hover:opacity-90", PIPELINES.GOLD.accent.solid)}>
        <Plus className="h-4 w-4" />New Gold run</button>
      <Modal open={open} onClose={() => setOpen(false)} wide title="New Gold run"
             description="Pick a domain and its tables. They are read in place (nothing is copied), profiled, modeled, mapped, tested and generated as gold dbt, with the same gates as a Silver run."
             footer={<>
               <button type="button" onClick={() => setOpen(false)} className="h-9 rounded-lg px-3 text-sm hover:bg-muted">Cancel</button>
               <button type="button" onClick={go} disabled={!ready || !picked.length || pending}
                       className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground disabled:opacity-50">
                 {pending && <Loader2 className="h-4 w-4 animate-spin" />}{pending ? "Selecting and profiling…" : `Start Gold run (${picked.length} table${picked.length === 1 ? "" : "s"})`}</button>
             </>}>
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-xs font-medium">Domain
              <select aria-label="Domain" value={domain} onChange={(e) => setDomain(e.target.value)} className="h-9 w-full rounded-lg border bg-background px-2 text-sm font-normal">
                <option value="">Choose a domain…</option>
                {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
              </select>
            </label>
            <label className="space-y-1 text-xs font-medium">Run name (optional)
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Availability gold, first cut"
                     className="h-9 w-full rounded-lg border bg-background px-3 text-sm font-normal" />
            </label>
          </div>
          {loading && <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-4 w-4 animate-spin" />Reading the domain&apos;s Silver tables…</p>}
          {inputs && !ready && (
            <p role="status" className="rounded-lg border bg-muted/50 px-3 py-2 text-sm">
              {inputs.domain_name} is not linked to MDM yet. On its <Link className="font-medium underline" href={`/domains/${inputs.domain_id}?tab=lineage`}>Lineage tab</Link>, mark which mastered domains (Company, Property) it consumes, then start the Gold run.
            </p>
          )}
          {inputs && ready && (
            <>
              {mastered && inputs.mastered ? (
                <p className="rounded-lg border bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
                  {inputs.domain_name} is mastered by Reltio. This run rebuilds its conformed dimension <span className="font-mono">{inputs.mastered.dimension}</span> (one
                  row per <span className="font-mono">{inputs.mastered.mastered_id}</span>) from the post-MDM golden record <span className="font-mono">{inputs.mastered.golden}</span>,
                  plus a child dimension for each related table you pick. Use it when a new source reaches gold through Reltio. Consumers&apos; facts keep referencing it.</p>
              ) : (
                <p className="text-xs text-muted-foreground">Consumes {inputs.consumes.map((c) => `${c.domain} (${c.silver_column} to ${c.gold_column})`).join(", ") || "nothing yet"}.</p>
              )}
              {!inputs.schemas.length && <p className="text-sm text-muted-foreground">No {mastered ? "post-MDM" : "Silver"} schema is known for {inputs.domain_name}: {mastered ? "derive its MDM contract from the post-MDM schema on the domain's References tab" : "run its Silver Pipeline first, or set its silver database and schema on the domain"}.</p>}
              {!!inputs.schemas.length && (
                <label className="block space-y-1 text-xs font-medium">{mastered ? "Post-MDM schema" : "Silver schema"}
                  <select aria-label="Silver schema" value={schemaKey} onChange={(e) => { setSchemaKey(e.target.value); setPicked([]); }}
                          className="h-9 w-full rounded-lg border bg-background px-2 text-sm font-normal">
                    {inputs.schemas.map((s) => <option key={`${s.database}.${s.schema}`} value={`${s.database}.${s.schema}`}>{s.database}.{s.schema}</option>)}
                  </select>
                </label>
              )}
              {schema?.error && <p role="alert" className="text-sm text-destructive">{schema.error}</p>}
              {!!schema?.missing?.length && <p className="text-xs text-muted-foreground">{mastered ? "In the MDM contract but not in Snowflake yet" : "Registered but not in Snowflake yet"}: {schema.missing.join(", ")}{mastered ? " (Reltio has not written them in this environment)" : " (load them with the Silver Pipeline first)"}.</p>}
              {schema && !schema.error && (
                <div className="max-h-72 overflow-y-auto rounded-xl border">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-muted/80 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                      <tr><th className="w-8 px-3 py-2" /><th className="px-3 py-2">Table</th><th className="px-3 py-2 text-right">Rows</th><th className="px-3 py-2">Updated</th></tr>
                    </thead>
                    <tbody>
                      {schema.tables.map((t) => (
                        <tr key={t.table} className="border-t">
                          <td className="px-3 py-1.5"><input type="checkbox" aria-label={`use ${t.table}`} checked={picked.includes(t.table)}
                                                            onChange={(e) => setPicked((p) => e.target.checked ? [...p, t.table] : p.filter((x) => x !== t.table))} /></td>
                          <td className="px-3 py-1.5 font-mono text-xs">{t.table}{t.suggested && <span className="ml-2 rounded bg-primary/10 px-1.5 py-0.5 font-sans text-[10px] font-medium text-primary">{!mastered ? "silver hub" : t.table.toUpperCase() === golden ? "golden record" : "related"}</span>}</td>
                          <td className="px-3 py-1.5 text-right tabular-nums text-xs">{t.row_count ?? "?"}</td>
                          <td className="px-3 py-1.5 text-xs text-muted-foreground">{t.last_altered?.slice(0, 16) ?? ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        </div>
      </Modal>
    </>
  );
}
