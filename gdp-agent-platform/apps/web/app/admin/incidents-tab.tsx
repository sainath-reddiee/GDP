"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, useTransition } from "react";
import { Check, Clock3, KeyRound, Loader2, Pencil, Plus, Send, Siren, Trash2, TriangleAlert, Users, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { AirflowEnv } from "../ops/actions";
import { When, parseTs } from "../ops/ops-shared";
import {
  createRule, createTeam, deleteRule, deleteTeam, getOpsSettings, listRouting, listTeams, removeTeamWebhook, saveOpsSettings,
  setTeamWebhook, testTeamWebhook, updateRule, updateTeam,
  type OpsSettings, type OpsTeam, type RoutingRule, type RuleInput, type TeamInput, type WebhookKind,
} from "../incidents/actions";
import { localInput, SEVERITIES } from "../incidents/incident-shared";

type Msg = { tone: "ok" | "info" | "error"; text: string } | null;
type OnMsg = (m: Msg) => void;
type Result<T> = { ok: true; data: T } | { ok: false; error: string };
const toneOf = (e: string): "info" | "error" => (/approval|request/i.test(e) ? "info" : "error");
const WORKER_STALE_MS = 5 * 60 * 1000;
const WEBHOOK_LABEL: Record<WebhookKind, string> = { alerts: "Alerts", escalation: "Escalation" };

/** Load once on mount and on demand; an answer to an older request is dropped. */
function useLoad<T>(fn: () => Promise<Result<T>>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    fn().then((r) => {
      if (!live) return;
      if (r.ok) { setData(r.data); setError(""); } else setError(r.error);
    }, (e: unknown) => { if (live) setError(e instanceof Error ? e.message : "Could not load."); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick]);
  return { data, error, reload: useCallback(() => setTick((n) => n + 1), []) };
}

/** Report a failed save: a pending approval is information, anything else an error shown next to the form. */
function failed(error: string, onMsg: OnMsg, setError: (e: string) => void) {
  if (toneOf(error) === "info") onMsg({ tone: "info", text: error }); else setError(error);
}

function Chip({ tone, children }: { tone: "good" | "warn" | "idle" | "bad"; children: React.ReactNode }) {
  return (
    <span className={cn("shrink-0 whitespace-nowrap rounded-full px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset",
      tone === "good" ? "bg-emerald-50 text-emerald-700 ring-emerald-100" : tone === "warn" ? "bg-amber-50 text-amber-700 ring-amber-100"
        : tone === "bad" ? "bg-rose-50 text-rose-700 ring-rose-100" : "bg-slate-100 text-slate-600 ring-slate-200")}>{children}</span>
  );
}

const FieldError = ({ children }: { children: React.ReactNode }) => (
  <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">{children}</p>
);

/** Admin, Integrations, Incidents: teams (Jira routing and Teams channels), routing rules and incident settings. */
export function IncidentsTab({ envs, may, onMsg }: { envs: AirflowEnv[]; may: boolean; onMsg: OnMsg }) {
  const teams = useLoad(listTeams);
  const rules = useLoad(listRouting);
  const settings = useLoad(getOpsSettings);
  const teamList = teams.data?.teams ?? [];
  return (
    <div className="space-y-4">
      <SettingsCard state={settings.data} error={settings.error} may={may} onMsg={onMsg} onSaved={settings.reload} />
      <TeamsCard teams={teams.data?.teams ?? null} error={teams.error} may={may} onMsg={onMsg} reload={() => { teams.reload(); rules.reload(); }} />
      <RulesCard rules={rules.data?.rules ?? null} error={rules.error} teams={teamList} envs={envs} may={may} onMsg={onMsg} reload={rules.reload} />
    </div>
  );
}

// ---------------------------------------------------------------- settings

function SettingsCard({ state, error, may, onMsg, onSaved }: {
  state: OpsSettings | null; error: string; may: boolean; onMsg: OnMsg; onSaved: () => void;
}) {
  if (!state) {
    return (
      <section className="surface p-5 text-sm">
        {error ? <FieldError>Incident settings did not load: {error}. Incidents need the latest deploy (migration V031).</FieldError>
          : <p className="flex items-center gap-2 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading incident settings</p>}
      </section>
    );
  }
  return <SettingsForm key={JSON.stringify(state)} state={state} may={may} onMsg={onMsg} onSaved={onSaved} />;
}

function WorkerStatus({ seen }: { seen: string | null }) {
  const at = parseTs(seen);
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => { setNow(Date.now()); }, []);
  const stale = !at || (now !== null && now - at.getTime() > WORKER_STALE_MS);
  return (
    <div className={cn("rounded-lg px-3 py-2 text-xs", stale ? "bg-amber-50 text-amber-900 dark:bg-amber-950/40 dark:text-amber-100" : "bg-muted/40")}>
      <p className="flex items-center gap-1.5"><Clock3 className="h-3.5 w-3.5" />Worker last seen <When iso={seen} rel /></p>
      {stale && (
        <p className="mt-1 flex items-start gap-1.5" role="alert"><TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>No incidents, tickets or Teams cards go out without it. Start the worker: <code className="font-mono">python -m app.worker</code> in apps/api.</span></p>
      )}
    </div>
  );
}

function SettingsForm({ state, may, onMsg, onSaved }: { state: OpsSettings; may: boolean; onMsg: OnMsg; onSaved: () => void }) {
  const [reopen, setReopen] = useState(String(state.reopen_hours));
  const [retryCritical, setRetryCritical] = useState(state.alert_on_retry_for_critical);
  const [transition, setTransition] = useState(state.transition_on_resolve);
  const [done, setDone] = useState(state.done_status ?? "");
  const [rate, setRate] = useState(String(state.rate_limit_per_10min));
  const [base, setBase] = useState(state.public_base_url ?? "");
  const [aiAuto, setAiAuto] = useState(!!state.ai_auto);
  const [aiSev, setAiSev] = useState<string[]>(state.ai_severities ?? ["P1", "P2"]);
  const [digest, setDigest] = useState(!!state.weekly_digest);
  const [origin, setOrigin] = useState("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  useEffect(() => { setOrigin(window.location.origin); }, []);
  const hours = Number(reopen);
  const perTen = Number(rate);
  const badHours = !Number.isInteger(hours) || hours < 0 || hours > 720;
  const badRate = !Number.isInteger(perTen) || perTen < 1 || perTen > 100;
  const badBase = !!base.trim() && !/^https?:\/\/[^\s/]+(\/\S*)?$/i.test(base.trim());
  const badDone = transition && !done.trim();
  const badSev = aiAuto && !aiSev.length;
  const toggleSev = (s: string) => setAiSev((v) => (v.includes(s) ? v.filter((x) => x !== s) : SEVERITIES.filter((x) => x === s || v.includes(x))));
  const save = () => start(async () => {
    setError("");
    const r = await saveOpsSettings({
      reopen_hours: hours, alert_on_retry_for_critical: retryCritical, transition_on_resolve: transition,
      done_status: done.trim() || null, rate_limit_per_10min: perTen, public_base_url: base.trim().replace(/\/+$/, "") || null,
      ai_auto: aiAuto, ai_severities: aiSev, weekly_digest: digest,
    });
    if (!r.ok) { failed(r.error, onMsg, setError); return; }
    onMsg({ tone: "ok", text: "Incident settings saved." });
    onSaved();
  });
  return (
    <section className="surface overflow-hidden">
      <header className="flex flex-wrap items-center gap-3 border-b px-5 py-4">
        <span className="grid h-10 w-10 place-items-center rounded-xl bg-rose-50 text-rose-600 ring-1 ring-inset ring-rose-100"><Siren className="h-5 w-5" /></span>
        <div className="min-w-[14rem] flex-1">
          <h3 className="text-base font-semibold">Incident settings</h3>
          <p className="text-xs text-muted-foreground">How incidents reopen, alert and close. Shown in <Link href="/incidents" className="text-primary hover:underline">Incidents</Link>.</p>
        </div>
      </header>
      <div className="grid gap-4 p-5 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <fieldset disabled={!may || pending} className="grid gap-3 md:grid-cols-2">
          <label className="space-y-1 text-xs font-medium">Reopen window (hours)
            <Input value={reopen} onChange={(e) => setReopen(e.target.value)} inputMode="numeric" className="text-xs" aria-invalid={badHours} />
            <span className="block font-normal text-muted-foreground">A repeat failure within this window reopens the incident instead of opening a new one.</span>
            {badHours && <span role="alert" className="block font-normal text-destructive">A whole number from 0 to 720.</span>}
          </label>
          <label className="space-y-1 text-xs font-medium">Teams cards per team, per 10 minutes
            <Input value={rate} onChange={(e) => setRate(e.target.value)} inputMode="numeric" className="text-xs" aria-invalid={badRate} />
            <span className="block font-normal text-muted-foreground">Beyond this, one summary card is sent instead.</span>
            {badRate && <span role="alert" className="block font-normal text-destructive">A whole number from 1 to 100.</span>}
          </label>
          <label className="space-y-1 text-xs font-medium md:col-span-2">Public base URL
            <span className="flex gap-2">
              <Input value={base} onChange={(e) => setBase(e.target.value)} placeholder={origin || "https://gdp.example.com"} className="font-mono text-xs" aria-invalid={badBase} />
              {origin && base.trim() !== origin && <Button type="button" variant="outline" size="sm" className="h-9" onClick={() => setBase(origin)}>Use {origin.replace(/^https?:\/\//, "")}</Button>}
            </span>
            <span className="block font-normal text-muted-foreground">Used for the links in Teams cards and Jira tickets.</span>
            {badBase && <span role="alert" className="block font-normal text-destructive">An http:// or https:// address.</span>}
          </label>
          <label className="flex items-start gap-2 text-xs md:col-span-2">
            <input type="checkbox" className="mt-0.5" checked={retryCritical} onChange={(e) => setRetryCritical(e.target.checked)} />
            <span>Alert on &quot;up for retry&quot; for critical DAGs <span className="block text-muted-foreground">Otherwise an incident opens only when retries are exhausted.</span></span>
          </label>
          <label className="flex items-start gap-2 text-xs">
            <input type="checkbox" className="mt-0.5" checked={transition} onChange={(e) => setTransition(e.target.checked)} />
            <span>Move the Jira ticket when the incident resolves</span>
          </label>
          <label className="space-y-1 text-xs font-medium">Done status in Jira
            <Input value={done} onChange={(e) => setDone(e.target.value)} placeholder="Done" className="text-xs" disabled={!transition} aria-invalid={badDone} />
            {badDone && <span role="alert" className="block font-normal text-destructive">Name the status to move tickets to.</span>}
          </label>
          <label className="flex items-start gap-2 text-xs md:col-span-2">
            <input type="checkbox" className="mt-0.5" checked={aiAuto} onChange={(e) => setAiAuto(e.target.checked)} />
            <span>Diagnose new incidents with AI <span className="block text-muted-foreground">The worker runs the AI diagnosis when an incident opens, for the severities below. Each diagnosis is a billed model call.</span></span>
          </label>
          <div className="space-y-1 text-xs font-medium md:col-span-2" role="group" aria-label="Severities diagnosed automatically">
            <span>Severities diagnosed automatically</span>
            <span className="flex flex-wrap gap-3 font-normal">
              {SEVERITIES.map((s) => (
                <label key={s} className="flex items-center gap-1.5">
                  <input type="checkbox" checked={aiSev.includes(s)} onChange={() => toggleSev(s)} disabled={!aiAuto} />{s}
                </label>
              ))}
            </span>
            {badSev && <span role="alert" className="block font-normal text-destructive">Pick at least one severity, or turn automatic diagnosis off.</span>}
          </div>
          <label className="flex items-start gap-2 text-xs md:col-span-2">
            <input type="checkbox" className="mt-0.5" checked={digest} onChange={(e) => setDigest(e.target.checked)} />
            <span>Weekly reliability digest <span className="block text-muted-foreground">Each team with an alerts webhook gets a Teams card with its MTTR, MTTA, top failing DAGs and repeat failures.</span></span>
          </label>
          {error && <div className="md:col-span-2"><FieldError>{error}</FieldError></div>}
          {may && (
            <div className="flex justify-end md:col-span-2">
              <Button onClick={save} disabled={pending || badHours || badRate || badBase || badDone || badSev}>
                {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}Save settings</Button>
            </div>
          )}
        </fieldset>
        <div className="space-y-3 text-xs">
          <WorkerStatus seen={state.worker_seen_at} />
          <div className="space-y-1.5 rounded-lg border px-3 py-2">
            <p className="flex items-center gap-2 font-medium">Jira bot {state.jira_bot ? <Chip tone="good">configured</Chip> : <Chip tone="warn">not configured</Chip>}</p>
            {state.jira_site && <p className="text-muted-foreground">Site {state.jira_site}</p>}
            <p className="text-muted-foreground">Tickets are raised by a bot account, not by a person. Set <code className="font-mono">JIRA_BOT_EMAIL</code> and{" "}
              <code className="font-mono">JIRA_BOT_TOKEN</code> as environment variables on the worker host, then restart the worker. Tokens are never entered here.
              {!state.jira_bot && " Until then incidents still open, marked \"ticket not raised\"."}</p>
          </div>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- teams

function TeamsCard({ teams, error, may, onMsg, reload }: { teams: OpsTeam[] | null; error: string; may: boolean; onMsg: OnMsg; reload: () => void }) {
  const [editing, setEditing] = useState<string | "new" | null>(null);
  return (
    <section className="surface overflow-hidden">
      <header className="flex flex-wrap items-center gap-3 border-b px-5 py-4">
        <span className="grid h-10 w-10 place-items-center rounded-xl bg-indigo-50 text-indigo-600 ring-1 ring-inset ring-indigo-100"><Users className="h-5 w-5" /></span>
        <div className="min-w-[14rem] flex-1">
          <h3 className="text-base font-semibold">Teams</h3>
          <p className="text-xs text-muted-foreground">Who owns an incident: its Jira project and assignee, and the Microsoft Teams channels for alerts and escalation.</p>
        </div>
        {may && editing === null && teams && <Button onClick={() => setEditing("new")}><Plus className="h-4 w-4" />Add team</Button>}
      </header>
      {error && <div className="mx-5 mt-4"><FieldError>Teams did not load: {error}</FieldError></div>}
      {teams === null && !error && <p className="flex items-center gap-2 px-5 py-6 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading teams</p>}
      {editing === "new" && <TeamForm onClose={() => setEditing(null)} onMsg={onMsg} onSaved={reload} />}
      {teams && (teams.length ? (
        <ul className="divide-y">
          {teams.map((t) => editing === t.team_id
            ? <li key={t.team_id}><TeamForm team={t} onClose={() => setEditing(null)} onMsg={onMsg} onSaved={reload} /></li>
            : <TeamRow key={t.team_id} team={t} may={may} onEdit={() => setEditing(t.team_id)} onMsg={onMsg} reload={reload} />)}
        </ul>
      ) : editing !== "new" && (
        <div className="px-6 py-10 text-center">
          <p className="text-sm font-semibold">No team yet</p>
          <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground">Without a team, incidents stay unrouted: the ticket goes to the default Jira project and no Teams card is sent. Add one, then a routing rule.</p>
          {may && <Button className="mt-4" onClick={() => setEditing("new")}><Plus className="h-4 w-4" />Add team</Button>}
        </div>
      ))}
    </section>
  );
}

function TeamRow({ team: t, may, onEdit, onMsg, reload }: { team: OpsTeam; may: boolean; onEdit: () => void; onMsg: OnMsg; reload: () => void }) {
  const [confirm, setConfirm] = useState(false);
  const [pending, start] = useTransition();
  const remove = () => start(async () => {
    const r = await deleteTeam(t.team_id);
    setConfirm(false);
    if (!r.ok) { onMsg({ tone: toneOf(r.error), text: r.error }); return; }
    onMsg({ tone: "ok", text: `${t.name} is removed.` });
    reload();
  });
  return (
    <li className="space-y-3 px-5 py-4">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-[14rem] flex-1 space-y-0.5">
          <p className="text-sm font-semibold">{t.name}</p>
          <p className="text-xs text-muted-foreground">
            Jira {t.jira_project ? <span className="font-mono">{t.jira_project}</span> : "project not set"}
            {t.jira_component && <> · component {t.jira_component}</>}
            {t.jira_assignee_account_id && <> · assignee <span className="font-mono">{t.jira_assignee_account_id}</span></>}
            {" · "}escalate after {t.escalation_minutes ? `${t.escalation_minutes} min` : "never"}
          </p>
          <p className="text-xs text-muted-foreground">{t.members?.length ? `Members: ${t.members.join(", ")}` : "No members listed"}</p>
        </div>
        {may && (
          <div className="flex items-center gap-1.5">
            <Button variant="outline" size="sm" disabled={pending} onClick={onEdit}><Pencil className="h-3.5 w-3.5" />Edit</Button>
            <Button variant="ghost" size="sm" disabled={pending} onClick={() => setConfirm(true)} aria-label={`Delete ${t.name}`}><Trash2 className="h-3.5 w-3.5 text-destructive" /></Button>
          </div>
        )}
      </div>
      {confirm && (
        <div role="alertdialog" aria-label="Confirm delete" className="flex flex-wrap items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs">
          <span className="flex-1">Delete {t.name}? Its webhooks are removed. A team that routing rules still use cannot be deleted.</span>
          <Button size="sm" variant="ghost" disabled={pending} onClick={() => setConfirm(false)}>Cancel</Button>
          <Button size="sm" variant="destructive" disabled={pending} onClick={remove}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}Delete</Button>
        </div>
      )}
      {t.webhook_detail && <p role="alert" className="text-xs text-amber-700 dark:text-amber-300">{t.webhook_detail}</p>}
      <div className="grid gap-2 md:grid-cols-2">
        <Webhook team={t} kind="alerts" has={t.has_teams_webhook} may={may} onMsg={onMsg} reload={reload} />
        <Webhook team={t} kind="escalation" has={t.has_escalation_webhook} may={may} onMsg={onMsg} reload={reload} />
      </div>
    </li>
  );
}

/** A Teams Workflows webhook. The URL is write only: it is sent once, stored as a secret, and never shown again. */
function Webhook({ team, kind, has, may, onMsg, reload }: {
  team: OpsTeam; kind: WebhookKind; has: boolean; may: boolean; onMsg: OnMsg; reload: () => void;
}) {
  const [url, setUrl] = useState("");
  const [confirm, setConfirm] = useState<"test" | "remove" | null>(null);
  const [busy, setBusy] = useState<"save" | "test" | "remove" | null>(null);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [pending, start] = useTransition();
  const badUrl = !!url.trim() && !/^https:\/\/\S+$/i.test(url.trim());
  const run = (what: NonNullable<typeof busy>, fn: () => Promise<void>) => start(async () => {
    setBusy(what); setError(""); setResult(null);
    try { await fn(); } finally { setBusy(null); }
  });
  const save = () => run("save", async () => {
    const value = url.trim();
    setUrl(""); // never keep the secret in the page, whatever the answer
    const r = await setTeamWebhook(team.team_id, kind, value);
    if (!r.ok) { failed(r.error, onMsg, setError); return; }
    onMsg({ tone: "ok", text: `${WEBHOOK_LABEL[kind]} webhook of ${team.name} is saved.` });
    reload();
  });
  const remove = () => run("remove", async () => {
    const r = await removeTeamWebhook(team.team_id, kind);
    setConfirm(null);
    if (!r.ok) { failed(r.error, onMsg, setError); return; }
    onMsg({ tone: "ok", text: `${WEBHOOK_LABEL[kind]} webhook of ${team.name} is removed.` });
    reload();
  });
  const test = () => run("test", async () => {
    const r = await testTeamWebhook(team.team_id, kind);
    setConfirm(null);
    if (!r.ok) { failed(r.error, onMsg, setError); return; }
    setResult({ ok: r.data.ok, text: r.data.detail || (r.data.ok ? "Test card sent. Check the channel." : "The test card was not accepted.") });
  });
  const spin = (b: typeof busy, icon: React.ReactNode) => (busy === b ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : icon);
  return (
    <div className="space-y-2 rounded-lg border px-3 py-2 text-xs">
      <p className="flex flex-wrap items-center gap-2 font-medium">{WEBHOOK_LABEL[kind]} channel
        {has ? <Chip tone="good">webhook set</Chip> : <Chip tone="idle">no webhook</Chip>}
        {kind === "escalation" && !team.escalation_minutes && <span className="font-normal text-muted-foreground">(set escalation minutes to use it)</span>}
      </p>
      {may && (
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (url.trim() && !badUrl) save(); }}>
          <Input type="password" autoComplete="off" value={url} onChange={(e) => setUrl(e.target.value)} className="font-mono text-xs"
                 placeholder={has ? "Paste a new URL to replace it" : "Paste the Teams Workflows webhook URL"} aria-label={`${WEBHOOK_LABEL[kind]} webhook URL for ${team.name}`}
                 aria-invalid={badUrl} />
          <Button size="sm" type="submit" className="h-9" disabled={pending || !url.trim() || badUrl}>{spin("save", <KeyRound className="h-3.5 w-3.5" />)}Save</Button>
        </form>
      )}
      {badUrl && <p role="alert" className="text-destructive">An https:// address.</p>}
      {may && has && confirm === null && (
        <div className="flex gap-1.5">
          <Button size="sm" variant="outline" disabled={pending} onClick={() => setConfirm("test")}><Send className="h-3.5 w-3.5" />Send test card</Button>
          <Button size="sm" variant="ghost" disabled={pending} onClick={() => setConfirm("remove")}>Remove</Button>
        </div>
      )}
      {confirm === "test" && (
        <div role="alertdialog" aria-label="Confirm test card" className="space-y-2 rounded-lg border border-primary/30 bg-primary/5 p-2">
          <p>This posts a test card to the {kind === "alerts" ? "alerts" : "escalation"} Teams channel of {team.name}. Everyone in that channel sees it. Send it?</p>
          <div className="flex gap-1.5">
            <Button size="sm" disabled={pending} onClick={test}>{spin("test", <Send className="h-3.5 w-3.5" />)}Yes, send</Button>
            <Button size="sm" variant="ghost" disabled={pending} onClick={() => setConfirm(null)}>Cancel</Button>
          </div>
        </div>
      )}
      {confirm === "remove" && (
        <div role="alertdialog" aria-label="Confirm remove" className="flex flex-wrap items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-2">
          <span className="flex-1">Remove this webhook? No more {kind} cards go to the channel.</span>
          <Button size="sm" variant="ghost" disabled={pending} onClick={() => setConfirm(null)}>Cancel</Button>
          <Button size="sm" variant="destructive" disabled={pending} onClick={remove}>{spin("remove", null)}Remove</Button>
        </div>
      )}
      {result && (
        <p role={result.ok ? "status" : "alert"} className={cn("flex items-start gap-2 rounded px-2 py-1", result.ok ? "bg-success/10 text-success" : "bg-destructive/10 text-destructive")}>
          <span className="flex-1">{result.text}</span>
          <button type="button" aria-label="Dismiss" onClick={() => setResult(null)}><X className="h-3 w-3" /></button>
        </p>
      )}
      {error && <p role="alert" className="text-destructive">{error}</p>}
    </div>
  );
}

function TeamForm({ team, onClose, onMsg, onSaved }: { team?: OpsTeam; onClose: () => void; onMsg: OnMsg; onSaved: () => void }) {
  const [name, setName] = useState(team?.name ?? "");
  const [project, setProject] = useState(team?.jira_project ?? "");
  const [component, setComponent] = useState(team?.jira_component ?? "");
  const [assignee, setAssignee] = useState(team?.jira_assignee_account_id ?? "");
  const [minutes, setMinutes] = useState(team?.escalation_minutes ? String(team.escalation_minutes) : "");
  const [members, setMembers] = useState((team?.members ?? []).join("\n"));
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const mins = minutes.trim() ? Number(minutes) : null;
  const badMinutes = mins !== null && (!Number.isInteger(mins) || mins < 1 || mins > 1440);
  const badProject = !!project.trim() && !/^[A-Z][A-Z0-9_]{1,19}$/.test(project.trim());
  const save = () => start(async () => {
    setError("");
    const body: TeamInput = {
      name: name.trim(), jira_project: project.trim() || null, jira_component: component.trim() || null,
      jira_assignee_account_id: assignee.trim() || null, escalation_minutes: mins,
      members: Array.from(new Set(members.split(/[\n,;]+/).map((m) => m.trim()).filter(Boolean))),
    };
    const r = team ? await updateTeam(team.team_id, body) : await createTeam(body);
    if (!r.ok) { failed(r.error, onMsg, setError); return; }
    onMsg({ tone: "ok", text: team ? `${body.name} is saved.` : `${body.name} is added. Paste its Teams webhooks next.` });
    onClose();
    onSaved();
  });
  return (
    <div className="space-y-3 border-b bg-muted/20 px-5 py-4">
      <p className="text-sm font-semibold">{team ? `Edit ${team.name}` : "Add a team"}</p>
      <div className="grid gap-3 md:grid-cols-3">
        <label className="space-y-1 text-xs font-medium">Name
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Data platform" className="text-xs" /></label>
        <label className="space-y-1 text-xs font-medium">Jira project key
          <Input value={project} onChange={(e) => setProject(e.target.value.toUpperCase())} placeholder="DATA" className="font-mono text-xs" aria-invalid={badProject} />
          {badProject && <span role="alert" className="block font-normal text-destructive">An uppercase project key, such as DATA.</span>}</label>
        <label className="space-y-1 text-xs font-medium">Jira component <span className="font-normal text-muted-foreground">(optional)</span>
          <Input value={component} onChange={(e) => setComponent(e.target.value)} placeholder="Pipelines" className="text-xs" /></label>
        <label className="space-y-1 text-xs font-medium">Jira assignee account id <span className="font-normal text-muted-foreground">(optional)</span>
          <Input value={assignee} onChange={(e) => setAssignee(e.target.value)} placeholder="5b10a2844c20165700ede21g" className="font-mono text-xs" /></label>
        <label className="space-y-1 text-xs font-medium">Escalate after (minutes)
          <Input value={minutes} onChange={(e) => setMinutes(e.target.value)} inputMode="numeric" placeholder="30" className="text-xs" aria-invalid={badMinutes} />
          <span className="block font-normal text-muted-foreground">An open incident not acknowledged by then goes to the escalation channel. Empty: never.</span>
          {badMinutes && <span role="alert" className="block font-normal text-destructive">A whole number from 1 to 1440.</span>}</label>
        <label className="space-y-1 text-xs font-medium">Members
          <textarea value={members} onChange={(e) => setMembers(e.target.value)} rows={3} placeholder={"one user name or email per line"}
                    className="w-full rounded-lg border bg-card px-3 py-2 text-xs" />
          <span className="block font-normal text-muted-foreground">Their incidents count as &quot;mine&quot; in the inbox.</span></label>
      </div>
      {error && <FieldError>{error}</FieldError>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" disabled={pending} onClick={onClose}>Cancel</Button>
        <Button disabled={pending || !name.trim() || badMinutes || badProject} onClick={save}>
          {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}{team ? "Save" : "Add team"}</Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- routing rules

function RulesCard({ rules, error, teams, envs, may, onMsg, reload }: {
  rules: RoutingRule[] | null; error: string; teams: OpsTeam[]; envs: AirflowEnv[]; may: boolean; onMsg: OnMsg; reload: () => void;
}) {
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const ordered = useMemo(() => [...(rules ?? [])].sort((a, b) => a.priority - b.priority), [rules]);
  const teamName = (id: string | null) => (id ? teams.find((t) => t.team_id === id)?.name ?? id : null);
  const envName = (id: string | null) => (id ? envs.find((e) => e.env_id === id)?.name ?? id : null);
  const nextPriority = ordered.length ? ordered[ordered.length - 1].priority + 10 : 10;
  return (
    <section className="surface overflow-hidden">
      <header className="flex flex-wrap items-center gap-3 border-b px-5 py-4">
        <div className="min-w-[14rem] flex-1">
          <h3 className="text-base font-semibold">Routing rules</h3>
          <p className="text-xs text-muted-foreground">The first enabled rule that matches an incident, lowest priority number first, picks its team. A DAG&apos;s own team setting wins over rules.</p>
        </div>
        {may && editing === null && rules && <Button onClick={() => setEditing("new")} disabled={!teams.length} title={teams.length ? undefined : "Add a team first"}><Plus className="h-4 w-4" />Add rule</Button>}
      </header>
      {error && <div className="mx-5 mt-4"><FieldError>Routing rules did not load: {error}</FieldError></div>}
      {rules === null && !error && <p className="flex items-center gap-2 px-5 py-6 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading rules</p>}
      {editing === "new" && <RuleForm priority={nextPriority} teams={teams} envs={envs} onClose={() => setEditing(null)} onMsg={onMsg} onSaved={reload} />}
      {rules && (ordered.length ? (
        <ol className="divide-y">
          {ordered.map((r) => editing === r.rule_id
            ? <li key={r.rule_id}><RuleForm rule={r} priority={r.priority} teams={teams} envs={envs} onClose={() => setEditing(null)} onMsg={onMsg} onSaved={reload} /></li>
            : <RuleRow key={r.rule_id} rule={r} team={teamName(r.team_id)} env={envName(r.env_id)} may={may} onEdit={() => setEditing(r.rule_id)} onMsg={onMsg} reload={reload} />)}
        </ol>
      ) : editing !== "new" && (
        <p className="px-6 py-8 text-center text-xs text-muted-foreground">No routing rule. {teams.length ? "Add one to send incidents to a team." : "Add a team first."}</p>
      ))}
    </section>
  );
}

function RuleRow({ rule: r, team, env, may, onEdit, onMsg, reload }: {
  rule: RoutingRule; team: string | null; env: string | null; may: boolean; onEdit: () => void; onMsg: OnMsg; reload: () => void;
}) {
  const [confirm, setConfirm] = useState(false);
  const [pending, start] = useTransition();
  const { rule_id: _id, ...input } = r;
  void _id;
  const toggle = () => start(async () => {
    const res = await updateRule(r.rule_id, { ...input, enabled: !r.enabled });
    if (!res.ok) { onMsg({ tone: toneOf(res.error), text: res.error }); return; }
    reload();
  });
  const remove = () => start(async () => {
    const res = await deleteRule(r.rule_id);
    setConfirm(false);
    if (!res.ok) { onMsg({ tone: toneOf(res.error), text: res.error }); return; }
    onMsg({ tone: "ok", text: "The rule is removed." });
    reload();
  });
  const match = [
    r.dag_pattern && <span key="d">DAG <code className="font-mono">{r.dag_pattern}</code></span>,
    r.tag && <span key="t">tag <code className="font-mono">{r.tag}</code></span>,
    r.owner && <span key="o">owner {r.owner}</span>,
    env && <span key="e">in {env}</span>,
  ].filter(Boolean);
  const muted = parseTs(r.mute_until);
  return (
    <li className={cn("flex flex-wrap items-center gap-3 px-5 py-3 text-xs", !r.enabled && "opacity-60")}>
      <span className="grid h-7 w-10 shrink-0 place-items-center rounded-md bg-muted font-mono tabular-nums" title="Priority">{r.priority}</span>
      <div className="min-w-[14rem] flex-1 space-y-0.5">
        <p className="flex flex-wrap items-center gap-x-1.5">
          {match.length ? match.reduce<React.ReactNode[]>((acc, m, i) => (i ? [...acc, <span key={`s${i}`}>·</span>, m] : [m]), []) : <span>Every incident</span>}
          <span className="text-muted-foreground">→</span><span className="font-medium">{team ?? "no team"}</span>
        </p>
        <p className="flex flex-wrap gap-1.5 text-muted-foreground">
          {r.severity_override && <Chip tone="warn">severity {r.severity_override}</Chip>}
          {muted && <Chip tone="idle">muted until <When iso={r.mute_until} /></Chip>}
          {!r.enabled && <Chip tone="idle">disabled</Chip>}
        </p>
      </div>
      {may && (
        <div className="flex items-center gap-1.5">
          <Button variant="outline" size="sm" disabled={pending} onClick={toggle}>{r.enabled ? "Disable" : "Enable"}</Button>
          <Button variant="outline" size="sm" disabled={pending} onClick={onEdit}><Pencil className="h-3.5 w-3.5" />Edit</Button>
          <Button variant="ghost" size="sm" disabled={pending} onClick={() => setConfirm(true)} aria-label="Delete rule"><Trash2 className="h-3.5 w-3.5 text-destructive" /></Button>
        </div>
      )}
      {confirm && (
        <div role="alertdialog" aria-label="Confirm delete" className="flex basis-full flex-wrap items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2">
          <span className="flex-1">Delete this rule? Incidents it matched fall through to the next rule.</span>
          <Button size="sm" variant="ghost" disabled={pending} onClick={() => setConfirm(false)}>Cancel</Button>
          <Button size="sm" variant="destructive" disabled={pending} onClick={remove}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}Delete</Button>
        </div>
      )}
    </li>
  );
}

function RuleForm({ rule, priority: initialPriority, teams, envs, onClose, onMsg, onSaved }: {
  rule?: RoutingRule; priority: number; teams: OpsTeam[]; envs: AirflowEnv[]; onClose: () => void; onMsg: OnMsg; onSaved: () => void;
}) {
  const [priority, setPriority] = useState(String(initialPriority));
  const [pattern, setPattern] = useState(rule?.dag_pattern ?? "");
  const [tag, setTag] = useState(rule?.tag ?? "");
  const [owner, setOwner] = useState(rule?.owner ?? "");
  const [envId, setEnvId] = useState(rule?.env_id ?? "");
  const [teamId, setTeamId] = useState(rule?.team_id ?? teams[0]?.team_id ?? "");
  const [severity, setSeverity] = useState(rule?.severity_override ?? "");
  const [muteUntil, setMuteUntil] = useState(() => { const d = parseTs(rule?.mute_until); return d ? localInput(d) : ""; });
  const [enabled, setEnabled] = useState(rule?.enabled ?? true);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const prio = Number(priority);
  const badPriority = !Number.isInteger(prio) || prio < 0 || prio > 100000;
  const muteDate = muteUntil ? new Date(muteUntil) : null;
  const badMute = !!muteDate && Number.isNaN(muteDate.getTime());
  const save = () => start(async () => {
    setError("");
    const body: RuleInput = {
      priority: prio, dag_pattern: pattern.trim() || null, tag: tag.trim() || null, owner: owner.trim() || null, env_id: envId || null,
      team_id: teamId || null, severity_override: severity || null, mute_until: muteDate ? muteDate.toISOString() : null, enabled,
    };
    const r = rule ? await updateRule(rule.rule_id, body) : await createRule(body);
    if (!r.ok) { failed(r.error, onMsg, setError); return; }
    onMsg({ tone: "ok", text: rule ? "The rule is saved." : "The rule is added." });
    onClose();
    onSaved();
  });
  return (
    <div className="space-y-3 border-b bg-muted/20 px-5 py-4">
      <p className="text-sm font-semibold">{rule ? "Edit rule" : "Add a routing rule"}</p>
      <div className="grid gap-3 md:grid-cols-4">
        <label className="space-y-1 text-xs font-medium">Priority
          <Input value={priority} onChange={(e) => setPriority(e.target.value)} inputMode="numeric" className="text-xs" aria-invalid={badPriority} />
          <span className="block font-normal text-muted-foreground">Lower runs first.</span>
          {badPriority && <span role="alert" className="block font-normal text-destructive">A whole number, 0 or more.</span>}</label>
        <label className="space-y-1 text-xs font-medium">DAG pattern
          <Input value={pattern} onChange={(e) => setPattern(e.target.value)} placeholder="finance_*" className="font-mono text-xs" />
          <span className="block font-normal text-muted-foreground">A glob: * and ? match any text.</span></label>
        <label className="space-y-1 text-xs font-medium">Tag
          <Input value={tag} onChange={(e) => setTag(e.target.value)} placeholder="finance" className="text-xs" /></label>
        <label className="space-y-1 text-xs font-medium">Owner
          <Input value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="airflow owner" className="text-xs" /></label>
        <label className="space-y-1 text-xs font-medium">Environment
          <Select value={envId} onChange={(e) => setEnvId(e.target.value)} className="text-xs">
            <option value="">Any</option>
            {envs.map((e) => <option key={e.env_id} value={e.env_id}>{e.name}</option>)}
            {envId && !envs.some((e) => e.env_id === envId) && <option value={envId}>{envId}</option>}
          </Select></label>
        <label className="space-y-1 text-xs font-medium">Team
          <Select value={teamId} onChange={(e) => setTeamId(e.target.value)} className="text-xs">
            <option value="">None (leave unrouted)</option>
            {teams.map((t) => <option key={t.team_id} value={t.team_id}>{t.name}</option>)}
          </Select></label>
        <label className="space-y-1 text-xs font-medium">Severity override
          <Select value={severity} onChange={(e) => setSeverity(e.target.value)} className="text-xs">
            <option value="">Keep computed</option>
            {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
          </Select></label>
        <label className="space-y-1 text-xs font-medium">Mute until <span className="font-normal text-muted-foreground">(optional)</span>
          <Input type="datetime-local" value={muteUntil} onChange={(e) => setMuteUntil(e.target.value)} className="text-xs" aria-invalid={badMute} />
          <span className="block font-normal text-muted-foreground">Matching incidents open muted, with no ticket or card.</span></label>
        <label className="flex items-center gap-2 text-xs md:col-span-4">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />Enabled</label>
      </div>
      {error && <FieldError>{error}</FieldError>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" disabled={pending} onClick={onClose}>Cancel</Button>
        <Button disabled={pending || badPriority || badMute} onClick={save}>
          {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}{rule ? "Save" : "Add rule"}</Button>
      </div>
    </div>
  );
}
