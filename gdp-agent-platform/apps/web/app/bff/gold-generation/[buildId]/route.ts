import { apiRaw } from "@/lib/api";

export const dynamic = "force-dynamic";

/** A gold build's generated files as a zip, with the caller's session. */
export async function GET(_req: Request, { params }: { params: { buildId: string } }) {
  const res = await apiRaw(`/api/gold/builds/${encodeURIComponent(params.buildId)}/generation.zip`).catch(() => null);
  if (!res) return new Response("Cannot reach the API", { status: 503 });
  if (!res.ok) return new Response(await res.text(), { status: res.status });
  return new Response(res.body, { headers: { "Content-Type": "application/zip",
    "Content-Disposition": res.headers.get("Content-Disposition") ?? 'attachment; filename="gold.zip"' } });
}
