"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle, Bot, Check, ChevronDown, ChevronRight, Circle, CircleDashed, FileDiff, Loader2, Minus, PauseCircle,
  Send, ShieldCheck, Sparkles, User, Wrench, XCircle,
} from "lucide-react";
import { Markdown } from "@/components/copilot/markdown";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  toItems, type Change, type ChatItem, type CopilotSession, type FormField, type PlanStep, type Workbench,
} from "@/lib/copilot-types";
import { START_KEY } from "../new-conversation";
import { loadCopilotSession } from "../actions";
import { DiffWorkbench } from "./workbench";

let localId = 0;
const nextId = () => `local-${++localId}`;

/** Reads one server-sent event stream, calling `on` for each event's JSON payload. */
async function readStream(res: Response, on: (e: Record<string, any>) => void) {
  const reader = res.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const data = block.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("\n");
      if (data) {
        try { on(JSON.parse(data)); } catch { /* a partial or foreign event: skip it */ }
      }
    }
  }
}

export function Workspace({ initial }: { initial: CopilotSession }) {
  const sessionId = initial.session.session_id;
  const [items, setItems] = useState<ChatItem[]>(() => toItems(initial.events));
  const [plan, setPlan] = useState<PlanStep[]>(initial.plan);
  const [changes, setChanges] = useState<Change[]>(initial.changes);
  const [workbench, setWorkbench] = useState<Workbench>(initial.workbench);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  const [revising, setRevising] = useState<Change | null>(null);
  const [showDiff, setShowDiff] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const open = changes.filter((c) => c.status === "PROPOSED");

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [items.length]);

  const refresh = useCallback(async () => {
    const r = await loadCopilotSession(sessionId);
    if (r.ok) { setPlan(r.data.plan); setChanges(r.data.changes); setWorkbench(r.data.workbench); }
  }, [sessionId]);

  const send = useCallback(async (body: { message?: string; form_answers?: Record<string, unknown>; change_id?: string },
                                   echo: ChatItem) => {
    setBusy(true);
    setItems((xs) => [...xs, echo]);
    try {
      const res = await fetch(`/bff/copilot/${encodeURIComponent(sessionId)}/turn`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!res.ok) {
        let detail = `The copilot could not answer (${res.status}).`;
        try { const j = await res.json(); if (typeof j.detail === "string") detail = j.detail; } catch { /* not JSON */ }
        setItems((xs) => [...xs, { id: nextId(), kind: "ERROR", text: detail }]);
        return;
      }
      await readStream(res, (e) => {
        switch (e.type) {
          case "thought": setItems((xs) => [...xs, { id: nextId(), kind: "THOUGHT", text: e.text }]); break;
          case "tool": setItems((xs) => [...xs, { id: nextId(), kind: "TOOL", tool: e.tool, ok: e.ok, error: e.error, summary: e.summary }]); break;
          case "plan": setPlan(e.plan); break;
          case "change":
            setChanges((cs) => [...cs.filter((c) => c.change_id !== e.change.change_id), e.change]);
            if (e.workbench) setWorkbench(e.workbench);
            setItems((xs) => [...xs, { id: nextId(), kind: "CHANGE", title: e.change.title, change_kind: e.change.kind }]);
            break;
          case "ask": setItems((xs) => [...xs, { id: nextId(), kind: "ASK", question: e.question, fields: e.fields ?? [] }]); break;
          case "reply": setItems((xs) => [...xs, { id: nextId(), kind: "REPLY", text: e.text }]); break;
          case "error": setItems((xs) => [...xs, { id: nextId(), kind: "ERROR", text: e.text }]); break;
        }
      });
    } catch {
      setItems((xs) => [...xs, { id: nextId(), kind: "ERROR", text: "The connection to the copilot dropped. Your drafts so far are kept." }]);
    } finally {
      setBusy(false);
      void refresh();
    }
  }, [sessionId, refresh]);

  // a conversation started on the Copilot page sends its first message once
  useEffect(() => {
    let first: string | null = null;
    try { first = sessionStorage.getItem(START_KEY(sessionId)); sessionStorage.removeItem(START_KEY(sessionId)); } catch { /* storage unavailable */ }
    if (first && !initial.events.length) void send({ message: first }, { id: nextId(), kind: "USER", text: first });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const submit = () => {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    if (revising) {
      const target = revising;
      setRevising(null);
      void send({ message: text, change_id: target.change_id }, { id: nextId(), kind: "USER", text, revise: { title: target.title } });
    } else {
      void send({ message: text }, { id: nextId(), kind: "USER", text });
    }
  };

  const lastAsk = [...items].reverse().find((i) => i.kind === "ASK" || i.kind === "USER" || i.kind === "ANSWER");

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
      <section aria-label="Conversation" className="surface flex min-h-[70vh] min-w-0 flex-col">
        <header className="flex flex-wrap items-center gap-2 border-b px-5 py-3">
          <Bot className="h-4 w-4 text-primary" />
          <h1 className="min-w-0 flex-1 truncate text-base font-semibold">{initial.session.title}</h1>
          {initial.session.jira_key && <span className="rounded-full border px-2 py-0.5 font-mono text-[11px]">{initial.session.jira_key}</span>}
        </header>

        <div className="flex-1 space-y-3 overflow-y-auto px-5 py-4" aria-live="polite">
          {!items.length && !busy && <p className="text-sm text-muted-foreground">Say what you need to start.</p>}
          {items.map((item) => (
            <Item key={item.id} item={item} active={item === lastAsk && !busy}
                  onAnswer={(answers) => void send({ form_answers: answers },
                    { id: nextId(), kind: "ANSWER", answers })} />
          ))}
          {busy && <p className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Working…</p>}
          <div ref={bottom} />
        </div>

        <footer className="space-y-2 border-t px-5 py-3">
          {revising && (
            <p className="flex items-center gap-2 text-xs text-muted-foreground">
              Revising <strong className="text-foreground">{revising.title}</strong>
              <button type="button" className="underline" onClick={() => setRevising(null)}>cancel</button>
            </p>
          )}
          <div className="flex items-end gap-2">
            <label htmlFor="copilot-message" className="sr-only">Message</label>
            <textarea id="copilot-message" value={draft} onChange={(e) => setDraft(e.target.value)} rows={2} disabled={busy}
                      onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
                      placeholder={revising ? "What should change in this draft?" : "Reply, or ask for something else"}
                      className="min-h-[44px] flex-1 resize-y rounded-xl border bg-card px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-primary/30" />
            <Button onClick={submit} disabled={busy || !draft.trim()} aria-label="Send"><Send className="h-4 w-4" /></Button>
          </div>
          <div className="flex flex-wrap items-center gap-2 border-t pt-3">
            <Button variant="outline" size="sm" disabled={!open.length} onClick={() => setShowDiff(true)}>
              <FileDiff className="h-4 w-4" /> Review changes{open.length ? ` (${open.length})` : ""}
            </Button>
            <RevisionPicker changes={open} disabled={busy} onPick={(c) => { setRevising(c); document.getElementById("copilot-message")?.focus(); }} />
            <Button size="sm" className="ml-auto" disabled
                    title="Approve and Publish replays the drafted changes through the platform's approval rules. It ships in the next update; until then, apply drafts in their stores.">
              <ShieldCheck className="h-4 w-4" /> Approve &amp; Publish
            </Button>
          </div>
        </footer>
      </section>

      <aside aria-label="Plan" className="space-y-4">
        <PlanRail plan={plan} />
        <ChangeList changes={changes} onOpen={() => setShowDiff(true)} />
      </aside>

      <DiffWorkbench open={showDiff} onClose={() => setShowDiff(false)} sessionId={sessionId} workbench={workbench}
                     changes={open} onChanged={refresh} />
    </div>
  );
}

function RevisionPicker({ changes, disabled, onPick }: { changes: Change[]; disabled: boolean; onPick: (c: Change) => void }) {
  const [open, setOpen] = useState(false);
  if (!changes.length) return <Button variant="outline" size="sm" disabled>Ask revision</Button>;
  return (
    <div className="relative">
      <Button variant="outline" size="sm" disabled={disabled} onClick={() => setOpen(!open)} aria-expanded={open}>
        Ask revision <ChevronDown className="h-3.5 w-3.5" />
      </Button>
      {open && (
        <div role="menu" className="popover absolute bottom-full z-20 mb-1 w-72 p-1 shadow-pop">
          {changes.map((c) => (
            <button key={c.change_id} role="menuitem" type="button" onClick={() => { setOpen(false); onPick(c); }}
                    className="block w-full truncate rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted">{c.title}</button>
          ))}
        </div>
      )}
    </div>
  );
}

function Item({ item, active, onAnswer }: { item: ChatItem; active: boolean; onAnswer: (a: Record<string, unknown>) => void }) {
  const [open, setOpen] = useState(false);
  switch (item.kind) {
    case "USER":
      return (
        <div className="flex justify-end">
          <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-3 py-2 text-sm text-primary-foreground">
            {item.revise && <p className="mb-1 text-[11px] opacity-80">Revise: {item.revise.title}</p>}
            <p className="whitespace-pre-wrap">{item.text}</p>
          </div>
        </div>
      );
    case "ANSWER":
      return (
        <div className="flex justify-end">
          <dl className="max-w-[85%] rounded-2xl rounded-br-sm border bg-muted/40 px-3 py-2 text-xs">
            {Object.entries(item.answers).map(([k, v]) => (
              <div key={k} className="flex gap-2"><dt className="text-muted-foreground">{k}</dt><dd className="font-medium">{String(v)}</dd></div>
            ))}
          </dl>
        </div>
      );
    case "THOUGHT":
      return (
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
                className="flex w-full items-start gap-1.5 text-left text-xs text-muted-foreground">
          {open ? <ChevronDown className="mt-0.5 h-3 w-3 shrink-0" /> : <ChevronRight className="mt-0.5 h-3 w-3 shrink-0" />}
          <Sparkles className="mt-0.5 h-3 w-3 shrink-0" />
          <span className={open ? "" : "line-clamp-1"}>{item.text}</span>
        </button>
      );
    case "TOOL":
      return (
        <div className="text-xs">
          <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
                  className={cn("inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono",
                    item.ok ? "text-muted-foreground" : "border-destructive/40 text-destructive")}>
            {item.ok ? <Wrench className="h-3 w-3" /> : <XCircle className="h-3 w-3" />} {item.tool}
          </button>
          {!item.ok && item.error && <p className="mt-1 pl-2 text-destructive">{item.error}</p>}
          {open && item.ok && item.summary != null && (
            <pre className="mt-1 max-h-64 overflow-auto rounded-lg bg-muted/50 p-2 text-[11px]">{JSON.stringify(item.summary, null, 2)}</pre>
          )}
        </div>
      );
    case "CHANGE":
      return (
        <div className="flex items-center gap-2 rounded-xl border border-primary/25 bg-primary/[0.04] px-3 py-2 text-xs">
          <FileDiff className="h-3.5 w-3.5 text-primary" />
          <span className="font-medium">Drafted</span>
          <span className="truncate">{item.title}</span>
          <span className="ml-auto rounded-full bg-muted px-1.5 py-0.5 text-[10px]">{item.change_kind}</span>
        </div>
      );
    case "ASK":
      return <AskCard question={item.question} fields={item.fields} active={active} onAnswer={onAnswer} />;
    case "ERROR":
      return (
        <p role="alert" className="flex items-start gap-2 rounded-xl border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {item.text}
        </p>
      );
    default:
      return (
        <div className="flex gap-2">
          <Bot className="mt-1 h-4 w-4 shrink-0 text-primary" />
          <div className="min-w-0 flex-1 text-sm"><Markdown text={item.text} /></div>
        </div>
      );
  }
}

function AskCard({ question, fields, active, onAnswer }: {
  question: string; fields: FormField[]; active: boolean; onAnswer: (a: Record<string, unknown>) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const complete = fields.every((f) => f.type === "confirm" || (values[f.name] ?? "").trim());
  return (
    <form className="space-y-3 rounded-xl border bg-card p-3" aria-label="Question from the copilot"
          onSubmit={(e) => {
            e.preventDefault();
            if (!active || !complete) return;
            onAnswer(Object.fromEntries(fields.map((f) => [f.label || f.name, f.type === "confirm" ? (values[f.name] ?? "no") : values[f.name]])));
          }}>
      <p className="flex items-start gap-2 text-sm font-medium"><User className="mt-0.5 h-4 w-4 shrink-0 text-primary" /> {question}</p>
      {fields.map((f) => {
        const id = `ask-${f.name}`;
        if (f.type === "confirm") {
          return (
            <div key={f.name} className="flex items-center gap-2 text-sm" role="radiogroup" aria-label={f.label}>
              <span className="flex-1">{f.label}</span>
              {["yes", "no"].map((v) => (
                <button key={v} type="button" disabled={!active} role="radio" aria-checked={values[f.name] === v}
                        onClick={() => setValues({ ...values, [f.name]: v })}
                        className={cn("rounded-lg border px-3 py-1 text-xs", values[f.name] === v && "border-primary bg-primary/10 text-primary")}>{v}</button>
              ))}
            </div>
          );
        }
        return (
          <div key={f.name} className="space-y-1">
            <label htmlFor={id} className="text-xs text-muted-foreground">{f.label}</label>
            {f.type === "select" && f.options?.length ? (
              <select id={id} disabled={!active} value={values[f.name] ?? ""} onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}
                      className="h-9 w-full rounded-lg border bg-card px-2 text-sm">
                <option value="">Choose…</option>
                {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            ) : (
              <input id={id} disabled={!active} value={values[f.name] ?? ""} onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}
                     className="h-9 w-full rounded-lg border bg-card px-2 text-sm" />
            )}
          </div>
        );
      })}
      {active && <Button type="submit" size="sm" disabled={!complete}><Check className="h-4 w-4" /> Answer</Button>}
    </form>
  );
}

const ICON: Record<PlanStep["status"], typeof Circle> = {
  PENDING: Circle, RUNNING: Loader2, DONE: Check, BLOCKED: AlertTriangle, SKIPPED: Minus,
};

function PlanRail({ plan }: { plan: PlanStep[] }) {
  return (
    <section className="surface p-4" aria-label="Plan">
      <h2 className="mb-3 text-sm font-semibold">Plan</h2>
      <ol className="space-y-2">
        {plan.map((s) => {
          const Icon = ICON[s.status] ?? CircleDashed;
          return (
            <li key={s.key} className="flex items-start gap-2 text-sm">
              <span className={cn("mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border",
                s.status === "DONE" && "border-success bg-success/10 text-success",
                s.status === "RUNNING" && "border-primary text-primary",
                s.status === "BLOCKED" && "border-warning text-warning",
                (s.status === "PENDING" || s.status === "SKIPPED") && "text-muted-foreground")}>
                <Icon className={cn("h-3 w-3", s.status === "RUNNING" && s.key !== "approval" && "animate-spin")} />
              </span>
              <span className="min-w-0">
                <span className={cn("block", s.status === "SKIPPED" && "text-muted-foreground line-through")}>{s.no}. {s.title}</span>
                {s.note && <span className="block truncate text-[11px] text-muted-foreground" title={s.note}>{s.note}</span>}
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

const STATUS_LABEL: Record<Change["status"], string> = {
  PROPOSED: "drafted", APPLIED: "applied", PENDING_APPROVAL: "sent for approval", FAILED: "failed", DISCARDED: "discarded",
};

function ChangeList({ changes, onOpen }: { changes: Change[]; onOpen: () => void }) {
  const shown = changes.filter((c) => c.status !== "DISCARDED");
  return (
    <section className="surface p-4" aria-label="Drafted changes">
      <h2 className="mb-2 text-sm font-semibold">Drafted changes</h2>
      {!shown.length ? <p className="text-xs text-muted-foreground">Nothing drafted yet.</p> : (
        <ul className="space-y-1.5">
          {shown.map((c) => (
            <li key={c.change_id}>
              <button type="button" onClick={onOpen} className="flex w-full items-start gap-2 rounded-lg px-1 py-1 text-left text-xs hover:bg-muted">
                {c.status === "PENDING_APPROVAL" ? <PauseCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
                  : <FileDiff className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{c.title}</span>
                  <span className="text-[11px] text-muted-foreground">{c.kind} · {STATUS_LABEL[c.status] ?? c.status}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
