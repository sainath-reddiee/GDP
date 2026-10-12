import { apiRaw } from "@/lib/api";

export const dynamic = "force-dynamic";

const TYPES: Record<string, string> = { ddl: "text/plain; charset=utf-8", dbt: "application/zip" };

/** A table model version as DDL or a dbt zip, streamed with the caller's session. */
export async function GET(req: Request, { params }: { params: { tableModelId: string } }) {
  const url = new URL(req.url);
  const format = url.searchParams.get("format") ?? "ddl";
  if (!(format in TYPES)) return new Response("Unknown format", { status: 400 });
  const version = url.searchParams.get("version");
  const qs = new URLSearchParams({ format, ...(version && /^\d+$/.test(version) ? { version } : {}) });
  const res = await apiRaw(`/api/hub/table-models/${encodeURIComponent(params.tableModelId)}/export?${qs}`).catch(() => null);
  if (!res) return new Response("Cannot reach the API", { status: 503 });
  if (!res.ok) return new Response(await res.text(), { status: res.status });
  return new Response(res.body, {
    headers: { "Content-Type": TYPES[format], "Content-Disposition": res.headers.get("Content-Disposition") ?? `attachment; filename="table_model.${format}"` },
  });
}
