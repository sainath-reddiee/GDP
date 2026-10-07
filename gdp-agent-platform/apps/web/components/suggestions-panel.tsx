"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Bot, Check, Loader2, RefreshCw, Sparkles, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { SuggestionItem, SuggestionResult } from "@/app/runs/[runId]/pipeline-actions";

export type SuggestionSource = {
  /** a key that changes when the subject changes (reloads earlier answers) */
  key: string;
  load: () => Promise<Result<SuggestionResult>>;
  ask: (refresh: boolean) => Promise<Result<SuggestionResult>>;
  decide: (body: { suggestion_id: string | null; scope_key: string; item: Record<string, unknown>;
                   decision: "ACCEPTED" | "REJECTED" }) => Promise<Result<{ applied?: string | null }>>;
};

type Result<T> = { ok: true; data: T } | { ok: false; error: string };

/** AI suggestions next to rule results (a run stage, a domain pack). The rules decide; a reviewer accepts or
 *  rejects each item; rejections are remembered. */
export function SuggestionsPanel({ source, summary, effect, intro, canAct = true }: {
  source: SuggestionSource;
  summary: (item: SuggestionItem) => { title: string; detail: string };
  effect: string;
  intro?: string;
  canAct?: boolean;
}) {
  const router = useRouter();
  const [data, setData] = useState<SuggestionResult | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [pending, start] = useTransition();

  useEffect(() => {
    let live = true;
    source.load().then((r) => {
      if (!live) return;
      if (r.ok) setData(r.data); else setError(r.error);
    });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source.key]);

  const ask = (refresh: boolean) => start(async () => {
    setError(""); setNotice("");
    const r = await source.ask(refresh);
    if (r.ok) setData(r.data); else setError(r.error);
  });

  const decide = (scopeKey: string, suggestionId: string | null, item: SuggestionItem, decision: "ACCEPTED" | "REJECTED") => {
    setBusy(item.item_key); setError(""); setNotice("");
    start(async () => {
      const plain = Object.fromEntries(Object.entries(item).filter(([k]) => k !== "item_key" && k !== "review"));
      const r = await source.decide({ suggestion_id: suggestionId, scope_key: scopeKey, item: plain, decision });
      setBusy(null);
      if (!r.ok) { setError(r.error); return; }
      setNotice(decision === "ACCEPTED" ? (r.data.applied ?? "Accepted") : "Rejected; it will not be suggested again for this scope.");
      setData((d) => d && {
        ...d,
        scopes: d.scopes.map((sc) => sc.scope_key !== scopeKey ? sc : {
          ...sc,
          items: decision === "REJECTED"
            ? sc.items.filter((i) => i.item_key !== item.item_key)
            : sc.items.map((i) => i.item_key === item.item_key ? { ...i, review: { decision } } : i),
        }),
      });
      router.refresh();
    });
  };

  const generated = data?.scopes.some((s) => s.generated) ?? false;
  const open = data?.scopes.flatMap((s) => s.items.filter((i) => !i.review)).length ?? 0;
  const model = data?.scopes.find((s) => s.model)?.model;
  const scopeErrors = data?.scopes.filter((s) => s.error).map((s) => s.error) ?? [];

  return (
    <section className="rounded-xl border border-violet-200 bg-violet-50/40 p-4 dark:border-violet-900 dark:bg-violet-950/20">
      <div className="flex flex-wrap items-center gap-2">
        <Bot className="h-4 w-4 text-violet-600" />
        <h4 className="text-sm font-semibold">AI review</h4>
        {generated && <Badge variant="outline">{open} open suggestion{open === 1 ? "" : "s"}</Badge>}
        {model && <span className="text-[11px] text-muted-foreground">{model}</span>}
        <div className="ml-auto flex gap-2">
          {!generated ? (
            <Button size="sm" variant="outline" disabled={pending || !canAct} onClick={() => ask(false)}>
              {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
              Ask AI to review
            </Button>
          ) : (
            <Button size="sm" variant="ghost" disabled={pending || !canAct} onClick={() => ask(true)} title="Ask again with a fresh answer">
              {pending && !busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
              Re-check
            </Button>
          )}
        </div>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        {intro ?? "The rules decide; AI suggests where it disagrees. One call per table, reused until the inputs change."} {effect}
      </p>
      {error && <p role="alert" className="mt-2 text-xs text-destructive">{error}</p>}
      {scopeErrors.map((e) => <p key={e} className="mt-2 text-xs text-warning">{e}</p>)}
      {notice && <p className="mt-2 text-xs text-success">{notice}</p>}
      {generated && open === 0 && !scopeErrors.length && (
        <p className="mt-3 text-xs text-muted-foreground">No suggestions: the AI agrees with the rule results.</p>
      )}
      <div className="mt-3 space-y-3">
        {data?.scopes.filter((sc) => sc.items.length).map((sc) => (
          <div key={sc.scope_key} className="space-y-1.5">
            {data.scopes.length > 1 && (
              <p className="font-mono text-[11px] text-muted-foreground">{sc.scope_key.split(".").slice(1).join(".")}</p>
            )}
            {sc.items.map((item) => {
              const { title, detail } = summary(item);
              return (
                <div key={item.item_key} className="flex items-start gap-3 rounded-lg border bg-card p-2.5">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{title}</p>
                    {detail && <p className="break-words font-mono text-[11px] text-muted-foreground">{detail}</p>}
                    <p className="mt-0.5 text-xs text-muted-foreground">{item.reason}</p>
                  </div>
                  {item.review?.decision === "ACCEPTED" ? (
                    <Badge variant="success"><Check className="mr-0.5 h-3 w-3" />Accepted</Badge>
                  ) : canAct && (
                    <div className="flex shrink-0 gap-1">
                      <Button size="sm" variant="outline" disabled={pending}
                              onClick={() => decide(sc.scope_key, sc.suggestion_id, item, "ACCEPTED")}>
                        {busy === item.item_key ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                        Accept
                      </Button>
                      <Button size="sm" variant="ghost" disabled={pending} aria-label="Reject"
                              onClick={() => decide(sc.scope_key, sc.suggestion_id, item, "REJECTED")}>
                        <X className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </section>
  );
}
