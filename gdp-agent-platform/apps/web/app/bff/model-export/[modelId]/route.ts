import { apiRaw } from "@/lib/api";

export const dynamic = "force-dynamic";

const TYPES: Record<string, string> = { ddl: "text/plain; charset=utf-8", csv: "text/csv; charset=utf-8", pack: "application/json", dbt: "application/zip" };

/** A domain model version as DDL, domain_pack.json, CSV data dictionary or dbt zip, streamed with the caller's session. */
export async function GET(req: Request, { params }: { params: { modelId: string } }) {
  const url = new URL(req.url);
  const format = url.searchParams.get("format") ?? "ddl";
  if (!(format in TYPES)) return new Response("Unknown format", { status: 400 });
  const version = url.searchParams.get("version");
  const qs = new URLSearchParams({ format, ...(version && /^\d+$/.test(version) ? { version } : {}) });
  const res = await apiRaw(`/api/hub/domain-models/${encodeURIComponent(params.modelId)}/export?${qs}`).catch(() => null);
  if (!res) return new Response("Cannot reach the API", { status: 503 });
  if (!res.ok) return new Response(await res.text(), { status: res.status });
  return new Response(res.body, {
    headers: { "Content-Type": TYPES[format], "Content-Disposition": res.headers.get("Content-Disposition") ?? `attachment; filename="model.${format}"` },
  });
}
