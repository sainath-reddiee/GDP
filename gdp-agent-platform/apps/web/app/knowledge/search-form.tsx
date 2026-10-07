"use client";

import { useState, useTransition } from "react";
import { Bot, Loader2, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { answerKnowledge, searchKnowledge, type KnowledgeHit } from "./actions";

const TYPES = ["GLOSSARY", "BUSINESS_RULE", "TRANSFORMATION_RULE", "MAPPING_PATTERN", "MODEL_DEFINITION", "SODA_PATTERN"];

/** Search the knowledge (Cortex Search over active items), or ask a question answered from the hits with citations. */
export function KnowledgeSearch({ domains }: { domains: string[] }) {
  const [query, setQuery] = useState("");
  const [domain, setDomain] = useState("");
  const [type, setType] = useState("");
  const [results, setResults] = useState<KnowledgeHit[] | null>(null);
  const [answer, setAnswer] = useState<{ text: string; citations: KnowledgeHit[]; model: string | null } | null>(null);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();

  const search = () => start(async () => {
    setError(""); setAnswer(null);
    const r = await searchKnowledge(query, domain, type);
    if ("error" in r) setError(r.error); else setResults(r.results || []);
  });
  const ask = () => start(async () => {
    setError(""); setResults(null);
    const r = await answerKnowledge(query, domain, type);
    if (!r.ok) { setError(r.error); return; }
    setAnswer({ text: r.data.answer, citations: r.data.citations, model: r.data.model });
    setResults(r.data.hits);
  });

  const select = "h-9 rounded-md border bg-card px-2 text-sm";
  return (
    <div className="space-y-3">
      <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); if (query.trim()) search(); }}>
        <input value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search or ask"
               placeholder="Search, or ask a question such as: what are the synonyms for the company name?"
               className="h-9 min-w-[280px] flex-1 rounded-md border bg-card px-3 text-sm" />
        <select aria-label="Domain" className={select} value={domain} onChange={(e) => setDomain(e.target.value)}>
          <option value="">All domains</option>
          {domains.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
        <select aria-label="Type" className={select} value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">All types</option>
          {TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ").toLowerCase()}</option>)}
        </select>
        <Button type="submit" variant="outline" disabled={pending || !query.trim()}>
          {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />} Search
        </Button>
        <Button type="button" disabled={pending || query.trim().length < 3} onClick={ask}>
          <Bot className="h-4 w-4" /> Ask AI
        </Button>
      </form>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {answer && (
        <div className="rounded-lg border border-violet-200 bg-violet-50/40 p-3 text-sm dark:border-violet-900 dark:bg-violet-950/20">
          <p className="whitespace-pre-wrap">{answer.text}</p>
          {answer.citations.length > 0 && (
            <p className="mt-2 text-[11px] text-muted-foreground">
              From: {answer.citations.map((c) => `${c.DOMAIN_NAME} · ${c.TITLE}`).join("; ")}{answer.model ? ` · ${answer.model}` : ""}
            </p>
          )}
        </div>
      )}
      {results && results.length === 0 && <p className="text-sm text-muted-foreground">No active knowledge matches that.</p>}
      {results && results.length > 0 && (
        <ul className="space-y-2 text-sm">
          {results.map((r, i) => (
            <li key={`${r.SOURCE_REFERENCE ?? r.TITLE}-${i}`} className="rounded-md border p-3">
              <div className="font-medium">{r.TITLE}</div>
              <div className="text-xs text-muted-foreground">{r.DOMAIN_NAME} · {r.KNOWLEDGE_TYPE?.replace(/_/g, " ").toLowerCase()}</div>
              <p className="mt-1 line-clamp-3 text-muted-foreground">{r.CONTENT}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
