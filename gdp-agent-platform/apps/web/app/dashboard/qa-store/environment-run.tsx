"use client";

import { useEffect, useState, useTransition } from "react";
import { Layers, Loader2, Play, ScanSearch } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/input";
import { explain } from "@/components/hub/states";
import { DryRunResults } from "@/components/quality/dry-run";
import { useToast } from "@/components/ui/toast";
import { roleLabel, type DryRun, type QaEnvironment, type QaExecution } from "@/lib/store-types";
import { dryRunPack, executeQa, loadQaEnvironments } from "./actions";

/** Run a pack version in an environment from Admin, Environments: a dry run (resolved SQL, guard, EXPLAIN) comes
 *  first, and the run is offered once the dry run of that environment and version is shown. */
export function EnvironmentRun({ packId, domainId, versions, onRan }: {
  packId: string; domainId: string; versions: { version: number; label: string }[]; onRan: (r: QaExecution) => void;
}) {
  const toast = useToast();
  const [envs, setEnvs] = useState<QaEnvironment[] | null>(null);
  const [env, setEnv] = useState("");
  const [version, setVersion] = useState(versions[0]?.version ?? 0);
  const [dry, setDry] = useState<DryRun | null>(null);
  const [busy, setBusy] = useState("");
  const [, start] = useTransition();

  useEffect(() => {
    void loadQaEnvironments().then((r) => {
      const list = r.ok ? r.data.environments : [];
      setEnvs(list);
      setEnv(list.find((e) => !e.current)?.name ?? list[0]?.name ?? "");
    });
  }, []);
  useEffect(() => { setDry(null); }, [env, version]);
  if (!envs || !envs.length || !versions.length) return null;  // no environments configured: the runs above cover it

  const chosen = envs.find((e) => e.name === env);
  const act = (label: string, fn: () => Promise<void>) => start(async () => { setBusy(label); try { await fn(); } finally { setBusy(""); } });
  const environment = chosen?.current ? null : env || null;
  const check = () => act("dry", async () => {
    const r = await dryRunPack({ domain_id: domainId, pack_id: packId, version, environment });
    if (r.ok) setDry(r.data); else toast("error", explain(r.error).message);
  });
  const run = () => act("run", async () => {
    if (dry && !dry.ok && !confirm(`The dry run found problems in ${dry.queries.filter((q) => !q.ok).length} queries. Run anyway? They will be recorded as errors.`)) return;
    const r = await executeQa({ domain_id: domainId, pack_id: packId, version, environment });
    if (r.ok) {
      onRan(r.data);
      const failed = (r.data.failed ?? 0) + (r.data.errors ?? 0);
      toast(failed ? "error" : "ok", `${env}: ${r.data.passed ?? 0} of ${r.data.tests ?? 0} passed${failed ? `, ${failed} failed or errored` : ""}`);
    } else toast("error", explain(r.error).message);
  });

  return (
    <div className="space-y-3 rounded-xl border border-border/80 p-3" data-testid="environment-run">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Layers className="h-4 w-4 text-muted-foreground" />
        <b>Run in an environment</b>
        <Select aria-label="Environment" className="h-8 w-auto text-xs" value={env} onChange={(e) => setEnv(e.target.value)}>
          {envs.map((e) => <option key={e.name} value={e.name}>{e.name}{e.current ? " (this deployment)" : ""}</option>)}
        </Select>
        <Select aria-label="Version to run" className="h-8 w-auto text-xs" value={version} onChange={(e) => setVersion(Number(e.target.value))}>
          {versions.map((v) => <option key={v.version} value={v.version}>{v.label}</option>)}
        </Select>
        <Button size="sm" variant="outline" disabled={!!busy || !env} onClick={check} data-testid="dry-run-pack">
          {busy === "dry" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ScanSearch className="h-3.5 w-3.5" />} Dry run
        </Button>
        <Button size="sm" disabled={!!busy || !dry} onClick={run} title={dry ? "Runs the queries and stores the result" : "Dry run first"}>
          {busy === "run" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />} Run on {env}
        </Button>
      </div>
      {chosen && (
        <p className="text-xs text-muted-foreground">
          {Object.keys(chosen.bindings).length
            ? Object.entries(chosen.bindings).map(([k, v]) => `${roleLabel(k)}: ${v}`).join(" · ")
            : "No databases mapped for this environment yet (Admin, Environments)."}
        </p>
      )}
      {dry && <DryRunResults result={dry} />}
    </div>
  );
}
