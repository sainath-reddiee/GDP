"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Check, CircleCheck, CircleDashed, ExternalLink, Link2, Loader2, LogOut, Unplug } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { CopyButton } from "../runs/[runId]/dbt/studio-ui";
import { connectJira, disconnectJira, saveJiraConfig, type JiraStatus } from "../jira/actions";

type Msg = { tone: "ok" | "info" | "error"; text: string } | null;
export const SCOPES = "read:jira-work write:jira-work read:jira-user offline_access";

function Item({ done, title, children }: { done: boolean; title: string; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      {done ? <CircleCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" /> : <CircleDashed className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />}
      <div className="min-w-0 flex-1 space-y-1.5"><p className="text-sm font-medium">{title}</p>{children}</div>
    </li>
  );
}

/** Admin, Integrations, Jira: the one-time setup an admin does, and each engineer's own connection. */
export function JiraTab({ status, may, onMsg }: { status: JiraStatus | null; may: boolean; onMsg: (m: Msg) => void }) {
  const router = useRouter();
  const params = useSearchParams();
  const [pending, start] = useTransition();
  const [site, setSite] = useState(status?.site_url ?? "");
  const [clientId, setClientId] = useState("");
  const [redirect, setRedirect] = useState(status?.redirect_uri ?? "");
  const [project, setProject] = useState(status?.default_project ?? "");
  useEffect(() => {
    const flag = params.get("jira");
    if (flag === "connected") onMsg({ tone: "ok", text: "Your Jira account is connected." });
    else if (flag) onMsg({ tone: "error", text: `Jira sign-in did not finish: ${params.get("reason") ?? flag}` });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (!status?.installed) {
    return <section className="surface p-5 text-sm text-muted-foreground">Jira needs the latest deploy (migration V027).</section>;
  }
  const save = () => start(async () => {
    const r = await saveJiraConfig({ site_url: site.trim(), redirect_uri: redirect.trim(), default_project: project.trim(), ...(clientId.trim() ? { client_id: clientId.trim() } : {}) });
    onMsg(r.ok ? { tone: "ok", text: "Jira settings saved." } : { tone: /approval|request/i.test(r.error) ? "info" : "error", text: r.error });
    if (r.ok) { setClientId(""); router.refresh(); }
  });
  const connect = () => start(async () => {
    const r = await connectJira("/admin?section=integrations&view=jira");
    if (r.ok) window.location.href = r.data.url;
    else onMsg({ tone: "error", text: r.error });
  });
  const disconnect = () => start(async () => {
    const r = await disconnectJira();
    onMsg(r.ok ? { tone: "ok", text: "Your Jira account is disconnected. You can also remove the app in your Atlassian account settings." } : { tone: "error", text: r.error });
    if (r.ok) router.refresh();
  });
  const me = status.connected;
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
      <section className="surface overflow-hidden">
        <header className="flex items-start gap-3 border-b px-5 py-4">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-sky-50 text-sky-600 ring-1 ring-inset ring-sky-100"><Unplug className="h-5 w-5" /></span>
          <div className="min-w-0 flex-1">
            <h3 className="flex items-center gap-2 text-base font-semibold">Jira Cloud
              <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ring-inset", status.ready ? "bg-emerald-50 text-emerald-700 ring-emerald-100" : "bg-slate-100 text-slate-600 ring-slate-200")}>
                {status.ready ? "ready" : "setup needed"}</span></h3>
            <p className="text-xs text-muted-foreground">Set up once here. Each engineer then connects their own Atlassian account, so issues, comments and status changes are made as them.
              {status.users_connected ? ` ${status.users_connected} engineer${status.users_connected === 1 ? " has" : "s have"} connected.` : ""}</p>
          </div>
        </header>
        <ol className="space-y-5 px-5 py-4">
          <Item done={!!status.client_id} title="1. Register an OAuth 2.0 (3LO) app">
            <p className="text-xs text-muted-foreground">A Jira site admin creates it at <a href="https://developer.atlassian.com/console/myapps/" target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-primary hover:underline">developer.atlassian.com<ExternalLink className="h-3 w-3" /></a>:
              Permissions, Jira API, add these scopes; Authorization, add the callback URL below. Then paste the app&apos;s client ID.</p>
            <p className="flex flex-wrap items-center gap-1.5 text-[11px]"><span className="text-muted-foreground">Scopes</span><code className="rounded bg-muted px-1.5 py-0.5 font-mono">{SCOPES}</code><CopyButton text={SCOPES} /></p>
            <label className="block space-y-1 text-xs font-medium">Client ID {status.client_id && <span className="font-normal text-emerald-700">(saved)</span>}
              <Input value={clientId} onChange={(e) => setClientId(e.target.value)} disabled={!may} placeholder={status.client_id ? "Leave empty to keep the saved one" : "From the app's Settings page"} className="h-9 font-mono text-xs" /></label>
          </Item>
          <Item done={!!status.redirect_uri} title="2. Callback URL">
            <p className="text-xs text-muted-foreground">Where Atlassian sends engineers back after they approve. It must match the app&apos;s callback exactly: this web app&apos;s address and /bff/jira/callback.</p>
            <div className="flex items-center gap-1.5"><Input value={redirect} onChange={(e) => setRedirect(e.target.value)} disabled={!may} className="h-9 font-mono text-xs" aria-label="Callback URL" /><CopyButton text={redirect} /></div>
          </Item>
          <Item done={!!status.client_secret && !!status.token_key} title="3. Secrets on the API host">
            <p className="text-xs text-muted-foreground">Kept out of the database and the browser. Set them where the API runs, then restart it:</p>
            <ul className="space-y-1 text-xs">
              <li className="flex items-center gap-2">{status.client_secret ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <CircleDashed className="h-3.5 w-3.5 text-muted-foreground" />}
                <code className="font-mono">JIRA_CLIENT_SECRET</code><span className="text-muted-foreground">the app&apos;s secret</span></li>
              <li className="flex items-center gap-2">{status.token_key ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <CircleDashed className="h-3.5 w-3.5 text-muted-foreground" />}
                <code className="font-mono">JIRA_TOKEN_KEY</code><span className="text-muted-foreground">a long random value that encrypts stored sign-ins</span></li>
            </ul>
          </Item>
          <Item done={!!status.site_url} title="4. Site and default project">
            <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_10rem]">
              <Input value={site} onChange={(e) => setSite(e.target.value)} disabled={!may} placeholder="https://yourteam.atlassian.net" className="h-9 font-mono text-xs" aria-label="Site URL" />
              <Input value={project} onChange={(e) => setProject(e.target.value.toUpperCase())} disabled={!may} placeholder="Project, e.g. QA" className="h-9 font-mono text-xs" aria-label="Default project" />
            </div>
            <p className="text-[11px] text-muted-foreground">Optional. With a site set, only that site is used even when an account can see several.</p>
          </Item>
        </ol>
        {may && (
          <footer className="flex items-center gap-2 border-t bg-muted/30 px-5 py-3">
            {!!status.missing?.length && <p className="text-xs text-muted-foreground">Still needed: {status.missing.length}</p>}
            <Button className="ml-auto" disabled={pending} onClick={save}>{pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}Save settings</Button>
          </footer>
        )}
      </section>

      <section className="surface h-fit space-y-3 p-5">
        <h3 className="text-sm font-semibold">Your Jira account</h3>
        {me ? (
          <>
            <p className="flex items-center gap-2 text-sm"><CircleCheck className="h-4 w-4 text-emerald-600" />Connected as <span className="font-medium">{me.display_name ?? me.account_id}</span></p>
            <p className="text-xs text-muted-foreground">{me.site_url} · since {new Date(me.connected_at).toLocaleDateString()}</p>
            <Button variant="outline" size="sm" disabled={pending} onClick={disconnect}><LogOut className="h-3.5 w-3.5" />Disconnect</Button>
          </>
        ) : (
          <>
            <p className="text-xs text-muted-foreground">Connect to see your issues in the QA workbench, triage them against a run and post results back. You approve the access in Atlassian; the platform never sees your password.</p>
            <Button disabled={pending || !status.ready} onClick={connect}>{pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}Connect Jira</Button>
            {!status.ready && <p className="text-[11px] text-muted-foreground">Available once the setup on the left is complete.</p>}
          </>
        )}
      </section>
    </div>
  );
}
