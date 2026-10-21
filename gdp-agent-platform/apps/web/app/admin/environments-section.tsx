"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Plus, ScanSearch, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { DryRunResults } from "@/components/quality/dry-run";
import { roleLabel, type DryRun, type EnvironmentConfig, type PackRow } from "@/lib/store-types";
import { previewEnvironment, saveEnvironments, type EnvironmentsState } from "./actions";
import { DirtyBar, useToast } from "./admin-ui";
import { Panel } from "./section";

type Env = { name: string; description: string };
type Role = { label: string; values: string[] };   // one value per environment, in column order
type Grid = { envs: Env[]; roles: Role[]; current: string };

const roleKey = (label: string) => label.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");

function toGrid(c: EnvironmentConfig): Grid {
  const keys = Array.from(new Set(c.environments.flatMap((e) => Object.keys(e.bindings))))
    .sort((a, b) => (a === "silver" ? -1 : b === "silver" ? 1 : a.localeCompare(b)));
  const envs = c.environments.map((e) => ({ name: e.name, description: e.description ?? "" }));
  const roles = keys.map((k) => ({ label: roleLabel(k), values: c.environments.map((e) => e.bindings[k] ?? "") }));
  if (!envs.length) return { envs: [{ name: "DEV", description: "" }], roles: [{ label: "Silver", values: [""] }], current: "DEV" };
  return { envs, roles: roles.length ? roles : [{ label: "Silver", values: envs.map(() => "") }], current: c.current ?? "" };
}

function toConfig(g: Grid): EnvironmentConfig {
  return {
    current: g.current || null,
    environments: g.envs.map((e, j) => ({
      name: e.name.trim().toUpperCase(), description: e.description.trim(),
      bindings: Object.fromEntries(g.roles.filter((r) => roleKey(r.label) && (r.values[j] ?? "").trim())
        .map((r) => [roleKey(r.label), r.values[j].trim()])),
    })),
  };
}

/** Admin, Environments: which real database plays each role (Silver, Landing, ...) in each environment, so a QA pack
 *  written against this deployment's tables also runs in QA, PROD or a client database. */
export function EnvironmentsSection({ state, packs }: { state: EnvironmentsState; packs: PackRow[] }) {
  const router = useRouter();
  const toast = useToast();
  const [pending, start] = useTransition();
  const [saved, setSaved] = useState(() => toGrid(state.config));
  const [grid, setGrid] = useState(saved);
  const config = useMemo(() => toConfig(grid), [grid]);
  const dirty = state.saved
    ? JSON.stringify(config) !== JSON.stringify(toConfig(saved))
    : config.environments.some((e) => Object.keys(e.bindings).length > 0);

  const [packId, setPackId] = useState(packs[0]?.pack_id ?? "");
  const [target, setTarget] = useState("");
  const [preview, setPreview] = useState<DryRun | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewing, startPreview] = useTransition();

  const setEnv = (j: number, patch: Partial<Env>) => setGrid((g) => {
    const was = g.envs[j].name.trim().toUpperCase();
    const envs = g.envs.map((e, i) => (i === j ? { ...e, ...patch } : e));
    const current = patch.name !== undefined && was && was === g.current ? patch.name.trim().toUpperCase() : g.current;
    return { ...g, envs, current };
  });
  const setCell = (r: number, j: number, value: string) =>
    setGrid((g) => ({ ...g, roles: g.roles.map((role, i) => (i === r ? { ...role, values: g.envs.map((_, k) => (k === j ? value : role.values[k] ?? "")) } : role)) }));
  const addEnv = () => setGrid((g) => ({ ...g, envs: [...g.envs, { name: "", description: "" }], roles: g.roles.map((r) => ({ ...r, values: [...r.values, ""] })) }));
  const removeEnv = (j: number) => setGrid((g) => ({
    envs: g.envs.filter((_, i) => i !== j), roles: g.roles.map((r) => ({ ...r, values: r.values.filter((_, i) => i !== j) })),
    current: g.envs[j].name.trim().toUpperCase() === g.current ? "" : g.current,
  }));
  const addRole = () => setGrid((g) => ({ ...g, roles: [...g.roles, { label: "", values: g.envs.map(() => "") }] }));
  const removeRole = (r: number) => setGrid((g) => ({ ...g, roles: g.roles.filter((_, i) => i !== r) }));

  const save = () => start(async () => {
    const r = await saveEnvironments(config);
    if (r.ok) {
      const next = toGrid(r.data.config);
      setSaved(next); setGrid(next);
      toast("ok", "Environment databases saved. QA packs use them from now on.");
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
      <Panel title="Environment databases"
             description={<>Each environment (DEV, QA, PROD or a client database) has its own databases. Enter the database, or
               DATABASE.SCHEMA, that plays each role in each environment, for example Silver is DEV_GDP_SILVER_DB in DEV and
               DA_GDP_SILVER_DB in DA. QA packs are written with real table names; run in another environment, a table in a
               mapped database is read from that environment&apos;s database for the same role. Tables in databases no role
               covers are read where they are. Platform database: <span className="font-mono">{state.platform_database}</span>.</>}
             actions={<Button size="sm" variant="outline" onClick={addEnv}><Plus className="h-3.5 w-3.5" /> Add environment</Button>}>
        {state.problems.length > 0 && (
          <p className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            The saved mapping has problems: {state.problems.join("; ")}
          </p>
        )}
        <div className="overflow-x-auto rounded-xl border">
          <table className="w-full min-w-[560px] text-sm" data-testid="environment-grid">
            <thead className="bg-muted/40 text-left align-top text-xs text-muted-foreground">
              <tr>
                <th className="w-44 px-3 py-2 font-medium">Database role</th>
                {grid.envs.map((env, j) => {
                  const upper = env.name.trim().toUpperCase();
                  return (
                    <th key={j} className="min-w-[200px] px-3 py-2 font-medium">
                      <div className="flex items-center gap-1">
                        <Input value={env.name} placeholder="Environment" aria-label={`Environment ${j + 1} name`}
                               className="h-8 font-mono text-xs uppercase" onChange={(e) => setEnv(j, { name: e.target.value })} />
                        <button type="button" aria-label={`Remove ${upper || "environment"}`} className="p-1 text-muted-foreground hover:text-destructive"
                                onClick={() => removeEnv(j)}><Trash2 className="h-3.5 w-3.5" /></button>
                      </div>
                      <Input value={env.description} placeholder="Description" aria-label={`${upper || "Environment"} description`}
                             className="mt-1 h-7 text-xs font-normal" onChange={(e) => setEnv(j, { description: e.target.value })} />
                      <label className="mt-1.5 flex items-center gap-1.5 font-normal">
                        <input type="radio" name="current-environment" checked={!!upper && upper === grid.current} disabled={!upper}
                               onChange={() => setGrid((g) => ({ ...g, current: upper }))} />
                        This deployment
                        {upper && upper === grid.current && <Badge variant="secondary">current</Badge>}
                      </label>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody className="divide-y">
              {grid.roles.map((role, r) => (
                <tr key={r}>
                  <td className="px-3 py-1.5 align-top">
                    <div className="flex items-center gap-1">
                      <Input value={role.label} placeholder="Role name" aria-label={`Database role ${r + 1}`} className="h-8 text-xs"
                             onChange={(e) => setGrid((g) => ({ ...g, roles: g.roles.map((x, i) => (i === r ? { ...x, label: e.target.value } : x)) }))} />
                      <button type="button" aria-label={`Remove ${role.label || "role"}`} className="p-1 text-muted-foreground hover:text-destructive"
                              onClick={() => removeRole(r)}><Trash2 className="h-3.5 w-3.5" /></button>
                    </div>
                  </td>
                  {grid.envs.map((env, j) => (
                    <td key={j} className="px-3 py-1.5 align-top">
                      <Input value={role.values[j] ?? ""} aria-label={`${role.label || "Role"} database in ${env.name.trim().toUpperCase() || `environment ${j + 1}`}`}
                             className="h-8 font-mono text-xs" onChange={(e) => setCell(r, j, e.target.value)} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Button size="sm" variant="ghost" className="mt-2" onClick={addRole}><Plus className="h-3.5 w-3.5" /> Add database role</Button>
        <p className="mt-2 text-xs text-muted-foreground">
          Leave a cell empty when the environment has no database for that role. When this deployment maps no Silver database,
          each domain&apos;s registered Silver database is used.
        </p>
      </Panel>

      <Panel title="Preview a pack"
             description="Shows a pack's queries as they would run in one environment with the databases above (saved or not), then runs the read-only guard and an EXPLAIN there with your role. Nothing runs and nothing is stored.">
        {!packs.length ? <p className="text-sm text-muted-foreground">There are no QA packs to preview yet.</p> : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-end gap-2">
              <div className="min-w-0 flex-1"><Label htmlFor="pv-pack">Pack</Label>
                <Select id="pv-pack" value={packId} onChange={(e) => setPackId(e.target.value)}>
                  {packs.map((p) => <option key={p.pack_id} value={p.pack_id}>{p.domain_name ?? "No domain"} · {p.name} (v{p.approved_version ?? p.version})</option>)}
                </Select></div>
              <div className="w-44"><Label htmlFor="pv-env">Environment</Label>
                <Select id="pv-env" value={target} onChange={(e) => setTarget(e.target.value)}>
                  <option value="">{grid.current ? `${grid.current} (this deployment)` : "This deployment"}</option>
                  {names.filter((n) => n !== grid.current).map((n) => <option key={n} value={n}>{n}</option>)}
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
        <Panel title="Saved versions" description="Every save is a new version of the mapping; the newest current one is used.">
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
      <DirtyBar count={dirty ? 1 : 0} pending={pending} onSave={save} onDiscard={() => setGrid(saved)} what="environment change" />
    </div>
  );
}
