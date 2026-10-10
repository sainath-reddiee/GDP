"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { createPortal } from "react-dom";
import { Loader2, MessageSquareWarning, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select, Textarea } from "@/components/ui/input";
import { useScrollLock } from "@/components/use-scroll-lock";
import { cn } from "@/lib/utils";
import { openCase, type CaseKind, type CaseOpened, type CaseSeverity, type CaseSource, type PageContext } from "@/app/qa/cases/actions";
import { caseHref, openedText } from "@/app/qa/cases/case-ui";
import { KINDS } from "@/app/qa/cases/case-filters";

const SEVERITIES: { id: CaseSeverity; label: string }[] = [
  { id: "P1", label: "P1, blocking" }, { id: "P2", label: "P2, major" }, { id: "P3", label: "P3, normal" }, { id: "P4", label: "P4, minor" },
];

/** What the reporter was looking at, read from the URL: the path, a run in /runs/<id>, and the table, check, test or
 *  model a page names in its query string. */
export function pageContext(pathname: string, search: string): PageContext {
  const q = new URLSearchParams(search);
  const pick = (...keys: string[]) => keys.map((k) => q.get(k)).find((v) => !!v) ?? undefined;
  const ctx: PageContext = { path: pathname };
  const run = /^\/runs\/([^/?#]+)/.exec(pathname)?.[1];
  const runId = run ? decodeURIComponent(run) : pick("run_id", "run");
  if (runId) ctx.run_id = runId;
  const table = pick("table", "target_table_id", "table_id");
  if (table) ctx.table = table;
  const check = pick("check_id", "check");
  if (check) ctx.check_id = check;
  const test = pick("test_id", "test");
  if (test) ctx.test_id = test;
  const codePath = pathname.startsWith("/code") ? q.get("path") : null;
  const model = pick("model") ?? (codePath && /\.sql$/i.test(codePath) ? codePath.split("/").pop()!.replace(/\.sql$/i, "") : undefined);
  if (model) ctx.model = model;
  return ctx;
}

/** Report a problem: title, description, severity and kind, with the page context attached. Shows the case it opened,
 *  or the one already tracking the same problem. */
export function ReportDialog({ onClose, source = "APP_REPORT", context, domains, title: heading = "Report a problem", onOpened }: {
  onClose: () => void; source?: CaseSource; context?: PageContext | null; domains?: { domain_id: string; name: string }[];
  title?: string; onOpened?: (o: CaseOpened) => void;
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [severity, setSeverity] = useState<CaseSeverity>("P3");
  const [kind, setKind] = useState<CaseKind>("DATA_BUG");
  const [domainId, setDomainId] = useState("");
  const [opened, setOpened] = useState<CaseOpened | null>(null);
  const [error, setError] = useState("");
  const [busy, start] = useTransition();
  useScrollLock();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const ready = title.trim().length >= 3 && description.trim().length > 0;
  const submit = () => start(async () => {
    setError("");
    try {
      const r = await openCase({
        title: title.trim(), description: description.trim(), severity, kind, source,
        ...(domainId ? { domain_id: domainId } : {}),
        ...(context?.run_id ? { run_id: context.run_id } : {}),
        ...(context ? { page_context: context } : {}),
      });
      if (!r.ok) { setError(r.error); return; }
      setOpened(r.data);
      onOpened?.(r.data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not open the case.");
    }
  });
  const facts = context ? ([["Page", context.path], ["Run", context.run_id], ["Table", context.table], ["Check", context.check_id],
    ["Test", context.test_id], ["Model", context.model]] as const).filter(([, v]) => !!v) : [];

  return createPortal(
    <div className="fixed inset-0 z-[80] grid place-items-center bg-black/40 p-4" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-labelledby="report-title" className="w-full max-w-lg space-y-3 rounded-2xl border bg-card p-5 text-foreground shadow-xl">
        <div className="flex items-start gap-2">
          <MessageSquareWarning className="mt-0.5 h-5 w-5 text-primary" />
          <div className="min-w-0 flex-1">
            <h3 id="report-title" className="text-base font-semibold">{heading}</h3>
            <p className="text-xs text-muted-foreground">Opens a case for the QA team. A matching open case is reused instead of a duplicate.</p>
          </div>
          <button type="button" aria-label="Close" disabled={busy} onClick={onClose} className="rounded p-1 hover:bg-muted"><X className="h-4 w-4" /></button>
        </div>
        {opened ? (
          <div className="space-y-3">
            <p role="status" className="rounded-lg bg-success/10 px-3 py-2 text-sm text-success">
              {openedText(opened)}.{" "}
              <Link href={caseHref(opened.case.case_id)} onClick={onClose} className="font-medium underline">Open {opened.case.number}</Link>
            </p>
            <div className="flex justify-end"><Button onClick={onClose}>Done</Button></div>
          </div>
        ) : (
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (ready) submit(); }}>
            <label className="block text-xs font-medium">Title
              <Input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="Customer counts doubled after the last load" className="mt-1" autoFocus />
            </label>
            <label className="block text-xs font-medium">What is wrong
              <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={5} maxLength={8000} className="mt-1 text-sm"
                        placeholder="What you expected, what you see, and where. Leave out personal data; counts are enough." />
            </label>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block text-xs font-medium">Severity
                <Select value={severity} onChange={(e) => setSeverity(e.target.value as CaseSeverity)} className="mt-1 text-sm">
                  {SEVERITIES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
                </Select>
              </label>
              <label className="block text-xs font-medium">Kind
                <Select value={kind} onChange={(e) => setKind(e.target.value as CaseKind)} className="mt-1 text-sm">
                  {KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
                </Select>
              </label>
            </div>
            {domains && domains.length > 0 && (
              <label className="block text-xs font-medium">Domain
                <Select value={domainId} onChange={(e) => setDomainId(e.target.value)} className="mt-1 text-sm">
                  <option value="">Work it out from the context</option>
                  {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.name}</option>)}
                </Select>
              </label>
            )}
            {facts.length > 0 && (
              <div className="rounded-lg bg-muted/40 px-3 py-2 text-[11px]">
                <p className="mb-0.5 font-medium text-muted-foreground">Attached from this page</p>
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
                  {facts.map(([k, v]) => <div key={k} className="contents"><dt className="text-muted-foreground">{k}</dt><dd className="truncate font-mono" title={v}>{v}</dd></div>)}
                </dl>
              </div>
            )}
            {error && <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">{error}</p>}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button>
              <Button type="submit" disabled={busy || !ready}>{busy && <Loader2 className="h-4 w-4 animate-spin" />}Open case</Button>
            </div>
          </form>
        )}
      </div>
    </div>,
    document.body,
  );
}

/** The sidebar's "Report a problem" entry; the dialog captures the page the person was on when they clicked. */
export function ReportProblemButton({ collapsed }: { collapsed: boolean }) {
  const pathname = usePathname() || "/";
  const [context, setContext] = useState<PageContext | null>(null);
  return (
    <>
      <button type="button" onClick={() => setContext(pageContext(pathname, window.location.search))}
              title={collapsed ? "Report a problem" : undefined} aria-label="Report a problem"
              className={cn("flex w-full items-center gap-2 rounded-xl py-2 text-xs text-white/50 transition hover:bg-white/5 hover:text-white/80",
                collapsed ? "justify-center" : "px-2.5")}>
        <MessageSquareWarning className="h-4 w-4" />{!collapsed && "Report a problem"}
      </button>
      {context && <ReportDialog context={context} onClose={() => setContext(null)} />}
    </>
  );
}
