import { CopilotApp } from "./_ui/copilot-app";
import { workspaceContext } from "./_ui/load";

export const dynamic = "force-dynamic";

/** The copilot: ask anything, attach files or Jira issues, and review what it drafts. */
export default async function CopilotHome({ searchParams }: { searchParams?: { jira?: string; q?: string } }) {
  const { sessions, disabled } = await workspaceContext();
  const key = (searchParams?.jira ?? "").trim().toUpperCase();
  const jira = /^[A-Z][A-Z0-9_]{0,30}-\d{1,9}$/.test(key) ? key : null;   // "Work on it in the Copilot" from a ticket
  const q = (searchParams?.q ?? "").trim().slice(0, 4000) || null;   // a request prepared elsewhere, ready to send
  return <CopilotApp key={jira ? `new-${jira}` : q ? `new-q-${q.length}` : "new"} sessions={sessions} initial={null} disabled={disabled}
                     initialJira={jira} initialMessage={q} />;
}
