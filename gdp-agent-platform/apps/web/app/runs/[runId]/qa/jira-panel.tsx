"use client";

import Link from "next/link";
import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  Bug, Check, ExternalLink, FileText, Link2, Loader2, MessageSquarePlus, Paperclip, RefreshCw, Search, Send,
  Sparkles, Unlink, TriangleAlert, X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  commentJira, connectJira, jiraAttachment, jiraIssue, jiraIssues, jiraReport, jiraStatus, linkJira, runJiraLinks,
  triageJira, unlinkJira,
  type JiraIssue, type JiraIssueDetail, type JiraStatus, type RunLink, type Triage, type TriageTest,
} from "../../../jira/actions";
import { StatusPill, TransitionControl, when } from "@/components/jira/jira-controls";
import { saveQaTest } from "./qa-actions";

type Scope = "run" | "mine" | "search";

/** QA workbench, Jira: the engineer's issues, reproduced against this run's data, with results posted back as them. */
export function JiraPanel({ runId, savedTests, canWrite, canAI }: {
  runId: string; savedTests: { test_id: string; title: string }[]; canWrite: boolean; canAI: boolean;
}) {
  const [status, setStatus] = useState<JiraStatus | null>(null);
  const [error, setError] = useState("");
  const [scope, setScope] = useState<Scope>("run");
  const [q, setQ] = useState("");
  const [issues, setIssues] = useState<JiraIssue[] | null>(null);
  const [links, setLinks] = useState<RunLink[]>([]);
  const [selected, setSelected] = useState("");
  const [loading, startLoad] = useTransition();
  const [connecting, startConnect] = useTransition();

  useEffect(() => { jiraStatus().then((r) => (r.ok ? setStatus(r.data) : setError(r.error))); }, []);
  const load = (s: Scope = scope, text = q) => startLoad(async () => {
    const [list, linked] = await Promise.all([jiraIssues(s, { runId, q: text }), runJiraLinks(runId)]);
    if (linked.ok) setLinks(linked.data.links);
    if (!list.ok) { setError(list.error); setIssues([]); return; }
    setError("");
    setIssues(list.data.issues);
    if (!selected && list.data.issues[0]) setSelected(list.data.issues[0].key);
  });
  useEffect(() => {
    if (status?.connected) load(links.length || scope !== "run" ? scope : "run");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.connected?.cloud_id]);
  useEffect(() => {
    // nothing linked yet: start from the engineer's own open issues
    if (status?.connected && scope === "run" && issues && !issues.length && !links.length) { setScope("mine"); load("mine"); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [issues]);

  if (!status && !error) return <p className="flex items-center gap-2 py-8 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Checking your Jira connection…</p>;
  if (!status && error) return <Empty title="Jira could not be reached" text={error} />;
  if (!status?.installed) return <Empty title="Jira is not installed" text="It needs the latest deploy (migration V027). Ask a platform admin." />;
  if (!status.ready) {
    return <Empty title="Jira is not set up yet" text="A platform admin connects the Jira site once in Admin, Integrations, Jira; then each engineer signs in with their own account."
                  action={<Link href="/admin?section=integrations&view=jira" className="text-sm text-primary hover:underline">Open Jira setup</Link>} />;
  }
  if (!status.connected) {
    return <Empty title="Connect your Jira account" text={`See the issues assigned to you${status.site_url ? ` on ${status.site_url.replace(/^https:\/\//, "")}` : ""}, reproduce a reported bug against this run's data, and post the results back, all as yourself. You approve the access in Atlassian.`}
                  action={<Button disabled={connecting} onClick={() => startConnect(async () => {
                    const r = await connectJira(`/runs/${runId}/qa?tab=jira`);
                    if (r.ok) window.location.href = r.data.url; else setError(r.error);
                  })}>{connecting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}Connect Jira</Button>} error={error} />;
  }
  const linkedKeys = new Set(links.map((l) => l.issue_key));
  return (
    <div className="grid gap-4 lg:grid-cols-[340px_minmax(0,1fr)]">
      <aside className="surface flex max-h-[78vh] flex-col overflow-hidden">
        <div className="space-y-2 border-b p-3">
          <div className="flex rounded-lg border p-0.5 text-xs" role="tablist">
            {([["run", `This run (${linkedKeys.size})`], ["mine", "Assigned to me"], ["search", "Search"]] as const).map(([k, l]) => (
              <button key={k} type="button" role="tab" aria-selected={scope === k} onClick={() => { setScope(k); if (k !== "search") load(k); else setIssues(null); }}
                      className={cn("flex-1 rounded-md px-2 py-1", scope === k ? "bg-primary text-primary-foreground" : "text-muted-foreground")}>{l}</button>
            ))}
          </div>
          {scope === "search" && (
            <form className="relative" onSubmit={(e) => { e.preventDefault(); load("search", q); }}>
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Issue key (QA-12) or words" className="h-8 pl-8 text-xs" aria-label="Search Jira" autoFocus />
            </form>
          )}
          <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">As {status.connected.display_name} on {(status.connected.site_url ?? "").replace(/^https:\/\//, "")}
            <button type="button" onClick={() => load()} className="ml-auto rounded p-0.5 hover:bg-muted" aria-label="Reload">{loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}</button></p>
        </div>
        {error && <p role="alert" className="border-b bg-destructive/10 px-3 py-2 text-xs text-destructive">{error}</p>}
        <ul className="min-h-0 flex-1 divide-y overflow-y-auto overscroll-contain">
          {(issues ?? []).map((i) => (
            <li key={i.key}>
              <button type="button" onClick={() => setSelected(i.key)} className={cn("w-full px-3 py-2.5 text-left hover:bg-muted/40", selected === i.key && "bg-primary/5")}>
                <p className="flex items-center gap-1.5 text-xs"><span className="font-mono font-medium">{i.key}</span><StatusPill name={i.status} category={i.status_category} />
                  {linkedKeys.has(i.key) && <Link2 className="h-3 w-3 text-primary" aria-label="linked to this run" />}
                  <span className="ml-auto text-[10px] text-muted-foreground">{i.priority}</span></p>
                <p className="mt-0.5 line-clamp-2 text-sm">{i.summary}</p>
                <p className="mt-0.5 text-[11px] text-muted-foreground">{i.type}{i.assignee ? ` · ${i.assignee}` : " · unassigned"}</p>
              </button>
            </li>
          ))}
          {issues && !issues.length && (
            <li className="px-3 py-8 text-center text-xs text-muted-foreground">
              {scope === "run" ? "No issue is linked to this run yet. Open one from Assigned to me or Search and link it." : scope === "mine" ? "No open issue is assigned to you." : "Search by key or words."}
            </li>
          )}
          {!issues && scope !== "search" && <li className="flex items-center gap-2 px-3 py-6 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Loading issues…</li>}
        </ul>
      </aside>
      {selected ? <IssueView key={selected} issueKey={selected} runId={runId} links={links.filter((l) => l.issue_key === selected)} savedTests={savedTests}
                             canWrite={canWrite} canAI={canAI} me={status.connected.display_name ?? "you"} onChanged={() => load()} />
        : <Empty title="Pick an issue" text="Its description, attachments and comments open here, with AI triage and posting results back." />}
    </div>
  );
}

function Empty({ title, text, action, error }: { title: string; text: string; action?: React.ReactNode; error?: string }) {
  return (
    <section className="surface flex flex-col items-center px-6 py-12 text-center">
      <span className="grid h-12 w-12 place-items-center rounded-2xl bg-sky-50 text-sky-600 ring-1 ring-inset ring-sky-100"><Bug className="h-6 w-6" /></span>
      <p className="mt-3 text-sm font-semibold">{title}</p>
      <p className="mt-1 max-w-md text-xs text-muted-foreground">{text}</p>
      {action && <div className="mt-4">{action}</div>}
      {error && <p className="mt-3 text-xs text-destructive">{error}</p>}
    </section>
  );
}

type View = "details" | "triage" | "post";

function IssueView({ issueKey, runId, links, savedTests, canWrite, canAI, me, onChanged }: {
  issueKey: string; runId: string; links: RunLink[]; savedTests: { test_id: string; title: string }[]; canWrite: boolean; canAI: boolean;
  me: string; onChanged: () => void;
}) {
  const router = useRouter();
  const [issue, setIssue] = useState<JiraIssueDetail | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [view, setView] = useState<View>("details");
  const [pending, start] = useTransition();
  const reload = () => jiraIssue(issueKey).then((r) => (r.ok ? setIssue(r.data) : setError(r.error)));
  useEffect(() => { reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [issueKey]);
  const linked = links.length > 0;
  const [remote, setRemote] = useState(true);
  const [testId, setTestId] = useState("");
  const link = (qaTestId?: string | null) => start(async () => {
    const r = await linkJira(runId, { issue_key: issueKey, qa_test_id: qaTestId ?? null, remote_link: remote && !linked });
    if (!r.ok) { setError(r.error); return; }
    setNote(`${issueKey} linked to this run${qaTestId ? " and the test" : ""}${r.data.remote_link ? `; Jira link ${r.data.remote_link}` : ""}.`);
    onChanged(); reload();
  });
  if (error && !issue) return <Empty title={`Could not open ${issueKey}`} text={error} />;
  if (!issue) return <p className="flex items-center gap-2 p-6 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Opening {issueKey}…</p>;
  return (
    <section className="surface flex min-w-0 flex-col overflow-hidden">
      <header className="space-y-2 border-b px-5 py-4">
        <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span className="font-mono font-semibold text-foreground">{issue.key}</span><StatusPill name={issue.status} category={issue.status_category} />
          <span>{issue.type}</span>{issue.priority && <span>· {issue.priority}</span>}
          {issue.url && <a href={issue.url} target="_blank" rel="noreferrer" className="ml-auto inline-flex items-center gap-1 text-primary hover:underline">Open in Jira<ExternalLink className="h-3 w-3" /></a>}
        </p>
        <h3 className="text-base font-semibold">{issue.summary}</h3>
        <p className="text-xs text-muted-foreground">Reported by {issue.reporter ?? "someone"} · assigned to {issue.assignee ?? "nobody"} · updated {when(issue.updated)}</p>
        <div className="flex flex-wrap items-center gap-2 pt-1">
          {linked ? (
            <span className="inline-flex items-center gap-1 rounded-md bg-primary/10 px-2 py-1 text-xs text-primary"><Link2 className="h-3.5 w-3.5" />Linked to this run{links.some((l) => l.qa_test_id) ? ` and ${links.filter((l) => l.qa_test_id).length} test(s)` : ""}</span>
          ) : canWrite && (
            <>
              <Button size="sm" disabled={pending} onClick={() => link()}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Link2 className="h-3.5 w-3.5" />}Link to this run</Button>
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground"><input type="checkbox" checked={remote} onChange={() => setRemote(!remote)} />also add a link to this run in Jira</label>
            </>
          )}
          {canWrite && <TransitionControl issueKey={issueKey} runId={runId} onDone={(to) => { setNote(`${issueKey} moved to ${to}.`); reload(); onChanged(); }} />}
        </div>
        {note && <p role="status" className="flex items-center gap-2 rounded-md bg-success/10 px-2.5 py-1.5 text-xs text-success"><Check className="h-3.5 w-3.5" />{note}
          <button type="button" className="ml-auto" aria-label="Dismiss" onClick={() => setNote("")}><X className="h-3 w-3" /></button></p>}
        {error && <p role="alert" className="rounded-md bg-destructive/10 px-2.5 py-1.5 text-xs text-destructive">{error}</p>}
      </header>
      <nav className="flex gap-1 border-b px-3" role="tablist">
        {([["details", "Details", FileText], ["triage", "Reproduce with AI", Sparkles], ["post", "Post results", MessageSquarePlus]] as const).map(([k, l, Icon]) => (
          <button key={k} type="button" role="tab" aria-selected={view === k} onClick={() => setView(k)}
                  className={cn("-mb-px inline-flex items-center gap-1.5 border-b-2 px-2.5 py-2.5 text-xs", view === k ? "border-primary font-medium text-primary" : "border-transparent text-muted-foreground hover:text-foreground")}>
            <Icon className="h-3.5 w-3.5" />{l}</button>
        ))}
      </nav>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-4">
        {view === "details" && <Details issue={issue} links={links} savedTests={savedTests} canWrite={canWrite} testId={testId} setTestId={setTestId}
                                        onLinkTest={() => testId && link(testId)} runId={runId} onUnlink={(id) => start(async () => {
                                          const r = await unlinkJira(runId, id); if (!r.ok) setError(r.error); else { onChanged(); reload(); }
                                        })} pending={pending} />}
        {view === "triage" && <TriagePane issueKey={issueKey} runId={runId} canAI={canAI} canWrite={canWrite}
                                          onSaved={(title) => { setNote(`Saved "${title}" as a QA test and linked it to ${issueKey}. Run it from the Tests tab.`); onChanged(); reload(); router.refresh(); }} />}
        {view === "post" && <PostPane issueKey={issueKey} runId={runId} canWrite={canWrite} me={me} hasTests={links.some((l) => l.qa_test_id)}
                                      onPosted={(text) => { setNote(text); reload(); }} />}
      </div>
    </section>
  );
}

function Details({ issue, links, savedTests, canWrite, testId, setTestId, onLinkTest, onUnlink, pending, runId }: {
  issue: JiraIssueDetail; links: RunLink[]; savedTests: { test_id: string; title: string }[]; canWrite: boolean; testId: string;
  setTestId: (v: string) => void; onLinkTest: () => void; onUnlink: (linkId: string) => void; pending: boolean; runId: string;
}) {
  const [preview, setPreview] = useState<{ name: string; text: string; truncated: boolean } | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [opening, startOpen] = useTransition();
  return (
    <div className="space-y-5 text-sm">
      <div>
        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Description</p>
        {issue.description ? <div className="whitespace-pre-wrap break-words rounded-lg bg-muted/30 p-3 text-[13px] leading-relaxed">{issue.description}</div>
          : <p className="text-xs text-muted-foreground">No description.</p>}
        {issue.environment && <p className="mt-2 whitespace-pre-wrap text-xs text-muted-foreground"><span className="font-medium text-foreground">Environment: </span>{issue.environment}</p>}
      </div>
      {issue.attachments.length > 0 && (
        <div>
          <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Attachments</p>
          <ul className="flex flex-wrap gap-1.5">{issue.attachments.map((a) => (
            <li key={a.id}>
              <button type="button" disabled={!a.previewable || opening} title={a.previewable ? "Preview" : "Only text files (CSV, SQL, logs) preview here"}
                      onClick={() => startOpen(async () => {
                        setPreviewError("");
                        const r = await jiraAttachment(issue.key, a.id);
                        if (r.ok) setPreview(r.data); else { setPreview(null); setPreviewError(`${a.name}: ${r.error}`); }
                      })}
                      className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs enabled:hover:border-primary disabled:opacity-60">
                <Paperclip className="h-3 w-3" />{a.name}{a.size ? <span className="text-muted-foreground">{Math.max(1, Math.round(a.size / 1024))} KB</span> : null}</button>
            </li>
          ))}</ul>
          {previewError && <p role="alert" className="mt-2 text-xs text-destructive">{previewError}</p>}
          {preview && (
            <div className="mt-2 rounded-lg border">
              <p className="flex items-center gap-2 border-b px-3 py-1.5 text-xs font-medium">{preview.name}{preview.truncated && <span className="text-muted-foreground">(first 256 KB)</span>}
                <button type="button" className="ml-auto" aria-label="Close preview" onClick={() => setPreview(null)}><X className="h-3.5 w-3.5" /></button></p>
              <pre className="max-h-72 overflow-auto p-3 font-mono text-[11px]">{preview.text}</pre>
            </div>
          )}
        </div>
      )}
      <div>
        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">QA tests linked to this issue</p>
        <ul className="space-y-1">
          {links.filter((l) => l.qa_test_id).map((l) => (
            <li key={l.link_id} className="flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs">
              <Link href={`/runs/${runId}/qa`} className="min-w-0 flex-1 truncate hover:text-primary">{l.test_title ?? l.qa_test_id}</Link>
              {canWrite && <button type="button" onClick={() => onUnlink(l.link_id)} disabled={pending} className="text-muted-foreground hover:text-destructive" aria-label="Unlink test"><Unlink className="h-3.5 w-3.5" /></button>}
            </li>
          ))}
          {!links.some((l) => l.qa_test_id) && <li className="text-xs text-muted-foreground">None yet. Reproduce with AI to propose tests, or link one you already saved.</li>}
        </ul>
        {canWrite && savedTests.length > 0 && (
          <div className="mt-2 flex items-center gap-2">
            <Select value={testId} onChange={(e) => setTestId(e.target.value)} className="h-8 max-w-sm text-xs" aria-label="Saved test">
              <option value="">Link a saved test…</option>
              {savedTests.map((t) => <option key={t.test_id} value={t.test_id}>{t.title}</option>)}
            </Select>
            <Button size="sm" variant="outline" disabled={!testId || pending} onClick={onLinkTest}><Link2 className="h-3.5 w-3.5" />Link</Button>
          </div>
        )}
      </div>
      <div>
        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Comments ({issue.comments.length})</p>
        <ol className="space-y-2">{issue.comments.map((c) => (
          <li key={c.id} className="rounded-lg border px-3 py-2">
            <p className="text-[11px] text-muted-foreground"><span className="font-medium text-foreground">{c.author}</span> · {when(c.created)}</p>
            <p className="mt-1 whitespace-pre-wrap break-words text-[13px]">{c.text}</p>
          </li>
        ))}</ol>
        {!issue.comments.length && <p className="text-xs text-muted-foreground">No comments.</p>}
      </div>
      {issue.history.length > 0 && (
        <div>
          <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">From the platform</p>
          <ul className="space-y-0.5 text-xs text-muted-foreground">{issue.history.map((h, i) => (
            <li key={i} className={cn(h.status === "FAILED" && "text-destructive")}>{when(h.acted_at)} · {h.acted_by} · {h.action.toLowerCase().replace("_", " ")}{h.status === "FAILED" ? ` failed: ${h.error ?? ""}` : ""}</li>
          ))}</ul>
        </div>
      )}
    </div>
  );
}

function TriagePane({ issueKey, runId, canAI, canWrite, onSaved }: { issueKey: string; runId: string; canAI: boolean; canWrite: boolean; onSaved: (title: string) => void }) {
  const [result, setResult] = useState<Triage | null>(null);
  const [error, setError] = useState("");
  const [busy, start] = useTransition();
  const [saved, setSaved] = useState<Set<number>>(new Set());
  const run = () => start(async () => {
    setError("");
    const r = await triageJira(runId, issueKey);
    if (r.ok) setResult(r.data); else setError(r.error);
  });
  const save = (t: TriageTest, i: number) => start(async () => {
    const r = await saveQaTest(runId, { title: `${issueKey}: ${t.title}`.slice(0, 500), sql: t.sql, objective: t.objective, expected: t.expected,
                                       category: t.category, prompt: `Reproduce Jira ${issueKey}` });
    if (!r.ok) { setError(r.error); return; }
    const l = await linkJira(runId, { issue_key: issueKey, qa_test_id: r.data.test_id });
    if (!l.ok) { setError(l.error); return; }
    setSaved((s) => new Set(s).add(i));
    onSaved(t.title);
  });
  if (!result) {
    return (
      <div className="space-y-3 text-sm">
        <p className="text-muted-foreground">The AI reads the report (description, comments, small text attachments) next to this run&apos;s STTM, profiles and the client&apos;s
          code, explains what is likely wrong, and proposes up to three read-only queries that would confirm it. Nothing runs until you save and run a test.
          The report is treated as data: instructions inside it are ignored.</p>
        <Button disabled={!canAI || busy} onClick={run}>{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}{busy ? "Reading the report…" : "Reproduce with AI"}</Button>
        {!canAI && <p className="text-xs text-muted-foreground">Your role cannot use AI (AI.USE).</p>}
        {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
      </div>
    );
  }
  return (
    <div className="space-y-4 text-sm">
      <div className="space-y-2 rounded-lg border bg-muted/20 p-3">
        <p className="flex items-center gap-2 text-xs"><span className={cn("rounded-full px-2 py-0.5 font-medium", result.reproducible ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700")}>
          {result.reproducible ? "testable" : "needs more detail"}</span><span className="text-muted-foreground">severity {result.severity.toLowerCase()} · {result.model}</span>
          <button type="button" onClick={run} disabled={busy} className="ml-auto text-primary hover:underline">{busy ? "Working…" : "Run again"}</button></p>
        <p><span className="font-medium">What is reported: </span>{result.diagnosis}</p>
        <p><span className="font-medium">Likely cause: </span>{result.likely_cause}</p>
        {result.affected_columns.length > 0 && <p className="text-xs"><span className="font-medium">Columns: </span><span className="font-mono">{result.affected_columns.join(", ")}</span></p>}
        {result.questions.length > 0 && <div className="text-xs"><p className="font-medium">Ask the reporter</p><ul className="list-inside list-disc text-muted-foreground">{result.questions.map((q) => <li key={q}>{q}</li>)}</ul></div>}
      </div>
      {result.tests.map((t, i) => (
        <article key={i} className="rounded-lg border">
          <header className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
            <p className="text-sm font-medium">{t.title}</p>
            <span className="rounded bg-muted px-1.5 text-[10px]">{t.category.toLowerCase().replace("_", " ")}</span>
            {t.valid ? <span className="rounded bg-emerald-50 px-1.5 text-[10px] text-emerald-700">compiles</span>
              : <span className="inline-flex items-center gap-1 rounded bg-rose-50 px-1.5 text-[10px] text-rose-700"><TriangleAlert className="h-3 w-3" />{t.compile_error ? "does not compile" : "rejected by the guard"}</span>}
            {canWrite && (saved.has(i)
              ? <span className="ml-auto inline-flex items-center gap-1 text-xs text-emerald-700"><Check className="h-3.5 w-3.5" />saved and linked</span>
              : <Button size="sm" variant="outline" className="ml-auto" disabled={!t.valid || busy} onClick={() => save(t, i)}>Save as QA test</Button>)}
          </header>
          <div className="space-y-1.5 px-3 py-2 text-xs">
            <p className="text-muted-foreground">{t.objective}</p>
            <p><span className="font-medium">Passes when: </span>{t.expected}</p>
            <pre className="max-h-56 overflow-auto rounded bg-muted/40 p-2 font-mono text-[11px]">{t.sql}</pre>
            {!t.valid && <p className="text-destructive">{t.compile_error ?? t.problems.join("; ")}</p>}
            {t.note && <p className="text-muted-foreground">{t.note}</p>}
          </div>
        </article>
      ))}
      {!result.tests.length && <p className="text-xs text-muted-foreground">No test could be proposed from this report. Ask the reporter the questions above.</p>}
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

function PostPane({ issueKey, runId, canWrite, me, hasTests, onPosted }: {
  issueKey: string; runId: string; canWrite: boolean; me: string; hasTests: boolean; onPosted: (text: string) => void;
}) {
  const [text, setText] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const [busy, start] = useTransition();
  const draft = () => start(async () => {
    const r = await jiraReport(runId, issueKey);
    if (r.ok) { setText(r.data.markdown); setLoaded(true); } else setError(r.error);
  });
  useEffect(() => { draft(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);
  const post = () => start(async () => {
    const r = await commentJira(issueKey, text, runId);
    if (!r.ok) { setError(r.error); setConfirming(false); return; }
    setConfirming(false);
    onPosted(`Comment posted to ${issueKey} as ${me}.`);
  });
  return (
    <div className="space-y-3 text-sm">
      <p className="text-muted-foreground">A draft from the latest results of the QA tests linked to {issueKey}. Edit it, then post it to the issue as yourself.
        Sample rows are left out on purpose (they can hold personal data); the comment links to the run instead.</p>
      {!hasTests && <p className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">No QA test is linked to this issue yet, so the draft has no results. Reproduce with AI or link a saved test first.</p>}
      {!loaded && busy ? <p className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Drafting…</p> : (
        <>
          <textarea value={text} onChange={(e) => { setText(e.target.value); setConfirming(false); }} rows={12} disabled={!canWrite}
                    className="w-full rounded-lg border bg-card p-3 font-mono text-xs" aria-label="Comment" />
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="ghost" size="sm" disabled={busy} onClick={draft}><RefreshCw className="h-3.5 w-3.5" />Draft again from results</Button>
            {canWrite && !confirming && <Button size="sm" className="ml-auto" disabled={!text.trim() || busy} onClick={() => setConfirming(true)}><Send className="h-3.5 w-3.5" />Post to {issueKey}</Button>}
          </div>
          {confirming && (
            <div className="space-y-2 rounded-lg border border-primary/30 bg-primary/5 p-3">
              <p className="text-xs font-medium">Post this comment to {issueKey} as {me}? It is visible to everyone who can see the issue.</p>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-card p-2 text-[11px]">{text}</pre>
              <div className="flex gap-2">
                <Button size="sm" disabled={busy} onClick={post}>{busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}Yes, post it</Button>
                <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>Cancel</Button>
              </div>
            </div>
          )}
        </>
      )}
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
