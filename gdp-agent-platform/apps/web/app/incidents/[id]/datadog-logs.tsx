"use client";

import { useEffect, useState } from "react";
import { Activity, ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { Select } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { incidentDatadogLogs, type IncidentDatadogLogs } from "../../ops/datadog-actions";

const WINDOWS = [15, 30, 60, 180, 720];
const TONE: Record<string, string> = {
  error: "text-rose-300", critical: "text-rose-300", emergency: "text-rose-300", alert: "text-rose-300",
  warn: "text-amber-300", warning: "text-amber-300",
};

/** The incident's Datadog logs (Admin, Integrations, Datadog), newest first, with the same search in Datadog.
 *  Renders nothing while Datadog is off. */
export function DatadogLogs({ incidentId }: { incidentId: string }) {
  const [data, setData] = useState<IncidentDatadogLogs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [minutes, setMinutes] = useState<number | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [seq, setSeq] = useState(0);
  useEffect(() => {
    let live = true;
    setLoading(true);
    incidentDatadogLogs(incidentId, minutes).then((r) => {
      if (!live) return;
      if (r.ok) { setData(r.data); setError(null); } else { setData(null); setError(r.error); }
      setLoading(false);
    });
    return () => { live = false; };
  }, [incidentId, minutes, seq]);
  if (data && !data.enabled) return null;
  if (!data && !error) return null;   // first load: no empty box while Datadog may be off
  return (
    <section className="surface overflow-hidden">
      <header className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
        <h2 className="flex flex-1 items-center gap-1.5 text-sm font-semibold"><Activity className="h-4 w-4 text-violet-600" />Datadog logs</h2>
        <Select aria-label="Minutes before the failure" value={minutes ?? ""} onChange={(e) => setMinutes(e.target.value ? Number(e.target.value) : undefined)} className="h-7 w-auto text-xs">
          <option value="">Default window</option>
          {WINDOWS.map((m) => <option key={m} value={m}>{m < 60 ? `${m} min` : `${m / 60} h`} before</option>)}
        </Select>
        <button type="button" aria-label="Refresh" onClick={() => setSeq((n) => n + 1)} className="rounded-md p-1 text-muted-foreground hover:bg-muted">
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}</button>
        {data?.link && <a href={data.link} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-xs text-primary hover:underline">Open in Datadog<ExternalLink className="h-3 w-3" /></a>}
      </header>
      <div className="space-y-2 p-4">
        {(error || data?.error) && <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">{error ?? data?.error}</p>}
        {data?.query && <p className="text-[11px] text-muted-foreground">Search <code className="font-mono">{data.query}</code> from {data.from?.replace("T", " ").replace("Z", " UTC")} to {data.to?.replace("T", " ").replace("Z", " UTC")}</p>}
        {data && !data.error && (data.logs.length
          ? (
            <pre className="max-h-96 overflow-auto rounded-lg bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-slate-200">
              {data.logs.map((l) => (
                <div key={l.id || `${l.at}${l.message.slice(0, 40)}`} className="whitespace-pre-wrap break-all">
                  <span className="text-slate-500">{(l.at ?? "").replace("T", " ").slice(0, 23)} </span>
                  <span className={cn("uppercase", TONE[(l.status ?? "").toLowerCase()] ?? "text-sky-300")}>{l.status ?? "info"} </span>
                  {l.service && <span className="text-violet-300">{l.service} </span>}{l.message}
                </div>
              ))}
            </pre>
          )
          : <p className="text-sm text-muted-foreground">Datadog has no logs for this search in this window. Widen the window, or check the log search in Admin, Integrations, Datadog.</p>)}
      </div>
    </section>
  );
}
