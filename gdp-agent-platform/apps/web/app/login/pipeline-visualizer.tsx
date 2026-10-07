"use client";

import { useEffect, useState, type ReactNode } from "react";
import {
  ArrowRight, Boxes, Building2, Check, CircleDollarSign, Database, FileJson, GitBranch, Layers, ScanSearch,
  ShieldCheck, Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";

type Column = { name: string; type: string; nulls: number; distinct: number };
type SourceTable = { name: string; rows: string; columns: Column[] };
type Domain = {
  key: string;
  label: string;
  icon: typeof Building2;
  source: string;
  tables: [SourceTable, SourceTable];
  metrics: string[];
  join: { left: string; right: string; cardinality: string; confidence: number };
  pack: string;
  signals: string[];
  confidence: number;
  target: string;
  layers: [string, string, string];
  checks: string[];
  model: string;
  sql: string;
};

const DOMAINS: Domain[] = [
  {
    key: "property",
    label: "Property",
    icon: Building2,
    source: "BRONZE.LIGHTBOX",
    tables: [
      { name: "BUILDINGS", rows: "1.2M", columns: [
        { name: "BUILDING_LID", type: "VARCHAR", nulls: 0, distinct: 100 },
        { name: "PARCEL_LID", type: "VARCHAR", nulls: 0.4, distinct: 71 },
        { name: "YEAR_BUILT", type: "NUMBER", nulls: 6.2, distinct: 9 },
        { name: "AREA_SQFT", type: "FLOAT", nulls: 1.1, distinct: 64 },
      ] },
      { name: "PARCELS", rows: "860K", columns: [
        { name: "PARCEL_LID", type: "VARCHAR", nulls: 0, distinct: 100 },
        { name: "APN", type: "VARCHAR", nulls: 0.2, distinct: 99.8 },
        { name: "LAND_USE", type: "VARCHAR", nulls: 3.4, distinct: 1 },
        { name: "ACRES", type: "FLOAT", nulls: 2.0, distinct: 48 },
      ] },
    ],
    metrics: ["0% nulls on BUILDING_LID", "100% distinct key", "APN shape 99% match"],
    join: { left: "BUILDINGS.PARCEL_LID", right: "PARCELS.PARCEL_LID", cardinality: "N:1", confidence: 98 },
    pack: "domain/property/domain_pack.json",
    signals: ["BUILDING", "PARCEL", "APN", "SQFT"],
    confidence: 70,
    target: "PROPERTY_CORE",
    layers: ["lightbox.buildings", "silver_property_core", "property_core + _hist"],
    checks: ["duplicate_count(source_unique_id) = 0", "missing_count(apn) = 0", "invalid_count(year_built) = 0", "row_count between 1.08M and 1.32M"],
    model: "lightbox_property_core.sql",
    sql: `{{ config(materialized='ephemeral') }}
with o as (
    select * from {{ source('lightbox', 'buildings') }}
)
select
    {{ m_property_core_hkey() }} as property_core_hkey,
    nullif(trim(o.building_lid), '') as source_unique_id,
    o.year_built::number(4,0) as year_built,
    p.apn as parcel_number
from o
left join {{ source('lightbox', 'parcels') }} as p
    on p.parcel_lid = o.parcel_lid`,
  },
  {
    key: "company",
    label: "Company / CRM",
    icon: Boxes,
    source: "BRONZE.MTA",
    tables: [
      { name: "PS_CUSTOMER", rows: "410K", columns: [
        { name: "CUST_ID", type: "VARCHAR", nulls: 0, distinct: 100 },
        { name: "NAME1", type: "VARCHAR", nulls: 0.1, distinct: 97 },
        { name: "DUNS", type: "VARCHAR", nulls: 12.5, distinct: 86 },
        { name: "COUNTRY", type: "VARCHAR", nulls: 0.3, distinct: 2 },
      ] },
      { name: "PS_CUST_ADDRESS", rows: "655K", columns: [
        { name: "CUST_ID", type: "VARCHAR", nulls: 0, distinct: 63 },
        { name: "ADDRESS1", type: "VARCHAR", nulls: 1.8, distinct: 92 },
        { name: "CITY", type: "VARCHAR", nulls: 0.9, distinct: 11 },
        { name: "POSTAL", type: "VARCHAR", nulls: 2.6, distinct: 34 },
      ] },
    ],
    metrics: ["0% nulls on CUST_ID", "99.9% distinct key", "DUNS 9-digit shape"],
    join: { left: "PS_CUST_ADDRESS.CUST_ID", right: "PS_CUSTOMER.CUST_ID", cardinality: "N:1", confidence: 97 },
    pack: "domain/company/domain_pack.json",
    signals: ["DUNS", "NAME1", "CUSTOMER", "COUNTRY"],
    confidence: 63,
    target: "COMPANY_CORE",
    layers: ["mta.ps_customer", "silver_company_core", "company_core + _hist"],
    checks: ["duplicate_count(source_unique_id) = 0", "missing_count(company_name) = 0", "valid regex on duns_number", "values in (ref_country_skey) exist"],
    model: "mta_company_core.sql",
    sql: `{{ config(materialized='ephemeral') }}
with o as (
    select * from {{ source('mta', 'ps_customer') }}
)
select
    {{ m_company_core_hkey() }} as company_core_hkey,
    upper(trim(o.name1)) || '||' || o.country as source_unique_id,
    nullif(trim(o.name1), '') as company_name,
    nullif(trim(o.duns), '') as duns_number
from o`,
  },
  {
    key: "opportunity",
    label: "Opportunity",
    icon: CircleDollarSign,
    source: "BRONZE.SALESFORCE",
    tables: [
      { name: "OPPORTUNITY", rows: "2.4M", columns: [
        { name: "ID", type: "VARCHAR", nulls: 0, distinct: 100 },
        { name: "ACCOUNTID", type: "VARCHAR", nulls: 0.6, distinct: 18 },
        { name: "STAGENAME", type: "VARCHAR", nulls: 0, distinct: 1 },
        { name: "CLOSEDATE", type: "DATE", nulls: 4.1, distinct: 21 },
      ] },
      { name: "ACCOUNT", rows: "390K", columns: [
        { name: "ID", type: "VARCHAR", nulls: 0, distinct: 100 },
        { name: "NAME", type: "VARCHAR", nulls: 0.2, distinct: 98 },
        { name: "INDUSTRY", type: "VARCHAR", nulls: 7.7, distinct: 1 },
        { name: "BILLINGCOUNTRY", type: "VARCHAR", nulls: 3.0, distinct: 1 },
      ] },
    ],
    metrics: ["0% nulls on ID", "100% distinct key", "9 accepted stage codes"],
    join: { left: "OPPORTUNITY.ACCOUNTID", right: "ACCOUNT.ID", cardinality: "N:1", confidence: 96 },
    pack: "domain/opportunity/domain_pack.json",
    signals: ["STAGENAME", "CLOSEDATE", "PIPELINE", "__C"],
    confidence: 57,
    target: "OPPORTUNITY_CORE",
    layers: ["salesforce.opportunity", "silver_opportunity_core", "opportunity_core + _hist"],
    checks: ["duplicate_count(source_unique_id) = 0", "invalid_count(stage_name) = 0", "freshness(close_date) < 1d", "missing_percent(account_name) < 2%"],
    model: "salesforce_opportunity_core.sql",
    sql: `{{ config(materialized='ephemeral') }}
with o as (
    select * from {{ source('salesforce', 'opportunity') }}
)
select
    {{ m_opportunity_core_hkey() }} as opportunity_core_hkey,
    o.id as source_unique_id,
    nullif(trim(o.stagename), '') as stage_name,
    a.name as account_name
from o
left join {{ source('salesforce', 'account') }} as a
    on a.id = o.accountid`,
  },
];

const STAGES = [
  { title: "Profile in place", hint: "Source inspection", icon: ScanSearch },
  { title: "Semantic model", hint: "Domain and joins", icon: Sparkles },
  { title: "Build and gate", hint: "dbt and quality", icon: Layers },
] as const;

const STAGE_MS = 4800;

/** One log line per stage; lines up to the current stage are shown, newest last. */
function runLog(d: Domain): { at: string; text: string; tone: string }[] {
  const cols = d.tables.reduce((n, t) => n + t.columns.length, 0);
  return [
    { at: "00:02", text: `profiled ${d.tables.length} tables, ${cols} columns in place · 0 bytes copied`, tone: "text-cyan-300" },
    { at: "00:05", text: `${d.label.toLowerCase()} domain matched · join ${d.join.cardinality} at ${d.join.confidence}% confidence`, tone: "text-violet-300" },
    { at: "00:09", text: `generated ${d.model} · ${d.checks.length} quality checks ready for review`, tone: "text-emerald-300" },
  ];
}
const SQL_TOKEN = /(\{\{.*?\}\}|'[^']*'|--.*$|\b(?:select|from|with|as|left|join|on|where|nullif|trim|upper|config|materialized)\b|::[a-z_]+(?:\(\d+(?:,\d+)?\))?)/gi;

/** Minimal deterministic SQL/Jinja highlighter (same output on server and client). */
function highlight(line: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of Array.from(line.matchAll(SQL_TOKEN))) {
    const at = m.index ?? 0;
    if (at > last) out.push(line.slice(last, at));
    const t = m[0];
    const cls = t.startsWith("{{") ? "text-amber-300"
      : t.startsWith("'") ? "text-emerald-300"
      : t.startsWith("--") ? "text-slate-500"
      : t.startsWith("::") ? "text-cyan-300"
      : "text-violet-300";
    out.push(<span key={`${at}-${t}`} className={cls}>{t}</span>);
    last = at + t.length;
  }
  if (last < line.length) out.push(line.slice(last));
  return out;
}

function Bar({ value, tone }: { value: number; tone: string }) {
  return (
    <span className="block h-1 w-14 overflow-hidden rounded-full bg-slate-800">
      <span className={cn("block h-full rounded-full", tone)} style={{ width: `${Math.max(3, Math.min(100, value))}%` }} />
    </span>
  );
}

function TableCard({ table, scanning }: { table: SourceTable; scanning: boolean }) {
  return (
    <div className="relative overflow-hidden rounded-xl border border-slate-800/80 bg-slate-950/70">
      <div className="flex items-center gap-2 border-b border-slate-800/80 px-3 py-2">
        <Database className="h-3.5 w-3.5 text-cyan-400" />
        <span className="font-mono text-xs font-semibold text-slate-100">{table.name}</span>
        <span className="ml-auto font-mono text-[10px] text-slate-500">{table.rows} rows</span>
      </div>
      <ul className="divide-y divide-slate-800/60">
        {table.columns.map((c) => (
          <li key={c.name} className="grid grid-cols-[1fr_auto_auto] items-center gap-3 px-3 py-1.5">
            <span className="truncate font-mono text-[11px] text-slate-300">
              {c.name} <span className="text-slate-600">{c.type}</span>
            </span>
            <span className="flex items-center gap-1.5" title={`${c.nulls}% nulls`}>
              <Bar value={100 - c.nulls} tone="bg-emerald-400/80" />
            </span>
            <span className="flex items-center gap-1.5" title={`${c.distinct}% distinct`}>
              <Bar value={c.distinct} tone="bg-cyan-400/80" />
            </span>
          </li>
        ))}
      </ul>
      {scanning && (
        <span aria-hidden className="pointer-events-none absolute inset-y-0 left-0 w-1/3 animate-scan-ray bg-gradient-to-r from-transparent via-cyan-400/15 to-transparent">
          <span className="absolute inset-y-0 right-0 w-px bg-cyan-300/70 shadow-[0_0_12px_2px_rgba(34,211,238,0.6)]" />
        </span>
      )}
    </div>
  );
}

function ProfileStage({ d }: { d: Domain }) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-400">
        Tables are profiled where they live in <span className="font-mono text-slate-200">{d.source}</span>. Nothing is copied;
        the profile is staged once and reused by every run.
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {d.tables.map((t) => <TableCard key={t.name} table={t} scanning />)}
      </div>
      <div className="flex flex-wrap gap-2">
        {d.metrics.map((m, i) => (
          <span key={m} style={{ animationDelay: `${200 + i * 140}ms` }}
                className="animate-stage-in inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1 font-mono text-[11px] text-emerald-300">
            <Check className="h-3 w-3" /> {m}
          </span>
        ))}
        <span className="inline-flex items-center gap-3 px-1 text-[10px] text-slate-500">
          <span className="flex items-center gap-1"><span className="h-1.5 w-3 rounded-full bg-emerald-400/80" /> filled</span>
          <span className="flex items-center gap-1"><span className="h-1.5 w-3 rounded-full bg-cyan-400/80" /> distinct</span>
        </span>
      </div>
    </div>
  );
}

function SemanticStage({ d }: { d: Domain }) {
  return (
    <div className="space-y-4">
      <div className="relative rounded-xl border border-violet-500/30 bg-violet-500/[0.07] p-3">
        <div className="flex flex-wrap items-center gap-2">
          <FileJson className="h-4 w-4 text-violet-300" />
          <span className="font-mono text-xs text-violet-200">{d.pack}</span>
          <span className="ml-auto rounded-full bg-violet-500/20 px-2 py-0.5 text-[11px] font-semibold text-violet-200">
            {d.label} detected · {d.confidence}%
          </span>
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {d.signals.map((s, i) => (
            <span key={s} style={{ animationDelay: `${i * 90}ms` }}
                  className="animate-stage-in rounded-md border border-slate-700 bg-slate-900/80 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
              {s}
            </span>
          ))}
        </div>
      </div>

      <div className="relative grid grid-cols-[1fr_auto_1fr] items-center gap-2">
        {[d.join.left, d.join.right].map((side, i) => (
          <div key={side} className={cn("min-w-0 rounded-xl border border-slate-800 bg-slate-950/70 px-3 py-3", i === 1 && "col-start-3")}>
            <p className="font-mono text-[10px] uppercase tracking-wider text-slate-500">{i === 0 ? "child" : "parent"}</p>
            <p className="mt-0.5 truncate font-mono text-xs font-semibold text-slate-100">{side.split(".")[0]}</p>
            <p className="truncate font-mono text-[11px] text-cyan-300">.{side.split(".")[1]}</p>
          </div>
        ))}
        <svg aria-hidden viewBox="0 0 120 40" className="col-start-2 row-start-1 h-10 w-24 sm:w-32">
          <defs>
            <linearGradient id="join-line" x1="0" x2="1">
              <stop offset="0%" stopColor="#06B6D4" />
              <stop offset="100%" stopColor="#8B5CF6" />
            </linearGradient>
          </defs>
          <path d="M2 20 C 40 2, 80 38, 118 20" fill="none" stroke="url(#join-line)" strokeWidth="2.5"
                strokeDasharray="6 6" className="animate-flow" strokeLinecap="round" />
          <circle cx="2" cy="20" r="3" fill="#06B6D4" />
          <circle cx="118" cy="20" r="3" fill="#8B5CF6" />
        </svg>
      </div>

      <div className="rounded-xl border border-cyan-500/25 bg-cyan-500/[0.06] px-3 py-2.5">
        <p className="font-mono text-[11px] text-cyan-200">
          AUTO JOIN ON {d.join.left.toLowerCase()} = {d.join.right.toLowerCase()}
        </p>
        <div className="mt-2 flex items-center gap-3">
          <span className="text-[11px] text-slate-400">{d.join.cardinality} · confidence</span>
          <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-800">
            <span className="animate-progress block h-full rounded-full bg-gradient-to-r from-cyan-400 to-emerald-400"
                  style={{ width: `${d.join.confidence}%`, animationDuration: "900ms" }} />
          </span>
          <span className="font-mono text-xs font-semibold text-emerald-300">{d.join.confidence}%</span>
        </div>
      </div>
    </div>
  );
}

function BuildStage({ d }: { d: Domain }) {
  const tones = ["from-amber-500/20 border-amber-500/30 text-amber-200", "from-slate-300/10 border-slate-400/30 text-slate-200", "from-cyan-500/20 border-cyan-500/30 text-cyan-200"];
  const names = ["Bronze source", "Silver ephemeral", "Hub + history"];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-[1fr_auto_1fr_auto_1fr] items-center gap-1.5">
        {d.layers.map((layer, i) => (
          <div key={layer} className="contents">
            <div style={{ animationDelay: `${i * 160}ms` }}
                 className={cn("animate-stage-in min-w-0 rounded-xl border bg-gradient-to-b to-transparent px-2.5 py-2", tones[i])}>
              <p className="flex items-center gap-1.5 truncate text-[10px] font-semibold uppercase tracking-wider">
                <span className="h-1.5 w-1.5 animate-glow rounded-full bg-current shadow-[0_0_8px_currentColor]" /> {names[i]}
              </p>
              <p className="mt-1 truncate font-mono text-[11px] text-slate-300">{layer}</p>
            </div>
            {i < 2 && <ArrowRight className="h-3.5 w-3.5 text-slate-600" />}
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1.35fr_1fr]">
        <div className="min-w-0 overflow-hidden rounded-xl border border-slate-800 bg-[#05070B]">
          <div className="flex items-center gap-2 border-b border-slate-800 px-3 py-1.5">
            <GitBranch className="h-3.5 w-3.5 text-slate-500" />
            <span className="font-mono text-[11px] text-slate-400">models/silver/{d.model}</span>
          </div>
          <pre className="overflow-x-auto px-3 py-2.5 font-mono text-[10.5px] leading-[1.55] text-slate-300">
            {d.sql.split("\n").map((line, i) => (
              <div key={i} className="flex">
                <span className="mr-3 w-4 shrink-0 select-none text-right text-slate-700">{i + 1}</span>
                <span className="whitespace-pre">{highlight(line)}</span>
              </div>
            ))}
          </pre>
        </div>
        <div className="rounded-xl border border-emerald-500/25 bg-emerald-500/[0.06] p-3">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-emerald-300" />
            <span className="text-xs font-semibold text-emerald-200">Soda quality gate</span>
            <span className="ml-auto rounded-full bg-emerald-500/20 px-2 py-0.5 font-mono text-[10px] font-semibold text-emerald-200">
              {d.checks.length}/{d.checks.length} passed
            </span>
          </div>
          <ul className="mt-2.5 space-y-1.5">
            {d.checks.map((c, i) => (
              <li key={c} style={{ animationDelay: `${300 + i * 160}ms` }} className="animate-stage-in flex items-start gap-1.5">
                <Check className="mt-0.5 h-3 w-3 shrink-0 text-emerald-400" />
                <span className="font-mono text-[10.5px] leading-snug text-slate-300">{c}</span>
              </li>
            ))}
          </ul>
          <p className="mt-3 border-t border-emerald-500/15 pt-2 text-[10px] text-slate-500">
            Also exported as a Great Expectations suite. Target: <span className="font-mono text-slate-300">{d.target}</span>
          </p>
        </div>
      </div>
    </div>
  );
}

/** Interactive walk through a run: in-place profiling, domain and join inference, then dbt build and quality gate. */
export function PipelineVisualizer() {
  const [domainKey, setDomainKey] = useState(DOMAINS[0].key);
  const [stage, setStage] = useState(0);
  const [paused, setPaused] = useState(false);
  const domain = DOMAINS.find((x) => x.key === domainKey) ?? DOMAINS[0];

  useEffect(() => {
    if (paused) return;
    const timer = setTimeout(() => setStage((s) => (s + 1) % STAGES.length), STAGE_MS);
    return () => clearTimeout(timer);
  }, [stage, paused, domainKey]);

  const pick = (key: string) => { setDomainKey(key); setStage(0); };

  return (
    <section aria-label="How a pipeline run works" onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}
             onFocus={() => setPaused(true)} onBlur={() => setPaused(false)}
             className="relative flex h-full flex-col overflow-hidden rounded-3xl border border-slate-800/60 bg-[rgba(15,23,42,0.65)] shadow-[0_30px_80px_-30px_rgba(2,6,23,0.9)] backdrop-blur-xl">
      <div className="flex items-center gap-3 border-b border-slate-800/60 px-5 py-3">
        <span className="flex gap-1.5" aria-hidden>
          <span className="h-2.5 w-2.5 rounded-full bg-rose-400/70" />
          <span className="h-2.5 w-2.5 rounded-full bg-amber-400/70" />
          <span className="h-2.5 w-2.5 rounded-full bg-emerald-400/70" />
        </span>
        <span className="font-mono text-[11px] text-slate-400">gdp-agent · pipeline run</span>
        <span className="ml-auto rounded-full border border-slate-700 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-slate-400">
          Illustrative run
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2 px-5 pt-4" role="group" aria-label="Domain">
        {DOMAINS.map(({ key, label, icon: Icon }) => (
          <button key={key} type="button" onClick={() => pick(key)} aria-pressed={key === domainKey}
                  className={cn("inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition",
                    key === domainKey
                      ? "border-indigo-400/60 bg-indigo-500/15 text-indigo-100 shadow-[0_0_18px_rgba(99,102,241,0.25)]"
                      : "border-slate-700/80 text-slate-400 hover:border-slate-600 hover:text-slate-200")}>
            <Icon className="h-3.5 w-3.5" /> {label}
          </button>
        ))}
      </div>

      <ol className="grid grid-cols-3 gap-2 px-4 pt-4 sm:px-5">
        {STAGES.map(({ title, hint, icon: Icon }, i) => {
          const active = i === stage;
          const done = i < stage;
          return (
            <li key={title} className="min-w-0">
              <button type="button" onClick={() => setStage(i)} aria-current={active ? "step" : undefined}
                      className={cn("w-full rounded-xl border px-3 py-2 text-left transition",
                        active ? "border-cyan-400/40 bg-cyan-500/[0.08]" : "border-slate-800 hover:border-slate-700")}>
                <span className="flex items-center gap-2">
                  <span className={cn("grid h-6 w-6 shrink-0 place-items-center rounded-lg",
                    done ? "bg-emerald-500/20 text-emerald-300" : active ? "bg-cyan-500/20 text-cyan-300" : "bg-slate-800 text-slate-500")}>
                    {done ? <Check className="h-3.5 w-3.5" /> : <Icon className="h-3.5 w-3.5" />}
                  </span>
                  <span className="min-w-0">
                    <span className={cn("block truncate text-xs font-semibold", active ? "text-slate-100" : "text-slate-400")}>{title}</span>
                    <span className="block truncate text-[10px] text-slate-500">{hint}</span>
                  </span>
                </span>
                <span className="mt-2 block h-0.5 overflow-hidden rounded-full bg-slate-800">
                  {active && !paused ? (
                    <span key={`${domainKey}-${stage}`} className="animate-progress block h-full bg-gradient-to-r from-cyan-400 to-indigo-400"
                          style={{ animationDuration: `${STAGE_MS}ms` }} />
                  ) : (
                    <span className={cn("block h-full", done || active ? "w-full bg-emerald-400/70" : "w-0")} />
                  )}
                </span>
              </button>
            </li>
          );
        })}
      </ol>

      <div key={`${domainKey}-${stage}`} className="animate-stage-in flex-1 px-5 pb-5 pt-4">
        {stage === 0 && <ProfileStage d={domain} />}
        {stage === 1 && <SemanticStage d={domain} />}
        {stage === 2 && <BuildStage d={domain} />}
      </div>

      <div className="border-t border-slate-800/60 bg-black/20 px-5 py-3 font-mono text-[11px]" aria-live="polite">
        <p className="mb-1.5 flex items-center gap-2 text-[10px] uppercase tracking-[0.16em] text-slate-500">
          <span className="h-1.5 w-1.5 animate-glow rounded-full bg-emerald-400" /> run log
        </p>
        <ol className="space-y-1">
          {runLog(domain).slice(0, stage + 1).map((line) => (
            <li key={`${domainKey}-${line.at}`} className="animate-stage-in flex gap-3">
              <span className="text-slate-600">{line.at}</span>
              <span className={line.tone}>✓</span>
              <span className="truncate text-slate-400">{line.text}</span>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
