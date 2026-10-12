"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PanelLeftOpen, PanelRightOpen, ShieldCheck, Ticket, Upload } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  readStream, toItems, type Attachment, type Change, type ChatItem, type CopilotSession, type PlanStep, type SessionRow,
  type Workbench,
} from "@/lib/copilot-types";
import {
  createCopilotSession, discardCopilotChange, loadCopilotSession, removeCopilotFile, uploadCopilotFiles,
} from "../actions";
import { Composer, type Uploading } from "./composer";
import { Home } from "./home";
import { Inspector } from "./inspector";
import { ReviewDrawer } from "./review-drawer";
import { ConversationList } from "./sidebar";
import { Thread } from "./thread";

const EMPTY: Workbench = { model: [], sttm: [], dbt: [], quality: [] };
let seq = 0;
const nextId = () => `local-${++seq}`;

/** The copilot workspace: conversations, the thread with its composer, and the plan and changes panel. */
export function CopilotApp({ sessions: initialSessions, initial, disabled }: {
  sessions: SessionRow[]; initial: CopilotSession | null; disabled?: string;
}) {
  const router = useRouter();
  const [sessions, setSessions] = useState(initialSessions);
  const [sessionId, setSessionId] = useState<string | null>(initial?.session.session_id ?? null);
  const [title, setTitle] = useState(initial?.session.title ?? "");
  const [jiraKey, setJiraKey] = useState<string | null>(initial?.session.jira_key ?? null);
  const [items, setItems] = useState<ChatItem[]>(() => toItems(initial?.events ?? []));
  const [plan, setPlan] = useState<PlanStep[]>(initial?.plan ?? []);
  const [changes, setChanges] = useState<Change[]>(initial?.changes ?? []);
  const [workbench, setWorkbench] = useState<Workbench>(initial?.workbench ?? EMPTY);
  const [attachments, setAttachments] = useState<Attachment[]>(initial?.attachments ?? []);
  const [pendingFiles, setPendingFiles] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState<Uploading[]>([]);
  const [jiraKeys, setJiraKeys] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [progress, setProgress] = useState<Record<string, string>>({});
  const [review, setReview] = useState<{ open: boolean; focus?: string | null }>({ open: false });
  const [revising, setRevising] = useState<Change | null>(null);
  const [showLeft, setShowLeft] = useState(true);
  const [showRight, setShowRight] = useState(true);
  const [dragging, setDragging] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const creating = useRef<Promise<string | null> | null>(null);

  const say = useCallback((item: ChatItem) => setItems((xs) => [...xs, item]), []);
  const sayError = useCallback((text: string) => say({ id: nextId(), kind: "ERROR", text }), [say]);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [items.length, busy]);

  const refresh = useCallback(async (id: string) => {
    const r = await loadCopilotSession(id);
    if (!r.ok) return;
    setPlan(r.data.plan); setChanges(r.data.changes); setWorkbench(r.data.workbench); setAttachments(r.data.attachments);
    setJiraKey(r.data.session.jira_key ?? null);
    const proposed = r.data.changes.filter((c) => c.status === "PROPOSED").length;
    setSessions((xs) => xs.map((s) => s.session_id === id ? { ...s, proposed, jira_key: r.data.session.jira_key, updated_at: new Date().toISOString() } : s));
  }, []);

  /** The conversation's id, creating the conversation on first use (first message, first file or first Jira issue). */
  const ensureSession = useCallback(async (hint: string): Promise<string | null> => {
    if (sessionId) return sessionId;
    if (creating.current) return creating.current;
    creating.current = (async () => {
      const name = (hint || "New conversation").replace(/\s+/g, " ").trim().slice(0, 120);
      const r = await createCopilotSession({ title: name });
      if (!r.ok) { sayError(r.error); creating.current = null; return null; }
      const id = r.data.session_id;
      setSessionId(id); setTitle(name);
      setSessions((xs) => [{ session_id: id, title: name, status: "OPEN", updated_at: new Date().toISOString() }, ...xs]);
      window.history.replaceState(null, "", `/dashboard/copilot/${id}`);
      await refresh(id);
      return id;
    })();
    return creating.current;
  }, [sessionId, refresh, sayError]);

  const attach = useCallback(async (files: File[]) => {
    const id = await ensureSession(draft || files[0]?.name || "Files");
    if (!id) return;
    const marks = files.map((f) => ({ key: nextId(), name: f.name }));
    setUploading((u) => [...u, ...marks]);
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    const r = await uploadCopilotFiles(id, form);
    setUploading((u) => u.filter((x) => !marks.some((m) => m.key === x.key)));
    if (!r.ok) { sayError(`Could not attach: ${r.error}`); return; }
    setPendingFiles((p) => [...p, ...r.data.attachments]);
    setAttachments((a) => [...a, ...r.data.attachments]);
  }, [ensureSession, draft, sayError]);

  const removeFile = useCallback(async (attachmentId: string) => {
    if (!sessionId) return;
    setPendingFiles((p) => p.filter((f) => f.attachment_id !== attachmentId));
    setAttachments((a) => a.filter((f) => f.attachment_id !== attachmentId));
    const r = await removeCopilotFile(sessionId, attachmentId);
    if (!r.ok) sayError(r.error);
  }, [sessionId, sayError]);

  const runTurn = useCallback(async (id: string, body: Record<string, unknown>) => {
    setBusy(true);
    try {
      const res = await fetch(`/bff/copilot/${encodeURIComponent(id)}/turn`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!res.ok) {
        let detail = `The copilot could not answer (${res.status}).`;
        try { const j = await res.json(); if (typeof j.detail === "string") detail = j.detail; } catch { /* not JSON */ }
        sayError(detail);
        return;
      }
      await readStream(res, (e) => {
        switch (e.type) {
          case "thought": say({ id: nextId(), kind: "THOUGHT", text: e.text }); break;
          case "tool": say({ id: nextId(), kind: "TOOL", tool: e.tool, ok: e.ok, error: e.error, summary: e.summary }); break;
          case "plan": setPlan(e.plan); break;
          case "change":
            setChanges((cs) => [...cs.filter((c) => c.change_id !== e.change.change_id), e.change]);
            if (e.workbench) setWorkbench(e.workbench);
            say({ id: nextId(), kind: "CHANGE", title: e.change.title, change_kind: e.change.kind, change_id: e.change.change_id });
            break;
          case "ask": say({ id: nextId(), kind: "ASK", question: e.question, fields: e.fields ?? [] }); break;
          case "reply": say({ id: nextId(), kind: "REPLY", text: e.text }); break;
          case "error": sayError(e.text); break;
        }
      });
    } catch {
      sayError("The connection to the copilot dropped. Everything drafted so far is kept.");
    } finally {
      setBusy(false);
      void refresh(id);
    }
  }, [say, sayError, refresh]);

  const send = useCallback(async (override?: string) => {
    const text = (override ?? draft).trim();
    if ((!text && !pendingFiles.length && !jiraKeys.length) || busy) return;
    const message = text || (jiraKeys.length ? `Pick up ${jiraKeys.join(", ")} and tell me what it needs.` : "Read the attached files and tell me what they need.");
    const id = await ensureSession(message);
    if (!id) return;
    const target = revising;
    say({ id: nextId(), kind: "USER", text: message, revise: target ? { title: target.title } : undefined, jira_keys: jiraKeys,
          attachments: pendingFiles.map((f) => ({ file_name: f.file_name })) });
    const body = { message, jira_keys: jiraKeys, attachment_ids: pendingFiles.map((f) => f.attachment_id), change_id: target?.change_id };
    setDraft(""); setPendingFiles([]); setJiraKeys([]); setRevising(null);
    await runTurn(id, body);
  }, [draft, pendingFiles, jiraKeys, busy, ensureSession, revising, say, runTurn]);

  const answer = useCallback(async (answers: Record<string, unknown>) => {
    if (!sessionId || busy) return;
    say({ id: nextId(), kind: "ANSWER", answers });
    await runTurn(sessionId, { form_answers: answers });
  }, [sessionId, busy, say, runTurn]);

  const publish = useCallback(async (ids: string[]) => {
    if (!sessionId) return;
    setPublishing(true);
    try {
      const res = await fetch(`/bff/copilot/${encodeURIComponent(sessionId)}/publish`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ change_ids: ids }),
      });
      if (!res.ok) {
        let detail = `Publishing failed (${res.status}).`;
        try { const j = await res.json(); if (typeof j.detail === "string") detail = j.detail; } catch { /* not JSON */ }
        sayError(detail);
        return;
      }
      await readStream(res, (e) => {
        if (e.type === "plan") setPlan(e.plan);
        if (e.type === "start") setProgress((p) => ({ ...p, [e.change_id]: "running" }));
        if (e.type === "result") {
          setProgress((p) => ({ ...p, [e.change_id]: "done" }));
          setChanges((cs) => cs.map((c) => c.change_id === e.change_id ? { ...c, status: e.status, request_id: e.request_id, result: { detail: e.detail } } : c));
        }
        if (e.type === "published") say({ id: nextId(), kind: "PUBLISH", results: e.results });
        if (e.type === "error") sayError(e.text);
      });
      setReview({ open: false });
    } catch {
      sayError("The connection dropped while publishing. Reopen the conversation to see what was applied.");
    } finally {
      setPublishing(false);
      setProgress({});
      void refresh(sessionId);
    }
  }, [sessionId, say, sayError, refresh]);

  const discard = useCallback(async (changeId: string) => {
    if (!sessionId) return;
    const r = await discardCopilotChange(sessionId, changeId);
    if (!r.ok) sayError(r.error); else void refresh(sessionId);
  }, [sessionId, sayError, refresh]);

  const lastAskId = useMemo(() => {
    for (let i = items.length - 1; i >= 0; i--) {
      if (items[i].kind === "USER" || items[i].kind === "ANSWER") return null;
      if (items[i].kind === "ASK") return items[i].id;
    }
    return null;
  }, [items]);

  const open = changes.filter((c) => c.status === "PROPOSED");
  const started = !!sessionId && (items.length > 0 || busy);
  const composer = (hero: boolean) => (
    <Composer value={draft} onChange={setDraft} onSend={() => void send()} busy={busy} disabled={disabled} hero={hero} autoFocus
              files={pendingFiles} uploading={uploading} onAttach={(f) => void attach(f)} onRemoveFile={(id) => void removeFile(id)}
              jiraKeys={jiraKeys} onAddJira={(k) => setJiraKeys((ks) => ks.includes(k) ? ks : [...ks, k])}
              onRemoveJira={(k) => setJiraKeys((ks) => ks.filter((x) => x !== k))}
              banner={revising ? `Revising: ${revising.title}` : null} onCancelBanner={() => setRevising(null)}
              placeholder={revising ? "What should change in this draft?" : lastAskId ? "Answer above, or reply in your own words" : undefined} />
  );

  return (
    <div className="-mx-6 -my-7 flex h-[calc(100dvh-3.5rem-1px)] min-h-[560px] overflow-hidden border-border/70 bg-background lg:-mx-10">
      {showLeft && (
        <div className="hidden w-[264px] shrink-0 border-r border-border/70 bg-muted/20 lg:block">
          <ConversationList sessions={sessions} current={sessionId} onHide={() => setShowLeft(false)}
                            onNew={() => router.push("/dashboard/copilot")} />
        </div>
      )}

      <section className="relative flex min-w-0 flex-1 flex-col"
               onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDragging(true); } }}
               onDragLeave={(e) => { if (e.currentTarget === e.target) setDragging(false); }}
               onDrop={(e) => { e.preventDefault(); setDragging(false); const f = Array.from(e.dataTransfer.files); if (f.length && !disabled) void attach(f); }}>
        <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border/70 px-3">
          {!showLeft && (
            <button type="button" onClick={() => setShowLeft(true)} aria-label="Show conversations"
                    className="hidden h-8 w-8 place-items-center rounded-lg text-muted-foreground hover:bg-muted lg:grid"><PanelLeftOpen className="h-4 w-4" /></button>
          )}
          <h1 className="min-w-0 flex-1 truncate px-1 text-sm font-semibold">{started ? title : "Copilot"}</h1>
          {jiraKey && <span className="hidden items-center gap-1 rounded-md bg-sky-500/10 px-1.5 py-0.5 font-mono text-[11px] text-sky-700 dark:text-sky-300 sm:inline-flex"><Ticket className="h-3 w-3" />{jiraKey}</span>}
          {open.length > 0 && (
            <button type="button" onClick={() => setReview({ open: true })}
                    className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-medium text-primary-foreground shadow-sm">
              <ShieldCheck className="h-3.5 w-3.5" /> Review {open.length}
            </button>
          )}
          {started && !showRight && (
            <button type="button" onClick={() => setShowRight(true)} aria-label="Show plan and changes"
                    className="hidden h-8 w-8 place-items-center rounded-lg text-muted-foreground hover:bg-muted xl:grid"><PanelRightOpen className="h-4 w-4" /></button>
          )}
        </header>

        {!started ? (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <Home composer={composer(true)} disabled={disabled} onStarter={(p) => { setDraft(p); document.getElementById("copilot-hero")?.focus(); }} />
          </div>
        ) : (
          <>
            <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto">
              <Thread items={items} busy={busy} changes={changes} lastAskId={lastAskId} onAnswer={(a) => void answer(a)}
                      onReview={(id) => setReview({ open: true, focus: id ?? null })}
                      onRevise={(c) => { setRevising(c); document.getElementById("copilot-message")?.focus(); }} />
            </div>
            <div className="shrink-0 px-6 pb-5 pt-2">
              <div className="mx-auto max-w-3xl">{composer(false)}</div>
            </div>
          </>
        )}

        {dragging && (
          <div className="pointer-events-none absolute inset-3 z-20 grid place-items-center rounded-2xl border-2 border-dashed border-primary/50 bg-primary/5 backdrop-blur-[1px]">
            <p className="flex items-center gap-2 text-sm font-medium text-primary"><Upload className="h-4 w-4" /> Drop files to attach them</p>
          </div>
        )}
      </section>

      {started && showRight && (
        <div className={cn("hidden w-[340px] shrink-0 border-l border-border/70 bg-muted/20 xl:block")}>
          <Inspector plan={plan} changes={changes} attachments={attachments} jiraKey={jiraKey} busy={busy}
                     onReview={(id) => setReview({ open: true, focus: id ?? null })} onPublish={() => setReview({ open: true })}
                     publishing={publishing} onRemoveFile={(id) => void removeFile(id)} onHide={() => setShowRight(false)} />
        </div>
      )}

      <ReviewDrawer open={review.open} focus={review.focus} onClose={() => !publishing && setReview({ open: false })}
                    changes={changes} workbench={workbench} onDiscard={(id) => void discard(id)}
                    onPublish={(ids) => void publish(ids)} publishing={publishing} progress={progress} />
    </div>
  );
}
