import { apiRaw } from "@/lib/api";

export const dynamic = "force-dynamic";

/** The run's Soda CLI kit as a zip, streamed through with the caller's session. */
export async function GET(_req: Request, { params }: { params: { runId: string } }) {
  const res = await apiRaw(`/api/runs/${encodeURIComponent(params.runId)}/soda/kit.zip`).catch(() => null);
  if (!res) return new Response("Cannot reach the API", { status: 503 });
  if (!res.ok) return new Response(await res.text(), { status: res.status });
  return new Response(res.body, {
    headers: {
      "Content-Type": "application/zip",
      "Content-Disposition": res.headers.get("Content-Disposition") ?? 'attachment; filename="soda-kit.zip"',
    },
  });
}
