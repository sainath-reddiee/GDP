"use client";

import { useAccess } from "@/components/access";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowRight, BookmarkPlus, Bot, Check, Loader2, Maximize2, MessageSquarePlus, Minimize2, Send, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { copilotAsk, copilotSuggestions, saveCopilotAnswer, type CopilotAnswer, type CopilotPage } from "@/app/copilot-actions";
import { Markdown } from "./markdown";

type Focus = { database: string; schema: string; table: string } | null;
type Message =
  | { role: "user"; content: string }
  | { role: "assistant"; content: string; data?: CopilotAnswer; error?: boolean }
  | { role: "divider"; content: string };

const CopilotCtx = createContext<{ setFocus: (f: Focus) => void; open: (question?: string) => void; shown: boolean }>({
  setFocus: () => undefined, open: () => undefined, shown: false,
});

/** Pages tell the copilot which table is in view (e.g. an open profile) and can open it with a question. */
export const useCopilot = () => useContext(CopilotCtx);

const STAGE_LABEL: Record<string, string> = {
  profile: "Profiling", domain: "Domain", mapping: "Mapping", sttm: "STTM", soda: "Data quality", qa: "QA",
  dbt: "dbt", validation: "Validation", review: "Review", source: "Source", landing: "Landing", access: "Access",
};

function describe(page: CopilotPage): string {
  if (page.table) return `${page.database}.${page.schema}.${page.table}`;
  const run = page.path.match(/^\/runs\/[0-9a-f-]{36}(?:\/([a-z]+))?/);
  if (run) return `Run · ${run[1] ? STAGE_LABEL[run[1]] ?? run[1] : "Overview"}`;
  if (page.path.startsWith("/sources")) return page.schema ? `Sources · ${page.database}.${page.schema}` : "Sources";
  return (page.path.split("/")[1] || "dashboard").replace(/^\w/, (c) => c.toUpperCase());
}

function SaveAnswer({ data, question }: { data: CopilotAnswer; question: string }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState("BUSINESS_RULE");
  const [title, setTitle] = useState(question.slice(0, 120));
  const [state, setState] = useState<"idle" | "saving" | "saved" | string>("idle");
  if (!data.domain?.id) return null;
  if (state === "saved") return <span className="flex items-center gap-1 text-[11px] text-success"><Check className="h-3 w-3" /> Saved to {data.domain.name} knowledge</span>;
  return open ? (
    <div className="mt-2 space-y-2 rounded-lg border bg-muted/30 p-2 text-xs">
      <div className="flex gap-2">
        <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Knowledge type" className="h-7 rounded border bg-card px-1">
          <option value="BUSINESS_RULE">Business rule</option>
          <option value="ONBOARDING_GUIDE">Guide / note</option>
          <option value="EXCEPTION">Known exception</option>
        </select>
        <input value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Title" className="h-7 min-w-0 flex-1 rounded border bg-card px-2" />
      </div>
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={!title.trim() || state === "saving"} onClick={async () => {
          setState("saving");
          const r = await saveCopilotAnswer({ domain_id: data.domain!.id!, knowledge_type: kind, title: title.trim(), content: data.answer.slice(0, 8000) });
          setState(r.ok ? "saved" : r.error);
        }}>{state === "saving" ? <Loader2 className="h-3 w-3 animate-spin" /> : null} Save</Button>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
        {state !== "idle" && state !== "saving" && <span className="text-destructive">{state}</span>}
      </div>
    </div>
  ) : (
    <button type="button" onClick={() => setOpen(true)} className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-primary">
      <BookmarkPlus className="h-3 w-3" /> Save to {data.domain.name} knowledge
    </button>
  );
}

function CopilotShell({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "/";
  // the query string (?db=&schema= on Sources) is read from the URL directly: useSearchParams would need a Suspense
  // boundary, and that boundary remounts the copilot (losing the conversation) whenever a server action returns
  const [query, setQuery] = useState("");
  const readQuery = useCallback(() => setQuery(typeof window === "undefined" ? "" : window.location.search), []);
  const [focus, setFocus] = useState<Focus>(null);
  const [show, setShow] = useState(false);
  const { can } = useAccess();
  const [wide, setWide] = useState(false);
  const [unread, setUnread] = useState(false);
  const showRef = useRef(show);
  showRef.current = show;
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversation, setConversation] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [starters, setStarters] = useState<string[]>([]);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const lastPage = useRef<string>("");

  useEffect(() => { readQuery(); }, [pathname, readQuery]);
  useEffect(() => { if (show) readQuery(); }, [show, readQuery]);
  const page: CopilotPage = useMemo(() => {
    const search = new URLSearchParams(query);
    const db = focus?.database ?? search.get("db") ?? undefined;
    const schema = focus?.schema ?? search.get("schema") ?? undefined;
    return { path: pathname, database: db || undefined, schema: schema || undefined, table: focus?.table };
  }, [pathname, query, focus]);
  const label = describe(page);

  useEffect(() => {
    if (!show) return;
    copilotSuggestions(page).then((r) => r.ok && setStarters(r.data.suggestions));
    if (lastPage.current && lastPage.current !== label && messages.length) {
      setMessages((m) => [...m, { role: "divider", content: `Now on ${label}` }]);
    }
    lastPage.current = label;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [show, label]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "j") { e.preventDefault(); setShow((s) => !s); }
      if (e.key === "Escape") setShow(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => { if (show) { setUnread(false); setTimeout(() => inputRef.current?.focus(), 50); } }, [show]);
  useEffect(() => { listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" }); }, [messages, busy]);

  const send = useCallback(async (text: string) => {
    const question = text.trim();
    if (!question || busy) return;
    setInput("");
    const history = messages.filter((m) => m.role !== "divider" && !("error" in m && m.error))
      .map((m) => ({ role: m.role, content: m.content })).slice(-8);
    setMessages((m) => [...m, { role: "user", content: question }]);
    setBusy(true);
    const r = await copilotAsk({ question, page, history, conversation_id: conversation });
    setBusy(false);
    if (r.ok) {
      setConversation(r.data.conversation_id);
      setMessages((m) => [...m, { role: "assistant", content: r.data.answer, data: r.data }]);
      if (!showRef.current) setUnread(true);
    } else {
      setMessages((m) => [...m, { role: "assistant", content: r.error, error: true }]);
    }
  }, [busy, messages, page, conversation]);

  const open = useCallback((question?: string) => {
    setShow(true);
    if (question) void send(question);
  }, [send]);

  return (
    <CopilotCtx.Provider value={{ setFocus, open, shown: show }}>
      {children}
      {can("AI.USE") && <button type="button" onClick={() => setShow((v) => !v)} aria-label={show ? "Close copilot" : "Open copilot"}
              title="Copilot (Ctrl+J)" aria-expanded={show}
              className={cn("fixed bottom-5 right-5 z-[71] grid h-14 w-14 place-items-center rounded-full bg-gradient-to-br from-violet-500 to-primary text-white shadow-lg ring-4 ring-background transition hover:scale-105 hover:shadow-xl",
                            show && "max-sm:hidden")}>
        {show ? <X className="h-6 w-6" /> : <Sparkles className="h-6 w-6" />}
        {unread && !show && <span className="absolute right-1 top-1 h-3 w-3 rounded-full bg-destructive ring-2 ring-background" />}
      </button>}
      {show && can("AI.USE") && (
        <aside role="dialog" aria-label="Copilot"
               className={cn("fixed z-[70] flex flex-col overflow-hidden bg-background shadow-2xl",
                             "max-sm:inset-0",
                             wide
                               ? "sm:inset-y-0 sm:right-0 sm:w-full sm:max-w-[520px] sm:border-l"
                               : "sm:bottom-24 sm:right-5 sm:h-[min(640px,calc(100vh-8rem))] sm:w-[400px] sm:origin-bottom-right sm:rounded-2xl sm:border sm:animate-[copilot-in_160ms_ease-out]")}>
          <header className="flex items-center gap-3 border-b bg-gradient-to-r from-violet-500/10 to-primary/5 px-4 py-3">
            <span className="grid h-8 w-8 place-items-center rounded-xl bg-gradient-to-br from-violet-500 to-primary text-white"><Bot className="h-4 w-4" /></span>
            <div className="min-w-0">
              <p className="text-sm font-semibold">Copilot</p>
              <p className="truncate text-[11px] text-muted-foreground" title={label}>Looking at {label}</p>
            </div>
            <button type="button" aria-label="New conversation" title="New conversation"
                    onClick={() => { setMessages([]); setConversation(null); }} className="ml-auto rounded-md p-1.5 text-muted-foreground hover:bg-muted">
              <MessageSquarePlus className="h-4 w-4" />
            </button>
            <button type="button" aria-label={wide ? "Make smaller" : "Expand"} title={wide ? "Make smaller" : "Expand"}
                    onClick={() => setWide((v) => !v)} className="rounded-md p-1.5 text-muted-foreground hover:bg-muted max-sm:hidden">
              {wide ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
            </button>
            <button type="button" aria-label="Close copilot" onClick={() => setShow(false)} className="rounded-md p-1.5 text-muted-foreground hover:bg-muted">
              <X className="h-4 w-4" />
            </button>
          </header>

          <div ref={listRef} className="flex-1 space-y-4 overflow-y-auto overscroll-contain px-4 py-4">
            {messages.length === 0 && (
              <div className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  Ask about what you are looking at. Answers use this page&apos;s run, profiles, checks and tests, and your
                  domain knowledge, and cite where each fact came from.
                </p>
                <div className="space-y-1.5">
                  {starters.map((s) => (
                    <button key={s} type="button" onClick={() => void send(s)}
                            className="flex w-full items-center gap-2 rounded-xl border px-3 py-2 text-left text-sm hover:border-primary/40 hover:bg-primary/5">
                      <Sparkles className="h-3.5 w-3.5 shrink-0 text-primary" /> {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m, i) => m.role === "divider" ? (
              <p key={i} className="flex items-center gap-2 text-[11px] text-muted-foreground"><span className="h-px flex-1 bg-border" />{m.content}<span className="h-px flex-1 bg-border" /></p>
            ) : m.role === "user" ? (
              <div key={i} className="ml-10 rounded-2xl rounded-tr-sm bg-primary px-3.5 py-2 text-sm text-primary-foreground">{m.content}</div>
            ) : (
              <div key={i} className={cn("mr-4 space-y-2 rounded-2xl rounded-tl-sm border bg-card px-3.5 py-3", m.error && "border-destructive/30 bg-destructive/5")}>
                {m.error ? <p className="text-sm text-destructive">{m.content}</p> : <Markdown text={m.content} />}
                {m.data && (
                  <>
                    {(m.data.citations.length > 0) && (
                      <div className="flex flex-wrap gap-1">
                        {m.data.citations.map((c) => {
                          const src = m.data!.sources.find((s) => s.key === c);
                          return <span key={c} title={src?.title ?? c} className="rounded-full border bg-muted/50 px-2 py-0.5 font-mono text-[10px] text-muted-foreground">{src ? `${src.type?.toLowerCase().replace("_", " ")}: ${src.title}` : c}</span>;
                        })}
                      </div>
                    )}
                    {m.data.actions.length > 0 && (
                      <div className="flex flex-wrap gap-2">
                        {m.data.actions.map((a) => (
                          <Link key={a.href + a.label} href={a.href} onClick={() => setShow(false)}
                                className="inline-flex items-center gap-1 rounded-lg border px-2.5 py-1 text-xs font-medium text-primary hover:bg-primary/5">
                            {a.label} <ArrowRight className="h-3 w-3" />
                          </Link>
                        ))}
                      </div>
                    )}
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <SaveAnswer data={m.data} question={(messages[i - 1]?.content ?? "").toString()} />
                      <span className="ml-auto text-[10px] text-muted-foreground">{m.data.model} · {(m.data.duration_ms / 1000).toFixed(1)}s</span>
                    </div>
                    {i === messages.length - 1 && m.data.follow_ups.length > 0 && (
                      <div className="space-y-1 border-t pt-2">
                        {m.data.follow_ups.map((f) => (
                          <button key={f} type="button" onClick={() => void send(f)} className="block text-left text-xs text-primary hover:underline">{f}</button>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            ))}
            {busy && (
              <div className="mr-4 flex items-center gap-2 rounded-2xl rounded-tl-sm border bg-card px-3.5 py-3 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> Reading {label} and your knowledge…
              </div>
            )}
          </div>

          <form className="border-t p-3" onSubmit={(e) => { e.preventDefault(); void send(input); }}>
            <div className="flex items-end gap-2 rounded-xl border bg-card p-2 focus-within:border-primary/50">
              <textarea ref={inputRef} value={input} onChange={(e) => setInput(e.target.value)} rows={2} aria-label="Ask the copilot"
                        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(input); } }}
                        placeholder={`Ask about ${label}…`} className="max-h-40 min-h-[40px] flex-1 resize-none bg-transparent text-sm outline-none" />
              <Button type="submit" size="sm" disabled={busy || !input.trim()} aria-label="Send"><Send className="h-3.5 w-3.5" /></Button>
            </div>
            <p className="mt-1.5 text-[10px] text-muted-foreground">Enter to send · Ctrl+J to toggle · AI answers, check before acting</p>
          </form>
        </aside>
      )}
    </CopilotCtx.Provider>
  );
}

export function CopilotProvider({ children }: { children: ReactNode }) {
  return <CopilotShell>{children}</CopilotShell>;
}
