"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ListChecks, MessagesSquare, PanelLeftOpen, PanelRightOpen, ShieldCheck, Ticket, Upload } from "lucide-react";
import { Drawer } from "@/components/hub/overlay";
import {
  readStream, toItems, type Attachment, type Change, type ChatItem, type CopilotSession, type PlanStep, type SessionRow,
  type Workbench,
} from "@/lib/copilot-types";
import {
  createCopilotSession, deleteCopilotSession, discardCopilotChange, loadCopilotSession, removeCopilotFile, uploadCopilotFiles,
} from "../actions";
import { Composer, DRAFT_KEY, MAX_FILE_BYTES, MAX_FILES, MAX_JIRA, type Uploading } from "./composer";
import { Home } from "./home";
import { Inspector } from "./inspector";
import { ReviewDrawer } from "./review-drawer";
import { ConversationList } from "./sidebar";
import { Thread } from "./thread";

const EMPTY: Workbench = { model: [], sttm: [], dbt: [], quality: [] };
let seq = 0;
const nextId = () => `local-${++seq}`;

async function failure(res: Response, what: string): Promise<string> {
  try {
    const j = await res.json();
    if (typeof j.detail === "string") return j.detail;
    if (Array.isArray(j.detail)) return j.detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join("; ") || `${what} (${res.status})`;
  } catch { /* not JSON */ }
  return `${what} (${res.status}).`;
}

const isStream = (res: Response) => (res.headers.get("content-type") ?? "").includes("text/event-stream");

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
  // files attached before the first message are still waiting to be sent with it
  const [pendingFiles, setPendingFiles] = useState<Attachment[]>(() => initial && !initial.events.length ? initial.attachments : []);
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
  const [mobilePanel, setMobilePanel] = useState<"conversations" | "plan" | null>(null);
  const [dragging, setDragging] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const creating = useRef<Promise<string | null> | null>(null);
  const createdHere = useRef(false);
  const working = useRef(false);        // a turn or a publish is in flight (set before any await)
  const refreshes = useRef(0);

  const say = useCallback((item: ChatItem) => setItems((xs) => [...xs, item]), []);
  const sayError = useCallback((text: string) => say({ id: nextId(), kind: "ERROR", text }), [say]);

  // follow the conversation while the reader is at the bottom; leave them alone when they scrolled up
  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTo({ top: el.scrollHeight, behavior: items.length > 1 ? "smooth" : "auto" });
  }, [items.length, busy]);
  useEffect(() => {  // a draft kept while the person connected Jira
    try { const kept = sessionStorage.getItem(DRAFT_KEY); if (kept) { setDraft(kept); sessionStorage.removeItem(DRAFT_KEY); } } catch { /* storage unavailable */ }
  }, []);
  useEffect(() => {
    const clear = () => setDragging(false);
    window.addEventListener("dragend", clear);
    window.addEventListener("drop", clear);
    return () => { window.removeEventListener("dragend", clear); window.removeEventListener("drop", clear); };
  }, []);

  const refresh = useCallback(async (id: string) => {
    const mine = ++refreshes.current;
    const r = await loadCopilotSession(id).catch(() => null);
    if (!r?.ok || mine !== refreshes.current) return;   // a newer refresh (or a newer stream) wins
    setPlan(r.data.plan); setChanges(r.data.changes); setWorkbench(r.data.workbench); setAttachments(r.data.attachments);
    setJiraKey(r.data.session.jira_key ?? null);
    const proposed = r.data.changes.filter((c) => c.status === "PROPOSED").length;
    setSessions((xs) => {
      const now = new Date().toISOString();
      const rows = xs.map((s) => s.session_id === id ? { ...s, proposed, jira_key: r.data.session.jira_key, updated_at: now } : s);
      return [...rows.filter((s) => s.session_id === id), ...rows.filter((s) => s.session_id !== id)];
    });
  }, []);

  /** The conversation's id, creating it on first use (first message, first file). Never throws. */
  const ensureSession = useCallback(async (hint: string): Promise<string | null> => {
    if (sessionId) return sessionId;
    if (creating.current) return creating.current;
    creating.current = (async () => {
      const name = (hint || "New conversation").replace(/\s+/g, " ").trim().slice(0, 120);
      try {
        const r = await createCopilotSession({ title: name });
        if (!r.ok) { sayError(r.error); creating.current = null; return null; }
        const id = r.data.session_id;
        createdHere.current = true;
        setSessionId(id); setTitle(name);
        setSessions((xs) => [{ session_id: id, title: name, status: "OPEN", updated_at: new Date().toISOString() }, ...xs]);
        window.history.replaceState(null, "", `/dashboard/copilot/${id}`);
        return id;
      } catch {
        creating.current = null;
        sayError("The conversation could not be started. Check your connection and try again.");
        return null;
      }
    })();
    return creating.current;
  }, [sessionId, sayError]);

  /** After the first turn of a conversation started on the blank page, align the router with its URL, so New
   *  conversation, Back and Forward work. */
  const settleRoute = useCallback((id: string) => {
    if (!createdHere.current) return;
    createdHere.current = false;
    router.replace(`/dashboard/copilot/${id}`, { scroll: false });
  }, [router]);

  const attach = useCallback(async (files: File[]) => {
    const tooBig = files.filter((f) => f.size > MAX_FILE_BYTES);
    if (tooBig.length) sayError(`${tooBig.map((f) => f.name).join(", ")}: larger than ${MAX_FILE_BYTES / 1048576} MB. Attach a smaller extract.`);
    let ok = files.filter((f) => f.size <= MAX_FILE_BYTES);
    const room = MAX_FILES - attachments.length;
    if (ok.length > room) { sayError(`A conversation holds at most ${MAX_FILES} files.`); ok = ok.slice(0, Math.max(room, 0)); }
    if (!ok.length) return;
    const marks = ok.map((f) => ({ key: nextId(), name: f.name }));
    setUploading((u) => [...u, ...marks]);            // before any await, so Send waits for the upload
    try {
      const id = await ensureSession(draft || "Files");
      if (!id) return;
      const form = new FormData();
      ok.forEach((f) => form.append("files", f));
      const r = await uploadCopilotFiles(id, form);
      if (!r.ok) { sayError(`Could not attach: ${r.error}`); return; }
      setPendingFiles((p) => [...p, ...r.data.attachments]);
      setAttachments((a) => [...a, ...r.data.attachments]);
    } catch {
      sayError("The upload failed. Check the file and your connection, then attach it again.");
    } finally {
      setUploading((u) => u.filter((x) => !marks.some((m) => m.key === x.key)));
    }
  }, [ensureSession, draft, attachments.length, sayError]);

  const removeFile = useCallback(async (attachmentId: string) => {
    if (!sessionId) return;
    setPendingFiles((p) => p.filter((f) => f.attachment_id !== attachmentId));
    setAttachments((a) => a.filter((f) => f.attachment_id !== attachmentId));
    const r = await removeCopilotFile(sessionId, attachmentId).catch(() => null);
    if (!r?.ok) sayError(r ? r.error : "The file could not be removed.");
  }, [sessionId, sayError]);

  const runTurn = useCallback(async (id: string, body: Record<string, unknown>) => {
    refreshes.current++;   // anything already loading is older than this turn
    try {
      const res = await fetch(`/bff/copilot/${encodeURIComponent(id)}/turn`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!res.ok) { sayError(await failure(res, "The copilot could not answer")); return; }
      if (!isStream(res)) { sayError("Your session has ended. Sign in again to continue."); return; }
      await readStream(res, (e) => {
        switch (e.type) {
          case "thought": say({ id: nextId(), kind: "THOUGHT", text: e.text }); break;
          case "tool": say({ id: nextId(), kind: "TOOL", tool: e.tool, ok: e.ok, error: e.error, summary: e.summary }); break;
          case "plan": setPlan(e.plan); break;
          case "change":
            setChanges((cs) => cs.some((c) => c.change_id === e.change.change_id)
              ? cs.map((c) => c.change_id === e.change.change_id ? e.change : c) : [...cs, e.change]);
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
      working.current = false;
      setBusy(false);
      await refresh(id);
      settleRoute(id);
    }
  }, [say, sayError, refresh, settleRoute]);

  const send = useCallback(async () => {
    if (working.current || publishing || uploading.length) return;
    const text = draft.trim();
    const files = pendingFiles, keys = jiraKeys, target = revising;
    if (!text && !files.length && !keys.length) return;
    working.current = true;
    setBusy(true);
    const message = text || (keys.length ? `Pick up ${keys.join(", ")} and tell me what it needs.` : "Read the attached files and tell me what they need.");
    // take the inputs now, so a second Enter or anything typed while the conversation starts is not lost or doubled
    setDraft(""); setPendingFiles([]); setJiraKeys([]); setRevising(null);
    stick.current = true;
    say({ id: nextId(), kind: "USER", text: message, revise: target ? { title: target.title } : undefined, jira_keys: keys,
          attachments: files.map((f) => ({ file_name: f.file_name })) });
    const id = await ensureSession(message);
    if (!id) {
      working.current = false; setBusy(false);
      setDraft(text); setPendingFiles(files); setJiraKeys(keys); setRevising(target);
      return;
    }
    await runTurn(id, { message, jira_keys: keys, attachment_ids: files.map((f) => f.attachment_id), change_id: target?.change_id });
  }, [draft, pendingFiles, jiraKeys, revising, publishing, uploading.length, ensureSession, say, runTurn]);

  const answer = useCallback(async (answers: Record<string, unknown>, shown: Record<string, unknown>) => {
    if (!sessionId || working.current || publishing) return;
    working.current = true;
    setBusy(true);
    stick.current = true;
    say({ id: nextId(), kind: "ANSWER", answers: shown });
    await runTurn(sessionId, { form_answers: answers });
  }, [sessionId, publishing, say, runTurn]);

  const publish = useCallback(async (ids: string[]) => {
    if (!sessionId || working.current) return;
    working.current = true;
    setPublishing(true);
    try {
      const res = await fetch(`/bff/copilot/${encodeURIComponent(sessionId)}/publish`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ change_ids: ids }),
      });
      if (!res.ok) { sayError(await failure(res, "Publishing failed")); return; }
      if (!isStream(res)) { sayError("Your session has ended. Sign in again to publish."); return; }
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
      working.current = false;
      setPublishing(false);
      setProgress({});
      await refresh(sessionId);
    }
  }, [sessionId, say, sayError, refresh]);

  const discard = useCallback(async (changeId: string) => {
    if (!sessionId) return;
    const r = await discardCopilotChange(sessionId, changeId).catch(() => null);
    if (!r?.ok) { sayError(r ? r.error : "The draft could not be discarded."); return; }
    if (!working.current) void refresh(sessionId);
    else setChanges((cs) => cs.map((c) => c.change_id === changeId ? { ...c, status: "DISCARDED" } : c));
  }, [sessionId, sayError, refresh]);

  const lastAskId = useMemo(() => {
    for (let i = items.length - 1; i >= 0; i--) {
      if (items[i].kind === "USER" || items[i].kind === "ANSWER") return null;
      if (items[i].kind === "ASK") return items[i].id;
    }
    return null;
  }, [items]);

  const open = changes.filter((c) => c.status === "PROPOSED");
  const started = items.length > 0 || busy;
  const newConversation = () => {
    if (!initial && sessionId) { window.location.assign("/dashboard/copilot"); return; }  // started here: start clean
    router.push("/dashboard/copilot");
  };
  const composer = (hero: boolean) => (
    <Composer value={draft} onChange={setDraft} onSend={() => void send()} busy={busy || publishing} disabled={disabled} hero={hero} autoFocus
              files={pendingFiles} uploading={uploading} onAttach={(f) => void attach(f)} onRemoveFile={(id) => void removeFile(id)}
              jiraKeys={jiraKeys} onAddJira={(k) => setJiraKeys((ks) => ks.includes(k) || ks.length >= MAX_JIRA ? ks : [...ks, k])}
              onRemoveJira={(k) => setJiraKeys((ks) => ks.filter((x) => x !== k))}
              banner={revising ? `Revising: ${revising.title}` : null} onCancelBanner={() => setRevising(null)}
              placeholder={revising ? "What should change in this draft?" : lastAskId ? "Answer above, or reply in your own words" : undefined} />
  );
  const inspector = (
    <Inspector plan={plan} changes={changes} attachments={attachments} jiraKey={jiraKey} busy={busy}
               onReview={(id) => { setMobilePanel(null); setReview({ open: true, focus: id ?? null }); }}
               onPublish={() => { setMobilePanel(null); setReview({ open: true }); }}
               publishing={publishing} onRemoveFile={(id) => void removeFile(id)} onHide={() => { setShowRight(false); setMobilePanel(null); }} />
  );
  /** Delete a conversation; leaving it when it is the one open. True when it was deleted. */
  const deleteConversation = async (id: string): Promise<boolean> => {
    const r = await deleteCopilotSession(id).catch(() => null);
    if (!r?.ok) { sayError(r ? r.error : "The conversation could not be deleted."); return false; }
    setSessions((xs) => xs.filter((s) => s.session_id !== id));
    if (id === sessionId) window.location.assign("/dashboard/copilot");
    return true;
  };
  const list = (
    <ConversationList sessions={sessions} current={sessionId} onHide={() => { setShowLeft(false); setMobilePanel(null); }}
                      onNew={newConversation} onDelete={deleteConversation} />
  );

  return (
    <div className="-mx-6 -my-7 flex h-[calc(100dvh-3.5rem-1px)] min-h-[560px] overflow-hidden bg-background lg:-mx-10">
      {showLeft && <div className="hidden w-[264px] shrink-0 border-r border-border/70 bg-muted/20 lg:block">{list}</div>}

      <section className="relative flex min-w-0 flex-1 flex-col"
               onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDragging(true); } }}
               onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false); }}
               onDrop={(e) => { e.preventDefault(); setDragging(false); const f = Array.from(e.dataTransfer.files); if (f.length && !disabled) void attach(f); }}>
        <header className="flex h-12 shrink-0 items-center gap-1.5 border-b border-border/70 px-3">
          <button type="button" onClick={() => setMobilePanel("conversations")} aria-label="Conversations"
                  className="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground hover:bg-muted lg:hidden"><MessagesSquare className="h-4 w-4" /></button>
          {!showLeft && (
            <button type="button" onClick={() => setShowLeft(true)} aria-label="Show conversations"
                    className="hidden h-8 w-8 place-items-center rounded-lg text-muted-foreground hover:bg-muted lg:grid"><PanelLeftOpen className="h-4 w-4" /></button>
          )}
          <p className="min-w-0 flex-1 truncate px-1 text-sm font-semibold">{started ? title || "New conversation" : "Copilot"}</p>
          {jiraKey && <span className="hidden items-center gap-1 rounded-md bg-sky-500/10 px-1.5 py-0.5 font-mono text-[11px] text-sky-700 dark:text-sky-300 sm:inline-flex"><Ticket className="h-3 w-3" />{jiraKey}</span>}
          {open.length > 0 && (
            <button type="button" onClick={() => setReview({ open: true })}
                    className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-medium text-primary-foreground shadow-sm">
              <ShieldCheck className="h-3.5 w-3.5" /> Review {open.length}
            </button>
          )}
          {started && (
            <button type="button" onClick={() => setMobilePanel("plan")} aria-label="Plan and changes"
                    className="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground hover:bg-muted xl:hidden"><ListChecks className="h-4 w-4" /></button>
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
            <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto"
                 onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120; }}>
              <Thread items={items} busy={busy} changes={changes} lastAskId={lastAskId}
                      onAnswer={(a, shown) => void answer(a, shown)}
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

      {started && showRight && <div className="hidden w-[340px] shrink-0 border-l border-border/70 bg-muted/20 xl:block">{inspector}</div>}

      <Drawer open={mobilePanel === "conversations"} onClose={() => setMobilePanel(null)} title="Conversations">
        <div className="-m-5 h-[calc(100%+2.5rem)]">{list}</div>
      </Drawer>
      <Drawer open={mobilePanel === "plan"} onClose={() => setMobilePanel(null)} title="Plan and changes">
        <div className="-m-5 h-[calc(100%+2.5rem)]">{inspector}</div>
      </Drawer>

      <ReviewDrawer open={review.open} focus={review.focus} onClose={() => !publishing && setReview({ open: false })}
                    changes={changes} workbench={workbench} onDiscard={(id) => void discard(id)} busy={busy}
                    onPublish={(ids) => void publish(ids)} publishing={publishing} progress={progress} />
    </div>
  );
}
