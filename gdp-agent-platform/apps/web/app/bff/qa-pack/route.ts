import { apiRaw } from "@/lib/api";

export const dynamic = "force-dynamic";

/** A QA pack version as a domain/<name>/qa/<pack>.sql download (GET /api/qa-packs/{id}/file). */
export async function GET(req: Request) {
  const url = new URL(req.url);
  const pack = url.searchParams.get("pack") ?? "";
  const version = url.searchParams.get("version");
  if (!/^[0-9a-f-]{36}$/i.test(pack)) return new Response("unknown pack", { status: 400 });
  // logical=1: table names through this environment's bindings ({{silver}}.SCHEMA.TABLE), to run in any environment
  const qs = new URLSearchParams({ ...(version ? { version } : {}), ...(url.searchParams.get("logical") === "1" ? { logical: "true" } : {}) });
  const query = qs.toString();
  const res = await apiRaw(`/api/qa-packs/${pack}/file${query ? `?${query}` : ""}`);
  return new Response(await res.text(), {
    status: res.status,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Content-Disposition": res.headers.get("content-disposition") ?? 'attachment; filename="pack.sql"',
    },
  });
}
