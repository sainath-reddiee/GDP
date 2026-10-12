import { API_URL, proxyHeaders } from "@/lib/api";

export const dynamic = "force-dynamic";

/** One copilot turn as a server-sent event stream (server actions cannot stream). */
export async function POST(req: Request, { params }: { params: { sessionId: string } }) {
  const upstream = await fetch(`${API_URL}/api/copilot/sessions/${encodeURIComponent(params.sessionId)}/turn`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...proxyHeaders() },
    body: await req.text(),
    cache: "no-store",
  });
  return new Response(upstream.body, {
    status: upstream.status,
    headers: { "Content-Type": upstream.headers.get("content-type") ?? "text/event-stream", "Cache-Control": "no-cache, no-transform" },
  });
}
