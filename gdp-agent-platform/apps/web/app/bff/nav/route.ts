import { api } from "@/lib/api";
import { can, type ProfileStoreRow, type RunSummary, type WhoAmI } from "@/lib/types";
import { summarize } from "@/lib/run-insights";

export const dynamic = "force-dynamic";

export type NavCounts = {
  running: number; review: number; drafts: number; staged: number; profiling: number; approvals: number;
  /** open plus acknowledged incidents; 0 without OPS.VIEW or when the incidents API is not there */
  incidents: number;
};

/** Sidebar counters as a plain GET (server actions run one at a time and would queue behind page actions).
 *  Uses the fast run list and profile store, never the per-source health check. */
export async function GET() {
  try {
    const meCall = api<WhoAmI>("/api/auth/me").catch(() => null);
    const [metrics, store, me, incidents] = await Promise.all([
      api<{ lifecycle: Record<string, number>; needs_review: number }>("/api/metrics/summary").catch(() => null),
      api<{ profiles: ProfileStoreRow[] }>("/api/profiles/store").catch(() => ({ profiles: [] as ProfileStoreRow[] })),
      meCall,
      meCall.then((who) => (who && can(who, "OPS.VIEW")
        ? api<{ open: number; ack: number }>("/api/ops/incidents/summary").then((r) => (r.open ?? 0) + (r.ack ?? 0), () => 0)
        : 0)),
    ]);
    const s = metrics
      ? { running: metrics.lifecycle.RUNNING ?? 0, review: metrics.needs_review, drafts: metrics.lifecycle.DRAFT ?? 0 }
      : summarize((await api<{ runs: RunSummary[] }>("/api/runs?limit=500")).runs);
    const counts: NavCounts = {
      running: s.running, review: s.review, drafts: s.drafts,
      staged: store.profiles.filter((p) => p.status !== "PROFILING" && p.status !== "FAILED").length,
      profiling: store.profiles.filter((p) => p.status === "PROFILING").length,
      approvals: me?.pending_for_me ?? 0,
      incidents,
    };
    return Response.json(counts, { headers: { "Cache-Control": "private, max-age=30" } });
  } catch {
    return Response.json(null, { status: 503 });
  }
}
