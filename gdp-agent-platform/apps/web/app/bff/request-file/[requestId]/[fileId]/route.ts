import { apiRaw } from "@/lib/api";

export const dynamic = "force-dynamic";

/** A file attached to an onboarding request, streamed with the caller's session (always a download). */
export async function GET(_req: Request, { params }: { params: { requestId: string; fileId: string } }) {
  const res = await apiRaw(`/api/onboarding-requests/${encodeURIComponent(params.requestId)}/files/${encodeURIComponent(params.fileId)}`)
    .catch(() => null);
  if (!res) return new Response("Cannot reach the API", { status: 503 });
  if (!res.ok) return new Response(await res.text(), { status: res.status });
  const headers = new Headers();
  for (const h of ["Content-Type", "Content-Disposition", "X-Content-Type-Options", "Cache-Control"]) {
    const v = res.headers.get(h);
    if (v) headers.set(h, v);
  }
  return new Response(res.body, { headers });
}
