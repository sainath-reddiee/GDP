"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Bot, Download, Loader2, MessageSquareText, RotateCcw, Send, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SuggestionsPanel } from "@/components/suggestions-panel";
import { cn } from "@/lib/utils";
import {
  askDomain, askDomainReview, decideDomainSuggestion, deleteDomain, exportPack, loadDomainReview, restoreDomain,
  type DomainAnswer,
} from "./actions";

type Tab = "ask" | "review" | null;

const KIND_LABEL: Record<string, string> = {
  GLOSSARY: "Glossary synonyms", TABLE_SIGNAL: "Table signal", COLUMN_SIGNAL: "Column signal",
  DEFINITION: "Column definition", BUSINESS_RULE: "Business rule",
};

/** Per-domain actions: ask the domain, AI review of the pack, export, delete (UI-added domains only) or restore. */
export function DomainTools({ domainId, name, deletable, reason, activeRuns, deleted }: {
  domainId: string; name: string; deletable: boolean; reason: string | null;
  activeRuns: { run_id: string; run_name: string }[]; deleted: boolean;
}) {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>(null);
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<DomainAnswer | null>(null);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();

  const download = () => start(async () => {
    setError("");
    const r = await exportPack(domainId);
    if (!r.ok) { setError(r.error); return; }
    const blob = new Blob([JSON.stringify(r.data.pack, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${name.toLowerCase()}_domain_pack.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  });

  const remove = () => start(async () => {
    setError("");
    const r = await deleteDomain(domainId, activeRuns.length > 0);
    if (!r.ok) { setError(r.error); return; }
    setConfirming(false);
    router.refresh();
  });

  const restore = () => start(async () => {
    setError("");
    const r = await restoreDomain(domainId);
    if (!r.ok) { setError(r.error); return; }
    router.refresh();
  });

  const ask = () => start(async () => {
    setError(""); setAnswer(null);
    const r = await askDomain(domainId, question);
    if (r.ok) setAnswer(r.data); else setError(r.error);
  });

  if (deleted) {
    return (
      <div className="flex flex-wrap items-center gap-2 border-t pt-3">
        <span className="text-xs text-muted-foreground">Deleted. Its targets and knowledge are inactive; past runs keep their history.</span>
        <Button size="sm" variant="outline" className="ml-auto" disabled={pending} onClick={restore}>
          {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />} Restore
        </Button>
        {error && <p role="alert" className="w-full text-xs text-destructive">{error}</p>}
      </div>
    );
  }

  return (
    <div className="space-y-3 border-t pt-3">
      <div className="flex flex-wrap items-center gap-2">
        {([["ask", "Ask the domain", MessageSquareText], ["review", "AI review of the pack", Bot]] as const).map(([value, label, Icon]) => (
          <Button key={value} size="sm" variant={tab === value ? "default" : "outline"} onClick={() => setTab(tab === value ? null : value)}>
            <Icon className="h-3.5 w-3.5" /> {label}
          </Button>
        ))}
        <Button size="sm" variant="ghost" disabled={pending} onClick={download}>
          <Download className="h-3.5 w-3.5" /> Export JSON
        </Button>
        <Button size="sm" variant="ghost" className={cn("ml-auto", deletable ? "text-destructive" : "")} disabled={!deletable}
                title={reason ?? "Delete this domain"} onClick={() => { setConfirming(true); setTyped(""); }}>
          <Trash2 className="h-3.5 w-3.5" /> Delete
        </Button>
      </div>
      {!deletable && reason && <p className="text-[11px] text-muted-foreground">{reason}</p>}

      {confirming && (
        <div className="space-y-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm">
          <p>
            Delete <span className="font-semibold">{name}</span>? Its targets and knowledge become inactive and detection stops
            offering it. Past runs keep their history, and you can restore it later.
          </p>
          {activeRuns.length > 0 && (
            <p className="text-destructive">
              {activeRuns.length} run(s) still use it: {activeRuns.slice(0, 5).map((r) => r.run_name).join(", ")}
              {activeRuns.length > 5 ? "…" : ""}. They will keep running on the deleted domain.
            </p>
          )}
          <label className="block text-xs">
            Type <span className="font-mono font-semibold">{name}</span> to confirm
            <input value={typed} onChange={(e) => setTyped(e.target.value)} aria-label="Confirm domain name"
                   className="mt-1 h-8 w-full rounded-md border bg-background px-2 text-sm" />
          </label>
          <div className="flex gap-2">
            <Button size="sm" variant="destructive" disabled={pending || typed.trim().toUpperCase() !== name.toUpperCase()} onClick={remove}>
              {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />} Delete domain
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>Cancel</Button>
          </div>
        </div>
      )}

      {tab === "ask" && (
        <div className="space-y-2">
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (question.trim().length >= 3) ask(); }}>
            <input value={question} onChange={(e) => setQuestion(e.target.value)} aria-label={`Ask about ${name}`}
                   placeholder={`For example: which columns identify a ${name.toLowerCase()} record?`}
                   className="h-9 flex-1 rounded-md border bg-background px-3 text-sm" />
            <Button size="sm" type="submit" disabled={pending || question.trim().length < 3}>
              {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />} Ask
            </Button>
          </form>
          {answer && (
            <div className="rounded-lg border bg-muted/30 p-3 text-sm">
              <p className="whitespace-pre-wrap">{answer.answer}</p>
              {answer.citations.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {answer.citations.map((c) => (
                    <span key={c.key} className="rounded-full border bg-card px-2 py-0.5 font-mono text-[11px]" title={c.kind}>
                      {c.title}
                    </span>
                  ))}
                </div>
              )}
              <p className="mt-2 text-[11px] text-muted-foreground">Answered from this domain&apos;s pack and knowledge · {answer.model}</p>
            </div>
          )}
        </div>
      )}

      {tab === "review" && (
        <SuggestionsPanel
          intro="AI reviews this pack for gaps that weaken detection and mapping. One call per pack version, reused until the pack changes."
          effect="Accepting writes the change into the domain (glossary, signals, definitions or rules); rejected suggestions are not offered again."
          summary={(item) => ({
            title: `${KIND_LABEL[String(item.kind).toUpperCase()] ?? String(item.kind)}${item.target_column ? ` · ${[item.target_table, item.target_column].filter(Boolean).join(".")}` : ""}`,
            detail: String(item.value ?? ""),
          })}
          source={{
            key: domainId,
            load: () => loadDomainReview(domainId),
            ask: (refresh) => askDomainReview(domainId, refresh),
            decide: (body) => decideDomainSuggestion(domainId, body),
          }}
        />
      )}
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
