"use server";

import { api, attemptValue } from "@/lib/api";

export type LineageLayer = "SOURCE" | "BRONZE" | "SILVER_PRE_MDM" | "MDM" | "SILVER_POST_MDM" | "GOLD";
export type LineageNode = { object: string; layer: LineageLayer; columns: string[] };
export type LineageEdge = { from_layer: LineageLayer; from_object: string; from_column: string | null; to_layer: LineageLayer;
  to_object: string; to_column: string | null; kind: "INGEST" | "MAP" | "MDM_MATCH" | "MDM_GOLDEN" | "BUILD" | "RESOLVE";
  expression: string | null; producer: string };
export type LineageTrace = { start: { object: string; column: string | null }; nodes: LineageNode[]; edges: LineageEdge[]; layers: LineageLayer[] };

export async function lineageObjects(q: string) {
  return attemptValue(() => api<{ objects: LineageNode[]; total: number }>(`/api/lineage/objects?q=${encodeURIComponent(q)}`));
}
export async function lineageTrace(object: string, column: string | null, direction: "up" | "down" | "both") {
  const qs = new URLSearchParams({ object, direction, ...(column ? { column } : {}) });
  return attemptValue(() => api<LineageTrace>(`/api/lineage/trace?${qs}`));
}
