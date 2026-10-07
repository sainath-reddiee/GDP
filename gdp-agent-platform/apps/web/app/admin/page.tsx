import { api, whoami } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import type { PlatformState, RulesState } from "./actions";
import { DeployButton } from "./deploy-button";
import { SettingsPanel } from "./settings-panel";

type State = { state: string; stage: string | null; kind: string; ordinal: number; phase: number; enabled: boolean; graph_version: string };
type Transition = { from_state: string; to_state: string; actor: string; enabled: boolean };

export default async function Admin() {
  const [me, graph, rules, platform] = await Promise.all([
    whoami(),
    api<{ states: State[]; transitions: Transition[] }>("/api/admin/workflow"),
    api<RulesState>("/api/config/rules").catch(() => null),
    api<PlatformState>("/api/config/platform").catch(() => null),
  ]);
  const outgoing = (s: string) => graph.transitions.filter((t) => t.from_state === s && t.enabled);
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Platform" title="Admin"
                  description="Session, platform settings, Snowflake deployment and the workflow graph that governs every run." />
      <Card>
        <CardHeader>
          <CardTitle>Session</CardTitle>
          <CardDescription>
            Signed in as {me?.user} with role {me?.role}. Auth mode {me?.auth_mode}. Agent {me?.agent ?? "not deployed"}.
          </CardDescription>
        </CardHeader>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Platform settings</CardTitle>
          <CardDescription>
            Rules, thresholds and name hints, the AI model and cost rates, catalog display lists and modeling standards.
            Changes are versioned and survive deploys.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {rules && platform
            ? <SettingsPanel rules={rules} platform={platform} />
            : <p className="text-sm text-muted-foreground">Settings are not available from the API yet. Restart the API after pulling.</p>}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Deploy to Snowflake</CardTitle>
          <CardDescription>
            Uploads the current services code and applies new migrations, procedures, the workflow graph, skills,
            search services and the agent, using your signed-in role ({me?.role}). Run this after pulling or changing code;
            it&apos;s safe to run again because migrations that were already applied are skipped.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <DeployButton />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Workflow graph {graph.states[0]?.graph_version}</CardTitle>
          <CardDescription>
            {graph.states.length} states, {graph.transitions.length} transitions. Disabled states belong to later phases and cannot be reached.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <THead><TR><TH>State</TH><TH>Stage</TH><TH>Kind</TH><TH>Phase</TH><TH>Next states</TH></TR></THead>
            <TBody>
              {graph.states.map((s) => (
                <TR key={s.state} className={s.enabled ? "" : "opacity-50"}>
                  <TD className="font-mono text-xs">{s.state}</TD>
                  <TD>{s.stage ?? "-"}</TD>
                  <TD>{s.kind}</TD>
                  <TD>{s.enabled ? s.phase : <Badge variant="secondary">disabled</Badge>}</TD>
                  <TD className="text-xs">
                    {outgoing(s.state).map((t) => `${t.to_state}${t.actor === "HUMAN" ? " (human)" : ""}`).join(", ")}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
