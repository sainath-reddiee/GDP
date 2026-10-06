"use client";

import { useState, useTransition } from "react";
import { Loader2, Rocket } from "lucide-react";
import { Button } from "@/components/ui/button";
import { deployPlatform } from "./actions";

export function DeployButton() {
  const [pending, start] = useTransition();
  const [log, setLog] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  const run = () => start(async () => {
    setError("");
    setDone(false);
    setLog([]);
    const result = await deployPlatform();
    if (!result.ok) { setError(result.error); return; }
    setLog(result.data.log);
    setDone(true);
  });

  return (
    <div className="space-y-3">
      <Button onClick={run} disabled={pending}>
        {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Rocket className="h-4 w-4" />}
        {pending ? "Deploying to Snowflake… (1–3 min)" : "Deploy latest code to Snowflake"}
      </Button>
      {done && <p className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-800">Deployed. {log.length} steps applied.</p>}
      {error && <p role="alert" className="whitespace-pre-wrap rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>}
      {log.length > 0 && (
        <ol className="max-h-80 space-y-0.5 overflow-auto rounded-md border bg-muted/40 p-2 font-mono text-xs">
          {log.map((line, i) => (
            <li key={i} className={/skipped|failed/i.test(line) ? "text-amber-700" : ""}>{line}</li>
          ))}
        </ol>
      )}
    </div>
  );
}
