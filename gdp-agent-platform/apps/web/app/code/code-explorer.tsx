"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import {
  Activity, ArrowDownRight, ArrowLeft, ArrowRight, ArrowUpRight, Boxes, CalendarClock, CheckCircle2, ChevronDown, ChevronRight,
  ExternalLink, FileCode2, Files, Flame, FolderGit2, FolderOpen, GitBranch, GitCommitHorizontal, LayoutGrid, Loader2, Network,
  Route, Search, Sparkles, Table2, TriangleAlert, X,
} from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { ago } from "../skills/types";
import {
  codeUsage, dependencyPath, impactOf, neighborhood, readFile, repoCatalog, repoFiles, searchCode,
  type Architecture, type Catalog, type CatalogModel, type CodeFile, type CodeHit, type CodeRepo, type CodeUsage, type GraphNode,
  type Impact, type Neighborhood, type PathStep, type RepoFile,
} from "./actions";

export type RepoSummary = {
  dbt?: { projects?: { name: string; root: string }[]; top_macros?: { name: string; uses: number }[]; sources?: string[] } | null;
  usage?: Record<string, { uses: number; runs: number }>;
  architecture?: Architecture | null;
};
type Tab = "overview" | "models" | "sources" | "files" | "lineage" | "usage";
const TABS: { id: Tab; label: string; icon: typeof LayoutGrid }[] = [
  { id: "overview", label: "Overview", icon: LayoutGrid }, { id: "models", label: "Models", icon: Table2 },
  { id: "sources", label: "Sources and macros", icon: Boxes }, { id: "files", label: "Files", icon: Files },
  { id: "lineage", label: "Lineage", icon: Network }, { id: "usage", label: "AI usage", icon: Activity },
];
const KINDS = [
  { id: "", label: "Everything" }, { id: "DBT_MODEL", label: "Models" }, { id: "DBT_MACRO", label: "Macros" },
  { id: "DBT_TEST", label: "Tests" }, { id: "DBT_SCHEMA_YML", label: "Schema" }, { id: "DBT_SOURCE", label: "Sources" },
  { id: "SQL", label: "SQL" }, { id: "PY_FUNC", label: "Python" }, { id: "DOC", label: "Docs" },
];
const KIND_TONE: Record<string, string> = {
  DBT_MODEL: "bg-orange-50 text-orange-700 ring-orange-100", DBT_SNAPSHOT: "bg-amber-50 text-amber-700 ring-amber-100",
  DBT_MACRO: "bg-violet-50 text-violet-700 ring-violet-100", DBT_TEST: "bg-emerald-50 text-emerald-700 ring-emerald-100",
  DBT_SCHEMA_YML: "bg-sky-50 text-sky-700 ring-sky-100", DBT_SOURCE: "bg-teal-50 text-teal-700 ring-teal-100",
  SQL: "bg-indigo-50 text-indigo-700 ring-indigo-100", PY_FUNC: "bg-yellow-50 text-yellow-800 ring-yellow-100",
  DOC: "bg-slate-100 text-slate-600 ring-slate-200", CONFIG: "bg-slate-100 text-slate-600 ring-slate-200",
};
const VIA_LABEL: Record<string, string> = {
  REF: "ref", SOURCE: "source", MACRO_USE: "macro", CALLS: "calls", IMPORTS: "imports", READS: "reads table", WRITES: "writes",
};
const STATUS_DOT: Record<string, string> = { READY: "bg-emerald-500", INDEXING: "bg-indigo-500 animate-pulse", FAILED: "bg-rose-500", NEW: "bg-slate-400" };
const kindLabel = (k: string) => k.replace(/^DBT_/, "").replace("SCHEMA_YML", "schema").replace("PY_FUNC", "python").replace(/_/g, " ").toLowerCase();
const pct = (n: number, d: number) => (d ? Math.round((n / d) * 100) : 0);
const bytes = (n: number | null) => (n == null ? "" : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);
const short = (name: string) => name.split(".").pop() ?? name;

function KindPill({ kind }: { kind: string }) {
  return <span className={cn("rounded-full px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset", KIND_TONE[kind] ?? KIND_TONE.DOC)}>{kindLabel(kind)}</span>;
}

function Stat({ label, value, hint, tone }: { label: string; value: string | number; hint?: string; tone?: "warn" | "good" }) {
  return (
    <div className="surface px-4 py-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={cn("mt-0.5 text-2xl font-semibold tabular-nums", tone === "warn" && "text-amber-600", tone === "good" && "text-emerald-600")}>
        {typeof value === "number" ? value.toLocaleString() : value}</p>
      {hint && <p className="truncate text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

function Meter({ label, value, total, hint }: { label: string; value: number; total: number; hint?: string }) {
  const p = pct(value, total);
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between text-xs"><span className="font-medium">{label}</span>
        <span className="tabular-nums text-muted-foreground">{value} of {total} · {p}%</span></div>
      <div className="h-2 overflow-hidden rounded-full bg-muted">
        <div className={cn("h-full rounded-full", p >= 80 ? "bg-emerald-500" : p >= 40 ? "bg-amber-500" : "bg-rose-500")} style={{ width: `${p}%` }} />
      </div>
      {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function CodeExplorer({ repos, summary, usage, initial }: {
  repos: CodeRepo[]; summary: Record<string, RepoSummary>; usage: CodeUsage | null;
  initial: { repo?: string; path?: string; line?: string; q?: string; tab?: string; node?: string };
}) {
  const [repoId, setRepoId] = useState(initial.repo ?? (repos.length === 1 ? repos[0].repo_id : ""));
  const [tab, setTab] = useState<Tab>((TABS.find((t) => t.id === initial.tab)?.id) ?? "overview");
  const [q, setQ] = useState(initial.q ?? "");
  const [kind, setKind] = useState("");
  const [hits, setHits] = useState<CodeHit[] | null>(null);
  const [file, setFile] = useState<CodeFile | null>(null);
  const [line, setLine] = useState<number | null>(initial.line ? Number(initial.line) : null);
  const [imp, setImp] = useState<Impact | null>(null);
  const [node, setNode] = useState(initial.node ?? "");
  const [catalogs, setCatalogs] = useState<Record<string, Catalog>>({});
  const [error, setError] = useState("");
  const [searching, startSearch] = useTransition();
  const [opening, startOpen] = useTransition();
  const [loadingCatalog, startCatalog] = useTransition();
  const repo = repos.find((r) => r.repo_id === repoId) ?? null;
  const catalog = repo ? catalogs[repo.repo_id] : undefined;

  const totals = useMemo(() => repos.reduce((t, r) => ({
    files: t.files + (r.stats?.files ?? 0), skipped: t.skipped + (r.stats?.skipped_files ?? 0),
    models: t.models + (r.stats?.by_kind?.DBT_MODEL ?? 0), snapshots: t.snapshots + (r.stats?.by_kind?.DBT_SNAPSHOT ?? 0),
    macros: t.macros + (r.stats?.by_kind?.DBT_MACRO ?? 0), edges: t.edges + (r.stats?.edges ?? 0),
    failing: t.failing + (r.status === "FAILED" ? 1 : 0),
  }), { files: 0, skipped: 0, models: 0, snapshots: 0, macros: 0, edges: 0, failing: 0 }), [repos]);

  const syncUrl = (patch: Record<string, string | null>) => {
    const p = new URLSearchParams(window.location.search);
    for (const [k, v] of Object.entries(patch)) if (v) p.set(k, v); else p.delete(k);
    // shallow: keep the URL shareable without re-rendering the server page
    window.history.replaceState(window.history.state, "", `${window.location.pathname}?${p}`);
  };
  useEffect(() => {
    if (!repo || catalogs[repo.repo_id]) return;
    startCatalog(async () => {
      const r = await repoCatalog(repo.repo_id);
      if (r.ok) setCatalogs((c) => ({ ...c, [repo.repo_id]: r.data }));
    });
  }, [repo, catalogs]);

  const chooseRepo = (id: string) => { setRepoId(id); setFile(null); setHits(null); setNode(""); syncUrl({ repo: id || null, path: null, line: null, q: null, node: null }); };
  const chooseTab = (t: Tab) => { setTab(t); setFile(null); setHits(null); syncUrl({ tab: t === "overview" ? null : t, path: null, line: null }); };
  const runSearch = (text = q, k = kind) => {
    if (!text.trim()) { setHits(null); syncUrl({ q: null }); return; }
    setFile(null);
    startSearch(async () => {
      const r = await searchCode(text.trim(), repoId || undefined, k || undefined);
      if (r.ok) { setHits(r.data.hits); setError(""); } else setError(r.error);
    });
    syncUrl({ q: text.trim() });
  };
  const open = (rid: string, path: string, at?: number, name?: string | null) => startOpen(async () => {
    const r = await readFile(rid, path);
    if (!r.ok) { setError(r.error); return; }
    if (rid !== repoId) setRepoId(rid);
    setFile(r.data); setLine(at ?? null); setError("");
    syncUrl({ repo: rid, path, line: at ? String(at) : null });
    const chunk = r.data.chunks.find((c) => at && c.start_line <= at && at <= c.end_line) ?? (name ? r.data.chunks.find((c) => c.name === name) : null)
      ?? r.data.chunks.find((c) => c.kind !== "DOC" && c.kind !== "CONFIG" && c.name);
    if (chunk?.name && chunk.kind !== "DOC" && chunk.kind !== "CONFIG") {
      const l = await impactOf(chunk.name, rid);
      setImp(l.ok && (l.data.uses.length || l.data.impact.length) ? l.data : null);
    } else setImp(null);
  });
  const showLineage = (name: string) => { setNode(name); setTab("lineage"); setFile(null); setHits(null); syncUrl({ tab: "lineage", node: name, path: null }); };
  useEffect(() => {
    if (initial.repo && initial.path) open(initial.repo, initial.path, initial.line ? Number(initial.line) : undefined);
    if (initial.q) runSearch(initial.q);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!repos.length) {
    return (
      <div className="surface flex flex-col items-center px-6 py-14 text-center">
        <span className="grid h-12 w-12 place-items-center rounded-2xl bg-indigo-50 text-indigo-600 ring-1 ring-inset ring-indigo-100"><FolderGit2 className="h-6 w-6" /></span>
        <p className="mt-3 text-sm font-semibold">No repositories connected</p>
        <p className="mt-1 max-w-md text-xs text-muted-foreground">Connect the client&apos;s dbt or SQL repository in Admin, Integrations. Once indexed, dbt review, QA tests,
          data quality checks, STTM and the copilot reuse its models, macros and tests, and the dbt workspace builds on it.</p>
        <Link href="/admin?section=integrations" className="mt-4 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground">Connect a repository</Link>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Repositories" value={repos.length} hint={totals.failing ? `${totals.failing} failed to index` : "all indexed"} tone={totals.failing ? "warn" : undefined} />
        <Stat label="Files indexed" value={totals.files} hint={totals.skipped ? `${totals.skipped} skipped (unreadable)` : "nothing skipped"} />
        <Stat label="dbt models" value={totals.models} hint={totals.snapshots ? `+ ${totals.snapshots} snapshot${totals.snapshots === 1 ? "" : "s"}` : "no snapshots"} />
        <Stat label="Macros" value={totals.macros} hint="defined in the repositories" />
        <Stat label="Graph links" value={totals.edges} hint="refs, sources, macro and function calls, table reads" />
        <Stat label="AI citations, 30 days" value={usage?.citations ?? 0} hint={usage ? `in ${usage.runs} run${usage.runs === 1 ? "" : "s"}` : "no usage data"} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[250px_minmax(0,1fr)]">
        <aside className="space-y-1 lg:sticky lg:top-4 lg:self-start">
          {repos.length > 1 && (
            <button type="button" onClick={() => chooseRepo("")}
                    className={cn("flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-sm", !repoId ? "bg-primary/10 font-medium text-primary" : "text-muted-foreground hover:bg-muted")}>
              <FolderGit2 className="h-4 w-4" />All repositories</button>
          )}
          {repos.map((r) => (
            <button key={r.repo_id} type="button" onClick={() => chooseRepo(r.repo_id)}
                    className={cn("w-full rounded-lg border px-2.5 py-2 text-left transition", repoId === r.repo_id ? "border-primary/30 bg-primary/5" : "border-transparent hover:bg-muted")}>
              <span className="flex items-center gap-2 text-sm font-medium">
                <span className={cn("h-2 w-2 shrink-0 rounded-full", STATUS_DOT[r.status] ?? STATUS_DOT.NEW)} title={r.status.toLowerCase()} />
                <span className="truncate">{r.name}</span>
                {!r.enabled && <span className="ml-auto rounded bg-muted px-1 text-[10px] text-muted-foreground">off</span>}
              </span>
              <span className="mt-0.5 flex items-center gap-1 truncate pl-4 font-mono text-[11px] text-muted-foreground"><GitBranch className="h-3 w-3 shrink-0" />{r.branch}</span>
              <span className="block truncate pl-4 text-[11px] text-muted-foreground">{(r.stats?.files ?? 0).toLocaleString()} files · {r.last_indexed ? `indexed ${ago(r.last_indexed)}` : "not indexed"}</span>
            </button>
          ))}
          <Link href="/admin?section=integrations" className="mt-2 block px-2.5 text-[11px] text-primary hover:underline">Manage repositories in Admin</Link>
        </aside>

        <div className="min-w-0 space-y-4">
          {repo && <RepoHeader repo={repo} catalog={catalog} />}

          <div className="surface space-y-2 p-2">
            <form className="relative" onSubmit={(e) => { e.preventDefault(); runSearch(); }}>
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search ${repo ? repo.name : "all repositories"}: a model, table, column, macro, function or a question`}
                     className="h-10 border-0 pl-9 pr-9 shadow-none focus-visible:ring-0" aria-label="Search code" />
              {searching ? <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-muted-foreground" />
                : q && <button type="button" aria-label="Clear search" onClick={() => { setQ(""); runSearch(""); }} className="absolute right-3 top-1/2 -translate-y-1/2 rounded p-0.5 hover:bg-muted"><X className="h-4 w-4" /></button>}
            </form>
            <div className="flex flex-wrap gap-1 px-1 pb-1">
              {KINDS.map((k) => (
                <button key={k.id || "all"} type="button" onClick={() => { setKind(k.id); if (q.trim()) runSearch(q, k.id); }} aria-pressed={kind === k.id}
                        className={cn("rounded-full border px-2.5 py-0.5 text-[11px]", kind === k.id ? "border-primary bg-primary/10 text-primary" : "text-muted-foreground hover:border-primary/40")}>{k.label}</button>
              ))}
            </div>
          </div>
          {error && <p role="alert" className="flex items-center gap-2 rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">{error}
            <button type="button" className="ml-auto" aria-label="Dismiss" onClick={() => setError("")}><X className="h-3.5 w-3.5" /></button></p>}

          {file ? (
            <FileView file={file} line={line} imp={imp} loading={opening} back={hits ? "results" : TABS.find((t) => t.id === tab)?.label ?? "overview"}
                      onClose={() => { setFile(null); setImp(null); syncUrl({ path: null, line: null }); }}
                      onPick={(n) => { setQ(n); runSearch(n); }} onLineage={showLineage} />
          ) : hits !== null ? (
            <Results hits={hits} onOpen={open} />
          ) : !repo ? (
            <AllRepos repos={repos} summary={summary} onPick={chooseRepo} />
          ) : (
            <>
              <nav className="flex gap-1 overflow-x-auto border-b" aria-label="Repository views">
                {TABS.map((t) => (
                  <button key={t.id} type="button" onClick={() => chooseTab(t.id)} aria-current={tab === t.id ? "page" : undefined}
                          className={cn("inline-flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2 text-sm", tab === t.id ? "border-primary font-medium text-primary" : "border-transparent text-muted-foreground hover:text-foreground")}>
                    <t.icon className="h-4 w-4" />{t.label}
                  </button>
                ))}
              </nav>
              {!catalog && (tab === "overview" || tab === "models" || tab === "sources") ? (
                <p className="flex items-center gap-2 py-6 text-sm text-muted-foreground">{loadingCatalog ? <><Loader2 className="h-4 w-4 animate-spin" />Reading the catalog…</> : "The catalog could not be read."}</p>
              ) : tab === "overview" && catalog ? (
                <OverviewTab repo={repo} catalog={catalog} summary={summary[repo.repo_id] ?? {}} onOpen={open} onLineage={showLineage} onSearch={(t) => { setQ(t); runSearch(t); }} />
              ) : tab === "models" && catalog ? (
                <ModelsTab repo={repo} catalog={catalog} onOpen={open} onLineage={showLineage} />
              ) : tab === "sources" && catalog ? (
                <SourcesTab repo={repo} catalog={catalog} onOpen={open} onLineage={showLineage} />
              ) : tab === "files" ? (
                <FilesTab repo={repo} onOpen={open} />
              ) : tab === "lineage" ? (
                <LineageTab repo={repo} catalog={catalog} node={node} onNode={(n) => { setNode(n); syncUrl({ node: n }); }} onOpen={open} />
              ) : (
                <UsageTab repo={repo} onOpen={open} />
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function RepoHeader({ repo, catalog }: { repo: CodeRepo; catalog?: Catalog }) {
  const host = repo.git_url.replace(/\.git$/, "");
  const github = /github\.com|gitlab\.com/.test(host);
  return (
    <section className="surface flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3">
      <div className="min-w-0">
        <p className="flex items-center gap-2 text-base font-semibold">{repo.name}
          <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ring-inset",
            repo.status === "READY" ? "bg-emerald-50 text-emerald-700 ring-emerald-100" : repo.status === "FAILED" ? "bg-rose-50 text-rose-700 ring-rose-100" : "bg-indigo-50 text-indigo-700 ring-indigo-100")}>
            {repo.status.toLowerCase()}</span>
          {repo.use_for_dbt !== false && <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700 ring-1 ring-inset ring-emerald-100">dbt workspace{repo.dbt_project_dir ? `: ${repo.dbt_project_dir}/` : ""}</span>}
        </p>
        <a href={repo.git_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono text-[11px] text-muted-foreground hover:text-primary">
          {repo.git_url.replace(/^https:\/\//, "")}<ExternalLink className="h-3 w-3" /></a>
      </div>
      <dl className="flex flex-wrap gap-x-5 gap-y-1 text-xs">
        <div><dt className="text-[10px] uppercase tracking-wide text-muted-foreground">Branch</dt><dd className="flex items-center gap-1 font-mono"><GitBranch className="h-3 w-3" />{repo.branch}</dd></div>
        <div><dt className="text-[10px] uppercase tracking-wide text-muted-foreground">Commit</dt>
          <dd className="flex items-center gap-1 font-mono"><GitCommitHorizontal className="h-3 w-3" />
            {repo.last_commit ? (github ? <a href={`${host}/commit/${repo.last_commit}`} target="_blank" rel="noreferrer" className="hover:text-primary hover:underline">{repo.last_commit.slice(0, 8)}</a> : repo.last_commit.slice(0, 8)) : "none"}</dd></div>
        <div><dt className="text-[10px] uppercase tracking-wide text-muted-foreground">Indexed</dt><dd>{repo.last_indexed ? ago(repo.last_indexed) : "never"}</dd></div>
        <div><dt className="text-[10px] uppercase tracking-wide text-muted-foreground">Refresh</dt><dd className="flex items-center gap-1"><CalendarClock className="h-3 w-3" />{repo.schedule_cron ?? "on demand"}</dd></div>
        {catalog && <div><dt className="text-[10px] uppercase tracking-wide text-muted-foreground">dbt</dt><dd>{(repo.stats?.dbt_projects ?? []).join(", ") || "no project"}</dd></div>}
      </dl>
      {repo.status === "FAILED" && repo.error && <p className="flex w-full items-start gap-1 text-xs text-destructive"><TriangleAlert className="mt-0.5 h-3 w-3 shrink-0" />{repo.error.slice(0, 300)}</p>}
      {!!repo.stats?.pending_files && <p className="w-full text-xs text-amber-700">{repo.stats.pending_files} changed files are still waiting for the next refresh.</p>}
    </section>
  );
}

function AllRepos({ repos, summary, onPick }: { repos: CodeRepo[]; summary: Record<string, RepoSummary>; onPick: (id: string) => void }) {
  return (
    <section className="grid gap-3 lg:grid-cols-2">
      {repos.map((r) => {
        const a = summary[r.repo_id]?.architecture;
        return (
          <button key={r.repo_id} type="button" onClick={() => onPick(r.repo_id)} className="surface space-y-2 p-4 text-left transition hover:shadow-hover">
            <p className="flex items-center gap-2 text-sm font-semibold"><span className={cn("h-2 w-2 rounded-full", STATUS_DOT[r.status] ?? STATUS_DOT.NEW)} />{r.name}
              <span className="ml-auto font-mono text-[11px] font-normal text-muted-foreground">{r.branch}</span></p>
            <p className="text-xs text-muted-foreground">{r.stats?.files ?? 0} files · {r.stats?.by_kind?.DBT_MODEL ?? 0} models · {r.stats?.by_kind?.DBT_MACRO ?? 0} macros · {r.stats?.edges ?? 0} links</p>
            {a && <p className="flex flex-wrap gap-1 text-[11px]">{Object.entries(a.languages ?? {}).map(([l, n]) => <span key={l} className="rounded bg-muted px-1.5 py-0.5">{l || "other"} {n}</span>)}</p>}
          </button>
        );
      })}
    </section>
  );
}

function Results({ hits, onOpen }: { hits: CodeHit[]; onOpen: (repo: string, path: string, at?: number, name?: string | null) => void }) {
  return (
    <section className="space-y-2">
      <p className="text-xs text-muted-foreground">{hits.length} result{hits.length === 1 ? "" : "s"} · <Sparkles className="inline h-3 w-3 text-violet-500" /> marks a match by meaning rather than by name</p>
      {hits.map((h) => (
        <button key={h.chunk_id} type="button" onClick={() => onOpen(h.repo_id, h.path, h.start_line, h.name)}
                className="w-full rounded-xl border border-border/80 bg-card p-3 text-left transition hover:border-primary/30 hover:shadow-card">
          <p className="flex flex-wrap items-center gap-1.5 text-sm font-medium"><KindPill kind={h.kind} />{h.name ?? h.path.split("/").pop()}
            {h.match === "search" && <Sparkles className="h-3 w-3 text-violet-500" aria-label="semantic match" />}</p>
          <p className="truncate font-mono text-[11px] text-muted-foreground">{h.repo_name}:{h.path}:{h.start_line}-{h.end_line}</p>
          <pre className="mt-1.5 line-clamp-3 whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">{h.text}</pre>
        </button>
      ))}
      {!hits.length && <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">Nothing matches. Try a table, column or macro name.</p>}
    </section>
  );
}

type OpenFn = (repo: string, path: string, at?: number, name?: string | null) => void;

function OverviewTab({ repo, catalog, summary, onOpen, onLineage, onSearch }: {
  repo: CodeRepo; catalog: Catalog; summary: RepoSummary; onOpen: OpenFn; onLineage: (n: string) => void; onSearch: (t: string) => void;
}) {
  const t = catalog.totals;
  const a = summary.architecture;
  const real = catalog.models.filter((m) => m.kind === "model");
  const untested = real.filter((m) => !m.tests);
  const undocumented = real.filter((m) => !m.schema_path);
  const hardCoded = catalog.models.filter((m) => m.hard_coded.length);
  const hotspots = (a?.hotspots ?? []).filter((h) => h.dependents >= 2);
  const langs = Object.entries(a?.languages ?? {});
  const langTotal = langs.reduce((n, [, v]) => n + v, 0);
  const issue = (title: string, items: CatalogModel[], detail: (m: CatalogModel) => string) => items.length > 0 && (
    <div>
      <p className="mb-1 text-xs font-medium">{title} <span className="text-muted-foreground">({items.length})</span></p>
      <div className="flex flex-wrap gap-1">{items.slice(0, 12).map((m) => (
        <button key={m.name} type="button" onClick={() => onOpen(repo.repo_id, m.path, m.line, m.name)} title={detail(m)}
                className="rounded-md border px-1.5 py-0.5 font-mono text-[11px] hover:border-primary">{m.name}</button>
      ))}</div>
    </div>
  );
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Models" value={t.models} hint={t.snapshots ? `+ ${t.snapshots} snapshot${t.snapshots === 1 ? "" : "s"}` : undefined} />
        <Stat label="Source tables" value={t.source_tables} />
        <Stat label="Macros" value={t.macros} hint={t.unused_macros ? `${t.unused_macros} not called` : "all called"} />
        <Stat label="Tests" value={t.tests} hint="one per test definition" tone={t.tests ? undefined : "warn"} />
        <Stat label="Hard-coded tables" value={t.hard_coded_models} hint="models reading a table without ref or source" tone={t.hard_coded_models ? "warn" : "good"} />
        <Stat label="Graph links" value={repo.stats?.edges ?? 0} />
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <section className="surface space-y-4 p-4">
          <h3 className="text-sm font-semibold">Coverage</h3>
          <Meter label="Models with tests" value={t.tested_models} total={t.models} hint="a model counts as tested when its schema.yml defines at least one test" />
          <Meter label="Models documented in schema.yml" value={t.documented_models} total={t.models} />
          <div className="space-y-3 border-t pt-3">
            {issue("Without tests", untested, (m) => m.path)}
            {issue("Not in any schema.yml", undocumented, (m) => m.path)}
            {issue("Read hard-coded tables", hardCoded, (m) => m.hard_coded.join(", "))}
            {!untested.length && !undocumented.length && !hardCoded.length && <p className="flex items-center gap-1.5 text-xs text-emerald-700"><CheckCircle2 className="h-4 w-4" />Every model is tested, documented and uses ref or source.</p>}
          </div>
        </section>
        <section className="surface space-y-4 p-4">
          <h3 className="text-sm font-semibold">Structure</h3>
          {langs.length > 0 && (
            <div>
              <div className="flex h-2 overflow-hidden rounded-full">{langs.map(([l, n], i) => (
                <div key={l} style={{ width: `${pct(n, langTotal)}%` }} className={["bg-indigo-500", "bg-sky-500", "bg-amber-500", "bg-emerald-500", "bg-rose-500", "bg-slate-400"][i % 6]} title={`${l} ${n}`} />
              ))}</div>
              <p className="mt-1.5 flex flex-wrap gap-x-3 text-[11px] text-muted-foreground">{langs.map(([l, n]) => <span key={l}>{l || "other"} {n} ({pct(n, langTotal)}%)</span>)}</p>
            </div>
          )}
          {!!Object.keys(a?.dbt_layers ?? {}).length && (
            <div>
              <p className="mb-1 text-xs font-medium">Models by folder</p>
              <div className="flex flex-wrap gap-1">{Object.entries(a?.dbt_layers ?? {}).map(([l, n]) => <span key={l} className="rounded-md bg-orange-50 px-2 py-0.5 font-mono text-[11px] text-orange-700">{l} · {n}</span>)}</div>
            </div>
          )}
          <div>
            <p className="mb-1 flex items-center gap-1 text-xs font-medium"><Flame className="h-3.5 w-3.5 text-rose-500" />Most depended on</p>
            {hotspots.length ? (
              <div className="flex flex-wrap gap-1">{hotspots.slice(0, 10).map((h) => (
                <button key={h.name} type="button" onClick={() => onLineage(h.name)} title={h.kinds.join(", ")}
                        className="rounded-md bg-rose-50 px-2 py-0.5 font-mono text-[11px] text-rose-700 hover:bg-rose-100">{h.name} · {h.dependents} dependents</button>
              ))}</div>
            ) : <p className="text-[11px] text-muted-foreground">Nothing has more than one dependent yet.</p>}
          </div>
          {!!a?.leaf_models?.length && (
            <div>
              <p className="mb-1 text-xs font-medium">End models <span className="font-normal text-muted-foreground">(nothing in the repository depends on them)</span></p>
              <div className="flex flex-wrap gap-1">{a.leaf_models.slice(0, 12).map((m) => (
                <button key={m} type="button" onClick={() => onLineage(m)} className="rounded-md border px-1.5 py-0.5 font-mono text-[11px] hover:border-primary">{m}</button>
              ))}</div>
            </div>
          )}
          {!!summary.dbt?.top_macros?.length && (
            <div>
              <p className="mb-1 text-xs font-medium">Most called macros</p>
              <div className="flex flex-wrap gap-1">{summary.dbt.top_macros.slice(0, 8).map((m) => (
                <button key={m.name} type="button" onClick={() => onSearch(m.name)} className="rounded-md bg-violet-50 px-2 py-0.5 font-mono text-[11px] text-violet-700 hover:bg-violet-100">{m.name} · {m.uses} call{m.uses === 1 ? "" : "s"}</button>
              ))}</div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function ModelsTab({ repo, catalog, onOpen, onLineage }: { repo: CodeRepo; catalog: Catalog; onOpen: OpenFn; onLineage: (n: string) => void }) {
  const [filter, setFilter] = useState("");
  const [issuesOnly, setIssuesOnly] = useState(false);
  const rows = catalog.models.filter((m) => (!filter || `${m.name} ${m.folder}`.toLowerCase().includes(filter.toLowerCase()))
    && (!issuesOnly || !m.tests || !m.schema_path || m.hard_coded.length));
  return (
    <section className="surface overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2.5">
        <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter models or folders" className="h-8 w-60 text-xs" aria-label="Filter models" />
        <label className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={issuesOnly} onChange={() => setIssuesOnly(!issuesOnly)} />Only models with gaps</label>
        <span className="ml-auto text-xs text-muted-foreground">{rows.length} of {catalog.models.length}</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[860px] text-xs">
          <thead className="bg-muted/40 text-left text-[10px] uppercase tracking-wide text-muted-foreground">
            <tr><th className="px-4 py-2">Model</th><th>Folder</th><th>Materialized</th><th className="text-right">Columns</th><th className="text-right">Tests</th>
              <th className="text-right" title="refs, sources and macros it uses">Uses</th><th className="text-right" title="models that use it directly / everything downstream">Used by</th><th className="px-4">Gaps</th></tr>
          </thead>
          <tbody className="divide-y">
            {rows.map((m) => (
              <tr key={`${m.kind}-${m.name}`} className="hover:bg-muted/30">
                <td className="px-4 py-2">
                  <button type="button" onClick={() => onOpen(repo.repo_id, m.path, m.line, m.name)} className="font-mono font-medium hover:text-primary hover:underline">{m.name}</button>
                  {m.kind === "snapshot" && <span className="ml-1.5"><KindPill kind="DBT_SNAPSHOT" /></span>}
                </td>
                <td className="font-mono text-muted-foreground">{m.folder}</td>
                <td>{m.materialized ?? <span className="text-muted-foreground">default</span>}</td>
                <td className="text-right tabular-nums" title={m.schema_path ? `${m.documented_columns} described in ${m.schema_path}` : "not in a schema.yml"}>
                  {m.columns}{m.schema_path ? <span className="text-muted-foreground"> · {m.documented_columns} doc</span> : ""}</td>
                <td className="text-right tabular-nums" title={m.test_list.join("\n")}>{m.tests || <span className="text-amber-600">0</span>}</td>
                <td className="text-right tabular-nums">{m.upstream}</td>
                <td className="text-right tabular-nums">
                  <button type="button" onClick={() => onLineage(m.name)} className="hover:text-primary hover:underline" title="Open its lineage">{m.downstream}{m.reach > m.downstream ? ` / ${m.reach}` : ""}</button></td>
                <td className="px-4">
                  <span className="flex flex-wrap gap-1">
                    {!m.tests && <span className="rounded bg-amber-50 px-1.5 text-[10px] text-amber-700">no tests</span>}
                    {!m.schema_path && <span className="rounded bg-amber-50 px-1.5 text-[10px] text-amber-700">no docs</span>}
                    {m.hard_coded.length > 0 && <span className="rounded bg-rose-50 px-1.5 text-[10px] text-rose-700" title={m.hard_coded.join(", ")}>hard-coded table</span>}
                    {!!m.tests && !!m.schema_path && !m.hard_coded.length && <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />}
                  </span>
                </td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={8} className="px-4 py-6 text-center text-muted-foreground">No models match.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function SourcesTab({ repo, catalog, onOpen, onLineage }: { repo: CodeRepo; catalog: Catalog; onOpen: OpenFn; onLineage: (n: string) => void }) {
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <section className="surface overflow-hidden">
        <h3 className="border-b px-4 py-2.5 text-sm font-semibold">Source tables <span className="font-normal text-muted-foreground">({catalog.sources.length})</span></h3>
        <table className="w-full text-xs">
          <thead className="bg-muted/40 text-left text-[10px] uppercase tracking-wide text-muted-foreground"><tr><th className="px-4 py-2">Table</th><th className="text-right">Tests</th><th className="px-4 text-right">Used by</th></tr></thead>
          <tbody className="divide-y">
            {catalog.sources.map((s) => (
              <tr key={s.table} className="hover:bg-muted/30">
                <td className="px-4 py-2"><button type="button" onClick={() => onOpen(repo.repo_id, s.path, s.line, s.source)} className="font-mono hover:text-primary hover:underline">{s.table}</button></td>
                <td className="text-right tabular-nums">{s.tests || <span className="text-muted-foreground">0</span>}</td>
                <td className="px-4 text-right tabular-nums"><button type="button" onClick={() => onLineage(s.table)} className="hover:text-primary hover:underline">{s.used_by}</button></td>
              </tr>
            ))}
            {!catalog.sources.length && <tr><td colSpan={3} className="px-4 py-6 text-center text-muted-foreground">No sources declared.</td></tr>}
          </tbody>
        </table>
      </section>
      <section className="surface overflow-hidden">
        <h3 className="border-b px-4 py-2.5 text-sm font-semibold">Macros <span className="font-normal text-muted-foreground">({catalog.macros.length})</span></h3>
        <table className="w-full text-xs">
          <thead className="bg-muted/40 text-left text-[10px] uppercase tracking-wide text-muted-foreground"><tr><th className="px-4 py-2">Macro</th><th>File</th><th className="px-4 text-right">Called by</th></tr></thead>
          <tbody className="divide-y">
            {catalog.macros.map((m) => (
              <tr key={`${m.path}-${m.name}`} className="hover:bg-muted/30">
                <td className="px-4 py-2"><button type="button" onClick={() => onOpen(repo.repo_id, m.path, m.line, m.name)} className="font-mono hover:text-primary hover:underline">{m.name}</button></td>
                <td className="font-mono text-muted-foreground">{m.path}</td>
                <td className="px-4 text-right tabular-nums">{m.used_by ? <button type="button" onClick={() => onLineage(m.name)} className="hover:text-primary hover:underline">{m.used_by}</button> : <span className="text-amber-600">not called</span>}</td>
              </tr>
            ))}
            {!catalog.macros.length && <tr><td colSpan={3} className="px-4 py-6 text-center text-muted-foreground">No macros.</td></tr>}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function FilesTab({ repo, onOpen }: { repo: CodeRepo; onOpen: OpenFn }) {
  const [files, setFiles] = useState<RepoFile[] | null>(null);
  const [error, setError] = useState("");
  const [closed, setClosed] = useState<Set<string>>(new Set());
  useEffect(() => {
    let live = true;
    setFiles(null); setError("");
    repoFiles(repo.repo_id).then((r) => { if (live) (r.ok ? setFiles(r.data.files) : setError(r.error)); });
    return () => { live = false; };
  }, [repo.repo_id]);
  if (error) return <p className="text-sm text-destructive">{error}</p>;
  if (!files) return <p className="flex items-center gap-2 py-6 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Listing files…</p>;
  const groups = files.reduce<Record<string, RepoFile[]>>((g, f) => {
    const dir = f.path.includes("/") ? f.path.slice(0, f.path.lastIndexOf("/")) : "(root)";
    (g[dir] ||= []).push(f);
    return g;
  }, {});
  const toggle = (d: string) => setClosed((s) => { const n = new Set(s); if (n.has(d)) n.delete(d); else n.add(d); return n; });
  return (
    <section className="surface overflow-hidden">
      <p className="border-b px-4 py-2.5 text-xs text-muted-foreground">{files.length} files in {Object.keys(groups).length} folders · {files.filter((f) => f.skipped_reason).length} skipped</p>
      <div className="divide-y">
        {Object.entries(groups).map(([dir, items]) => (
          <div key={dir}>
            <button type="button" onClick={() => toggle(dir)} className="flex w-full items-center gap-1.5 bg-muted/30 px-4 py-1.5 text-left font-mono text-xs font-medium">
              {closed.has(dir) ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}<FolderOpen className="h-3.5 w-3.5 text-amber-600" />{dir}
              <span className="ml-auto font-sans font-normal text-muted-foreground">{items.length}</span>
            </button>
            {!closed.has(dir) && items.map((f) => (
              <div key={f.path} className="flex items-center gap-2 px-4 py-1.5 pl-10 text-xs hover:bg-muted/30">
                <FileCode2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                {f.skipped_reason
                  ? <span className="min-w-0 truncate font-mono text-muted-foreground line-through" title={f.skipped_reason}>{f.path.split("/").pop()}</span>
                  : <button type="button" onClick={() => onOpen(repo.repo_id, f.path)} className="min-w-0 truncate font-mono hover:text-primary hover:underline">{f.path.split("/").pop()}</button>}
                <span className="flex flex-wrap gap-1">{f.kinds.slice(0, 3).map((k) => <KindPill key={k} kind={k} />)}</span>
                {f.skipped_reason && <span className="rounded bg-amber-50 px-1.5 text-[10px] text-amber-700" title={f.skipped_reason}>skipped</span>}
                <span className="ml-auto shrink-0 tabular-nums text-muted-foreground">{f.chunks} chunk{f.chunks === 1 ? "" : "s"} · {bytes(f.size)}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}

const NODE_W = 190, NODE_H = 36, COL_GAP = 70, ROW_GAP = 14;

function LineageTab({ repo, catalog, node, onNode, onOpen }: {
  repo: CodeRepo; catalog?: Catalog; node: string; onNode: (n: string) => void; onOpen: OpenFn;
}) {
  const [input, setInput] = useState(node);
  const [depth, setDepth] = useState(2);
  const [data, setData] = useState<Neighborhood | null>(null);
  const [error, setError] = useState("");
  const [loading, start] = useTransition();
  const fallback = useMemo(() => catalog?.models.slice().sort((a, b) => (b.reach + b.upstream) - (a.reach + a.upstream))[0]?.name ?? "", [catalog]);
  const center = node || fallback;
  useEffect(() => {
    if (!center) return;
    setInput(center);
    setError("");
    start(async () => {
      const r = await neighborhood(center, repo.repo_id, depth);
      setData(r.ok ? r.data : null);
      if (!r.ok) setError(r.error);
    });
  }, [center, depth, repo.repo_id]);
  const layout = useMemo(() => {
    if (!data) return null;
    const levels = Array.from(new Set(data.nodes.map((n) => n.level))).sort((a, b) => a - b);
    const byLevel = new Map(levels.map((l) => [l, data.nodes.filter((n) => n.level === l)]));
    const tallest = Math.max(...Array.from(byLevel.values()).map((v) => v.length));
    const height = tallest * (NODE_H + ROW_GAP) + ROW_GAP;
    const pos = new Map<string, { x: number; y: number }>();
    levels.forEach((l, i) => {
      const items = byLevel.get(l) ?? [];
      const top = (height - items.length * (NODE_H + ROW_GAP)) / 2;
      items.forEach((n, j) => pos.set(n.id, { x: i * (NODE_W + COL_GAP) + 10, y: top + j * (NODE_H + ROW_GAP) + ROW_GAP / 2 }));
    });
    return { pos, width: levels.length * (NODE_W + COL_GAP) - COL_GAP + 20, height, levels };
  }, [data]);
  const modelPath = (name: string) => catalog?.models.find((m) => m.name.toUpperCase() === short(name).toUpperCase());
  return (
    <section className="surface space-y-3 p-4">
      <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (input.trim()) onNode(input.trim()); }}>
        <Network className="h-4 w-4 text-muted-foreground" />
        <Input value={input} onChange={(e) => setInput(e.target.value)} list="lineage-names" placeholder="A model, source table, macro or function" className="h-8 w-72 font-mono text-xs" aria-label="Centre the lineage on" />
        <datalist id="lineage-names">{catalog?.models.map((m) => <option key={m.name} value={m.name} />)}{catalog?.sources.map((s) => <option key={s.table} value={s.table} />)}</datalist>
        <label className="flex items-center gap-1.5 text-xs">Hops each way
          <select value={depth} onChange={(e) => setDepth(Number(e.target.value))} className="h-8 rounded-md border bg-card px-2 text-xs">{[1, 2, 3, 4].map((d) => <option key={d} value={d}>{d}</option>)}</select></label>
        {loading && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
        <span className="ml-auto flex flex-wrap gap-3 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-sm border-2 border-sky-400" />depends on</span>
          <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-sm bg-primary" />selected</span>
          <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-sm border-2 border-rose-300" />affected by a change</span>
        </span>
      </form>
      {!center ? <p className="text-sm text-muted-foreground">Pick a model to see its lineage.</p>
        : data && !data.known ? <p className="text-sm text-muted-foreground">{center} is not in the graph of {repo.name}.</p>
          : layout && data ? (
            <div className="overflow-auto rounded-lg border bg-muted/20">
              <div className="flex text-[10px] font-medium uppercase tracking-wide text-muted-foreground" style={{ width: layout.width }}>
                {layout.levels.map((l) => <span key={l} className="px-2.5 pt-2" style={{ width: NODE_W + COL_GAP }}>{l < 0 ? `${-l} up` : l === 0 ? "selected" : `${l} down`}</span>)}
              </div>
              <svg width={layout.width} height={layout.height} role="img" aria-label={`Lineage of ${center}`}>
                {data.edges.map((e, i) => {
                  const a = layout.pos.get(e.from), b = layout.pos.get(e.to);
                  if (!a || !b) return null;
                  const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2, mid = (x1 + x2) / 2;
                  return (
                    <g key={i}>
                      <path d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`} fill="none" stroke="hsl(var(--border))" strokeWidth={1.5} />
                      <text x={mid} y={(y1 + y2) / 2 - 3} textAnchor="middle" className="fill-muted-foreground" fontSize={9}>{VIA_LABEL[e.via] ?? e.via}</text>
                    </g>
                  );
                })}
                {data.nodes.map((n) => {
                  const p = layout.pos.get(n.id);
                  if (!p) return null;
                  const selected = n.level === 0;
                  return (
                    <g key={n.id} transform={`translate(${p.x},${p.y})`} className="cursor-pointer" onClick={() => (selected ? (() => { const m = modelPath(n.name); if (m) onOpen(repo.repo_id, m.path, m.line, m.name); else if (n.path) onOpen(repo.repo_id, n.path); })() : onNode(n.name))}>
                      <title>{`${n.name}${n.path ? `\n${n.path}` : ""}\n${selected ? "click to open the file" : "click to centre the lineage here"}`}</title>
                      <rect width={NODE_W} height={NODE_H} rx={8} className={selected ? "fill-primary" : "fill-card"}
                            stroke={selected ? "none" : n.level < 0 ? "#38bdf8" : "#fda4af"} strokeWidth={1.5} />
                      {n.name.includes(".") && (
                        <text x={10} y={13} fontSize={8.5} className={selected ? "fill-primary-foreground/80" : "fill-muted-foreground"}>
                          {(() => { const prefix = n.name.slice(0, n.name.lastIndexOf(".")); return prefix.length > 32 ? `${prefix.slice(0, 31)}…` : prefix; })()}</text>
                      )}
                      <text x={10} y={n.name.includes(".") ? 27 : NODE_H / 2 + 4} fontSize={11} className={cn("font-mono", selected ? "fill-primary-foreground" : "fill-foreground")}>
                        {(() => { const label = short(n.name); return label.length > 26 ? `${label.slice(0, 25)}…` : label; })()}</text>
                    </g>
                  );
                })}
              </svg>
            </div>
          ) : error ? <p role="alert" className="text-sm text-destructive">The lineage could not be read: {error}</p>
            : <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Drawing the lineage…</p>}
      <p className="text-[11px] text-muted-foreground">Click a box to centre the lineage on it; click the selected box to open its file. Links come from refs, sources, macro and function calls, and hard-coded table reads.</p>
    </section>
  );
}

function UsageTab({ repo, onOpen }: { repo: CodeRepo; onOpen: OpenFn }) {
  const [data, setData] = useState<CodeUsage | null>(null);
  const [days, setDays] = useState(30);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    setData(null); setError("");
    codeUsage(repo.repo_id, days).then((r) => { if (live) (r.ok ? setData(r.data) : setError(r.error)); });
    return () => { live = false; };
  }, [repo.repo_id, days]);
  if (error) return <p className="text-sm text-destructive">{error}</p>;
  if (!data) return <p className="flex items-center gap-2 py-6 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Reading usage…</p>;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted-foreground">Window</span>
        {[7, 30, 90].map((d) => <button key={d} type="button" onClick={() => setDays(d)} className={cn("rounded-full border px-2.5 py-0.5", days === d ? "border-primary bg-primary/10 text-primary" : "hover:border-primary/40")}>{d} days</button>)}
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Citations" value={data.citations} hint="code chunks quoted into AI prompts" />
        <Stat label="Runs" value={data.runs} />
        {Object.entries(data.stages).slice(0, 2).map(([s, v]) => <Stat key={s} label={s.toLowerCase()} value={v.citations} hint={`${v.runs} run${v.runs === 1 ? "" : "s"}`} />)}
      </div>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <section className="surface overflow-hidden">
          <h3 className="border-b px-4 py-2.5 text-sm font-semibold">Recent use by run</h3>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-xs">
              <thead className="bg-muted/40 text-left text-[10px] uppercase tracking-wide text-muted-foreground"><tr><th className="px-4 py-2">Run</th><th>Step</th><th className="text-right">Citations</th><th className="pl-4">Code quoted</th><th className="px-4 text-right">When</th></tr></thead>
              <tbody className="divide-y">
                {data.recent.map((r, i) => (
                  <tr key={`${r.run_id}-${r.stage}-${i}`}>
                    <td className="px-4 py-2">{r.run_id ? <Link href={`/runs/${r.run_id}`} className="hover:text-primary hover:underline">{r.run_name || r.run_id.slice(0, 8)}</Link> : <span className="text-muted-foreground">copilot</span>}</td>
                    <td>{r.stage.toLowerCase()}</td>
                    <td className="text-right tabular-nums">{r.citations}</td>
                    <td className="max-w-[16rem] truncate pl-4 font-mono text-muted-foreground" title={r.names.join(", ")}>{r.names.join(", ")}</td>
                    <td className="px-4 text-right text-muted-foreground">{ago(r.last_used)}</td>
                  </tr>
                ))}
                {!data.recent.length && <tr><td colSpan={5} className="px-4 py-6 text-center text-muted-foreground">No AI step used this repository in the last {days} days.</td></tr>}
              </tbody>
            </table>
          </div>
        </section>
        <section className="surface overflow-hidden">
          <h3 className="border-b px-4 py-2.5 text-sm font-semibold">Most quoted code</h3>
          <ul className="divide-y text-xs">
            {data.top.map((t) => (
              <li key={`${t.path}-${t.start_line}`} className="flex items-center gap-2 px-4 py-2">
                <KindPill kind={t.kind} />
                <button type="button" onClick={() => onOpen(t.repo_id, t.path, t.start_line, t.name)} className="min-w-0 truncate font-mono hover:text-primary hover:underline">{t.name ?? t.path}</button>
                <span className="ml-auto tabular-nums text-muted-foreground">{t.citations}×</span>
              </li>
            ))}
            {!data.top.length && <li className="px-4 py-6 text-center text-muted-foreground">Nothing quoted yet.</li>}
          </ul>
        </section>
      </div>
    </div>
  );
}

function ImpactPanel({ imp, repoId, onPick, onLineage }: { imp: Impact; repoId: string; onPick: (name: string) => void; onLineage: (n: string) => void }) {
  const [target, setTarget] = useState("");
  const [steps, setSteps] = useState<PathStep[] | null>(null);
  const [tracing, startTrace] = useTransition();
  const direct = imp.impact.filter((i) => i.depth === 1);
  const further = imp.impact.filter((i) => i.depth > 1);
  const chip = (n: GraphNode, tone: string) => (
    <button key={`${n.name}-${n.depth}`} type="button" onClick={() => onPick(short(n.name))} title={`${VIA_LABEL[n.via] ?? n.via} · via ${n.from}${n.path ? ` · ${n.path}` : ""}`}
            className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono hover:border-primary", tone)}>
      {n.via === "MACRO_USE" ? <Sparkles className="h-2.5 w-2.5 text-violet-500" /> : n.via === "SOURCE" ? <Boxes className="h-2.5 w-2.5 text-teal-600" /> : <Network className="h-2.5 w-2.5 text-orange-600" />}
      {n.name}
    </button>
  );
  return (
    <div className="space-y-2.5 border-b px-4 py-2.5 text-[11px]">
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <p className="mb-1 flex items-center gap-1 font-medium"><ArrowUpRight className="h-3 w-3" />{imp.name} depends on</p>
          <div className="flex flex-wrap gap-1">{imp.uses.map((n) => chip(n, ""))}{!imp.uses.length && <span className="text-muted-foreground">nothing in the indexed code</span>}</div>
        </div>
        <div>
          <p className="mb-1 flex items-center gap-1 font-medium"><ArrowDownRight className="h-3 w-3" />Changing it affects {imp.total ? `${imp.total} (${imp.direct} directly)` : "nothing indexed"}
            <button type="button" onClick={() => onLineage(imp.name)} className="ml-1 text-primary hover:underline">see lineage</button></p>
          <div className="flex flex-wrap gap-1">{direct.map((n) => chip(n, "border-rose-200 bg-rose-50/50"))}</div>
          {further.length > 0 && <div className="mt-1 flex flex-wrap gap-1"><span className="text-muted-foreground">then</span>{further.slice(0, 30).map((n) => chip(n, "border-dashed"))}</div>}
        </div>
      </div>
      <form className="flex flex-wrap items-center gap-1.5" onSubmit={(e) => {
        e.preventDefault();
        if (!target.trim()) return;
        startTrace(async () => { const r = await dependencyPath(imp.name, target.trim(), repoId); setSteps(r.ok ? r.data.steps : []); });
      }}>
        <Route className="h-3 w-3 text-muted-foreground" />
        <span className="text-muted-foreground">How does it connect to</span>
        <Input value={target} onChange={(e) => setTarget(e.target.value)} placeholder="a model, table, macro or function" className="h-7 w-56 font-mono text-[11px]" aria-label="Connect to" />
        {tracing && <Loader2 className="h-3 w-3 animate-spin" />}
        {steps && (steps.length
          ? <span className="flex flex-wrap items-center gap-1 font-mono">{steps[0].from}{steps.map((st, i) => <span key={i} className="inline-flex items-center gap-1"><ArrowRight className="h-3 w-3 text-muted-foreground" /><span className="text-muted-foreground">{VIA_LABEL[st.via] ?? st.via}</span>{st.to}</span>)}</span>
          : <span className="text-muted-foreground">no dependency path between them</span>)}
      </form>
    </div>
  );
}

function FileView({ file, line, imp, loading, back, onClose, onPick, onLineage }: {
  file: CodeFile; line: number | null; imp: Impact | null; loading: boolean; back: string;
  onClose: () => void; onPick: (name: string) => void; onLineage: (n: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const lines = file.text.split("\n");
  const active = file.chunks.find((c) => line && c.start_line <= line && line <= c.end_line) ?? (file.chunks.length === 1 ? file.chunks[0] : undefined);
  useEffect(() => {
    if (!line || !ref.current) return;
    ref.current.querySelector(`[data-line="${line}"]`)?.scrollIntoView({ block: "center" });
  }, [line, file]);
  const host = file.repo.git_url.replace(/\.git$/, "");
  const remote = /github\.com|gitlab\.com/.test(host) ? `${host}/blob/${file.repo.commit || file.repo.branch}/${file.path}${line ? `#L${line}` : ""}` : null;
  return (
    <section className="surface flex min-w-0 flex-col overflow-hidden">
      <header className="flex flex-wrap items-center gap-2 border-b px-4 py-2.5">
        <button type="button" onClick={onClose} className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"><ArrowLeft className="h-3.5 w-3.5" />Back to {back.toLowerCase()}</button>
        <FileCode2 className="h-4 w-4 text-muted-foreground" />
        <p className="min-w-0 flex-1 truncate font-mono text-xs"><span className="text-muted-foreground">{file.repo.name}:</span>{file.path}</p>
        <span className="text-[11px] text-muted-foreground">{lines.length} lines{file.repo.commit ? ` · at ${file.repo.commit.slice(0, 8)}` : ""}</span>
        {loading && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
        {remote && <a href={remote} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-primary hover:underline">Open in Git<ExternalLink className="h-3 w-3" /></a>}
      </header>
      {!!file.chunks.length && (
        <div className="flex flex-wrap gap-1.5 border-b px-4 py-2">
          {file.chunks.slice(0, 24).map((c) => (
            <a key={c.chunk_id} href={`#L${c.start_line}`} onClick={(e) => { e.preventDefault(); ref.current?.querySelector(`[data-line="${c.start_line}"]`)?.scrollIntoView({ block: "start" }); }}
               className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px]", active?.chunk_id === c.chunk_id && "border-primary bg-primary/5")}>
              <KindPill kind={c.kind} />{c.name ?? `L${c.start_line}`}<span className="text-muted-foreground">L{c.start_line}-{c.end_line}</span>
            </a>
          ))}
        </div>
      )}
      {active && (active.columns.length > 0 || active.tests.length > 0) && (
        <div className="space-y-1 border-b bg-muted/30 px-4 py-2 text-[11px]">
          {active.columns.length > 0 && <p><span className="font-medium">Columns ({active.columns.length}):</span> <span className="font-mono text-muted-foreground">{active.columns.slice(0, 60).join(", ")}</span></p>}
          {active.tests.length > 0 && <p><span className="font-medium">Tests ({active.tests.length}):</span> <span className="font-mono text-muted-foreground">{active.tests.map((t) => t.replace(":", " on ")).join(", ")}</span></p>}
        </div>
      )}
      {imp && <ImpactPanel imp={imp} repoId={file.repo.repo_id} onPick={onPick} onLineage={onLineage} />}
      <div ref={ref} className="max-h-[70vh] overflow-auto overscroll-contain">
        <table className="w-full border-collapse font-mono text-[12px] leading-5">
          <tbody>
            {lines.map((text, i) => {
              const n = i + 1;
              const inChunk = active && active.start_line <= n && n <= active.end_line;
              return (
                <tr key={n} data-line={n} className={cn(n === line ? "bg-amber-100/70" : inChunk ? "bg-indigo-50/60" : "")}>
                  <td className="w-12 select-none border-r px-2 text-right align-top text-muted-foreground/60">{n}</td>
                  <td className="whitespace-pre px-3">{text || " "}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
