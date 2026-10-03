import { API_URL, sessionHeaderValue } from "@/lib/api";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const session = sessionHeaderValue();
  const upstream = await fetch(`${API_URL}/api/agent/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(session ? { "X-AIP-Session": session } : {}) },
    body: await req.text(),
    cache: "no-store",
  });
  return new Response(upstream.body, {
    status: upstream.status,
    headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache, no-transform" },
  });
}
