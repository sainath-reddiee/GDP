"use client";

import { useId, useMemo, useState } from "react";
import {
  AlertTriangle, Check, CheckCircle2, ChevronDown, ChevronRight, CircleAlert, Clock3, FileDiff, FileText, Loader2,
  MessageCircleQuestion, PenLine, ShieldCheck, Sparkles, Ticket, Wrench, XCircle,
} from "lucide-react";
import { Markdown } from "@/components/copilot/markdown";
import { cn } from "@/lib/utils";
import { KIND_LABEL, TOOL_LABEL, type Change, type ChatItem, type FormField, type PublishResult } from "@/lib/copilot-types";

type Turn = { user: ChatItem[]; agent: ChatItem[] };

function group(items: ChatItem[]): Turn[] {
  const turns: Turn[] = [];
  for (const item of items) {
    if (item.kind === "USER" || item.kind === "ANSWER") {
      const last = turns[turns.length - 1];
      if (last && !last.agent.length) last.user.push(item); else turns.push({ user: [item], agent: [] });
    } else {
      if (!turns.length) turns.push({ user: [], agent: [] });
      turns[turns.length - 1].agent.push(item);
    }
  }
  return turns;
}

export function AgentMark({ className }: { className?: string }) {
  return (
    <span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-primary text-white shadow-sm", className)}>
      <Sparkles className="h-3.5 w-3.5" />
    </span>
  );
}

/** The conversation, one turn at a time: what the person said, then what the agent did, drafted, asked and replied. */
export function Thread({ items, busy, changes, onAnswer, onReview, onRevise, lastAskId }: {
  items: ChatItem[]; busy: boolean; changes: Change[]; lastAskId: string | null;
  onAnswer: (answers: Record<string, unknown>, shown: Record<string, unknown>) => void; onReview: (changeId?: string) => void; onRevise: (c: Change) => void;
}) {
  const turns = useMemo(() => group(items), [items]);
  const byId = useMemo(() => Object.fromEntries(changes.map((c) => [c.change_id, c])), [changes]);
  return (
    <div role="log" aria-live="polite" aria-label="Conversation" className="mx-auto w-full max-w-3xl space-y-8 px-6 py-8">
      {turns.map((turn, i) => {
        const live = busy && i === turns.length - 1;
        return (
          <div key={turn.user[0]?.id ?? turn.agent[0]?.id ?? i} className="space-y-4">
            {turn.user.map((u) => <UserMessage key={u.id} item={u} />)}
            {(turn.agent.length > 0 || live) && (
              <AgentTurn items={turn.agent} live={live} byId={byId} onAnswer={onAnswer} onReview={onReview}
                         onRevise={onRevise} lastAskId={lastAskId} />
            )}
          </div>
        );
      })}
    </div>
  );
}

function UserMessage({ item }: { item: ChatItem }) {
  if (item.kind === "ANSWER") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-tr-md border border-border bg-muted/50 px-4 py-2.5">
          <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Your answers</p>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            {Object.entries(item.answers).map(([k, v]) => (
              <div key={k} className="contents"><dt className="text-muted-foreground">{k}</dt>
                <dd className="font-medium">{Array.isArray(v) ? v.join(", ") : String(v ?? "")}</dd></div>
            ))}
          </dl>
        </div>
      </div>
    );
  }
  if (item.kind !== "USER") return null;
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] space-y-2">
        {item.revise && <p className="flex items-center justify-end gap-1 text-[11px] text-muted-foreground"><PenLine className="h-3 w-3" /> Revising {item.revise.title}</p>}
        {(item.jira_keys?.length || item.attachments?.length) ? (
          <div className="flex flex-wrap justify-end gap-1.5">
            {item.jira_keys?.map((k) => <span key={k} className="inline-flex items-center gap-1 rounded-md bg-sky-500/10 px-1.5 py-0.5 font-mono text-[11px] text-sky-700 dark:text-sky-300"><Ticket className="h-3 w-3" />{k}</span>)}
            {item.attachments?.map((a, i) => <span key={`${a.file_name}-${i}`} className="inline-flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 text-[11px]"><FileText className="h-3 w-3" />{a.file_name}</span>)}
          </div>
        ) : null}
        {item.text && <div className="whitespace-pre-wrap rounded-2xl rounded-tr-md bg-primary px-4 py-2.5 text-sm text-primary-foreground shadow-sm">{item.text}</div>}
      </div>
    </div>
  );
}

function AgentTurn({ items, live, byId, onAnswer, onReview, onRevise, lastAskId }: {
  items: ChatItem[]; live: boolean; byId: Record<string, Change>; lastAskId: string | null;
  onAnswer: (a: Record<string, unknown>, shown: Record<string, unknown>) => void; onReview: (id?: string) => void; onRevise: (c: Change) => void;
}) {
  const activity = items.filter((i) => i.kind === "THOUGHT" || i.kind === "TOOL");
  const rest = items.filter((i) => i.kind !== "THOUGHT" && i.kind !== "TOOL");
  return (
    <div className="flex gap-3">
      <AgentMark className="mt-0.5" />
      <div className="min-w-0 flex-1 space-y-3">
        {(activity.length > 0 || live) && <Activity items={activity} live={live} />}
        {rest.map((item) => {
          switch (item.kind) {
            case "CHANGE": {
              const c = item.change_id ? byId[item.change_id] : undefined;
              return <ChangeCard key={item.id} title={item.title} kind={item.change_kind} change={c} onReview={() => onReview(c?.change_id)} onRevise={c ? () => onRevise(c) : undefined} />;
            }
            case "ASK": return <AskCard key={item.id} question={item.question} fields={item.fields} active={item.id === lastAskId && !live} onAnswer={onAnswer} />;
            case "PUBLISH": return <PublishCard key={item.id} results={item.results} />;
            case "ERROR":
              return (
                <p key={item.id} role="alert" className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {item.text}
                </p>
              );
            case "REPLY": return <div key={item.id} className="prose-sm text-sm leading-6 text-foreground"><Markdown text={item.text} /></div>;
            default: return null;
          }
        })}
      </div>
    </div>
  );
}

/** What the agent did this turn, folded into one line; open it to see each step. */
function Activity({ items, live }: { items: ChatItem[]; live: boolean }) {
  const [open, setOpen] = useState(false);
  const tools = items.filter((i) => i.kind === "TOOL") as Extract<ChatItem, { kind: "TOOL" }>[];
  const failed = tools.filter((t) => !t.ok).length;
  const last = items[items.length - 1];
  const headline = live
    ? last ? (last.kind === "TOOL" ? (TOOL_LABEL[last.tool] ?? last.tool) : (last as { text: string }).text) : "Thinking"
    : `Worked through ${tools.length} step${tools.length === 1 ? "" : "s"}${failed ? `, ${failed} with a problem` : ""}`;
  return (
    <div className="rounded-xl border border-border/70 bg-muted/30">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-muted-foreground">
        {live ? <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" /> : <Wrench className="h-3.5 w-3.5 shrink-0" />}
        <span className={cn("min-w-0 flex-1 truncate", live && "text-foreground")}>{headline}</span>
        {items.length > 0 && (open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />)}
      </button>
      {open && items.length > 0 && (
        <ol className="space-y-1.5 border-t border-border/60 px-3 py-2.5">
          {items.map((i) => i.kind === "THOUGHT"
            ? <li key={i.id} className="flex gap-2 text-xs text-muted-foreground"><Sparkles className="mt-0.5 h-3 w-3 shrink-0" />{i.text}</li>
            : i.kind === "TOOL" ? <ToolStep key={i.id} item={i} /> : null)}
        </ol>
      )}
    </div>
  );
}

/** One step the copilot took, in words: what it looked at, and why it failed if it did (never the raw tool output). */
function ToolStep({ item }: { item: Extract<ChatItem, { kind: "TOOL" }> }) {
  return (
    <li className="flex items-start gap-2 text-xs">
      {item.ok ? <Check className="mt-0.5 h-3 w-3 shrink-0 text-success" /> : <XCircle className="mt-0.5 h-3 w-3 shrink-0 text-destructive" />}
      <span><span className="font-medium text-foreground">{TOOL_LABEL[item.tool] ?? item.tool}</span>
        {!item.ok && item.error && <span className="block text-destructive">{item.error}</span>}</span>
    </li>
  );
}

const STATUS: Record<string, { label: string; tone: string }> = {
  PROPOSED: { label: "Drafted", tone: "bg-primary/10 text-primary" },
  PUBLISHING: { label: "Publishing", tone: "bg-primary/10 text-primary" },
  APPLIED: { label: "Applied", tone: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" },
  PENDING_APPROVAL: { label: "Waiting for approval", tone: "bg-amber-500/15 text-amber-700 dark:text-amber-300" },
  FAILED: { label: "Failed", tone: "bg-destructive/10 text-destructive" },
  DISCARDED: { label: "Discarded", tone: "bg-muted text-muted-foreground" },
};

export function StatusPill({ status }: { status: string }) {
  const s = STATUS[status] ?? STATUS.PROPOSED;
  return <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", s.tone)}>{s.label}</span>;
}

function ChangeCard({ title, kind, change, onReview, onRevise }: {
  title: string; kind: Change["kind"]; change?: Change; onReview: () => void; onRevise?: () => void;
}) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-border bg-card px-3.5 py-3 shadow-xs">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary"><FileDiff className="h-4 w-4" /></span>
      <span className="min-w-0 flex-1">
        <span className="block text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{KIND_LABEL[kind] ?? kind}</span>
        <span className="block truncate text-sm font-medium">{title}</span>
      </span>
      {change && <StatusPill status={change.status} />}
      {change?.status === "PROPOSED" && onRevise && (
        <button type="button" onClick={onRevise} className="rounded-lg px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground">Revise</button>
      )}
      <button type="button" onClick={onReview} className="rounded-lg border border-border px-2.5 py-1 text-xs font-medium hover:border-primary/40 hover:text-primary">Review</button>
    </div>
  );
}

function PublishCard({ results }: { results: PublishResult[] }) {
  const icon = (s: string) => s === "APPLIED" ? <CheckCircle2 className="h-4 w-4 text-success" />
    : s === "PENDING_APPROVAL" ? <Clock3 className="h-4 w-4 text-amber-500" /> : <CircleAlert className="h-4 w-4 text-destructive" />;
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card">
      <div className="flex items-center gap-2 border-b bg-muted/40 px-3.5 py-2 text-xs font-semibold"><ShieldCheck className="h-4 w-4 text-primary" /> Published through the platform</div>
      <ul className="divide-y">
        {results.map((r) => (
          <li key={r.change_id} className="flex items-start gap-3 px-3.5 py-2.5 text-sm">
            {icon(r.status)}
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium">{r.title}</span>
              {r.detail && <span className="block text-xs text-muted-foreground">{r.detail}</span>}
            </span>
            <StatusPill status={r.status} />
          </li>
        ))}
      </ul>
      {results.some((r) => r.status === "PENDING_APPROVAL") && (
        <a href="/os/review/approvals" className="block border-t px-3.5 py-2 text-xs font-medium text-primary hover:underline">Open Approvals</a>
      )}
    </div>
  );
}

/** A question from the agent as a small form: choices as chips, defaults prefilled. Answers go back keyed by each
 *  field's name (what the agent asked for); the chat shows them by label. */
function AskCard({ question, fields, active, onAnswer }: {
  question: string; fields: FormField[]; active: boolean;
  onAnswer: (a: Record<string, unknown>, shown: Record<string, unknown>) => void;
}) {
  const uid = useId();
  const [values, setValues] = useState<Record<string, string | string[]>>(() =>
    Object.fromEntries(fields.map((f) => [f.name, f.type === "multiselect" ? (f.default ? f.default.split(",").map((x) => x.trim()).filter(Boolean) : []) : (f.default ?? "")])));
  const set = (name: string, v: string | string[]) => setValues((x) => ({ ...x, [name]: v }));
  const missing = fields.filter((f) => f.required !== false && f.type !== "confirm" &&
    (Array.isArray(values[f.name]) ? !(values[f.name] as string[]).length : !String(values[f.name] ?? "").trim()));
  const value = (f: FormField) => values[f.name] || (f.type === "confirm" ? "no" : "");
  return (
    <form aria-label="Question from the copilot"
          onSubmit={(e) => {
            e.preventDefault();
            if (!active || missing.length || !fields.length) return;
            onAnswer(Object.fromEntries(fields.map((f) => [f.name, value(f)])),
                     Object.fromEntries(fields.map((f) => [f.label || f.name, value(f)])));
          }}
          className={cn("overflow-hidden rounded-xl border bg-card shadow-xs", active ? "border-primary/40 ring-4 ring-primary/5" : "border-border opacity-80")}>
      <div className="flex items-start gap-2 border-b border-border/70 bg-primary/[0.04] px-4 py-3">
        <MessageCircleQuestion className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <p className="text-sm font-medium">{question}</p>
      </div>
      {fields.length > 0 ? (
        <div className="space-y-4 px-4 py-4">
          {fields.map((f, i) => <Field key={`${f.name}-${i}`} id={`${uid}-${i}`} field={f} value={values[f.name]} onChange={(v) => set(f.name, v)} disabled={!active} />)}
        </div>
      ) : active ? <p className="px-4 py-3 text-xs text-muted-foreground">Reply in the message box below.</p> : null}
      {active && fields.length > 0 && (
        <div className="flex items-center gap-3 border-t border-border/70 px-4 py-2.5">
          <span className="text-xs text-muted-foreground">{missing.length ? `${missing.length} to answer` : "Ready"}</span>
          <span className="ml-auto hidden text-[11px] text-muted-foreground sm:inline">or reply in your own words below</span>
          <button type="submit" disabled={!!missing.length}
                  className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-medium text-primary-foreground disabled:opacity-50">
            <Check className="h-3.5 w-3.5" /> Send answers
          </button>
        </div>
      )}
    </form>
  );
}

function Field({ id, field: f, value, onChange, disabled }: {
  id: string; field: FormField; value: string | string[] | undefined; onChange: (v: string | string[]) => void; disabled: boolean;
}) {
  const optional = f.required === false ? <span className="font-normal text-muted-foreground"> (optional)</span> : null;
  const help = f.help ? <p className="text-[11px] text-muted-foreground">{f.help}</p> : null;
  const chip = (on: boolean) => cn("rounded-lg border px-2.5 py-1 text-xs transition", on ? "border-primary bg-primary/10 font-medium text-primary" : "border-border hover:border-primary/40");
  const input = "w-full rounded-lg border border-border bg-background px-3 text-sm outline-none focus-visible:border-primary/50 focus-visible:ring-2 focus-visible:ring-primary/15 disabled:opacity-60";
  const options = (f.options ?? []).filter(Boolean);
  const multi = f.type === "multiselect";
  const chosen = multi ? (Array.isArray(value) ? value : value ? [String(value)] : []) : [String(value ?? "")];

  if ((f.type === "select" || multi) && options.length && options.length <= 10) {
    return (
      <div className="space-y-1.5">
        <p id={id} className="text-xs font-medium">{f.label}{optional}</p>
        <div role={multi ? "group" : "radiogroup"} aria-labelledby={id} className="flex flex-wrap gap-1.5">
          {options.map((o) => (
            <button key={o} type="button" disabled={disabled} role={multi ? "checkbox" : "radio"} aria-checked={chosen.includes(o)}
                    onClick={() => onChange(multi ? (chosen.includes(o) ? chosen.filter((x) => x !== o) : [...chosen, o]) : o)}
                    className={chip(chosen.includes(o))}>{o}</button>
          ))}
        </div>
        {help}
      </div>
    );
  }
  if (f.type === "confirm") {
    return (
      <div className="flex items-center gap-3">
        <span id={id} className="flex-1 text-xs font-medium">{f.label}</span>
        <div role="radiogroup" aria-labelledby={id} className="flex gap-1.5">
          {["yes", "no"].map((v) => (
            <button key={v} type="button" disabled={disabled} role="radio" aria-checked={value === v} onClick={() => onChange(v)} className={chip(value === v)}>{v}</button>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-xs font-medium">{f.label}{optional}</label>
      {multi && options.length ? (
        <select id={id} multiple disabled={disabled} value={chosen} size={Math.min(options.length, 6)}
                onChange={(e) => onChange(Array.from(e.target.selectedOptions, (o) => o.value))} className={cn(input, "py-1")}>
          {options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      ) : f.type === "select" && options.length ? (
        <select id={id} disabled={disabled} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} className={cn(input, "h-9")}>
          <option value="">Choose…</option>
          {options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      ) : f.type === "textarea" ? (
        <textarea id={id} rows={3} disabled={disabled} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} className={cn(input, "py-2")} />
      ) : (
        <input id={id} type={f.type === "number" ? "number" : "text"} disabled={disabled}
               value={Array.isArray(value) ? value.join(", ") : String(value ?? "")}
               onChange={(e) => onChange(multi ? e.target.value.split(",").map((x) => x.trim()).filter(Boolean) : e.target.value)}
               placeholder={multi ? "Separate values with commas" : undefined} className={cn(input, "h-9")} />
      )}
      {help}
    </div>
  );
}
