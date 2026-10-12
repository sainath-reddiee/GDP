import { CopilotApp } from "./_ui/copilot-app";
import { workspaceContext } from "./_ui/load";

export const dynamic = "force-dynamic";

/** The copilot: ask anything, attach files or Jira issues, and review what it drafts. */
export default async function CopilotHome() {
  const { sessions, disabled } = await workspaceContext();
  return <CopilotApp key="new" sessions={sessions} initial={null} disabled={disabled} />;
}
