"use client";

import { useEffect, useState } from "react";
import { Gauge } from "lucide-react";
import { runAccelerator, type Accelerator } from "@/app/runs/[runId]/autopilot-actions";

/** What the platform did for this run: mapping proposals accepted as generated, time spent in automatic steps, and
 *  the human gates passed first time. */
export function AcceleratorCard({ runId }: { runId: string }) {
  const [data, setData] = useState<Accelerator | null>(null);
  useEffect(() => { void runAccelerator(runId).then((r) => r.ok && setData(r.data)); }, [runId]);
  if (!data || (!data.mapping.decided && !data.stages.length)) return null;
  const gates = data.gates.filter((g) => g.passed);
  const first = gates.filter((g) => g.first_time_right).length;
  const human = data.stages.filter((s) => s.human_gate).reduce((n, s) => n + (s.minutes ?? 0), 0);
  const tiles: [string, string][] = [
    ["Mappings accepted as generated", data.mapping.generated_share != null
      ? `${data.mapping.generated_share}% (${data.mapping.accepted} of ${data.mapping.decided})` : "not mapped yet"],
    ["Automatic steps", `${data.automatic_steps} in ${data.automatic_minutes} min`],
    ["Time at reviews", `${Math.round(human)} min`],
    ["Gates first time right", gates.length ? `${first} of ${gates.length}` : "none passed yet"],
  ];
  return (
    <section className="surface p-4" aria-label="Accelerator">
      <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold"><Gauge className="h-4 w-4 text-primary" />What the platform did for this run</h3>
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {tiles.map(([k, v]) => (
          <div key={k} className="rounded-lg border px-3 py-2">
            <dt className="text-[11px] text-muted-foreground">{k}</dt>
            <dd className="text-sm font-semibold tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
