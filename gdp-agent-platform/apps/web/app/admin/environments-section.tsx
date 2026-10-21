"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Plus, ScanSearch, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { DryRunResults } from "@/components/quality/dry-run";
import type { DryRun, EnvironmentConfig, PackRow } from "@/lib/store-types";
import { previewEnvironment, saveEnvironments, type EnvironmentsState } from "./actions";
import { DirtyBar, useToast } from "./admin-ui";
import { Panel } from "./section";

type Row = { key: string; value: string };
type Env = { name: string; description: string; rows: Row[] };

const toEnvs = (c: EnvironmentConfig): Env[] =>
  c.environments.map((e) => ({ name: e.name, description: e.description ?? "", rows: Object.entries(e.bindings).map(([key, value]) => ({ key, value })) }));

function toConfig(envs: Env[], current: string): EnvironmentConfig {
  return {
    current: current || null,
    environments: envs.map((e) => ({
      name: e.name.trim().toUpperCase(), description: e.description.trim(),
      bindings: Object.fromEntries(e.rows.filter((r) => r.key.trim() || r.value.trim()).map((r) => [r.key.trim().toLowerCase(), r.value.trim()])),
    })),
  };
}

/** Admin, Environments: where this deployment's logical names point, and the other environments a QA pack can run in. */
export function EnvironmentsSection({ state, packs }: { state: EnvironmentsState; packs: PackRow[] }) {
  const router = useRouter();
  const toast = useToast();
  const [pending, start] = useTransition();
  const [saved, setSaved] = useState(() => ({ envs: toEnvs(state.config), current: state.config.current ?? "" }));
  const [envs, setEnvs] = useState(saved.envs);
  const [current, setCurrent] = useState(saved.current);
  const config = useMemo(() => toConfig(envs, current), [envs, current]);
  const dirty = JSON.stringify(config) !== JSON.stringify(toConfig(saved.envs, saved.current));

  const [packId, setPackId] = useState(packs[0]?.pack_id ?? "");
  const [target, setTarget] = useState("");
  const [preview, setPreview] = useState<DryRun | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewing, startPreview] = useTransition();

  const change = (i: number, fn: (e: Env) => Env) => setEnvs((all) => all.map((e, j) => (j === i ? fn(e) : e)));
  const addEnv = () => {
    const first = !envs.length;
    setEnvs((all) => [...all, { name: first ? "DEV" : "", description: "", rows: [{ key: "silver", value: "" }] }]);
    if (first) setCurrent("DEV");
  };

  const save = () => start(async () => {
    const r = await saveEnvironments(config);
    if (r.ok) {
      setSaved({ envs: toEnvs(r.data.config), current: r.data.config.current ?? "" });
      setEnvs(toEnvs(r.data.config)); setCurrent(r.data.config.current ?? "");
      toast("ok", "Environment bindings saved. Packs resolve their logical names with them from now on.");
      router.refresh();
    } else if (r.pending) toast("info", r.error);
    else toast("error", r.error);
  });

  const runPreview = () => startPreview(async () => {
    const pack = packs.find((p) => p.pack_id === packId);
    if (!pack) return;
    setPreview(null); setPreviewError("");
    const r = await previewEnvironment({ config, environment: target || null, pack_id: pack.pack_id, domain_id: pack.domain_id });
    if (r.ok) setPreview(r.data);
    else setPreviewError(r.error);
  });

  const names = config.environments.map((e) => e.name).filter(Boolean);
  return (
    <div className="space-y-5">
      <Panel title="Environment bindings"
             description={<>QA pack SQL and Data Quality suite files can name tables with logical names instead of a fixed database:{" "}
               <code className="font-mono">{"{{silver}}"}.COMPANY.COMPANY_CORE</code> when <code className="font-mono">silver</code> is a database,
               or <code className="font-mono">{"{{company}}"}.COMPANY_CORE</code> when it is a database and schema.{" "}
               <code className="font-mono">{"{{domain}}"}</code> is always the pack&apos;s own domain. Each environment maps the names to its own
               databases; fully qualified SQL keeps working as written. Platform database: <span className="font-mono">{state.platform_database}</span>.</>}
             actions={<Button size="sm" variant="outline" onClick={addEnv}><Plus className="h-3.5 w-3.5" /> Add environment</Button>}>
        {state.problems.length > 0 && (
          <p className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            The saved value has problems: {state.problems.join("; ")}
          </p>
        )}
        {!envs.length ? (
          <p className="text-sm text-muted-foreground">
            No environments yet. Add this deployment&apos;s environment first (for example DEV), bind its databases, then add the
            environments packs should also run in (QA, PROD or a client database).
          </p>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {envs.map((env, i) => {
              const upper = env.name.trim().toUpperCase();
              return (
                <section key={i} className="rounded-xl border p-4" aria-label={`Environment ${upper || i + 1}`}>
                  <div className="flex flex-wrap items-end gap-2">
                    <div className="w-36"><Label htmlFor={`env-name-${i}`}>Name</Label>
                      <Input id={`env-name-${i}`} value={env.name} placeholder="QA" className="h-8 font-mono uppercase"
                             onChange={(e) => {
                               const was = upper;
                               change(i, (x) => ({ ...x, name: e.target.value }));
                               if (was && was === current) setCurrent(e.target.value.trim().toUpperCase());
                             }} /></div>
                    <div className="min-w-0 flex-1"><Label htmlFor={`env-desc-${i}`}>Description</Label>
                      <Input id={`env-desc-${i}`} value={env.description} placeholder="Client QA account" className="h-8"
                             onChange={(e) => change(i, (x) => ({ ...x, description: e.target.value }))} /></div>
                    <Button size="sm" variant="ghost" aria-label={`Remove ${upper || "environment"}`} className="text-destructive hover:text-destructive"
                            onClick={() => { setEnvs((all) => all.filter((_, j) => j !== i)); if (upper === current) setCurrent(""); }}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                  <label className="mt-3 flex items-center gap-2 text-xs">
                    <input type="radio" name="current-environment" checked={!!upper && upper === current} disabled={!upper}
                           onChange={() => setCurrent(upper)} />
                    This deployment&apos;s tables live here
                    {upper && upper === current && <Badge variant="secondary">current</Badge>}
                  </label>
                  <table className="mt-3 w-full text-sm">
                    <thead className="text-left text-xs text-muted-foreground">
                      <tr><th className="pb-1 font-medium">Logical name</th><th className="pb-1 font-medium">DATABASE or DATABASE.SCHEMA</th><th /></tr>
                    </thead>
                    <tbody>
                      {env.rows.map((row, k) => (
                        <tr key={k}>
                          <td className="py-1 pr-2 align-top">
                            <Input value={row.key} placeholder="silver" aria-label={`${upper} binding ${k + 1} name`} className="h-8 font-mono text-xs"
                                   onChange={(e) => change(i, (x) => ({ ...x, rows: x.rows.map((r, m) => (m === k ? { ...r, key: e.target.value } : r)) }))} />
                          </td>
                          <td className="py-1 pr-2 align-top">
                            <Input value={row.value} placeholder="DEV_GDP_SILVER_DB" aria-label={`${upper} binding ${k + 1} target`} className="h-8 font-mono text-xs"
                                   onChange={(e) => change(i, (x) => ({ ...x, rows: x.rows.map((r, m) => (m === k ? { ...r, value: e.target.value } : r)) }))} />
                          </td>
                          <td className="w-8 py-1 align-top">
                            <button type="button" aria-label={`Remove binding ${row.key || k + 1}`} className="mt-2 text-muted-foreground hover:text-destructive"
                                    onClick={() => change(i, (x) => ({ ...x, rows: x.rows.filter((_, m) => m !== k) }))}>
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <Button size="sm" variant="ghost" className="mt-1" onClick={() => change(i, (x) => ({ ...x, rows: [...x.rows, { key: "", value: "" }] }))}>
                    <Plus className="h-3.5 w-3.5" /> Add binding
                  </Button>
                </section>
              );
            })}
          </div>
        )}
      </Panel>

      <Panel title="Preview a pack"
             description="Resolves a pack's queries with the bindings above (saved or not) for one environment, then runs the read-only guard and an EXPLAIN against it with your role. Nothing runs and nothing is stored.">
        {!packs.length ? <p className="text-sm text-muted-foreground">There are no QA packs to preview yet.</p> : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-end gap-2">
              <div className="min-w-0 flex-1"><Label htmlFor="pv-pack">Pack</Label>
                <Select id="pv-pack" value={packId} onChange={(e) => setPackId(e.target.value)}>
                  {packs.map((p) => <option key={p.pack_id} value={p.pack_id}>{p.domain_name ?? "No domain"} · {p.name} (v{p.approved_version ?? p.version})</option>)}
                </Select></div>
              <div className="w-44"><Label htmlFor="pv-env">Environment</Label>
                <Select id="pv-env" value={target} onChange={(e) => setTarget(e.target.value)}>
                  <option value="">{current ? `${current} (this deployment)` : "This deployment"}</option>
                  {names.filter((n) => n !== current).map((n) => <option key={n} value={n}>{n}</option>)}
                </Select></div>
              <Button variant="outline" disabled={previewing || !packId} onClick={runPreview} data-testid="preview-environment">
                {previewing ? <Loader2 className="h-4 w-4 animate-spin" /> : <ScanSearch className="h-4 w-4" />} Preview
              </Button>
            </div>
            {previewError && <p className="rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-sm text-destructive">{previewError}</p>}
            {preview && <DryRunResults result={preview} />}
          </div>
        )}
      </Panel>

      {state.history.length > 0 && (
        <Panel title="Saved versions" description="Every save is a new version of the setting; the newest current one is used.">
          <ul className="divide-y text-sm">
            {state.history.map((h) => (
              <li key={h.version} className="flex flex-wrap items-center gap-2 py-2">
                <span className="font-medium">v{h.version}</span>
                {h.is_current && <Badge variant="secondary">current</Badge>}
                <span className="text-muted-foreground">{h.created_by ?? "unknown"} · {String(h.created_at ?? "").slice(0, 16)}</span>
                {h.description && <span className="text-xs text-muted-foreground">{h.description}</span>}
              </li>
            ))}
          </ul>
        </Panel>
      )}
      <DirtyBar count={dirty ? 1 : 0} pending={pending} onSave={save}
                onDiscard={() => { setEnvs(saved.envs); setCurrent(saved.current); }} what="environment change" />
    </div>
  );
}
