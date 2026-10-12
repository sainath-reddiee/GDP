import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { CopilotSession } from "@/lib/copilot-types";
import { Workspace } from "./workspace";

export const dynamic = "force-dynamic";

export default async function CopilotSessionPage({ params }: { params: { sessionId: string } }) {
  let data: CopilotSession;
  try {
    data = await api<CopilotSession>(`/api/copilot/sessions/${encodeURIComponent(params.sessionId)}`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }
  return (
    <div className="space-y-3">
      <Link href="/dashboard/copilot" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-3.5 w-3.5" /> All conversations
      </Link>
      <Workspace initial={data} />
    </div>
  );
}
