"use client";

import Link from "next/link";
import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  CalendarClock, Check, ExternalLink, FileCode2, GitBranch, Github, Loader2, Plus, RefreshCw, Settings2, Trash2,
  TriangleAlert, Unplug, X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { useAccess } from "@/components/access";
import { useScrollLock } from "@/components/use-scroll-lock";
import { cn } from "@/lib/utils";
import { ago } from "../skills/types";
import {
  connectRepo, indexRuns, loadSetup, refreshRepo, removeRepo, scheduleRepo, updateRepo,
  type CodeRepo, type CodeSetup, type IndexRun,
} from "../code/actions";

type Domain = { domain_id: string; domain_name: string };
const PRESETS = [
  { label: "Every hour", cron: "0 * * * * UTC" }, { label: "Daily 06:00 UTC", cron: "0 6 * * * UTC" },
  { label: "Weekdays 06:00 UTC", cron: "0 6 * * 1-5 UTC" }, { label: "Weekly, Monday 06:00 UTC", cron: "0 6 * * 1 UTC" },
];
const STATUS_TONE: Record<string, string> = {
  READY: "bg-emerald-50 text-emerald-700 ring-emerald-100", INDEXING: "bg-indigo-50 text-indigo-700 ring-indigo-100",
  FAILED: "bg-rose-50 text-rose-700 ring-rose-100", NEW: "bg-slate-100 text-slate-600 ring-slate-200",
};
type Msg = { tone: "ok" | "info" | "error"; text: string } | null;
const toneOf = (e: string): "info" | "error" => (/approval|request/i.test(e) ? "info" : "error");

/** Admin, Integrations: code repositories that feed dbt, QA, Soda, STTM and copilot prompts, and (next) Jira. */
export function IntegrationsSection({ repos, domains }: { repos: CodeRepo[]; domains: Domain[] }) {
  const router = useRouter();
  const { canAct } = useAccess();
  const may = canAct("INTEGRATION.MANAGE");
  const [connecting, setConnecting] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  // while something indexes, refresh the list every few seconds
  useEffect(() => {
    if (!repos.some((r) => r.refreshing || r.status === "INDEXING")) return;
    const t = setInterval(() => router.refresh(), 5000);
    return () => clearInterval(t);
  }, [repos, router]);
  return (
    <div className="space-y-5">
      <section className="surface p-5">
        <div className="flex flex-wrap items-start gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-indigo-50 text-indigo-600 ring-1 ring-inset ring-indigo-100"><FileCode2 className="h-5 w-5" /></span>
          <div className="min-w-[16rem] flex-1">
            <h3 className="text-base font-semibold">Code repositories</h3>
            <p className="text-sm text-muted-foreground">Client dbt and SQL repositories, indexed in Snowflake. Their models, macros, tests and schema files are
              quoted into dbt review, QA tests, data quality checks, STTM and the copilot, with citations. Browse them on <Link href="/code" className="text-primary hover:underline">Code</Link>.</p>
          </div>
          {may && <Button onClick={() => setConnecting(true)}><Plus className="h-4 w-4" />Connect repository</Button>}
        </div>
        {msg && <p role={msg.tone === "error" ? "alert" : "status"} className={cn("mt-3 rounded-lg px-3 py-2 text-xs",
          msg.tone === "error" ? "bg-destructive/10 text-destructive" : msg.tone === "info" ? "bg-sky-50 text-sky-800" : "bg-success/10 text-success")}>{msg.text}</p>}
        <div className="mt-4 space-y-3">
          {repos.map((r) => <RepoCard key={r.repo_id} repo={r} domains={domains} may={may} onMsg={setMsg} />)}
          {!repos.length && (
            <div className="rounded-xl border border-dashed p-8 text-center">
              <GitBranch className="mx-auto h-7 w-7 text-muted-foreground" />
              <p className="mt-2 text-sm font-medium">No repositories connected yet</p>
              <p className="text-xs text-muted-foreground">GitHub, GitLab, Bitbucket and Azure DevOps work through Snowflake&apos;s Git integration. Credentials stay in a Snowflake secret.</p>
            </div>
          )}
        </div>
      </section>
      <section className="surface flex items-start gap-3 p-5">
        <span className="grid h-10 w-10 place-items-center rounded-xl bg-sky-50 text-sky-600 ring-1 ring-inset ring-sky-100"><Unplug className="h-5 w-5" /></span>
        <div>
          <h3 className="text-base font-semibold">Jira</h3>
          <p className="text-sm text-muted-foreground">QA engineers will read their Jira issues, reproduce reported bugs with QA tests, and post results back. Coming in the next release.</p>
        </div>
      </section>
      {connecting && <ConnectDrawer domains={domains} onClose={() => setConnecting(false)}
                                    onDone={(text) => { setConnecting(false); setMsg({ tone: "ok", text }); router.refresh(); }} />}
    </div>
  );
}

function RepoCard({ repo: r, domains, may, onMsg }: { repo: CodeRepo; domains: Domain[]; may: boolean; onMsg: (m: Msg) => void }) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [panel, setPanel] = useState<"" | "schedule" | "settings" | "runs">("");
  const [runs, setRuns] = useState<IndexRun[] | null>(null);
  const [cron, setCron] = useState(r.schedule_cron ?? PRESETS[1].cron);
  const [branch, setBranch] = useState(r.branch);
  const [doms, setDoms] = useState<string[]>(r.domain_ids);
  const [include, setInclude] = useState(r.include_globs.join(", "));
  const [exclude, setExclude] = useState(r.exclude_globs.join(", "));
  const busy = r.refreshing || r.status === "INDEXING";
  const act = (fn: () => Promise<{ ok: boolean; error?: string }>, ok: string) => start(async () => {
    const res = await fn();
    onMsg(res.ok ? { tone: "ok", text: ok } : { tone: toneOf(res.error ?? ""), text: res.error ?? "Failed" });
    if (res.ok) router.refresh();
  });
  const split = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
  const kinds = Object.entries(r.stats?.by_kind ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 5);
  return (
    <article className="rounded-xl border border-border/80 bg-card">
      <div className="flex flex-wrap items-start gap-3 p-4">
        <span className="grid h-9 w-9 place-items-center rounded-lg bg-slate-100 text-slate-700">{r.provider === "GITHUB" ? <Github className="h-4 w-4" /> : <GitBranch className="h-4 w-4" />}</span>
        <div className="min-w-[14rem] flex-1">
          <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">{r.name}
            <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ring-inset", STATUS_TONE[busy ? "INDEXING" : r.status] ?? STATUS_TONE.NEW)}>
              {busy ? "indexing" : r.status.toLowerCase()}</span>
            {!r.enabled && <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">disabled</span>}
          </p>
          <a href={r.git_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono text-[11px] text-muted-foreground hover:text-primary">
            {r.git_url.replace(/^https:\/\//, "")}<ExternalLink className="h-3 w-3" /></a>
          <p className="mt-1 text-xs text-muted-foreground">
            branch <span className="font-mono">{r.branch}</span>{r.last_commit ? <> @ <span className="font-mono">{r.last_commit.slice(0, 8)}</span></> : ""}
            {" · "}{r.last_indexed ? `indexed ${ago(r.last_indexed)}` : "not indexed yet"}
            {r.schedule_cron ? <> · <CalendarClock className="inline h-3 w-3" /> {r.schedule_cron}</> : ""}
            {r.domain_ids.length ? ` · ${r.domain_ids.map((d) => domains.find((x) => x.domain_id === d)?.domain_name ?? d).join(", ")}` : " · all domains"}
          </p>
          {r.status === "FAILED" && r.error && <p className="mt-1.5 flex items-start gap-1 text-xs text-destructive"><TriangleAlert className="mt-0.5 h-3 w-3 shrink-0" />{r.error.slice(0, 400)}</p>}
        </div>
        <div className="grid grid-cols-3 gap-2 text-center text-xs">
          {[["files", r.stats?.files], ["chunks", r.stats?.chunks], ["edges", r.stats?.edges]].map(([k, v]) => (
            <div key={String(k)} className="rounded-lg bg-muted/50 px-3 py-1.5"><p className="text-sm font-semibold tabular-nums">{Number(v ?? 0).toLocaleString()}</p><p className="text-[10px] text-muted-foreground">{k}</p></div>
          ))}
        </div>
      </div>
      {(kinds.length > 0 || (r.stats?.dbt_projects ?? []).length > 0) && (
        <div className="flex flex-wrap gap-1.5 border-t px-4 py-2 text-[11px]">
          {(r.stats?.dbt_projects ?? []).map((p) => <span key={p} className="rounded-full bg-orange-50 px-2 py-0.5 text-orange-700 ring-1 ring-inset ring-orange-100">dbt: {p}</span>)}
          {kinds.map(([k, n]) => <span key={k} className="rounded-full bg-muted px-2 py-0.5 text-muted-foreground">{k.toLowerCase().replace(/_/g, " ")} {n}</span>)}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-1.5 border-t px-3 py-2">
        <Button size="sm" variant="outline" disabled={pending || busy} onClick={() => act(() => refreshRepo(r.repo_id), "Refresh started; only changed files are read.")}>
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}{busy ? "Indexing…" : "Refresh now"}</Button>
        <Link href={`/code?repo=${r.repo_id}`} className="inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-medium hover:bg-muted"><FileCode2 className="h-3.5 w-3.5" />Browse</Link>
        <Button size="sm" variant="ghost" onClick={() => { setPanel(panel === "runs" ? "" : "runs"); if (!runs) indexRuns(r.repo_id).then((x) => x.ok && setRuns(x.data.runs)); }}>History</Button>
        {may && <Button size="sm" variant="ghost" onClick={() => setPanel(panel === "schedule" ? "" : "schedule")}><CalendarClock className="h-3.5 w-3.5" />Schedule</Button>}
        {may && <Button size="sm" variant="ghost" onClick={() => setPanel(panel === "settings" ? "" : "settings")}><Settings2 className="h-3.5 w-3.5" />Settings</Button>}
        {may && <Button size="sm" variant="ghost" className="ml-auto text-muted-foreground hover:text-destructive" disabled={pending}
                        onClick={() => { if (window.confirm(`Disconnect ${r.name}? Its index is removed; the Snowflake Git repository and secret stay.`)) act(() => removeRepo(r.repo_id), `${r.name} disconnected`); }}>
          <Trash2 className="h-3.5 w-3.5" />Disconnect</Button>}
      </div>
      {panel === "schedule" && (
        <div className="space-y-2 border-t bg-muted/20 px-4 py-3 text-xs">
          <div className="flex flex-wrap gap-1.5">
            {PRESETS.map((p) => <button key={p.cron} type="button" onClick={() => setCron(p.cron)}
                                        className={cn("rounded-full border px-2.5 py-1", cron === p.cron ? "border-primary bg-primary/10 text-primary" : "bg-card hover:border-primary/40")}>{p.label}</button>)}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Input value={cron} onChange={(e) => setCron(e.target.value)} className="h-8 w-56 font-mono text-xs" aria-label="Cron with time zone" />
            <Button size="sm" disabled={pending} onClick={() => act(() => scheduleRepo(r.repo_id, cron), `Scheduled: ${cron} (a Snowflake task)`)}><Check className="h-3.5 w-3.5" />Save schedule</Button>
            {r.schedule_cron && <Button size="sm" variant="ghost" disabled={pending} onClick={() => act(() => scheduleRepo(r.repo_id, null), "Schedule removed")}>Remove schedule</Button>}
          </div>
          <p className="text-muted-foreground">Runs as a Snowflake task with the warehouse of this workspace; only files that changed since the last refresh are read.</p>
        </div>
      )}
      {panel === "settings" && (
        <div className="grid gap-3 border-t bg-muted/20 px-4 py-3 text-xs md:grid-cols-2">
          <label className="space-y-1">Branch<Input value={branch} onChange={(e) => setBranch(e.target.value)} className="h-8 font-mono text-xs" /></label>
          <div className="space-y-1">Domains it serves (none means all)
            <div className="flex flex-wrap gap-1">{domains.map((d) => {
              const on = doms.includes(d.domain_id);
              return <button key={d.domain_id} type="button" onClick={() => setDoms(on ? doms.filter((x) => x !== d.domain_id) : [...doms, d.domain_id])}
                             className={cn("rounded-full border px-2 py-0.5", on ? "border-primary bg-primary/10 text-primary" : "bg-card")}>{d.domain_name}</button>;
            })}</div>
          </div>
          <label className="space-y-1">Only these paths (globs, comma separated)<Input value={include} onChange={(e) => setInclude(e.target.value)} placeholder="models/**, macros/**" className="h-8 font-mono text-xs" /></label>
          <label className="space-y-1">Skip these paths<Input value={exclude} onChange={(e) => setExclude(e.target.value)} placeholder="models/legacy/**" className="h-8 font-mono text-xs" /></label>
          <div className="flex items-center gap-2 md:col-span-2">
            <label className="flex items-center gap-1.5"><input type="checkbox" checked={r.enabled} onChange={() => act(() => updateRepo(r.repo_id, { enabled: !r.enabled }), r.enabled ? "Disabled: no longer used in prompts" : "Enabled")} />Used in prompts</label>
            <Button size="sm" className="ml-auto" disabled={pending}
                    onClick={() => act(() => updateRepo(r.repo_id, { branch, domain_ids: doms, include_globs: split(include), exclude_globs: split(exclude) }),
                                       "Saved. A changed branch or paths starts a clean index on the next refresh.")}>
              <Check className="h-3.5 w-3.5" />Save settings</Button>
          </div>
        </div>
      )}
      {panel === "runs" && (
        <div className="border-t px-4 py-3 text-xs">
          {!runs ? <p className="flex items-center gap-2 text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Loading…</p> : runs.length ? (
            <table className="w-full">
              <thead className="text-left text-[10px] uppercase tracking-wide text-muted-foreground"><tr><th className="py-1">Started</th><th>Status</th><th>Commit</th><th className="text-right">Changed</th><th className="text-right">Chunks</th><th className="text-right">Time</th><th>By</th></tr></thead>
              <tbody className="divide-y">
                {runs.map((x) => (
                  <tr key={x.index_run_id} title={x.error ?? ""}>
                    <td className="py-1.5">{ago(x.started_at)}</td>
                    <td><span className={cn("rounded-full px-1.5 py-0.5 text-[10px] ring-1 ring-inset", STATUS_TONE[x.status === "SUCCEEDED" ? "READY" : x.status === "RUNNING" ? "INDEXING" : "FAILED"])}>{x.status.toLowerCase()}</span></td>
                    <td className="font-mono">{(x.commit_sha ?? "").slice(0, 8)}</td>
                    <td className="text-right tabular-nums">{x.files_changed ?? 0}{x.files_removed ? ` / -${x.files_removed}` : ""}</td>
                    <td className="text-right tabular-nums">{x.chunks ?? 0}</td>
                    <td className="text-right tabular-nums">{x.duration_ms ? `${Math.round(x.duration_ms / 1000)} s` : ""}</td>
                    <td className="text-muted-foreground">{x.triggered_by}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p className="text-muted-foreground">No refreshes yet.</p>}
        </div>
      )}
    </article>
  );
}

function ConnectDrawer({ domains, onClose, onDone }: { domains: Domain[]; onClose: () => void; onDone: (text: string) => void }) {
  useScrollLock();
  const [setup, setSetup] = useState<CodeSetup | null>(null);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const [f, setF] = useState({ name: "", git_url: "", branch: "main", api_integration: "", existing_git_repository: "",
                                auth: "token" as "token" | "secret" | "public", secret_name: "", username: "", token: "",
                                domain_ids: [] as string[], include: "", exclude: "", kind: "DBT", index_now: true });
  useEffect(() => { loadSetup().then((r) => (r.ok ? setSetup(r.data) : setError(r.error))); }, []);
  const host = (() => { try { return new URL(f.git_url).origin; } catch { return ""; } })();
  const integrations = setup?.integrations.filter((i) => i.usable !== false) ?? [];
  const fits = integrations.filter((i) => !host || !(i.allowed_prefixes ?? []).length || (i.allowed_prefixes ?? []).some((p) => f.git_url.startsWith(p.replace(/\/$/, "")) || host.startsWith(p.replace(/\/$/, ""))));
  const set = (patch: Partial<typeof f>) => setF({ ...f, ...patch });
  const nameFromUrl = (url: string) => (url.split("/").pop() ?? "").replace(/\.git$/, "").replace(/[^A-Za-z0-9]+/g, "_").toUpperCase().replace(/^_+|_+$/g, "");
  const submit = () => start(async () => {
    setError("");
    const r = await connectRepo({
      name: f.name, git_url: f.git_url, branch: f.branch, api_integration: f.api_integration,
      existing_git_repository: f.existing_git_repository || null,
      secret_name: f.auth === "secret" ? f.secret_name : null, username: f.auth === "token" ? f.username || null : null,
      token: f.auth === "token" ? f.token || null : null, domain_ids: f.domain_ids,
      include_globs: f.include.split(",").map((s) => s.trim()).filter(Boolean), exclude_globs: f.exclude.split(",").map((s) => s.trim()).filter(Boolean),
      kind: f.kind, index_now: f.index_now,
    });
    if (r.ok) onDone(`${r.data.name} connected${f.index_now ? "; indexing started" : ""}.`);
    else setError(r.error);
  });
  const valid = f.name.trim().length >= 2 && /^https:\/\//.test(f.git_url) && f.api_integration && (f.auth !== "token" || f.token.trim()) && (f.auth !== "secret" || f.secret_name);
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Connect repository">
      <button type="button" aria-label="Close" className="absolute inset-0 bg-black/30" onClick={onClose} />
      <aside className="relative flex h-full w-[620px] max-w-full flex-col border-l bg-background shadow-2xl">
        <header className="flex items-center gap-3 border-b px-5 py-4">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-indigo-50 text-indigo-600 ring-1 ring-inset ring-indigo-100"><GitBranch className="h-4 w-4" /></span>
          <div className="flex-1"><h3 className="text-base font-semibold">Connect a code repository</h3>
            <p className="text-xs text-muted-foreground">Snowflake clones it with a Git integration; the token goes straight into a Snowflake secret.</p></div>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-md p-1 hover:bg-muted"><X className="h-4 w-4" /></button>
        </header>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain px-5 py-4 text-sm">
          {!setup && !error && <p className="flex items-center gap-2 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Reading Git integrations…</p>}
          <label className="block space-y-1 text-xs font-medium">Repository URL (https)
            <Input value={f.git_url} onChange={(e) => set({ git_url: e.target.value.trim(), name: f.name || nameFromUrl(e.target.value.trim()) })}
                   placeholder="https://github.com/acme/analytics-dbt" className="font-mono" /></label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-xs font-medium">Name<Input value={f.name} onChange={(e) => set({ name: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_") })} className="font-mono" /></label>
            <label className="space-y-1 text-xs font-medium">Branch<Input value={f.branch} onChange={(e) => set({ branch: e.target.value })} className="font-mono" /></label>
          </div>
          <label className="block space-y-1 text-xs font-medium">Snowflake Git integration
            <Select value={f.api_integration} onChange={(e) => set({ api_integration: e.target.value })}>
              <option value="">Choose…</option>
              {fits.map((i) => <option key={i.name} value={i.name}>{i.name}{i.allowed_prefixes?.length ? ` (${i.allowed_prefixes.join(", ")})` : ""}</option>)}
            </Select>
          </label>
          {setup && !fits.length && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
              <p className="font-medium">No usable Git integration allows {host || "this host"}.</p>
              <p className="mt-1">An account admin can create one, then grant USAGE to your role:</p>
              <pre className="mt-2 overflow-x-auto rounded bg-white/70 p-2 font-mono text-[11px]">{`CREATE API INTEGRATION GIT_CODE_CONTEXT
  API_PROVIDER = GIT_HTTPS_API
  API_ALLOWED_PREFIXES = ('${host || "https://github.com/<org>"}')
  ALLOWED_AUTHENTICATION_SECRETS = ALL
  ENABLED = TRUE;`}</pre>
            </div>
          )}
          {!!setup?.repositories.length && (
            <label className="block space-y-1 text-xs font-medium">Or reuse a Git repository object already in Snowflake (optional)
              <Select value={f.existing_git_repository} onChange={(e) => {
                const repo = setup.repositories.find((x) => x.fqn === e.target.value);
                set({ existing_git_repository: e.target.value, api_integration: repo?.api_integration ?? f.api_integration, git_url: repo?.origin || f.git_url,
                      name: f.name || nameFromUrl(repo?.origin ?? "") });
              }}>
                <option value="">Create a new one</option>
                {setup.repositories.map((x) => <option key={x.fqn} value={x.fqn}>{x.fqn}{x.origin ? ` (${x.origin})` : ""}</option>)}
              </Select>
            </label>
          )}
          <div className="space-y-2">
            <p className="text-xs font-medium">Credentials</p>
            <div className="flex rounded-lg border p-0.5 text-xs">
              {([["token", "New read-only token"], ["secret", "Existing secret"], ["public", "Public repository"]] as const).map(([k, l]) => (
                <button key={k} type="button" onClick={() => set({ auth: k })} className={cn("flex-1 rounded-md px-2 py-1", f.auth === k ? "bg-primary text-primary-foreground" : "text-muted-foreground")}>{l}</button>
              ))}
            </div>
            {f.auth === "token" && (
              <div className="grid gap-2 sm:grid-cols-2">
                <Input value={f.username} onChange={(e) => set({ username: e.target.value })} placeholder="Git username (any value for GitHub tokens)" className="text-xs" />
                <Input type="password" value={f.token} onChange={(e) => set({ token: e.target.value })} placeholder="Personal access token (read-only)" className="text-xs" autoComplete="off" />
                <p className="text-[11px] text-muted-foreground sm:col-span-2">Stored only as a Snowflake secret; use a fine-grained, read-only token limited to this repository.</p>
              </div>
            )}
            {f.auth === "secret" && (
              <Select value={f.secret_name} onChange={(e) => set({ secret_name: e.target.value })} className="text-xs">
                <option value="">Choose a PASSWORD secret…</option>
                {(setup?.secrets ?? []).map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
              </Select>
            )}
          </div>
          <div className="space-y-1.5">
            <p className="text-xs font-medium">Domains it serves <span className="font-normal text-muted-foreground">(none selected means every domain)</span></p>
            <div className="flex flex-wrap gap-1.5">{domains.map((d) => {
              const on = f.domain_ids.includes(d.domain_id);
              return <button key={d.domain_id} type="button" onClick={() => set({ domain_ids: on ? f.domain_ids.filter((x) => x !== d.domain_id) : [...f.domain_ids, d.domain_id] })}
                             className={cn("rounded-full border px-2.5 py-0.5 text-xs", on ? "border-primary bg-primary/10 text-primary" : "text-muted-foreground")}>{d.domain_name}</button>;
            })}</div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-xs font-medium">Only these paths<Input value={f.include} onChange={(e) => set({ include: e.target.value })} placeholder="models/**, macros/**, tests/**" className="font-mono text-xs" /></label>
            <label className="space-y-1 text-xs font-medium">Skip these paths<Input value={f.exclude} onChange={(e) => set({ exclude: e.target.value })} placeholder="models/legacy/**" className="font-mono text-xs" /></label>
          </div>
          <p className="text-[11px] text-muted-foreground">Never indexed: profiles.yml, .env files, keys and certificates, target/, dbt_packages/. Key and token assignments inside files are redacted.</p>
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={f.index_now} onChange={() => set({ index_now: !f.index_now })} />Index now</label>
          {error && <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">{error}</p>}
        </div>
        <footer className="flex items-center gap-2 border-t px-5 py-3">
          <span className="ml-auto" />
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button disabled={pending || !valid} onClick={submit}>{pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}Connect</Button>
        </footer>
      </aside>
    </div>
  );
}
