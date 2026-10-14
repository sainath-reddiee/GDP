"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { BadgeCheck, Bot, Check, FileCode2, GitBranch, Loader2, ShieldAlert, Sparkles, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { prettyType } from "../../knowledge/knowledge-ui";
import { domainProvenance, type DomainProvenance, type ProvenanceSource } from "../actions";

const TONE: Record<string, string> = {
  pack: "#6366f1", contract: "#8b5cf6", people: "#0ea5e9", copilot: "#a855f7", ai: "#d946ef", mapping: "#14b8a6",
  dbt: "#f97316", quality: "#22c55e", profiling: "#eab308", modeling: "#3b82f6", qa: "#ef4444", code: "#64748b",
  confluence: "#2563eb", document: "#0891b2", other: "#94a3b8",
};
const copilot = (prompt: string) => `/dashboard/copilot?q=${encodeURIComponent(prompt)}`;

/** Loads a domain's provenance once (shared by the Overview card and the Lineage tab). */
export function useProvenance(domainId: string) {
  const [data, setData] = useState<DomainProvenance | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void domainProvenance(domainId).then((r) => {
      if (!live) return;
      if (r.ok) setData(r.data); else setError(r.error);
    }).catch(() => live && setError("The lineage could not be loaded."));
    return () => { live = false; };
  }, [domainId]);
  return { data, error };
}

function Ring({ value, size = 56 }: { value: number; size?: number }) {
  const r = size / 2 - 5, c = 2 * Math.PI * r;
  const tone = value >= 80 ? "stroke-emerald-500" : value >= 50 ? "stroke-amber-500" : "stroke-rose-500";
  return (
    <svg width={size} height={size} role="img" aria-label={`${value} out of 100`}>
      <circle cx={size / 2} cy={size / 2} r={r} className="fill-none stroke-muted" strokeWidth="6" />
      <circle cx={size / 2} cy={size / 2} r={r} className={cn("fill-none", tone)} strokeWidth="6" strokeLinecap="round"
              strokeDasharray={`${(value / 100) * c} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle" className="fill-foreground text-[13px] font-semibold">{value}</text>
    </svg>
  );
}

/** The Overview card: the score and where the domain's knowledge comes from, with a way into the Lineage tab. */
export function BuiltFromCard({ domainId, onOpen }: { domainId: string; onOpen: () => void }) {
  const { data, error } = useProvenance(domainId);
  return (
    <section className="surface space-y-3 p-4" data-testid="built-from-card">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold">How this domain is built</h3>
          <p className="text-xs text-muted-foreground">Where its knowledge comes from, and how complete it is.</p>
        </div>
        <button type="button" onClick={onOpen} className="text-xs text-primary hover:underline">Open lineage</button>
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
      {!data && !error && <p className="text-xs text-muted-foreground"><Loader2 className="mr-1 inline h-3 w-3 animate-spin" />Tracing its sources…</p>}
      {data && (
        <>
          <div className="flex items-center gap-3">
            <Ring value={data.coverage.score} />
            <dl className="grid flex-1 grid-cols-2 gap-x-3 gap-y-0.5 text-xs">
              <dt className="text-muted-foreground">Completeness</dt><dd className="text-right font-medium">{data.coverage.tables_score}</dd>
              <dt className="text-muted-foreground">Trust</dt><dd className="text-right font-medium">{data.coverage.trust_score}</dd>
              <dt className="text-muted-foreground">Verified</dt><dd className="text-right">{data.coverage.knowledge.verified} of {data.coverage.knowledge.items}</dd>
              <dt className="text-muted-foreground">Gaps</dt><dd className="text-right">{data.coverage.actions.length}</dd>
            </dl>
          </div>
          <ul className="flex flex-wrap gap-1.5">
            {data.provenance.upstream.map((u) => (
              <li key={u.id} className="rounded-full px-2 py-0.5 text-[11px] text-white" style={{ background: TONE.contract }} title={u.file}>{u.label}</li>
            ))}
            {data.provenance.sources.map((s) => (
              <li key={s.id} className="rounded-full px-2 py-0.5 text-[11px] text-white" style={{ background: TONE[s.id] ?? TONE.other }}>
                {s.label} · {s.count}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

/** The source map: what the domain was built from (left) feeding what it holds (right). */
function SourceMap({ data, selected, onSelect }: { data: DomainProvenance["provenance"]; selected: string | null; onSelect: (id: string | null) => void }) {
  const left = [...data.upstream.map((u) => ({ id: u.id, label: u.label, sub: u.file.split("/").pop() ?? u.file, count: null as number | null })),
                ...data.sources.map((s) => ({ id: s.id, label: s.label, sub: `${s.count} ${s.id === "code" ? (s.count === 1 ? "match" : "matches") : (s.count === 1 ? "item" : "items")}`, count: s.count }))];
  const right = data.parts;
  const ROW = 52, W = 760, NODE_W = 210, top = 20;
  const height = Math.max(left.length, right.length) * ROW + top * 2;
  const ly = (i: number) => top + i * ROW + ROW / 2;
  const ry = (i: number) => top + i * ROW * (left.length > right.length ? left.length / Math.max(right.length, 1) : 1) + ROW / 2;
  const max = Math.max(1, ...data.edges.map((e) => e.count));
  const li = (id: string) => left.findIndex((n) => n.id === id);
  const ri = (id: string) => right.findIndex((n) => n.id === id);
  const links = [
    ...data.upstream.map((u) => ({ from: u.id, to: u.feeds, count: max / 3, toLeft: true })),
    ...data.edges.map((e) => ({ from: e.source, to: e.part, count: e.count, toLeft: false })),
  ];
  return (
    <div className="overflow-x-auto rounded-2xl border bg-card" data-testid="source-map">
      <svg viewBox={`0 0 ${W} ${height}`} className="min-w-[640px]" role="img" aria-label="Where this domain's knowledge comes from">
        {links.map((l, i) => {
          const a = li(l.from);
          if (a < 0) return null;
          const hot = !selected || selected === l.from || selected === l.to;
          const width = 2 + 14 * (l.count / max);
          if (l.toLeft) {   // the contract feeds the pack, on the left side
            const b = li(l.to);
            if (b < 0) return null;
            const x = 12, y1 = ly(a), y2 = ly(b);
            return <path key={i} d={`M${x},${y1} C${x - 10},${y1} ${x - 10},${y2} ${x},${y2}`} fill="none" stroke={TONE.contract} strokeWidth={2} opacity={hot ? 0.8 : 0.15} />;
          }
          const b = ri(l.to);
          if (b < 0) return null;
          const x1 = NODE_W + 12, x2 = W - NODE_W - 12, y1 = ly(a), y2 = ry(b);
          return (
            <path key={i} d={`M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`} fill="none"
                  stroke={TONE[l.from] ?? TONE.other} strokeWidth={width} strokeLinecap="round" opacity={hot ? 0.55 : 0.08}>
              <title>{`${left[a].label} → ${right[b].label}: ${Math.round(l.count)}`}</title>
            </path>
          );
        })}
        {left.map((n, i) => (
          <g key={n.id} transform={`translate(12,${ly(i) - 20})`} onClick={() => onSelect(selected === n.id ? null : n.id)} className="cursor-pointer"
             role="button" aria-label={`${n.label}: ${n.sub}`} aria-pressed={selected === n.id}>
            <rect width={NODE_W} height={40} rx={10} className={cn("fill-card", selected === n.id ? "stroke-primary" : "stroke-border")} strokeWidth={selected === n.id ? 2 : 1} />
            <rect width={6} height={40} rx={3} fill={TONE[n.id] ?? TONE.other} />
            <text x={16} y={17} className="fill-foreground text-[12px] font-semibold">{n.label}</text>
            <text x={16} y={31} className="fill-muted-foreground text-[10px]">{n.sub.length > 34 ? `${n.sub.slice(0, 33)}…` : n.sub}</text>
          </g>
        ))}
        {right.map((p, i) => (
          <g key={p.id} transform={`translate(${W - NODE_W - 12},${ry(i) - 20})`}>
            <rect width={NODE_W} height={40} rx={10} className="fill-muted/40 stroke-border" />
            <text x={14} y={17} className="fill-foreground text-[12px] font-semibold">{p.label}</text>
            <text x={14} y={31} className="fill-muted-foreground text-[10px]">{p.count} {p.id === "targets" ? (p.count === 1 ? "table" : "tables") : (p.count === 1 ? "item" : "items")}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}

function SourcePanel({ source, onClose, domainId }: { source: ProvenanceSource; onClose: () => void; domainId?: string }) {
  return (
    <section className="surface space-y-3 p-4" aria-label={`${source.label} details`}>
      <div className="flex items-start gap-2">
        <span className="mt-1 h-3 w-3 rounded-full" style={{ background: TONE[source.id] ?? TONE.other }} />
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold">{source.label}</h3>
          <p className="text-xs text-muted-foreground">{source.meaning}</p>
        </div>
        <button type="button" onClick={onClose} aria-label="Close" className="rounded p-1 text-muted-foreground hover:bg-muted"><X className="h-3.5 w-3.5" /></button>
      </div>
      <div className="flex flex-wrap gap-3 text-xs">
        <span><b>{source.count}</b> {source.id === "code" ? "code matches" : "items"}</span>
        {source.id !== "code" && <><span><b>{source.verified}</b> verified by a person</span><span><b>{source.used}</b> used in 90 days</span>
          {!!source.draft && <span className="text-amber-600"><b>{source.draft}</b> waiting for review</span>}</>}
      </div>
      {!!source.files?.length && <p className="text-xs"><FileCode2 className="mr-1 inline h-3.5 w-3.5" /><span className="font-mono">{source.files.join(", ")}</span></p>}
      {!!source.repos?.length && <ul className="text-xs">{source.repos.map((r) => <li key={r.name}><GitBranch className="mr-1 inline h-3.5 w-3.5" />{r.name}: {r.matched} files or models name this domain&apos;s tables</li>)}</ul>}
      {!!source.people.length && <p className="text-xs text-muted-foreground">By {source.people.join(", ")}</p>}
      {!!Object.keys(source.types).length && (
        <div className="flex flex-wrap gap-1">{Object.entries(source.types).map(([t, n]) => <span key={t} className="rounded bg-muted px-1.5 py-0.5 text-[11px]">{prettyType(t)} · {n}</span>)}</div>
      )}
      {!!source.items.length && (
        <ul className="divide-y divide-border/60 text-xs">
          {source.items.map((i) => (
            <li key={i.knowledge_id} className="flex items-center gap-2 py-1.5">
              {i.verified ? <BadgeCheck className="h-3.5 w-3.5 shrink-0 text-emerald-600" aria-label="verified" /> : <span className="w-3.5" />}
              <Link href={`/knowledge?${new URLSearchParams({ tab: "items", q: i.title, ...(domainId ? { domain_id: domainId } : {}) })}`}
                    className="min-w-0 flex-1 truncate hover:text-primary" title={i.ref ?? undefined}>{i.title}</Link>
              <span className="text-muted-foreground">{prettyType(i.type)}</span>
              {!!i.uses && <span className="text-muted-foreground">{i.uses} uses</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The Lineage tab: the source map, the coverage scorecard with gaps to close, and the build timeline. */
export function LineageTab({ domainId, domainName }: { domainId: string; domainName: string }) {
  const { data, error } = useProvenance(domainId);
  const [selected, setSelected] = useState<string | null>(null);
  const source = useMemo(() => data?.provenance.sources.find((s) => s.id === selected) ?? null, [data, selected]);
  const upstream = data?.provenance.upstream.find((u) => u.id === selected);
  if (error) return <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">{error}</p>;
  if (!data) return <p className="py-8 text-center text-sm text-muted-foreground"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />Tracing where {domainName} comes from…</p>;
  const cov = data.coverage;
  const CHECK_IDS = cov.checks.map((c) => c.id);
  return (
    <div className="space-y-4" data-testid="lineage-tab">
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-2">
          <div className="flex items-baseline gap-2">
            <h3 className="text-sm font-semibold">Where {domainName} comes from</h3>
            <p className="text-xs text-muted-foreground">Sources on the left feed what the domain holds on the right; line width is the number of items. Click a source to see its items.</p>
          </div>
          <SourceMap data={data.provenance} selected={selected} onSelect={setSelected} />
        </div>
        <aside className="space-y-4">
          {source && <SourcePanel source={source} domainId={domainId} onClose={() => setSelected(null)} />}
          {upstream && (
            <section className="surface space-y-1.5 p-4">
              <h3 className="text-sm font-semibold">{upstream.label}</h3>
              <p className="text-xs text-muted-foreground">{upstream.meaning}</p>
              <p className="font-mono text-xs">{upstream.file}</p>
            </section>
          )}
          <section className="surface space-y-3 p-4" aria-label="Coverage">
            <div className="flex items-center gap-3">
              <Ring value={cov.score} size={64} />
              <div className="text-xs">
                <p className="text-sm font-semibold">Domain strength</p>
                <p className="text-muted-foreground">Completeness {cov.tables_score} · Trust {cov.trust_score}</p>
                <p className="text-muted-foreground">{cov.knowledge.verified} of {cov.knowledge.items} verified · {cov.knowledge.used_90d} used in 90 days{cov.knowledge.awaiting_review ? ` · ${cov.knowledge.awaiting_review} waiting for review` : ""}</p>
              </div>
            </div>
            <ul className="space-y-1.5">
              {cov.checks.map((c) => (
                <li key={c.id} className="text-xs">
                  <div className="flex"><span>{c.label}</span><span className="ml-auto tabular-nums text-muted-foreground">{c.done} of {c.of}</span></div>
                  <div className="mt-0.5 h-1.5 rounded-full bg-muted"><div className="h-1.5 rounded-full bg-primary" style={{ width: `${c.of ? (100 * c.done) / c.of : 0}%` }} /></div>
                </li>
              ))}
            </ul>
          </section>
        </aside>
      </div>

      <section className="surface overflow-x-auto p-0" aria-label="Target tables coverage">
        <table className="w-full text-xs">
          <thead className="bg-muted/50 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
            <tr><th className="px-3 py-2">Target table</th>{cov.checks.map((c) => <th key={c.id} className="px-2 py-2 text-center">{c.label}</th>)}<th className="px-3 py-2 text-right">Score</th></tr>
          </thead>
          <tbody>
            {cov.targets.map((t) => (
              <tr key={t.fqn} className="border-t">
                <td className="px-3 py-1.5 font-mono font-medium">{t.table}</td>
                {CHECK_IDS.map((id) => (
                  <td key={id} className="px-2 py-1.5 text-center">
                    {t.checks[id] ? <Check className="mx-auto h-3.5 w-3.5 text-emerald-600" aria-label="done" />
                      : <X className="mx-auto h-3.5 w-3.5 text-rose-500" aria-label={id === "glossary" && t.glossary_missing.length ? `missing for ${t.glossary_missing.join(", ")}` : "missing"} />}
                  </td>
                ))}
                <td className="px-3 py-1.5 text-right font-semibold tabular-nums">{t.score}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="surface space-y-2 p-4" aria-label="Gaps">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold"><ShieldAlert className="h-4 w-4 text-amber-500" /> Gaps to close</h3>
          <p className="text-xs text-muted-foreground">Each one opens the Copilot with the request ready; it drafts from the code, mappings and profiles, and you review before anything is saved.</p>
          {!cov.actions.length ? <p className="text-xs text-muted-foreground"><Sparkles className="mr-1 inline h-3.5 w-3.5" />No gaps found.</p> : (
            <ul className="divide-y divide-border/60">
              {cov.actions.slice(0, 15).map((a, i) => (
                <li key={i} className="flex items-center gap-2 py-1.5 text-xs">
                  <span className="font-mono">{a.table}</span><span className="text-muted-foreground">{a.label}</span>
                  <Link href={copilot(a.prompt)} className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 font-medium text-primary hover:bg-primary/10" aria-label={`Fix ${a.label} for ${a.table} with the Copilot`}>
                    <Bot className="h-3.5 w-3.5" /> Fix with the Copilot
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="surface space-y-2 p-4" aria-label="Build timeline">
          <h3 className="text-sm font-semibold">Build timeline</h3>
          <ol className="relative space-y-2.5 before:absolute before:left-[5px] before:top-1 before:h-[calc(100%-8px)] before:w-px before:bg-border">
            {data.provenance.timeline.map((e, i) => (
              <li key={i} className="relative pl-5 text-xs">
                <span className={cn("absolute left-0 top-1 h-[11px] w-[11px] rounded-full border-2 border-card", e.kind === "version" ? "bg-primary" : "bg-emerald-500")} />
                <p className="font-medium">{e.label}</p>
                <p className="text-muted-foreground">{e.at}{e.by ? ` · ${e.by}` : ""}{e.detail ? ` · ${e.detail}` : ""}</p>
              </li>
            ))}
          </ol>
        </section>
      </div>
    </div>
  );
}
