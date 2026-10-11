import { api } from "@/lib/api";
import { BUILD_SHA, BUILD_TIME } from "@/lib/build";

export const dynamic = "force-dynamic";

/** Which code is running: the web build and the API's commit (npm run doctor reads this). */
export async function GET() {
  const health = await api<{ build?: string | null; database?: string }>("/api/health").catch(() => null);
  return Response.json({
    web: BUILD_SHA,
    built_at: BUILD_TIME || null,
    api: health ? health.build ?? "unknown" : null,
    api_reachable: Boolean(health),
    features: ["shell", "qa-store", "dq-store", "command-center", "asset-stores-sidebar"],
  }, { headers: { "Cache-Control": "no-store" } });
}
