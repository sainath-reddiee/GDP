"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import { ArrowRight, Crown, Database, GitMerge, Layers, Loader2, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAccess } from "@/components/access";
import { domainMdm, domainMdmCoverage, saveDomainMdm, type DomainMdm, type MdmCoverage, type MdmHop } from "../actions";

const LAYER: Record<MdmHop["layer"], { label: string; cls: string; icon: typeof Database }> = {
  SILVER_PRE_MDM: { label: "Silver, pre-MDM", cls: "border-slate-400/40 bg-slate-500/5", icon: Database },
  MDM: { label: "MDM cross-reference", cls: "border-violet-500/40 bg-violet-500/5", icon: GitMerge },
  SILVER_POST_MDM: { label: "Silver, post-MDM (golden)", cls: "border-sky-500/40 bg-sky-500/5", icon: ShieldCheck },
  GOLD: { label: "Gold", cls: "border-amber-500/50 bg-amber-500/10", icon: Crown },
};

function Chain({ hops, note }: { hops: MdmHop[]; note?: string }) {
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-stretch gap-1.5">
        {hops.map((h, i) => {
          const L = LAYER[h.layer];
          return (
            <Fragment key={h.object}>
              {i > 0 && <span className="flex items-center text-muted-foreground" title={h.kind}><ArrowRight className="h-4 w-4" /></span>}
              <div className={cn("min-w-[11rem] flex-1 rounded-xl border px-3 py-2", L.cls)}>
                <p className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground"><L.icon className="h-3 w-3" />{L.label}</p>
                <p className="font-mono text-xs font-semibold">{h.object}</p>
                <p className="text-[11px] text-muted-foreground">on {h.key.join(" + ")}{h.cardinality ? ` · ${h.cardinality}` : ""}{h.skey ? ` · skey ${h.skey}` : ""}</p>
              </div>
            </Fragment>
          );
        })}
      </div>
      {note && <p className="text-[11px] text-muted-foreground">{note}</p>}
    </div>
  );
}

/** Where the domain sits in the medallion and how its records reach gold: a domain Reltio masters shows its chain from
 *  pre-MDM silver through the cross-reference to the gold dimension, the domains that consume it and a live match
 *  rate per source; a consumer shows the mastered dimensions it resolves to; any other domain can be linked. */
export function MdmPanel({ domainId }: { domainId: string }) {
  const { canAct } = useAccess();
  const may = canAct("DOMAIN.EDIT");
  const [data, setData] = useState<DomainMdm | null>(null);
  const [error, setError] = useState("");
  const [cov, setCov] = useState<MdmCoverage | null>(null);
  const [covBusy, setCovBusy] = useState(false);
  const [covError, setCovError] = useState("");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<{ domain: string; silver_column: string; gold_column: string }[]>([]);
  const [saving, setSaving] = useState(false);
  const load = () => void domainMdm(domainId).then((r) => (r.ok ? setData(r.data) : setError(r.error)));
  useEffect(load, [domainId]);   // eslint-disable-line react-hooks/exhaustive-deps

  if (error) return <section className="surface p-4 text-xs text-destructive" role="alert">{error}</section>;
  if (!data) return <section className="surface p-4 text-xs text-muted-foreground"><Loader2 className="mr-1 inline h-3 w-3 animate-spin" />Reading the MDM layer…</section>;
  const name = data.domain.domain_name;
  const check = async () => {
    setCovBusy(true); setCovError("");
    const r = await domainMdmCoverage(domainId).catch(() => null);
    setCovBusy(false);
    if (!r?.ok) setCovError(r && !r.ok ? r.error : "Could not measure the match rate."); else setCov(r.data);
  };
  const startEdit = () => {
    setDraft((data.consumes ?? []).map((c) => ({ domain: c.domain, silver_column: c.silver_column, gold_column: c.gold_column })));
    setEditing(true);
  };
  const save = async () => {
    setSaving(true);
    const r = await saveDomainMdm(domainId, draft.filter((d) => d.domain)).catch(() => null);
    setSaving(false);
    if (!r?.ok) { setError(r && !r.ok ? r.error : "Could not save."); return; }
    setData(r.data); setEditing(false);
  };
  const free = data.mastered_domains.filter((m) => m.domain_name !== name && !draft.some((d) => d.domain === m.domain_name));

  return (
    <section className="surface space-y-3 p-4" data-testid="domain-mdm">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="flex items-center gap-2 text-sm font-semibold"><Layers className="h-4 w-4 text-primary" />Medallion and MDM</h3>
          <p className="text-xs text-muted-foreground">
            {data.role === "MASTERED" ? <>Reltio masters {name}: every source record in pre-MDM silver is matched to one mastered id through the cross-reference, and gold holds one row per mastered id.</>
              : data.role === "CONSUMER" ? <>{name} records carry pre-MDM keys of mastered domains; gold resolves them to the mastered dimensions through the full cross-reference (never the delta).</>
                : <>{name} is not linked to MDM. If its records refer to companies or properties, mark it as a consumer so Modeling adds the pre-MDM key lookups and gold resolves them to the mastered dimensions.</>}
          </p>
        </div>
        {data.rules > 0 && <Link href={`/knowledge?tab=items&domain_id=${encodeURIComponent(domainId)}&type=EXCEPTION`} className="text-xs text-primary hover:underline">{data.rules} MDM rules</Link>}
        {may && data.role !== "MASTERED" && !editing && (
          <button type="button" onClick={startEdit} className="inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-medium hover:bg-muted">
            {data.role === "CONSUMER" ? "Change" : "Link to MDM"}</button>
        )}
      </div>

      {data.role === "MASTERED" && data.chain && (
        <>
          <Chain hops={data.chain} note="Match on the source key and the upper-cased source-system name; ids collide across sources. Unresolved records get -1." />
          {!!data.consumers?.length && (
            <p className="text-xs"><span className="text-muted-foreground">Consumed by </span>
              {data.consumers.map((c, i) => <Fragment key={c.domain_id}>{i > 0 && ", "}<Link href={`/domains/${c.domain_id}?tab=lineage`} className="font-medium text-primary hover:underline">{c.domain_name}</Link>
                <span className="text-muted-foreground"> ({c.via.map((v) => `${v.silver_column} to ${v.gold_column}`).join(", ")})</span></Fragment>)}</p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={() => void check()} disabled={covBusy}
                    className="inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-medium hover:bg-muted disabled:opacity-50">
              {covBusy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Check match rate on {data.env}</button>
            <span className="text-[11px] text-muted-foreground">Read only: active pre-MDM records per source and how many have an active cross-reference row.</span>
          </div>
          {covError && <p role="alert" className="text-xs text-destructive">{covError}</p>}
          {cov && (
            <div className="overflow-x-auto rounded-xl border">
              <table className="w-full text-xs">
                <thead className="bg-muted/50 text-left text-[10px] uppercase tracking-wide text-muted-foreground">
                  <tr><th className="px-3 py-2">Source system</th><th className="px-3 py-2 text-right">Records</th><th className="px-3 py-2 text-right">Matched</th><th className="w-48 px-3 py-2">Match rate</th></tr>
                </thead>
                <tbody>
                  {cov.sources.map((s) => (
                    <tr key={s.source_system} className="border-t">
                      <td className="px-3 py-1.5 font-mono">{s.source_system}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{Number(s.records).toLocaleString()}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{Number(s.matched).toLocaleString()}</td>
                      <td className="px-3 py-1.5">
                        <span className="flex items-center gap-2"><span className="block h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                          <span className={cn("block h-full rounded-full", (s.matched_pct ?? 0) >= 97 ? "bg-emerald-500" : (s.matched_pct ?? 0) >= 80 ? "bg-amber-500" : "bg-rose-500")}
                                style={{ width: `${Math.min(100, s.matched_pct ?? 0)}%` }} /></span>
                          <span className="w-12 text-right tabular-nums">{s.matched_pct == null ? "—" : `${s.matched_pct}%`}</span></span>
                      </td>
                    </tr>
                  ))}
                  <tr className="border-t font-medium"><td className="px-3 py-1.5">All sources</td><td className="px-3 py-1.5 text-right tabular-nums">{cov.records.toLocaleString()}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">{cov.matched.toLocaleString()}</td><td className="px-3 py-1.5 text-right tabular-nums">{cov.matched_pct == null ? "—" : `${cov.matched_pct}%`}</td></tr>
                </tbody>
              </table>
            </div>
          )}
          {data.sql && (
            <details className="text-xs">
              <summary className="cursor-pointer text-muted-foreground">Resolution SQL (source record to mastered gold key)</summary>
              <pre className="mt-1 max-h-72 overflow-auto rounded-lg bg-muted/50 p-3 font-mono text-[11px]">{data.sql.resolution}</pre>
            </details>
          )}
        </>
      )}

      {data.role === "CONSUMER" && !editing && (data.consumes ?? []).map((c) => (
        <div key={c.domain} className="space-y-1">
          <p className="text-xs"><span className="font-mono font-medium">{c.silver_column}</span> resolves to <span className="font-mono font-medium">{c.gold_column}</span> through{" "}
            {c.domain_id ? <Link href={`/domains/${c.domain_id}?tab=lineage`} className="font-medium text-primary hover:underline">{c.domain}</Link> : <span className="text-amber-600">{c.domain} (not a mastered domain here)</span>}</p>
          {c.chain && <Chain hops={c.chain} />}
        </div>
      ))}

      {editing && (
        <div className="space-y-2 rounded-xl border p-3 text-xs">
          <p className="font-medium">Mastered domains {name} refers to</p>
          {draft.map((d, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <span className="w-28 font-medium">{d.domain}</span>
              <label className="flex items-center gap-1">silver key<input value={d.silver_column} onChange={(e) => setDraft(draft.map((x, j) => j === i ? { ...x, silver_column: e.target.value.toUpperCase() } : x))}
                className="h-8 w-48 rounded-lg border bg-background px-2 font-mono" aria-label={`${d.domain} silver key`} /></label>
              <label className="flex items-center gap-1">gold key<input value={d.gold_column} onChange={(e) => setDraft(draft.map((x, j) => j === i ? { ...x, gold_column: e.target.value.toUpperCase() } : x))}
                className="h-8 w-48 rounded-lg border bg-background px-2 font-mono" aria-label={`${d.domain} gold key`} /></label>
              <button type="button" aria-label={`Remove ${d.domain}`} onClick={() => setDraft(draft.filter((_, j) => j !== i))} className="rounded p-1 text-muted-foreground hover:text-destructive"><Trash2 className="h-3.5 w-3.5" /></button>
            </div>
          ))}
          <div className="flex flex-wrap gap-1.5">
            {free.map((m) => (
              <button key={m.domain_id} type="button" onClick={() => setDraft([...draft, { domain: m.domain_name, silver_column: m.silver_column ?? "", gold_column: m.gold_column ?? "" }])}
                      className="inline-flex items-center gap-1 rounded-md border px-2 py-1 hover:bg-muted"><Plus className="h-3 w-3" />{m.domain_name}</button>
            ))}
            {!data.mastered_domains.length && <span className="text-muted-foreground">No mastered domains are registered.</span>}
          </div>
          <div className="flex gap-2 pt-1">
            <button type="button" onClick={() => void save()} disabled={saving} className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 font-medium text-primary-foreground disabled:opacity-50">
              {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}{draft.length ? "Save" : "Save: not linked"}</button>
            <button type="button" onClick={() => setEditing(false)} className="h-8 rounded-lg px-2 hover:bg-muted">Cancel</button>
          </div>
        </div>
      )}
    </section>
  );
}
