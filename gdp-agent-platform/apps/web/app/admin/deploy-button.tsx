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
  const [confirming, setConfirming] = useState(false);
  const [force, setForce] = useState(false);

  const run = () => start(async () => {
    setError("");
    setDone(false);
    setLog([]);
    const result = await deployPlatform(force);
    if (!result.ok) { setError(result.error); return; }
    setLog(result.data.log);
    setDone(true);
  });

  return (
    <div className="space-y-3">
      {confirming && !pending ? (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
          <span>{force ? "Re-apply every procedure, view, search service, agent and seed for everyone using this database?"
            : "Deploy applies what changed since the last deploy, for everyone using this database. Continue?"}</span>
          <Button size="sm" onClick={() => { setConfirming(false); run(); }}>Deploy now</Button>
          <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>Cancel</Button>
        </div>
      ) : (
        <Button onClick={() => setConfirming(true)} disabled={pending}>
          {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Rocket className="h-4 w-4" />}
          {pending ? (force ? "Re-applying everything… (1 to 3 min)" : "Deploying changes to Snowflake…") : "Deploy latest code to Snowflake"}
        </Button>
      )}
      <label className="flex items-center gap-2 text-xs text-muted-foreground">
        <input type="checkbox" checked={force} disabled={pending} onChange={(e) => setForce(e.target.checked)} />
        Re-apply everything (use when an object was changed or dropped in Snowflake by hand)
      </label>
      {done && <p className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300">
        Deployed. {log.filter((l) => /: applied|uploaded|refreshed|^seed/.test(l)).length} steps applied; unchanged steps were skipped.</p>}
      {error && <p role="alert" className="whitespace-pre-wrap rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>}
      {log.length > 0 && (
        <ol className="max-h-80 space-y-0.5 overflow-auto rounded-md border bg-muted/40 p-2 font-mono text-xs">
          {log.map((line, i) => (
            <li key={i} className={/unchanged/i.test(line) ? "text-muted-foreground" : /skipped|failed/i.test(line) ? "text-amber-700" : ""}>{line}</li>
          ))}
        </ol>
      )}
    </div>
  );
}
