import { CopilotApp } from "./_ui/copilot-app";
import { workspaceContext } from "./_ui/load";

export const dynamic = "force-dynamic";

/** The copilot: ask anything, attach files or Jira issues, and review what it drafts. */
export default async function CopilotHome({ searchParams }: { searchParams?: { jira?: string } }) {
  const { sessions, disabled } = await workspaceContext();
  const key = (searchParams?.jira ?? "").trim().toUpperCase();
  const jira = /^[A-Z][A-Z0-9_]{0,30}-\d{1,9}$/.test(key) ? key : null;   // "Work on it in the Copilot" from a ticket
  return <CopilotApp key={jira ? `new-${jira}` : "new"} sessions={sessions} initial={null} disabled={disabled} initialJira={jira} />;
}
