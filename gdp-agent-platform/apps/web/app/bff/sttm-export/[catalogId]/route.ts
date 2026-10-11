import { apiRaw } from "@/lib/api";

export const dynamic = "force-dynamic";

/** A catalog mapping version as a dbt project zip (or the lines as CSV with `format=csv`), streamed through
 *  with the caller's session. */
export async function GET(req: Request, { params }: { params: { catalogId: string } }) {
  const url = new URL(req.url);
  const version = url.searchParams.get("version");
  const csv = url.searchParams.get("format") === "csv";
  const qs = version && /^\d+$/.test(version) ? `?version=${version}` : "";
  const res = await apiRaw(`/api/hub/sttm/${encodeURIComponent(params.catalogId)}/${csv ? "export.csv" : "export"}${qs}`).catch(() => null);
  if (!res) return new Response("Cannot reach the API", { status: 503 });
  if (!res.ok) return new Response(await res.text(), { status: res.status });
  return new Response(res.body, {
    headers: {
      "Content-Type": csv ? "text/csv; charset=utf-8" : "application/zip",
      "Content-Disposition": res.headers.get("Content-Disposition") ?? (csv ? 'attachment; filename="sttm.csv"' : 'attachment; filename="sttm.zip"'),
    },
  });
}
