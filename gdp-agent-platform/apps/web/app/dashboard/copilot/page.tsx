import Link from "next/link";
import { Bot, MessagesSquare } from "lucide-react";
import { api, whoami } from "@/lib/api";
import { can, type WhoAmI } from "@/lib/types";
import type { SessionRow } from "@/lib/copilot-types";
import { PageHeader } from "@/components/page-header";
import { NewConversation } from "./new-conversation";

export const dynamic = "force-dynamic";

/** The copilot workspace: start a conversation or reopen one. */
export default async function CopilotHome() {
  const [found, me] = await Promise.all([
    api<{ sessions: SessionRow[]; deployed: boolean }>("/api/copilot/sessions").catch(() => ({ sessions: [] as SessionRow[], deployed: false })),
    whoami(),
  ]);
  const allowed = !me || can(me as WhoAmI, "AI.USE");
  const disabled = !found.deployed ? "The copilot workspace is not deployed yet (migration V048). An admin can apply it in Admin, Deploy."
    : !allowed ? "Using the copilot needs the AI.USE privilege. Ask a governance admin for a role that has it." : undefined;
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Overview" title="Copilot"
                  description="Describe a change or name a Jira issue. The copilot reads the domain, asks what it cannot find, and drafts the model, mapping and quality changes for you to review. Nothing is saved until you publish it." />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <NewConversation disabled={disabled} />
        <section aria-label="Conversations" className="surface p-4">
          <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold"><MessagesSquare className="h-4 w-4" /> Your conversations</h2>
          {!found.sessions.length ? <p className="text-xs text-muted-foreground">None yet.</p> : (
            <ul className="space-y-1">
              {found.sessions.map((s) => (
                <li key={s.session_id}>
                  <Link href={`/dashboard/copilot/${s.session_id}`} className="block rounded-lg px-2 py-1.5 transition hover:bg-muted">
                    <span className="flex items-center gap-2 text-sm"><Bot className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      <span className="truncate">{s.title}</span></span>
                    <span className="block pl-5 text-[11px] text-muted-foreground">
                      {[s.jira_key, s.proposed ? `${s.proposed} drafted` : null, s.updated_at?.slice(0, 16)].filter(Boolean).join(" · ")}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
