import { api } from "@/lib/api";
import type { ProfileStoreRow, RunSummary } from "@/lib/types";
import { summarize } from "@/lib/run-insights";

export const dynamic = "force-dynamic";

export type NavCounts = { running: number; review: number; drafts: number; staged: number; profiling: number };

/** Sidebar counters as a plain GET (server actions run one at a time and would queue behind page actions).
 *  Uses the fast run list and profile store, never the per-source health check. */
export async function GET() {
  try {
    const [{ runs }, store] = await Promise.all([
      api<{ runs: RunSummary[] }>("/api/runs?limit=500"),
      api<{ profiles: ProfileStoreRow[] }>("/api/profiles/store").catch(() => ({ profiles: [] as ProfileStoreRow[] })),
    ]);
    const s = summarize(runs);
    const counts: NavCounts = {
      running: s.running, review: s.review, drafts: s.drafts,
      staged: store.profiles.filter((p) => p.status !== "PROFILING" && p.status !== "FAILED").length,
      profiling: store.profiles.filter((p) => p.status === "PROFILING").length,
    };
    return Response.json(counts, { headers: { "Cache-Control": "private, max-age=30" } });
  } catch {
    return Response.json(null, { status: 503 });
  }
}
