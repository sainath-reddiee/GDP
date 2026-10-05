"use client";

import type { ModelGraph } from "@/app/onboarding/intent-types";
import { Button } from "@/components/ui/button";

function download(name: string, body: string, type: string) {
  const blob = new Blob([body], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

export function ModelEr({ graph, runName }: { graph: ModelGraph; runName?: string }) {
  const sources = graph.sources.length ? graph.sources : [{ object_name: "(no source tables yet)" }];
  const targets = graph.targets.length ? graph.targets : [{ target_table: "(no target selected)", fqn: "" }];
  const leftX = 24;
  const rightX = 420;
  const row = 86;
  const height = Math.max(sources.length, targets.length) * row + 48;
  const slug = (runName || "model-graph").replace(/\s+/g, "-");

  return (
    <div className="space-y-3">
      <svg viewBox={`0 0 700 ${height}`} className="w-full rounded-lg border bg-card" role="img" aria-label="Source to target model diagram">
        {graph.edges.map((edge, i) => {
          const from = sources.findIndex((s) => s.object_name === edge.from);
          const to = targets.findIndex((t) => t.target_table === edge.to || t.fqn === edge.to);
          if (from < 0 || to < 0) return null;
          const y1 = 40 + from * row + 22;
          const y2 = 40 + to * row + 22;
          return (
            <line
              key={`${edge.from}-${edge.to}-${i}`}
              x1={leftX + 150} y1={y1} x2={rightX} y2={y2}
              stroke={edge.kind === "mapped" ? "hsl(var(--primary))" : "hsl(var(--muted-foreground))"}
              strokeDasharray={edge.kind === "planned" ? "5 4" : undefined}
              strokeWidth="1.5"
            />
          );
        })}
        {sources.map((src, i) => (
          <g key={`s-${src.object_name}`} transform={`translate(${leftX}, ${24 + i * row})`}>
            <rect width="150" height="56" rx="6" className="fill-background stroke-border" />
            <text x="10" y="22" className="fill-foreground" fontSize="12" fontWeight="600">{src.object_name}</text>
            <text x="10" y="40" className="fill-muted-foreground" fontSize="10">source</text>
          </g>
        ))}
        {targets.map((tgt, i) => (
          <g key={`t-${tgt.fqn || tgt.target_table}`} transform={`translate(${rightX}, ${24 + i * row})`}>
            <rect width="250" height="56" rx="6" className="fill-background stroke-border" />
            <text x="10" y="22" className="fill-foreground" fontSize="12" fontWeight="600">{tgt.target_table}</text>
            <text x="10" y="40" className="fill-muted-foreground" fontSize="10">
              {tgt.domain_name ? `${tgt.domain_name} · ` : ""}{tgt.selected ? "selected" : "candidate"}
            </text>
          </g>
        ))}
      </svg>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" onClick={() => download(`${slug}-model-graph.json`, JSON.stringify(graph, null, 2), "application/json")}>
          Download model graph
        </Button>
      </div>
    </div>
  );
}
