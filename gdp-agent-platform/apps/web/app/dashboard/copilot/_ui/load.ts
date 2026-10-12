import { api, whoami } from "@/lib/api";
import { can, type WhoAmI } from "@/lib/types";
import type { SessionRow } from "@/lib/copilot-types";

/** The conversation list and why the copilot cannot be used, if it cannot (server side). */
export async function workspaceContext(): Promise<{ sessions: SessionRow[]; disabled?: string }> {
  const [found, me] = await Promise.all([
    api<{ sessions: SessionRow[]; deployed: boolean }>("/api/copilot/sessions").catch(() => ({ sessions: [] as SessionRow[], deployed: false })),
    whoami(),
  ]);
  const allowed = !me || can(me as WhoAmI, "AI.USE");
  const disabled = !found.deployed ? "The copilot is not deployed yet (migration V048). A platform admin can apply it in Admin, Deploy."
    : !allowed ? "Using the copilot needs the AI.USE privilege. Ask a governance admin for a role that has it." : undefined;
  return { sessions: found.sessions, disabled };
}
