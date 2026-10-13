import { notFound } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type { CopilotSession } from "@/lib/copilot-types";
import { CopilotApp } from "../_ui/copilot-app";
import { workspaceContext } from "../_ui/load";

export const dynamic = "force-dynamic";

export default async function CopilotSessionPage({ params }: { params: { sessionId: string } }) {
  let data: CopilotSession;
  try {
    data = await api<CopilotSession>(`/api/copilot/sessions/${encodeURIComponent(params.sessionId)}`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }
  const { sessions, disabled } = await workspaceContext();
  return <CopilotApp key={params.sessionId} sessions={sessions} initial={data} disabled={disabled} />;
}
