"use client";

import { useState, useTransition } from "react";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";

type Hit = { TITLE?: string; KNOWLEDGE_TYPE?: string; DOMAIN_NAME?: string; CONTENT?: string };

export function KnowledgeSearch({ action }: {
  action: (query: string) => Promise<{ results: Hit[] } | { error: string }>;
}) {
  const [query, setQuery] = useState("customer identifier cust_id");
  const [results, setResults] = useState<Hit[]>([]);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  return (
    <div className="space-y-3">
      <Label htmlFor="q">Search domain knowledge</Label>
      <div className="flex gap-2">
        <Input id="q" value={query} onChange={(e) => setQuery(e.target.value)} />
        <Button disabled={pending} onClick={() => start(async () => {
          setError("");
          const result = await action(query);
          if ("error" in result) setError(result.error);
          else setResults(result.results || []);
        })}>
          {pending ? "Searching…" : "Search"}
        </Button>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <ul className="space-y-2 text-sm">
        {results.map((r, i) => (
          <li key={i} className="rounded-md border p-3">
            <div className="font-medium">{r.TITLE}</div>
            <div className="text-xs text-muted-foreground">{r.DOMAIN_NAME} · {r.KNOWLEDGE_TYPE}</div>
            <p className="mt-1 text-muted-foreground">{r.CONTENT}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}
