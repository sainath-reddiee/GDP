"use client";

import { useState } from "react";
import { Bot } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/input";

type Line = { kind: "user" | "text" | "status" | "tool" | "error"; text: string };

function parseBlock(block: string): { event: string; data: any } | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  if (!data.length) return null;
  try {
    return { event, data: JSON.parse(data.join("\n")) };
  } catch {
    return { event, data: data.join("\n") };
  }
}

/** Chat with the supervisor agent. It can propose; approvals still go through the review form. */
export function AgentPanel({ runId }: { runId: string }) {
  const [lines, setLines] = useState<Line[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const append = (line: Line) =>
    setLines((prev) => {
      const last = prev[prev.length - 1];
      if (line.kind === "text" && last?.kind === "text") return [...prev.slice(0, -1), { kind: "text", text: last.text + line.text }];
      return [...prev, line];
    });

  async function send() {
    if (!message.trim()) return;
    setBusy(true);
    append({ kind: "user", text: message });
    setMessage("");
    try {
      const res = await fetch("/bff/agent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: runId, message }),
      });
      if (!res.ok || !res.body) throw new Error(await res.text());
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() ?? "";
        for (const block of blocks) {
          const parsed = parseBlock(block);
          if (!parsed) continue;
          const { event, data } = parsed;
          if (event === "response.text.delta") append({ kind: "text", text: data.text ?? "" });
          else if (event === "response.status") append({ kind: "status", text: data.message ?? data.status });
          else if (event === "response.tool_use") append({ kind: "tool", text: `Calling ${data.name ?? data.type ?? "tool"}` });
          else if (event === "error") append({ kind: "error", text: data.message ?? JSON.stringify(data) });
        }
      }
    } catch (e) {
      append({ kind: "error", text: e instanceof Error ? e.message : "request failed" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Bot className="h-4 w-4" /> Engineering agent</CardTitle>
        <CardDescription>Ask about this run. The agent proposes artifacts; it cannot approve gates.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="mb-3 max-h-72 space-y-2 overflow-auto text-sm" data-testid="agent-log">
          {lines.map((l, i) => (
            <div
              key={i}
              className={
                l.kind === "user" ? "font-medium" :
                l.kind === "error" ? "text-destructive" :
                l.kind === "text" ? "whitespace-pre-wrap" : "text-muted-foreground"
              }
            >
              {l.kind === "user" ? `You: ${l.text}` : l.text}
            </div>
          ))}
        </div>
        <Textarea value={message} onChange={(e) => setMessage(e.target.value)} rows={2} placeholder="e.g. Which source tables are selected?" />
        <Button className="mt-2" onClick={send} disabled={busy}>{busy ? "Waiting…" : "Send"}</Button>
      </CardContent>
    </Card>
  );
}
