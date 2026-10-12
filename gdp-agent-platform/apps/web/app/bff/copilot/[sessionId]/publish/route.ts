import { API_URL, proxyHeaders } from "@/lib/api";

export const dynamic = "force-dynamic";

/** Approve and Publish as a server-sent event stream: each drafted change replayed through the API as the user. */
export async function POST(req: Request, { params }: { params: { sessionId: string } }) {
  const upstream = await fetch(`${API_URL}/api/copilot/sessions/${encodeURIComponent(params.sessionId)}/publish`, {
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
