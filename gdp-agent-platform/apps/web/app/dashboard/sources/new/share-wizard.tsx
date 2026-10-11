"use client";

import Link from "next/link";
import { useMemo, useState, useTransition } from "react";
import { ArrowLeft, ArrowRight, CheckCircle2, Loader2, Play, XCircle } from "lucide-react";
import type { OnboardResult, ShareList, ShareTable } from "@/lib/hub-types";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { ErrorNote } from "@/components/hub/states";
import { cn } from "@/lib/utils";
import { onboardShare, previewShare } from "../actions";
import type { DomainOption } from "./wizard";

const STEPS = ["Share", "Tables", "Landing", "Run"] as const;
const NAME = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;

export function ShareWizard({ shares, domains }: { shares: ShareList; domains: DomainOption[] }) {
  const [step, setStep] = useState(0);
  const [pending, start] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [mount, setMount] = useState(shares.mounted.length === 0);
  const [provider, setProvider] = useState("");
  const [shareName, setShareName] = useState("");
  const [database, setDatabase] = useState(shares.mounted[0] ?? "");
  const [schema, setSchema] = useState("");
  const [tables, setTables] = useState<ShareTable[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");
  const [sourceName, setSourceName] = useState("");
  const [runName, setRunName] = useState("");
  const [domainId, setDomainId] = useState("");
  const [landingDb, setLandingDb] = useState("");
  const [landingSchema, setLandingSchema] = useState("");
  const [result, setResult] = useState<OnboardResult | null>(null);

  const shown = useMemo(() => (tables ?? []).filter((t) => t.table_name.toLowerCase().includes(filter.toLowerCase())), [tables, filter]);

  const loadTables = () => start(async () => {
    setError(null);
    if (mount) { setTables([]); setPicked(new Set()); return setStep(1); }
    const r = await previewShare(database, schema);
    if (!r.ok) return setError(r.error);
    setTables(r.data.tables);
    setPicked(new Set());
    setStep(1);
  });

  const run = () => start(async () => {
    setError(null);
    setResult(null);
    const tableList = tables?.length ? Array.from(picked) : filter.split(/[\s,]+/).filter(Boolean);
    const r = await onboardShare({
      database, schema, tables: tableList, source_system_name: sourceName, run_name: runName || undefined,
      domain_id: domainId || undefined, mount, provider_account: mount ? provider : undefined, share_name: mount ? shareName : undefined,
      landing_database: landingDb || undefined, landing_schema: landingSchema || undefined,
    });
    if (!r.ok) return setError(r.error);
    setResult(r.data);
  });

  const canNext = [
    !!database && !!schema && (!mount || (!!provider && !!shareName)),
    tables?.length ? picked.size > 0 : filter.trim().length > 0,
    NAME.test(sourceName),
    true,
  ][step];

  return (
    <div className="surface p-5">
      <ol className="mb-5 flex flex-wrap gap-2">
        {STEPS.map((s, i) => (
          <li key={s} className={cn("flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset",
            i === step ? "bg-primary text-primary-foreground ring-primary" : i < step ? "bg-accent text-accent-foreground ring-primary/15" : "text-muted-foreground ring-border")}>
            <span className="tabular-nums">{i + 1}</span>{s}
          </li>
        ))}
      </ol>
      {error && <div className="mb-4"><ErrorNote>{error}</ErrorNote></div>}

      {step === 0 && (
        <div className="grid gap-x-6 md:grid-cols-2">
          <div>
            <Label>Share database</Label>
            <div className="flex gap-2 text-sm">
              <label className="flex items-center gap-1.5"><input type="radio" checked={!mount} onChange={() => setMount(false)} disabled={!shares.mounted.length} /> Already mounted</label>
              <label className="flex items-center gap-1.5"><input type="radio" checked={mount} onChange={() => setMount(true)} /> Mount it now</label>
            </div>
            {!mount ? (
              <>
                <Label htmlFor="db">Database</Label>
                <Select id="db" value={database} onChange={(e) => setDatabase(e.target.value)}>
                  {shares.mounted.map((d) => <option key={d}>{d}</option>)}
                </Select>
              </>
            ) : (
              <>
                <Label htmlFor="prov">Provider account</Label>
                <Input id="prov" placeholder="ORG.ACCOUNT or locator" value={provider} onChange={(e) => setProvider(e.target.value)}
                       list="share-providers" />
                <datalist id="share-providers">{shares.shares.map((s) => <option key={`${s.owner_account}.${s.name}`} value={s.owner_account} />)}</datalist>
                <Label htmlFor="share">Share name</Label>
                <Input id="share" value={shareName} onChange={(e) => setShareName(e.target.value)} list="share-names" />
                <datalist id="share-names">{shares.shares.map((s) => <option key={s.name} value={s.name} />)}</datalist>
                <Label htmlFor="newdb">Database to create from the share</Label>
                <Input id="newdb" value={database} onChange={(e) => setDatabase(e.target.value)} placeholder="PARTNER_SHARE" />
                <p className="mt-1 text-xs text-muted-foreground">Needs the IMPORT SHARE privilege. Otherwise ask an admin to mount it once.</p>
              </>
            )}
          </div>
          <div>
            <Label htmlFor="schema">Schema</Label>
            <Input id="schema" value={schema} onChange={(e) => setSchema(e.target.value)} placeholder="PUBLIC" />
            <p className="mt-2 text-xs text-muted-foreground">
              Each table is unloaded once to Snappy Parquet on SOURCE.LANDING_STAGE, so the share is read by a single statement,
              then copied into landing with its exact column types (COPY INTO ... MATCH_BY_COLUMN_NAME).
            </p>
          </div>
        </div>
      )}

      {step === 1 && (
        <div>
          {tables && tables.length > 0 ? (
            <>
              <div className="mb-3 flex items-center gap-3">
                <Input placeholder="Filter tables" value={filter} onChange={(e) => setFilter(e.target.value)} className="max-w-xs" />
                <button className="text-xs text-primary hover:underline" onClick={() => setPicked(new Set(shown.map((t) => t.table_name)))}>Select shown</button>
                <button className="text-xs text-muted-foreground hover:underline" onClick={() => setPicked(new Set())}>Clear</button>
                <span className="ml-auto text-xs text-muted-foreground">{picked.size} of {tables.length} selected</span>
              </div>
              <div className="grid max-h-80 gap-1 overflow-y-auto sm:grid-cols-2 lg:grid-cols-3">
                {shown.map((t) => (
                  <label key={t.table_name} className="flex items-center gap-2 rounded-lg border border-border/70 px-3 py-2 text-sm hover:bg-accent/40">
                    <input type="checkbox" checked={picked.has(t.table_name)}
                           onChange={(e) => setPicked((prev) => { const n = new Set(prev); if (e.target.checked) n.add(t.table_name); else n.delete(t.table_name); return n; })} />
                    <span className="flex-1 truncate font-mono text-xs">{t.table_name}</span>
                    {t.row_count != null && <span className="text-[11px] tabular-nums text-muted-foreground">{Number(t.row_count).toLocaleString()} rows</span>}
                  </label>
                ))}
              </div>
            </>
          ) : (
            <>
              <Label htmlFor="tables">Tables to onboard</Label>
              <Input id="tables" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="ORDERS, CUSTOMERS" />
              <p className="mt-1 text-xs text-muted-foreground">The share is mounted when you run, so its tables are listed by name here.</p>
            </>
          )}
        </div>
      )}

      {step === 2 && (
        <div className="grid gap-x-6 md:grid-cols-2">
          <div>
            <Label htmlFor="src">Source system name</Label>
            <Input id="src" value={sourceName} onChange={(e) => setSourceName(e.target.value)} placeholder="PARTNER_SALES" />
            {!!sourceName && !NAME.test(sourceName) && <p className="mt-1 text-xs text-destructive">Letters, digits and _, starting with a letter.</p>}
            <Label htmlFor="run">Run name</Label>
            <Input id="run" value={runName} onChange={(e) => setRunName(e.target.value)} placeholder={`Share ${database}.${schema}`} />
            <Label htmlFor="dom">Domain</Label>
            <Select id="dom" value={domainId} onChange={(e) => setDomainId(e.target.value)}>
              <option value="">Decide later</option>
              {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
            </Select>
          </div>
          <div>
            <Label htmlFor="ldb">Landing database</Label>
            <Input id="ldb" value={landingDb} onChange={(e) => setLandingDb(e.target.value)} placeholder="platform database" />
            <Label htmlFor="lsc">Landing schema</Label>
            <Input id="lsc" value={landingSchema} onChange={(e) => setLandingSchema(e.target.value)} placeholder="LANDING" />
            <p className="mt-1 text-xs text-muted-foreground">Landing tables are named SOURCE__TABLE and always managed tables.</p>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="space-y-4">
          <dl className="grid gap-2 text-sm sm:grid-cols-2">
            <div><dt className="text-xs text-muted-foreground">Share</dt><dd className="font-mono">{database}.{schema}{mount ? ` (mount ${provider}.${shareName})` : ""}</dd></div>
            <div><dt className="text-xs text-muted-foreground">Tables</dt><dd>{tables?.length ? picked.size : filter.split(/[\s,]+/).filter(Boolean).length}</dd></div>
            <div><dt className="text-xs text-muted-foreground">Source system</dt><dd className="font-mono">{sourceName.toUpperCase()}</dd></div>
            <div><dt className="text-xs text-muted-foreground">Landing</dt><dd className="font-mono">{landingDb || "platform"}.{landingSchema || "LANDING"}</dd></div>
          </dl>
          {result && (
            <ol className="space-y-1.5">
              {result.steps.map((s) => (
                <li key={s.step} className="flex items-start gap-2 text-sm">
                  {s.ok ? <CheckCircle2 className="mt-0.5 h-4 w-4 text-emerald-600" /> : <XCircle className="mt-0.5 h-4 w-4 text-rose-600" />}
                  <span className="w-20 shrink-0 font-medium capitalize">{s.step}</span>
                  <span className="text-muted-foreground">{s.detail}</span>
                </li>
              ))}
            </ol>
          )}
          {result && (
            <Link href={result.continue_at} className={buttonVariants({ variant: result.ok ? "default" : "outline" })}>
              {result.ok ? "Continue to profiling" : "Open the run to finish"} <ArrowRight className="h-4 w-4" />
            </Link>
          )}
        </div>
      )}

      <div className="mt-6 flex items-center justify-between border-t border-border/70 pt-4">
        <Button variant="ghost" disabled={step === 0 || pending} onClick={() => setStep((s) => s - 1)}><ArrowLeft className="h-4 w-4" /> Back</Button>
        {step < 3 ? (
          <Button disabled={!canNext || pending} onClick={() => (step === 0 ? loadTables() : setStep((s) => s + 1))}>
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : null} Next <ArrowRight className="h-4 w-4" />
          </Button>
        ) : (
          <Button disabled={pending || !!result?.ok} onClick={run}>
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} {pending ? "Landing..." : "Onboard and land"}
          </Button>
        )}
      </div>
    </div>
  );
}
