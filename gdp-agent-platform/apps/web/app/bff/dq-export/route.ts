import { apiRaw } from "@/lib/api";

export const dynamic = "force-dynamic";

const TYPES: Record<string, string> = { soda: "application/x-yaml; charset=utf-8", gx: "application/json", portable: "application/json" };

/** A data quality suite version as SodaCL, a Great Expectations suite or the portable file (logical table names),
 *  streamed with the caller's session. */
export async function GET(req: Request) {
  const url = new URL(req.url);
  const format = url.searchParams.get("format") ?? "soda";
  const table = url.searchParams.get("table") ?? "";
  if (!(format in TYPES) || table.split(".").length !== 3) return new Response("Unknown format or table", { status: 400 });
  const version = url.searchParams.get("version");
  const qs = new URLSearchParams({ table, format, ...(version && /^\d+$/.test(version) ? { version } : {}) });
  const res = await apiRaw(`/api/dq/suites/export?${qs}`).catch(() => null);
  if (!res) return new Response("Cannot reach the API", { status: 503 });
  if (!res.ok) return new Response(await res.text(), { status: res.status });
  return new Response(res.body, {
    headers: { "Content-Type": TYPES[format], "Content-Disposition": res.headers.get("Content-Disposition") ?? `attachment; filename="checks.${format}"` },
  });
}
