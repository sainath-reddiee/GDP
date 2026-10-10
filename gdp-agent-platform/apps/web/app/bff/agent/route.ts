import { API_URL, proxyHeaders } from "@/lib/api";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const upstream = await fetch(`${API_URL}/api/agent/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...proxyHeaders() },
    body: await req.text(),
    cache: "no-store",
  });
  return new Response(upstream.body, {
    status: upstream.status,
    headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache, no-transform" },
  });
}
