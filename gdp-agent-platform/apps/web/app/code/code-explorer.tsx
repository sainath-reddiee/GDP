"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import {
  ArrowDownRight, ArrowUpRight, Boxes, FileCode2, FolderGit2, GitBranch, Loader2, Network, Search, Sparkles, X,
} from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { ago } from "../skills/types";
import { lineage, readFile, searchCode, type CodeFile, type CodeHit, type CodeRepo, type Edge } from "./actions";

export type RepoSummary = {
  dbt?: { projects?: { name: string; root: string }[]; top_macros?: { name: string; uses: number }[]; sources?: string[] } | null;
  usage?: Record<string, { uses: number; runs: number }>;
};

const KINDS = [
  { id: "", label: "Everything" }, { id: "DBT_MODEL", label: "Models" }, { id: "DBT_MACRO", label: "Macros" },
  { id: "DBT_TEST", label: "Tests" }, { id: "DBT_SCHEMA_YML", label: "Schema" }, { id: "DBT_SOURCE", label: "Sources" },
  { id: "SQL", label: "SQL" }, { id: "PY_FUNC", label: "Python" }, { id: "DOC", label: "Docs" },
];
const KIND_TONE: Record<string, string> = {
  DBT_MODEL: "bg-orange-50 text-orange-700 ring-orange-100", DBT_SNAPSHOT: "bg-orange-50 text-orange-700 ring-orange-100",
  DBT_MACRO: "bg-violet-50 text-violet-700 ring-violet-100", DBT_TEST: "bg-emerald-50 text-emerald-700 ring-emerald-100",
  DBT_SCHEMA_YML: "bg-sky-50 text-sky-700 ring-sky-100", DBT_SOURCE: "bg-teal-50 text-teal-700 ring-teal-100",
  SQL: "bg-indigo-50 text-indigo-700 ring-indigo-100", PY_FUNC: "bg-amber-50 text-amber-700 ring-amber-100",
  DOC: "bg-slate-100 text-slate-600 ring-slate-200", CONFIG: "bg-slate-100 text-slate-600 ring-slate-200",
};
const kindLabel = (k: string) => k.replace(/^DBT_/, "").replace("SCHEMA_YML", "schema").replace("PY_FUNC", "python").replace(/_/g, " ").toLowerCase();

function KindPill({ kind }: { kind: string }) {
  return <span className={cn("rounded-full px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset", KIND_TONE[kind] ?? KIND_TONE.DOC)}>{kindLabel(kind)}</span>;
}

export function CodeExplorer({ repos, summary, initial }: {
  repos: CodeRepo[]; summary: Record<string, RepoSummary>; initial: { repo?: string; path?: string; line?: string; q?: string };
}) {
  const [repoId, setRepoId] = useState(initial.repo ?? "");
  const [q, setQ] = useState(initial.q ?? "");
  const [kind, setKind] = useState("");
  const [hits, setHits] = useState<CodeHit[] | null>(null);
  const [file, setFile] = useState<CodeFile | null>(null);
  const [line, setLine] = useState<number | null>(initial.line ? Number(initial.line) : null);
  const [lin, setLin] = useState<{ name: string; upstream: Edge[]; downstream: Edge[] } | null>(null);
  const [error, setError] = useState("");
  const [searching, startSearch] = useTransition();
  const [opening, startOpen] = useTransition();

  const totals = useMemo(() => repos.reduce((t, r) => ({
    files: t.files + (r.stats?.files ?? 0), chunks: t.chunks + (r.stats?.chunks ?? 0),
    models: t.models + (r.stats?.by_kind?.DBT_MODEL ?? 0), macros: t.macros + (r.stats?.by_kind?.DBT_MACRO ?? 0),
    tests: t.tests + (r.stats?.by_kind?.DBT_TEST ?? 0) + (r.stats?.by_kind?.DBT_SCHEMA_YML ?? 0),
  }), { files: 0, chunks: 0, models: 0, macros: 0, tests: 0 }), [repos]);
  const uses = Object.values(summary).reduce((n, s) => n + Object.values(s.usage ?? {}).reduce((m, u) => m + u.uses, 0), 0);

  const syncUrl = (patch: Record<string, string | null>) => {
    const p = new URLSearchParams(window.location.search);
    for (const [k, v] of Object.entries(patch)) if (v) p.set(k, v); else p.delete(k);
    // shallow: keep the URL shareable without re-rendering the server page
    window.history.replaceState(window.history.state, "", `${window.location.pathname}?${p}`);
  };
  const runSearch = (text = q, k = kind, repo = repoId) => {
    if (!text.trim()) { setHits(null); return; }
    startSearch(async () => {
      const r = await searchCode(text.trim(), repo || undefined, k || undefined);
      if (r.ok) { setHits(r.data.hits); setError(""); } else setError(r.error);
    });
    syncUrl({ q: text.trim() || null });
  };
  const open = (repo: string, path: string, at?: number, name?: string | null) => startOpen(async () => {
    const r = await readFile(repo, path);
    if (!r.ok) { setError(r.error); return; }
    setFile(r.data); setLine(at ?? null); setError("");
    syncUrl({ repo, path, line: at ? String(at) : null });
    const chunk = r.data.chunks.find((c) => at && c.start_line <= at && at <= c.end_line) ?? (name ? r.data.chunks.find((c) => c.name === name) : null);
    if (chunk?.name && ["DBT_MODEL", "DBT_MACRO", "DBT_SOURCE", "DBT_SNAPSHOT", "DBT_SCHEMA_YML"].includes(chunk.kind)) {
      const l = await lineage(chunk.name, repo);
      setLin(l.ok ? l.data : null);
    } else setLin(null);
  });
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
          data quality checks, STTM and the copilot reuse its models, macros and tests.</p>
        <Link href="/admin?section=integrations" className="mt-4 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground">Connect a repository</Link>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {[["Repositories", repos.length], ["Files", totals.files], ["dbt models", totals.models], ["Macros", totals.macros],
          ["Tests and schema", totals.tests], ["Used by AI, 30 days", uses]].map(([l, v]) => (
          <div key={String(l)} className="surface px-4 py-3">
            <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{l}</p>
            <p className="mt-0.5 text-2xl font-semibold tabular-nums">{Number(v).toLocaleString()}</p>
          </div>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-[240px_minmax(0,1fr)]">
        <aside className="space-y-1 lg:sticky lg:top-4 lg:self-start">
          <button type="button" onClick={() => { setRepoId(""); runSearch(q, kind, ""); }}
                  className={cn("flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-sm", !repoId ? "bg-primary/10 font-medium text-primary" : "text-muted-foreground hover:bg-muted")}>
            <FolderGit2 className="h-4 w-4" />All repositories</button>
          {repos.map((r) => (
            <button key={r.repo_id} type="button" onClick={() => { setRepoId(r.repo_id); runSearch(q, kind, r.repo_id); }}
                    className={cn("w-full rounded-lg px-2.5 py-1.5 text-left", repoId === r.repo_id ? "bg-primary/10 text-primary" : "hover:bg-muted")}>
              <span className="flex items-center gap-2 text-sm font-medium"><GitBranch className="h-3.5 w-3.5 shrink-0" /><span className="truncate">{r.name}</span></span>
              <span className="block truncate pl-5 text-[11px] text-muted-foreground">{(r.stats?.files ?? 0).toLocaleString()} files · {r.last_indexed ? ago(r.last_indexed) : r.status.toLowerCase()}</span>
            </button>
          ))}
        </aside>

        <div className="min-w-0 space-y-4">
          <div className="surface space-y-2 p-2">
            <form className="relative" onSubmit={(e) => { e.preventDefault(); runSearch(); }}>
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search models, macros, tests and SQL: a table, a column, a macro name or a question"
                     className="h-10 border-0 pl-9 shadow-none focus-visible:ring-0" aria-label="Search code" />
              {searching && <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-muted-foreground" />}
            </form>
            <div className="flex flex-wrap gap-1 px-1 pb-1">
              {KINDS.map((k) => (
                <button key={k.id || "all"} type="button" onClick={() => { setKind(k.id); runSearch(q, k.id); }} aria-pressed={kind === k.id}
                        className={cn("rounded-full border px-2.5 py-0.5 text-[11px]", kind === k.id ? "border-primary bg-primary/10 text-primary" : "text-muted-foreground hover:border-primary/40")}>{k.label}</button>
              ))}
            </div>
          </div>
          {error && <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">{error}</p>}

          <div className={cn("grid gap-4", file && hits !== null && "xl:grid-cols-[minmax(0,380px)_minmax(0,1fr)]")}>
            {hits !== null ? (
              <section className="space-y-2">
                <p className="text-xs text-muted-foreground">{hits.length} result{hits.length === 1 ? "" : "s"}</p>
                {hits.map((h) => (
                  <button key={h.chunk_id} type="button" onClick={() => open(h.repo_id, h.path, h.start_line, h.name)}
                          className={cn("w-full rounded-xl border border-border/80 bg-card p-3 text-left transition hover:border-primary/30 hover:shadow-card",
                                        file?.path === h.path && file.repo.repo_id === h.repo_id && "border-primary/40 ring-1 ring-primary/15")}>
                    <p className="flex flex-wrap items-center gap-1.5 text-sm font-medium"><KindPill kind={h.kind} />{h.name ?? h.path.split("/").pop()}
                      {h.match === "search" && <Sparkles className="h-3 w-3 text-violet-500" aria-label="semantic match" />}</p>
                    <p className="truncate font-mono text-[11px] text-muted-foreground">{h.repo_name}:{h.path}:{h.start_line}</p>
                    <pre className="mt-1.5 line-clamp-3 whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">{h.text}</pre>
                  </button>
                ))}
                {!hits.length && <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">Nothing matches. Try a table, column or macro name.</p>}
              </section>
            ) : !file ? (
              <Overview repos={repos} summary={summary} onSearch={(t) => { setQ(t); runSearch(t); }} />
            ) : null}
            {file && <FileView file={file} line={line} lin={lin} loading={opening} onClose={() => { setFile(null); setLin(null); syncUrl({ path: null, line: null }); }}
                               onPick={(n) => { setQ(n); runSearch(n); }} />}
          </div>
        </div>
      </div>
    </div>
  );
}

function Overview({ repos, summary, onSearch }: { repos: CodeRepo[]; summary: Record<string, RepoSummary>; onSearch: (t: string) => void }) {
  return (
    <section className="grid gap-3 lg:grid-cols-2">
      {repos.map((r) => {
        const s = summary[r.repo_id] ?? {};
        return (
          <article key={r.repo_id} className="surface space-y-3 p-4">
            <div className="flex items-center gap-2">
              <GitBranch className="h-4 w-4 text-muted-foreground" />
              <p className="text-sm font-semibold">{r.name}</p>
              <span className="ml-auto font-mono text-[11px] text-muted-foreground">{r.branch}{r.last_commit ? ` @ ${r.last_commit.slice(0, 8)}` : ""}</span>
            </div>
            {!!s.dbt?.projects?.length && (
              <p className="flex flex-wrap gap-1.5 text-[11px]">{s.dbt.projects.map((p) => <span key={p.name} className="rounded-full bg-orange-50 px-2 py-0.5 text-orange-700 ring-1 ring-inset ring-orange-100">dbt: {p.name}</span>)}</p>
            )}
            {!!s.dbt?.top_macros?.length && (
              <div>
                <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Most used macros</p>
                <div className="flex flex-wrap gap-1">{s.dbt.top_macros.slice(0, 8).map((m) => (
                  <button key={m.name} type="button" onClick={() => onSearch(m.name)} className="rounded-md bg-violet-50 px-2 py-0.5 font-mono text-[11px] text-violet-700 hover:bg-violet-100">{m.name} · {m.uses}</button>
                ))}</div>
              </div>
            )}
            {!!s.dbt?.sources?.length && (
              <div>
                <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Sources</p>
                <div className="flex flex-wrap gap-1">{s.dbt.sources.slice(0, 12).map((x) => (
                  <button key={x} type="button" onClick={() => onSearch(x.split(".").pop() ?? x)} className="rounded-md bg-teal-50 px-2 py-0.5 font-mono text-[11px] text-teal-700 hover:bg-teal-100">{x}</button>
                ))}</div>
              </div>
            )}
            <p className="border-t pt-2 text-[11px] text-muted-foreground">
              {Object.entries(s.usage ?? {}).length
                ? <>Used by AI in the last 30 days: {Object.entries(s.usage ?? {}).map(([st, u]) => `${st.toLowerCase()} ${u.uses}`).join(", ")}</>
                : "Not used by an AI step yet."}
            </p>
          </article>
        );
      })}
    </section>
  );
}

function FileView({ file, line, lin, loading, onClose, onPick }: {
  file: CodeFile; line: number | null; lin: { name: string; upstream: Edge[]; downstream: Edge[] } | null; loading: boolean;
  onClose: () => void; onPick: (name: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const lines = file.text.split("\n");
  const active = file.chunks.find((c) => line && c.start_line <= line && line <= c.end_line);
  useEffect(() => {
    if (!line || !ref.current) return;
    ref.current.querySelector(`[data-line="${line}"]`)?.scrollIntoView({ block: "center" });
  }, [line, file]);
  const host = file.repo.git_url.replace(/\.git$/, "");
  const remote = /github\.com|gitlab\.com/.test(host) ? `${host}/blob/${file.repo.commit || file.repo.branch}/${file.path}${line ? `#L${line}` : ""}` : null;
  return (
    <section className="surface flex min-w-0 flex-col overflow-hidden">
      <header className="flex items-center gap-2 border-b px-4 py-2.5">
        <FileCode2 className="h-4 w-4 text-muted-foreground" />
        <p className="min-w-0 flex-1 truncate font-mono text-xs"><span className="text-muted-foreground">{file.repo.name}:</span>{file.path}</p>
        {loading && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
        {remote && <a href={remote} target="_blank" rel="noreferrer" className="text-xs text-primary hover:underline">Open in Git</a>}
        <button type="button" onClick={onClose} aria-label="Close file" className="rounded p-0.5 hover:bg-muted"><X className="h-4 w-4" /></button>
      </header>
      {!!file.chunks.length && (
        <div className="flex flex-wrap gap-1.5 border-b px-4 py-2">
          {file.chunks.slice(0, 20).map((c) => (
            <a key={c.chunk_id} href={`#L${c.start_line}`} onClick={(e) => { e.preventDefault(); ref.current?.querySelector(`[data-line="${c.start_line}"]`)?.scrollIntoView({ block: "start" }); }}
               className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px]", active?.chunk_id === c.chunk_id && "border-primary bg-primary/5")}>
              <KindPill kind={c.kind} />{c.name ?? `L${c.start_line}`}
            </a>
          ))}
        </div>
      )}
      {active && (active.columns.length > 0 || active.tests.length > 0) && (
        <div className="space-y-1 border-b bg-muted/30 px-4 py-2 text-[11px]">
          {active.columns.length > 0 && <p><span className="font-medium">Columns:</span> <span className="font-mono text-muted-foreground">{active.columns.slice(0, 40).join(", ")}</span></p>}
          {active.tests.length > 0 && <p><span className="font-medium">Tests:</span> <span className="font-mono text-muted-foreground">{active.tests.join(", ")}</span></p>}
        </div>
      )}
      {lin && (lin.upstream.length > 0 || lin.downstream.length > 0) && (
        <div className="grid gap-3 border-b px-4 py-2.5 text-[11px] sm:grid-cols-2">
          <div>
            <p className="mb-1 flex items-center gap-1 font-medium"><ArrowUpRight className="h-3 w-3" />{lin.name} uses</p>
            <div className="flex flex-wrap gap-1">{dedupe(lin.upstream.map((e) => `${e.kind}:${e.to_name}`)).map((k) => {
              const [kind, name] = k.split(":");
              return <button key={k} type="button" onClick={() => onPick(name.split(".").pop() ?? name)} className="inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono hover:border-primary">
                {kind === "MACRO_USE" ? <Sparkles className="h-2.5 w-2.5 text-violet-500" /> : kind === "SOURCE" ? <Boxes className="h-2.5 w-2.5 text-teal-600" /> : <Network className="h-2.5 w-2.5 text-orange-600" />}{name}</button>;
            })}</div>
          </div>
          <div>
            <p className="mb-1 flex items-center gap-1 font-medium"><ArrowDownRight className="h-3 w-3" />Used by</p>
            <div className="flex flex-wrap gap-1">{dedupe(lin.downstream.map((e) => e.from_name)).map((n) => (
              <button key={n} type="button" onClick={() => onPick(n)} className="rounded-md border px-1.5 py-0.5 font-mono hover:border-primary">{n}</button>
            ))}{!lin.downstream.length && <span className="text-muted-foreground">nothing in the indexed code</span>}</div>
          </div>
        </div>
      )}
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

const dedupe = (xs: string[]) => Array.from(new Set(xs)).slice(0, 40);
