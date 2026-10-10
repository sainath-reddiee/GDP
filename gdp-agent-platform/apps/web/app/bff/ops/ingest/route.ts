import { API_URL } from "@/lib/api";

export const dynamic = "force-dynamic";

const MAX_BYTES = 64 * 1024;

const reply = (status: number, body: unknown) => Response.json(body, { status, headers: { "Cache-Control": "no-store" } });

/** Airflow push events from the gdp_listener plugin. Public on purpose: there is no user session, the API checks the
 *  HMAC signature, timestamp and event id from the X-GDP-* headers over the exact bytes received, so the body is
 *  passed through untouched and the API can stay on a private network. */
export async function POST(req: Request) {
  const declared = Number(req.headers.get("content-length") ?? "0");
  if (Number.isFinite(declared) && declared > MAX_BYTES) return reply(413, { detail: "Event body is larger than 64 KB." });
  let body: ArrayBuffer;
  try {
    body = await req.arrayBuffer();
  } catch {
    return reply(400, { detail: "Could not read the event body." });
  }
  if (body.byteLength > MAX_BYTES) return reply(413, { detail: "Event body is larger than 64 KB." });

  const headers: Record<string, string> = { "Content-Type": req.headers.get("content-type") ?? "application/json" };
  req.headers.forEach((value, key) => {
    if (key.toLowerCase().startsWith("x-gdp-")) headers[key] = value;
  });

  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/ops/ingest`, { method: "POST", body, headers, cache: "no-store" });
  } catch {
    return reply(502, { detail: "The platform API could not be reached." });
  }
  const text = await res.text();
  let json: unknown;
  try {
    json = text ? JSON.parse(text) : {};
  } catch {
    json = { detail: text.slice(0, 500) };
  }
  return reply(res.status, json);
}
