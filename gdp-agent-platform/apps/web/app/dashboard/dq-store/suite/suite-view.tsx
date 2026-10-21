"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, useTransition } from "react";
import {
  AlertTriangle, Check, Download, ExternalLink, FileCode2, GitCompare, GitPullRequest, History, Loader2, Pause, Pencil, Play,
  Plus, RefreshCw, RotateCcw, Send, ShieldCheck, Trash2, Undo2, X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { DqSuiteChange, DqSuiteCheck, DqSuiteDetail, DqSuiteDiff, DqSuiteVersion } from "@/lib/store-types";
import { Button } from "@/components/ui/button";
import { Label, Select, Textarea } from "@/components/ui/input";
import { EmptyState, StatusPill, explain } from "@/components/hub/states";
import { useToast } from "@/components/ui/toast";
import { SodaCloudCard } from "@/components/quality/soda-cloud-card";
import {
  applySuite, approveSuite, diffSuite, loadSuitePublishing, openSuiteDraft, publishSuite, rejectSuite, setCheckStatus,
  submitSuite, syncSuitePrs, withdrawSuite, type SuitePublishing,
} from "../actions";
import { CheckModal, stagedMessage, suiteHref, type EditableCheck } from "../dq-store-view";

/** The tab in the address: after an action the page can remount with the props of the address it was loaded with,
 *  so the tab the user is on is read back from the address itself (on the server there is none). */
function rememberedTab(): string | null {
  try { return typeof window === "undefined" ? null : new URL(window.location.href).searchParams.get("tab"); } catch { return null; }
}

/** Keep the open tab in the address, so a refresh after an action (which can remount the page) stays on it. */
function rememberTab(tab: string) {
  try {
    const url = new URL(window.location.href);
    url.searchParams.set("tab", tab);
    window.history.replaceState(window.history.state, "", url);
  } catch { /* the tab still switches */ }
}

type Tab = "review" | "versions" | "publish" | "export";
const TABS: [Tab, string, typeof History][] = [
  ["review", "Changes and review", ShieldCheck], ["versions", "Versions", History], ["publish", "Publish", GitPullRequest],
  ["export", "Export", FileCode2],
];
const CLASS_TONE: Record<DqSuiteChange["class"], string> = {
  TIGHTENED: "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:ring-emerald-900",
  LOOSENED: "bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:ring-amber-900",
  CHANGED: "bg-sky-50 text-sky-700 ring-sky-200 dark:bg-sky-950/40 dark:text-sky-300 dark:ring-sky-900",
  DOCUMENTATION: "bg-slate-100 text-slate-600 ring-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700",
};
const CLASS_LABEL: Record<DqSuiteChange["class"], string> = {
  TIGHTENED: "stricter", LOOSENED: "looser", CHANGED: "changed", DOCUMENTATION: "reason only",
};
const PR_TONE: Record<string, string> = {
  OPEN: "bg-sky-100 text-sky-800", MERGED: "bg-blue-100 text-blue-800", CLOSED: "bg-rose-100 text-rose-700",
  FAILED: "bg-rose-100 text-rose-700", NO_CHANGES: "bg-slate-100 text-slate-700",
};

function ChangeList({ diff, empty }: { diff: DqSuiteDiff; empty: string }) {
  if (!diff.changes.length) return <p className="text-sm text-muted-foreground">{empty}</p>;
  return (
    <div className="space-y-2" data-testid="suite-diff">
      <p className="flex flex-wrap gap-1.5 text-xs">
        {(Object.keys(CLASS_LABEL) as DqSuiteChange["class"][]).filter((k) => diff.counts[k]).map((k) => (
          <span key={k} className={cn("rounded-full px-2 py-0.5 ring-1 ring-inset", CLASS_TONE[k])}>{diff.counts[k]} {CLASS_LABEL[k]}</span>
        ))}
      </p>
      <ul className="divide-y divide-border/60 rounded-xl border border-border/70">
        {diff.changes.map((c) => (
          <li key={`${c.check_id}-${c.kind}`} className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm">
            <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ring-1 ring-inset", CLASS_TONE[c.class])}>{CLASS_LABEL[c.class]}</span>
            <span className="text-xs text-muted-foreground">{c.kind.toLowerCase()}</span>
            <span className="min-w-0 flex-1 truncate">{c.message}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function CheckRow({ check, editable, issues, onEdit, onStatus }: {
  check: DqSuiteCheck; editable: boolean; issues: string[]; onEdit: () => void; onStatus: (s: "ACTIVE" | "PAUSED" | "RETIRED") => void;
}) {
  return (
    <div className={cn("grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-3 py-2 text-sm md:grid-cols-[minmax(0,1fr)_90px_90px_auto]",
      check.status === "PAUSED" && "opacity-60")} data-testid={`suite-check-${check.check_id}`}>
      <div className="min-w-0">
        <code className="block truncate font-mono text-[12.5px]" title={check.soda_dsl}>{check.headline}</code>
        <span className="text-[11px] text-muted-foreground">{check.target_column ?? "table"} · {check.check_type.toLowerCase().replace(/_/g, " ")} · from {check.origin.toLowerCase()}{check.status === "PAUSED" ? " · paused" : ""}</span>
        {check.requirement && <span className="block truncate text-[11px] text-muted-foreground">Why: {check.requirement}</span>}
        {issues.map((m) => <span key={m} className="block text-[11px] text-amber-700 dark:text-amber-400">{m}</span>)}
      </div>
      <span className={cn("hidden text-xs font-medium md:block", check.severity === "FAIL" ? "text-rose-600" : "text-amber-600")}>{check.severity === "FAIL" ? "blocks" : "alerts"}</span>
      <span className="hidden md:block"><StatusPill status={check.status} /></span>
      {editable ? (
        <span className="flex items-center gap-0.5">
          <Button size="sm" variant="ghost" onClick={onEdit} aria-label="Edit check"><Pencil className="h-3.5 w-3.5" /></Button>
          {check.status === "ACTIVE"
            ? <Button size="sm" variant="ghost" onClick={() => onStatus("PAUSED")} aria-label="Pause check"><Pause className="h-3.5 w-3.5" /></Button>
            : <Button size="sm" variant="ghost" onClick={() => onStatus("ACTIVE")} aria-label="Resume check"><Play className="h-3.5 w-3.5" /></Button>}
          <Button size="sm" variant="ghost" className="text-destructive" onClick={() => onStatus("RETIRED")} aria-label="Remove check"><Trash2 className="h-3.5 w-3.5" /></Button>
        </span>
      ) : <span />}
    </div>
  );
}

function ReviewTab({ table, detail, me, canEdit, canApprove }: { table: string; detail: DqSuiteDetail; me: string; canEdit: boolean; canApprove: boolean }) {
  const router = useRouter();
  const toast = useToast();
  const [pending, start] = useTransition();
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [ack, setAck] = useState(false);
  const [editing, setEditing] = useState<{ check?: EditableCheck } | null>(null);
  const cur = detail.current;
  const suite = detail.suite;
  if (!cur) {
    return (
      <EmptyState icon={ShieldCheck} title="No checks for this table yet"
                  action={canEdit ? <Button onClick={() => setEditing({})} data-testid="suite-add-first"><Plus className="h-4 w-4" /> Add the first check</Button> : undefined}>
        The first check opens version 1 as a draft; it runs once another person approves it.
        {editing && <CheckModal table={table} columns={[]} onClose={() => { setEditing(null); router.refresh(); }} />}
      </EmptyState>
    );
  }
  const isDraft = cur.status === "DRAFT";
  const inReview = cur.status === "IN_REVIEW";
  const own = [...cur.authors, cur.submitted_by ?? ""].map((a) => a.toUpperCase()).includes(me);
  const errors = cur.issues.filter((i) => i.severity === "ERROR");
  const byCheck = cur.issues.reduce<Record<string, string[]>>((m, i) => { if (i.check_id) (m[i.check_id] ??= []).push(i.message); return m; }, {});
  const columns = Object.keys(cur.model?.columns ?? {});
  const run = (fn: () => Promise<{ ok: boolean; error?: string; data?: unknown }>, ok: string) => start(async () => {
    const r = await fn();
    if (r.ok) { toast("ok", ok); router.refresh(); } else toast("error", explain(r.error ?? "failed").message);
  });
  const status = (c: DqSuiteCheck, s: "ACTIVE" | "PAUSED" | "RETIRED") => start(async () => {
    const r = await setCheckStatus(c.check_id, s, table);
    if (r.ok) { toast("ok", stagedMessage(r.data, s === "RETIRED" ? "Check removed" : s === "PAUSED" ? "Check paused" : "Check resumed")); router.refresh(); }
    else toast("error", explain(r.error).message);
  });
  const approve = () => start(async () => {
    const r = await approveSuite(table, cur.version, ack);
    if (!r.ok) { toast("error", explain(r.error).message); return; }
    toast(r.data.catalog.applied ? "ok" : "error", r.data.catalog.applied
      ? `v${cur.version} approved; ${r.data.catalog.checks ?? 0} checks now run on ${table.split(".").pop()}`
      : `v${cur.version} approved, but the catalog was not updated: ${r.data.catalog.error}. Apply it again from Versions.`);
    router.refresh();
  });

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
      <div className="space-y-4">
        <section className="surface space-y-3 p-4">
          <h3 className="text-sm font-semibold">Changes from the approved version{suite?.approved_version ? ` (v${suite.approved_version})` : ""}</h3>
          <ChangeList diff={cur.changes} empty={cur.status === "APPROVED" || cur.status === "PUBLISHED" ? "This is the approved version." : "No changes yet."} />
        </section>
        <section className="surface overflow-hidden">
          <div className="flex items-center gap-2 border-b border-border/70 px-4 py-2.5">
            <h3 className="text-sm font-semibold">Checks in v{cur.version}</h3>
            <span className="text-xs text-muted-foreground">{cur.checks.length} · {cur.checks.filter((c) => c.status === "ACTIVE").length} active</span>
            {isDraft && canEdit && <Button size="sm" className="ml-auto" onClick={() => setEditing({})} data-testid="suite-add-check"><Plus className="h-3.5 w-3.5" /> Add check</Button>}
          </div>
          {cur.checks.length ? (
            <div className="divide-y divide-border/50">
              {cur.checks.map((c) => (
                <CheckRow key={c.check_id} check={c} editable={isDraft && canEdit} issues={byCheck[c.check_id] ?? []}
                          onEdit={() => setEditing({ check: { check_id: c.check_id, column_name: c.target_column, severity: c.severity, check_definition: c.definition, requirement: c.requirement } })}
                          onStatus={(s) => status(c, s)} />
              ))}
            </div>
          ) : <p className="p-4 text-sm text-muted-foreground">No checks in this version.</p>}
        </section>
      </div>

      <aside className="space-y-4">
        <section className="surface space-y-2 p-4 text-sm" data-testid="suite-model">
          <h3 className="text-sm font-semibold">Domain model</h3>
          {cur.model?.entity ? (
            <p>Checked against <Link className="font-medium text-primary hover:underline" href={`/dashboard/modeling/${cur.model.model_id}`}>{cur.model.entity}</Link> in the approved model v{cur.model.model_version}.</p>
          ) : cur.model ? <p className="text-muted-foreground">The domain has an approved model (v{cur.model.model_version}), but this table is not one of its entities, so columns are not checked.</p>
            : <p className="text-muted-foreground">The table&apos;s domain has no approved model yet, so columns are not checked.</p>}
          {cur.issues.length > 0 && (
            <ul className="space-y-1 text-xs">
              {cur.issues.map((i, k) => (
                <li key={k} className={cn("flex gap-1.5", i.severity === "ERROR" ? "text-destructive" : "text-amber-700 dark:text-amber-400")}>
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{i.message}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="surface space-y-3 p-4" data-testid="suite-decision">
          <h3 className="text-sm font-semibold">v{cur.version} · {cur.status.toLowerCase().replace("_", " ")}</h3>
          {cur.change_note && <p className="text-xs text-muted-foreground">{cur.change_note}</p>}
          <p className="text-xs text-muted-foreground">
            {cur.authors.length ? `Edited by ${cur.authors.join(", ")}` : "No edits yet"}{cur.submitted_by ? ` · submitted by ${cur.submitted_by}` : ""}
            {cur.approved_by ? ` · approved by ${cur.approved_by}${cur.model_version ? ` against domain model v${cur.model_version}` : ""}` : ""}
            {cur.rejected_by ? ` · rejected by ${cur.rejected_by}: ${cur.reject_reason}` : ""}
          </p>
          {isDraft && canEdit && (
            <>
              <Textarea aria-label="Change note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="What changed and why (shown to reviewers)" />
              <Button className="w-full" disabled={pending || errors.length > 0 || (!cur.changes.changes.length && !!suite?.approved_version)}
                      onClick={() => run(() => submitSuite(table, note), `v${cur.version} sent for review`)} data-testid="suite-submit">
                {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />} Submit for review
              </Button>
              {errors.length > 0 && <p className="text-xs text-destructive">Fix {errors.length} error{errors.length === 1 ? "" : "s"} first.</p>}
            </>
          )}
          {inReview && own && (
            <>
              <p className="text-xs text-muted-foreground">You submitted or edited this version, so another data quality approver decides it.</p>
              <Button variant="outline" className="w-full" disabled={pending} onClick={() => run(() => withdrawSuite(table), `v${cur.version} back to draft`)}>
                <Undo2 className="h-4 w-4" /> Withdraw to keep editing
              </Button>
            </>
          )}
          {inReview && !own && canApprove && (
            <>
              {cur.changes.loosened && (
                <label className="flex items-start gap-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
                  <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-0.5" data-testid="suite-ack" />
                  {cur.changes.counts.LOOSENED} change{cur.changes.counts.LOOSENED === 1 ? "" : "s"} relax the checks (removed, paused, softer severity or threshold). I confirm this is intended.
                </label>
              )}
              <Button className="w-full" disabled={pending || errors.length > 0 || (cur.changes.loosened && !ack)} onClick={approve} data-testid="suite-approve">
                {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />} Approve and put live
              </Button>
              <Textarea aria-label="Reject reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why it is rejected" />
              <Button variant="outline" className="w-full text-destructive" disabled={pending || reason.trim().length < 3}
                      onClick={() => run(() => rejectSuite(table, cur.version, reason), `v${cur.version} rejected`)}>
                <X className="h-4 w-4" /> Reject
              </Button>
            </>
          )}
          {inReview && !own && !canApprove && <p className="text-xs text-muted-foreground">Waiting for a data quality approver.</p>}
          {!detail.open && canEdit && (
            <Button variant="outline" className="w-full" disabled={pending} onClick={() => run(() => openSuiteDraft(table), "Draft opened")} data-testid="suite-open-draft">
              <Pencil className="h-4 w-4" /> Open a draft to change the checks
            </Button>
          )}
          {detail.open && detail.open.version !== cur.version && (
            <Link className="block text-xs text-primary hover:underline" href={suiteHref(table, "review")}>Go to the open v{detail.open.version}</Link>
          )}
        </section>
      </aside>
      {editing && <CheckModal table={table} check={editing.check} columns={columns} onClose={() => { setEditing(null); router.refresh(); }} />}
    </div>
  );
}

function VersionsTab({ table, versions, canEdit, canApprove, hasOpen }: { table: string; versions: DqSuiteVersion[]; canEdit: boolean; canApprove: boolean; hasOpen: boolean }) {
  const router = useRouter();
  const toast = useToast();
  const [pending, start] = useTransition();
  const [from, setFrom] = useState(versions[1]?.version ?? versions[0]?.version ?? 0);
  const [to, setTo] = useState(versions[0]?.version ?? 0);
  const [diff, setDiff] = useState<DqSuiteDiff | null>(null);
  const compare = () => start(async () => {
    const r = await diffSuite(table, from, to);
    if (r.ok) setDiff(r.data); else toast("error", explain(r.error).message);
  });
  const rollback = (v: number) => start(async () => {
    const r = await openSuiteDraft(table, v, `Rollback to version ${v}`);
    if (r.ok) { toast("ok", `Draft v${r.data.version} opened from v${v}; review and submit it`); router.push(suiteHref(table, "review")); }
    else toast("error", explain(r.error).message);
  });
  const reapply = (v: number) => start(async () => {
    const r = await applySuite(table, v);
    if (r.ok && r.data.applied) toast("ok", `v${v} applied to the catalog (${r.data.checks ?? 0} checks)`);
    else toast("error", r.ok ? r.data.error ?? "not applied" : explain(r.error).message);
    router.refresh();
  });
  return (
    <div className="space-y-4">
      <section className="surface overflow-x-auto">
        <table className="w-full text-sm" data-testid="suite-versions">
          <thead className="bg-muted/60 text-[11px] uppercase tracking-[0.06em] text-muted-foreground">
            <tr><th className="px-3 py-2 text-left">Version</th><th className="px-3 py-2 text-left">Status</th><th className="px-3 py-2 text-left">People</th>
              <th className="px-3 py-2 text-left">Domain model</th><th className="px-3 py-2 text-left">Note</th><th className="px-3 py-2" /></tr>
          </thead>
          <tbody>
            {versions.map((v) => (
              <tr key={v.version} className="border-t border-border/60 align-top">
                <td className="px-3 py-2 font-medium"><Link className="text-primary hover:underline" href={`/dashboard/dq-store/suite?table=${encodeURIComponent(table)}&version=${v.version}`}>v{v.version}</Link></td>
                <td className="px-3 py-2"><StatusPill status={v.status} /></td>
                <td className="px-3 py-2 text-xs text-muted-foreground">{v.authors.join(", ") || v.created_by}{v.submitted_by ? ` · submitted ${v.submitted_by}` : ""}{v.approved_by ? ` · approved ${v.approved_by}` : ""}{v.rejected_by ? ` · rejected ${v.rejected_by}` : ""}</td>
                <td className="px-3 py-2 text-xs">{v.model_version ? `v${v.model_version}` : "-"}</td>
                <td className="max-w-xs px-3 py-2 text-xs text-muted-foreground">{v.reject_reason ?? v.change_note ?? ""}</td>
                <td className="px-3 py-2 text-right">
                  {canEdit && !hasOpen && ["APPROVED", "PUBLISHED", "SUPERSEDED"].includes(v.status) && (
                    <Button size="sm" variant="ghost" disabled={pending} onClick={() => rollback(v.version)}><RotateCcw className="h-3.5 w-3.5" /> Draft from v{v.version}</Button>
                  )}
                  {canApprove && ["APPROVED", "PUBLISHED"].includes(v.status) && !v.catalog_synced_at && (
                    <Button size="sm" variant="outline" disabled={pending} onClick={() => reapply(v.version)}>Apply to catalog</Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {versions.length > 1 && (
        <section className="surface space-y-3 p-4">
          <div className="flex flex-wrap items-end gap-2">
            <div><Label htmlFor="cmp-from">From</Label>
              <Select id="cmp-from" value={from} onChange={(e) => setFrom(Number(e.target.value))}>{versions.map((v) => <option key={v.version} value={v.version}>v{v.version}</option>)}</Select></div>
            <div><Label htmlFor="cmp-to">To</Label>
              <Select id="cmp-to" value={to} onChange={(e) => setTo(Number(e.target.value))}>{versions.map((v) => <option key={v.version} value={v.version}>v{v.version}</option>)}</Select></div>
            <Button variant="outline" onClick={compare} disabled={pending || from === to} data-testid="suite-compare"><GitCompare className="h-4 w-4" /> Compare</Button>
          </div>
          {diff && <ChangeList diff={diff} empty="The two versions have the same checks." />}
        </section>
      )}
    </div>
  );
}

function PublishTab({ table, versions, canPublish }: { table: string; versions: DqSuiteVersion[]; canPublish: boolean }) {
  const router = useRouter();
  const toast = useToast();
  const [pub, setPub] = useState<SuitePublishing | null>(null);
  const [repo, setRepo] = useState("");
  const [pending, start] = useTransition();
  const approved = versions.find((v) => v.status === "APPROVED");
  const live = versions.filter((v) => ["APPROVED", "PUBLISHED"].includes(v.status) || v.pr_state);
  useEffect(() => {
    (async () => {
      const r = await loadSuitePublishing();
      if (r.ok) { setPub(r.data); setRepo(r.data.repos[0]?.repo_id ?? ""); }
      if (versions.some((v) => v.pr_state === "OPEN")) {
        const s = await syncSuitePrs(table);
        if (s.ok && (s.data.merged.length || s.data.closed.length)) router.refresh();
      }
    })();
  }, [table]); // eslint-disable-line react-hooks/exhaustive-deps
  const publish = (v: DqSuiteVersion) => start(async () => {
    const r = await publishSuite(table, v.version, repo);
    if (!r.ok) { toast("error", explain(r.error).message); return; }
    if (r.data.status === "PUBLISHED") toast("ok", `Pull request opened for v${v.version}: ${r.data.head_branch}`);
    else if (r.data.status === "NO_CHANGES") toast("ok", "The repository already has these checks; marked published");
    else toast("error", r.data.detail || `Publishing failed (${r.data.status})`);
    router.refresh();
  });
  const sync = () => start(async () => {
    const r = await syncSuitePrs(table, true);
    if (r.ok) { toast("ok", r.data.detail || `${r.data.checked} pull request(s) checked`); router.refresh(); } else toast("error", explain(r.error).message);
  });
  return (
    <div className="space-y-4" data-testid="suite-publish">
      <section className="surface space-y-3 p-4">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold"><GitPullRequest className="h-4 w-4" /> Publish to Git</h3>
        <p className="text-sm text-muted-foreground">Only an approved version is published: its SodaCL file and Great Expectations suite go to <span className="font-mono">dq/&lt;domain&gt;/</span> on a branch with a pull request. The version becomes published when the pull request is merged.</p>
        {!pub ? <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />Loading repositories...</p>
          : !pub.publisher_ready ? <p className="text-sm text-amber-700">GitHub publishing is not set up. An admin can set it up in Admin, Integrations (GitHub publishing).</p>
          : !pub.repos.length ? <p className="text-sm text-amber-700">No GitHub repository is connected. Connect one in Admin, Integrations.</p>
          : (
            <div className="flex flex-wrap items-end gap-2">
              <div><Label htmlFor="dq-repo">Repository</Label>
                <Select id="dq-repo" className="h-9 w-72" value={repo} onChange={(e) => setRepo(e.target.value)}>
                  {pub.repos.map((r) => <option key={r.repo_id} value={r.repo_id}>{r.name} ({r.branch})</option>)}</Select></div>
              {approved && canPublish && (!approved.pr_state || ["CLOSED", "FAILED"].includes(approved.pr_state)) && (
                <Button onClick={() => publish(approved)} disabled={pending || !repo} data-testid="suite-publish-version">
                  {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitPullRequest className="h-4 w-4" />}
                  {approved.pr_state ? `Publish v${approved.version} again` : `Publish v${approved.version}`}</Button>
              )}
              <Button variant="outline" onClick={sync} disabled={pending}><RefreshCw className="h-4 w-4" /> Check pull requests</Button>
            </div>
          )}
      </section>
      <section className="surface overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/60 text-[11px] uppercase tracking-[0.06em] text-muted-foreground">
            <tr><th className="px-3 py-2 text-left">Version</th><th className="px-3 py-2 text-left">Status</th><th className="px-3 py-2 text-left">Pull request</th><th className="px-3 py-2 text-left">Branch</th></tr>
          </thead>
          <tbody>
            {live.map((v) => (
              <tr key={v.version} className="border-t border-border/60">
                <td className="px-3 py-2 font-medium">v{v.version}</td>
                <td className="px-3 py-2"><StatusPill status={v.status} /></td>
                <td className="px-3 py-2 text-xs">
                  {v.pr_state && <span className={cn("mr-2 rounded px-1.5 py-0.5 text-[10px] font-semibold", PR_TONE[v.pr_state] ?? "bg-muted")}>{v.pr_state.toLowerCase().replace(/_/g, " ")}</span>}
                  {v.pr_url ? <a className="inline-flex items-center gap-1 text-primary hover:underline" href={v.pr_url} target="_blank" rel="noreferrer">#{v.pr_number} <ExternalLink className="h-3 w-3" /></a> : v.pr_state ? null : <span className="text-muted-foreground">not published</span>}
                </td>
                <td className="px-3 py-2 font-mono text-xs">{v.branch ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!live.length && <p className="p-4 text-sm text-muted-foreground">No approved version yet.</p>}
      </section>
      <SodaCloudCard />
    </div>
  );
}

function ExportTab({ table, version }: { table: string; version: number | null }) {
  const [format, setFormat] = useState<"soda" | "gx" | "portable">("soda");
  const [text, setText] = useState<string | null>(null);
  const href = `/bff/dq-export?${new URLSearchParams({ table, format, ...(version ? { version: String(version) } : {}) })}`;
  useEffect(() => {
    setText(null);
    fetch(href).then((r) => (r.ok ? r.text() : Promise.resolve(`Could not render: ${r.status}`))).then(setText, () => setText("Could not reach the API"));
  }, [href]);
  return (
    <section className="surface space-y-3 p-4" data-testid="suite-export">
      <div className="flex flex-wrap items-center gap-2">
        <div role="radiogroup" aria-label="Format" className="inline-flex rounded-xl bg-muted p-1">
          {([["soda", "SodaCL"], ["gx", "Great Expectations"], ["portable", "Portable (any environment)"]] as const).map(([id, label]) => (
            <button key={id} type="button" role="radio" aria-checked={format === id} onClick={() => setFormat(id)}
                    className={cn("rounded-lg px-3 py-1.5 text-xs font-medium", format === id ? "bg-card text-foreground shadow-xs" : "text-muted-foreground")}>{label}</button>
          ))}
        </div>
        <span className="text-xs text-muted-foreground">
          {format === "portable"
            ? `Checks of v${version ?? "?"} with this environment's table names and database mapping: import it in another environment's Data Quality Store and its tables move to that environment's databases, or commit it as domain/<name>/dq/<table>.dq.json to seed one at deploy.`
            : `Active checks of v${version ?? "?"}; paused checks are left out.`}
        </span>
        <a href={href} className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-muted"><Download className="h-4 w-4" /> Download</a>
      </div>
      <pre className="max-h-[480px] overflow-auto rounded-xl bg-slate-950 p-3 font-mono text-xs text-emerald-300">{text ?? "Rendering..."}</pre>
    </section>
  );
}

/** One table's data quality suite: what changed, review and approval, history, Git and export. */
export function SuiteView({ table, detail, me, initialTab, canEdit, canApprove }: {
  table: string; detail: DqSuiteDetail; me: string; initialTab: string | null; canEdit: boolean; canApprove: boolean;
}) {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>(() => (TABS.find(([t]) => t === (rememberedTab() ?? initialTab))?.[0]) ?? "review");
  useEffect(() => rememberTab(tab), [tab]);
  const versions = detail.versions;
  const picked = detail.current?.version ?? null;
  const options = useMemo(() => versions.map((v) => ({ v: v.version, label: `v${v.version} · ${v.status.toLowerCase().replace("_", " ")}` })), [versions]);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {options.length > 0 && (
          <Select aria-label="Version" className="h-9 w-56" value={picked ?? ""}
                  onChange={(e) => router.push(`/dashboard/dq-store/suite?table=${encodeURIComponent(table)}&version=${e.target.value}&tab=${tab}`)}>
            {options.map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
          </Select>
        )}
      </div>
      <nav className="flex flex-wrap gap-1 border-b border-border/70" aria-label="Suite sections">
        {TABS.map(([id, label, Icon]) => (
          <button key={id} type="button" onClick={() => setTab(id)} aria-current={tab === id ? "page" : undefined} data-testid={`suite-tab-${id}`}
                  className={cn("-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition", tab === id ? "border-primary font-medium text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}>
            <Icon className="h-4 w-4" /> {label}
          </button>
        ))}
      </nav>
      {tab === "review" && <ReviewTab table={table} detail={detail} me={me} canEdit={canEdit} canApprove={canApprove} />}
      {tab === "versions" && <VersionsTab table={table} versions={versions} canEdit={canEdit} canApprove={canApprove} hasOpen={!!detail.open} />}
      {tab === "publish" && <PublishTab table={table} versions={versions} canPublish={canEdit} />}
      {tab === "export" && <ExportTab table={table} version={picked} />}
    </div>
  );
}
