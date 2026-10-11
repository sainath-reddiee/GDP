import { apiRaw } from "@/lib/api";

export const dynamic = "force-dynamic";

/** A catalog mapping version as a dbt project zip, streamed through with the caller's session. */
export async function GET(req: Request, { params }: { params: { catalogId: string } }) {
  const version = new URL(req.url).searchParams.get("version");
  const qs = version && /^\d+$/.test(version) ? `?version=${version}` : "";
  const res = await apiRaw(`/api/hub/sttm/${encodeURIComponent(params.catalogId)}/export${qs}`).catch(() => null);
  if (!res) return new Response("Cannot reach the API", { status: 503 });
  if (!res.ok) return new Response(await res.text(), { status: res.status });
  return new Response(res.body, {
    headers: {
      "Content-Type": "application/zip",
      "Content-Disposition": res.headers.get("Content-Disposition") ?? 'attachment; filename="sttm.zip"',
    },
  });
}
