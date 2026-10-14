import { apiRaw } from "@/lib/api";

export const dynamic = "force-dynamic";

/** An uploaded domain document's original file, streamed with the caller's session. ?inline=1 shows a PDF or an image
 *  in the browser; the API decides (anything else downloads) and its security headers are passed on. */
export async function GET(req: Request, { params }: { params: { domainId: string; sourceId: string } }) {
  const inline = new URL(req.url).searchParams.get("inline") === "1";
  const res = await apiRaw(`/api/domains/${encodeURIComponent(params.domainId)}/documents/${encodeURIComponent(params.sourceId)}/file${inline ? "?inline=true" : ""}`)
    .catch(() => null);
  if (!res) return new Response("Cannot reach the API", { status: 503 });
  if (!res.ok) return new Response(await res.text(), { status: res.status });
  const headers = new Headers();
  for (const h of ["Content-Type", "Content-Disposition", "X-Content-Type-Options", "Cache-Control", "Content-Security-Policy"]) {
    const v = res.headers.get(h);
    if (v) headers.set(h, v);
  }
  return new Response(res.body, { headers });
}
