import { apiRaw } from "@/lib/api";

export const dynamic = "force-dynamic";

/** A QA pack version as a domain/<name>/qa/<pack>.sql download (GET /api/qa-packs/{id}/file). */
export async function GET(req: Request) {
  const url = new URL(req.url);
  const pack = url.searchParams.get("pack") ?? "";
  const version = url.searchParams.get("version");
  if (!/^[0-9a-f-]{36}$/i.test(pack)) return new Response("unknown pack", { status: 400 });
  const res = await apiRaw(`/api/qa-packs/${pack}/file${version ? `?version=${encodeURIComponent(version)}` : ""}`);
  return new Response(await res.text(), {
    status: res.status,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Content-Disposition": res.headers.get("content-disposition") ?? 'attachment; filename="pack.sql"',
    },
  });
}
