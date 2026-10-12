"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { ArrowRight, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { createCopilotSession } from "./actions";

export const START_KEY = (id: string) => `aip.copilot.start.${id}`;

const EXAMPLES = [
  "Add a loyalty_tier column to DIM_CUSTOMER, sourced from CRM.CUSTOMER.TIER, with a not-null check.",
  "Onboard the Salesforce Account object through SnapLogic into landing, loaded daily.",
  "Pick up GDP-123 and draft the model, mapping and quality changes it needs.",
];

/** Start a conversation: the first message is sent by the workspace as soon as it opens. */
export function NewConversation({ disabled }: { disabled?: string }) {
  const router = useRouter();
  const [text, setText] = useState("");
  const [jira, setJira] = useState("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const jiraKey = jira.trim().toUpperCase();
  const valid = text.trim().length > 3 || /^[A-Z][A-Z0-9_]*-\d+$/.test(jiraKey);

  const go = () => start(async () => {
    setError("");
    const message = text.trim() || `Pick up ${jiraKey} and draft the changes it needs.`;
    const r = await createCopilotSession({ title: message.slice(0, 120), jira_key: jiraKey || null });
    if (!r.ok) { setError(r.error); return; }
    try { sessionStorage.setItem(START_KEY(r.data.session_id), message); } catch { /* storage unavailable: the workspace asks again */ }
    router.push(`/dashboard/copilot/${r.data.session_id}`);
  });

  return (
    <div className="surface space-y-3 p-5">
      <label htmlFor="copilot-start" className="text-sm font-semibold">What do you need?</label>
      <textarea id="copilot-start" value={text} onChange={(e) => setText(e.target.value)} rows={4} disabled={!!disabled || pending}
                onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && valid) go(); }}
                placeholder="Describe the change, paste a requirement, or name a Jira issue below"
                className="w-full resize-y rounded-xl border bg-card px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-primary/30" />
      <div className="flex flex-wrap items-center gap-3">
        <label htmlFor="copilot-jira" className="text-xs text-muted-foreground">Jira issue (optional)</label>
        <input id="copilot-jira" value={jira} onChange={(e) => setJira(e.target.value)} placeholder="GDP-123" disabled={!!disabled || pending}
               className="h-8 w-32 rounded-lg border bg-card px-2 font-mono text-xs uppercase outline-none focus-visible:ring-2 focus-visible:ring-primary/30" />
        <Button className="ml-auto" disabled={!valid || !!disabled || pending} onClick={go} title={disabled}>
          {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />} Start
        </Button>
      </div>
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
      {disabled && <p className="text-xs text-muted-foreground">{disabled}</p>}
      <div className="flex flex-wrap gap-2 pt-1">
        {EXAMPLES.map((x) => (
          <button key={x} type="button" onClick={() => setText(x)} disabled={!!disabled}
                  className="rounded-full border px-3 py-1 text-left text-xs text-muted-foreground transition hover:border-primary/30 hover:text-foreground">
            {x}
          </button>
        ))}
      </div>
    </div>
  );
}
