"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Check, CircleCheck, KeyRound, Loader2, Pencil, Plug, Plus, RefreshCw, ShieldCheck, Trash2, Workflow, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { CopyButton } from "../runs/[runId]/dbt/studio-ui";
import {
  createEnv, deleteEnv, pollEnv, rotatePushSecret, testEnv, updateEnv,
  type AirflowEnv, type EnvInput, type EnvTest, type PushSecret,
} from "../ops/actions";
import { explain, When } from "../ops/ops-shared";

type Msg = { tone: "ok" | "info" | "error"; text: string } | null;
const toneOf = (e: string): "info" | "error" => (/approval|request/i.test(e) ? "info" : "error");
const IAM_ACTIONS = ["airflow:InvokeRestApi", "airflow:GetEnvironment", "logs:FilterLogEvents", "logs:GetLogEvents"];
const PUSH_ENV = ["GDP_INGEST_URL", "GDP_ENV_ID", "GDP_PUSH_SECRET"];

/** Admin, Integrations, Airflow: Amazon MWAA environments the platform polls (and optionally receives pushes from). */
export function AirflowTab({ envs, error, may, canPoll, onMsg }: {
  envs: AirflowEnv[] | null; error: string | null; may: boolean; canPoll: boolean; onMsg: (m: Msg) => void;
}) {
  const [editing, setEditing] = useState<AirflowEnv | "new" | null>(null);
  if (envs === null) {
    return <section className="surface p-5 text-sm text-muted-foreground">Airflow needs the latest deploy (migration V030).</section>;
  }
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
      <section className="surface h-fit overflow-hidden">
        <header className="flex flex-wrap items-center gap-3 border-b px-5 py-4">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-teal-50 text-teal-600 ring-1 ring-inset ring-teal-100"><Workflow className="h-5 w-5" /></span>
          <div className="min-w-[14rem] flex-1">
            <h3 className="text-base font-semibold">Amazon MWAA environments</h3>
            <p className="text-xs text-muted-foreground">Polled for DAGs, runs, tasks and logs; shown in <Link href="/ops" className="text-primary hover:underline">Pipelines</Link>.</p>
          </div>
          {may && editing === null && <Button onClick={() => setEditing("new")}><Plus className="h-4 w-4" />Add environment</Button>}
        </header>
        {error && <p role="alert" className="mx-5 mt-4 rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">Environments did not load: {explain(error)}</p>}
        {editing === "new" && <EnvForm onClose={() => setEditing(null)} onMsg={onMsg} />}
        {envs.length ? (
          <ul className="divide-y">
            {envs.map((e) => editing !== null && editing !== "new" && editing.env_id === e.env_id
              ? <li key={e.env_id}><EnvForm env={e} onClose={() => setEditing(null)} onMsg={onMsg} /></li>
              : <EnvRow key={e.env_id} env={e} may={may} canPoll={canPoll} onEdit={() => setEditing(e)} onMsg={onMsg} />)}
          </ul>
        ) : editing !== "new" && !error && (
          <div className="px-6 py-10 text-center">
            <p className="text-sm font-semibold">No environment yet</p>
            <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground">Add an Amazon MWAA environment by its name and region. The platform calls the Airflow REST API through MWAA, so no Airflow password is stored.</p>
            {may && <Button className="mt-4" onClick={() => setEditing("new")}><Plus className="h-4 w-4" />Add environment</Button>}
          </div>
        )}
      </section>

      <section className="surface h-fit space-y-3 p-5 text-xs">
        <h3 className="flex items-center gap-2 text-sm font-semibold"><ShieldCheck className="h-4 w-4 text-muted-foreground" />AWS access</h3>
        <p className="text-muted-foreground">The API host signs every MWAA and CloudWatch Logs call with its own IAM role (or AWS profile). No AWS keys are entered here. The role needs:</p>
        <ul className="space-y-1">
          {IAM_ACTIONS.map((a) => <li key={a}><code className="rounded bg-muted px-1.5 py-0.5 font-mono">{a}</code></li>)}
        </ul>
        <p className="text-muted-foreground">Scope them to the environment and its log groups. When a call is refused, the message names the missing action.</p>
        <h3 className="pt-2 text-sm font-semibold">Polling and push</h3>
        <p className="text-muted-foreground">Polling alone is enough, and is the only option when MWAA runs on a private network that cannot reach this app.
          Push adds near real time events: the gdp_listener plugin posts each DAG and task state change, signed with the environment&apos;s push secret.</p>
      </section>
    </div>
  );
}

function EnvRow({ env: e, may, canPoll, onEdit, onMsg }: {
  env: AirflowEnv; may: boolean; canPoll: boolean; onEdit: () => void; onMsg: (m: Msg) => void;
}) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [busy, setBusy] = useState<"test" | "poll" | "delete" | "secret" | null>(null);
  const [test, setTest] = useState<EnvTest | null>(null);
  const [confirm, setConfirm] = useState<"delete" | "secret" | null>(null);
  const [secret, setSecret] = useState<PushSecret | null>(null);
  const run = (what: NonNullable<typeof busy>, fn: () => Promise<void>) => start(async () => {
    setBusy(what);
    try { await fn(); } finally { setBusy(null); }
  });
  const doTest = () => run("test", async () => {
    setTest(null);
    const r = await testEnv(e.env_id);
    if (r.ok) setTest(r.data); else onMsg({ tone: toneOf(r.error), text: explain(r.error) });
  });
  const doPoll = () => run("poll", async () => {
    const r = await pollEnv(e.env_id);
    onMsg(r.ok ? { tone: "ok", text: r.data.detail || (r.data.started ? `Polling ${e.name} now.` : `A poll of ${e.name} is already running.`) }
      : { tone: toneOf(r.error), text: explain(r.error) });
    if (r.ok) router.refresh();
  });
  const doDelete = () => run("delete", async () => {
    const r = await deleteEnv(e.env_id);
    setConfirm(null);
    onMsg(r.ok ? { tone: "ok", text: `${e.name} is removed.` } : { tone: toneOf(r.error), text: explain(r.error) });
    if (r.ok) router.refresh();
  });
  const doSecret = () => run("secret", async () => {
    const r = await rotatePushSecret(e.env_id);
    setConfirm(null);
    if (r.ok) { setSecret(r.data); router.refresh(); } else onMsg({ tone: toneOf(r.error), text: explain(r.error) });
  });
  const status = !e.enabled ? { tone: "bg-slate-100 text-slate-600 ring-slate-200", text: "disabled" }
    : e.last_error ? { tone: "bg-rose-50 text-rose-700 ring-rose-100", text: "failing" }
      : e.last_poll_at ? { tone: "bg-emerald-50 text-emerald-700 ring-emerald-100", text: "polling" }
        : { tone: "bg-amber-50 text-amber-700 ring-amber-100", text: "not polled yet" };
  return (
    <li className="space-y-3 px-5 py-4">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-[14rem] flex-1">
          <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">{e.name}
            <span className={cn("rounded-full px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset", status.tone)}>{status.text}</span>
            {e.push_enabled && <span className="rounded-full bg-sky-50 px-1.5 py-0.5 text-[10px] font-medium text-sky-700 ring-1 ring-inset ring-sky-100">
              push{e.has_push_secret ? "" : ", no secret yet"}</span>}
          </p>
          {e.push_secret_detail && <p role="alert" className="text-xs text-amber-700 dark:text-amber-300">{e.push_secret_detail}</p>}
          <p className="text-xs text-muted-foreground">
            <span className="font-mono">{e.mwaa_env}</span> · {e.region} · every {e.poll_seconds} s · {e.dags} DAG{e.dags === 1 ? "" : "s"}
            {e.api_version && <> · API {e.api_version}</>} · last poll <When iso={e.last_poll_at} rel />
          </p>
          {e.airflow_url && <a href={e.airflow_url} target="_blank" rel="noreferrer" className="text-xs text-primary hover:underline">{e.airflow_url}</a>}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {may && (
            <Button variant="outline" size="sm" disabled={pending} onClick={doTest}>
              {busy === "test" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plug className="h-3.5 w-3.5" />}Test connection</Button>
          )}
          {canPoll && (
            <Button variant="outline" size="sm" disabled={pending || !e.enabled} onClick={doPoll} title={e.enabled ? undefined : "The environment is disabled"}>
              {busy === "poll" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}Poll now</Button>
          )}
          {may && <>
            <Button variant="outline" size="sm" disabled={pending} onClick={onEdit}><Pencil className="h-3.5 w-3.5" />Edit</Button>
            <Button variant="outline" size="sm" disabled={pending} onClick={() => setConfirm("secret")}><KeyRound className="h-3.5 w-3.5" />Push setup</Button>
            <Button variant="ghost" size="sm" disabled={pending} onClick={() => setConfirm("delete")} aria-label={`Delete ${e.name}`}>
              <Trash2 className="h-3.5 w-3.5 text-destructive" /></Button>
          </>}
        </div>
      </div>

      {e.last_error && <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">Last poll failed: {explain(e.last_error)}</p>}

      {test && (
        <div role={test.ok ? "status" : "alert"} className={cn("rounded-lg px-3 py-2 text-xs", test.ok ? "bg-success/10 text-success" : "bg-destructive/10 text-destructive")}>
          <p className="flex items-start gap-2">
            <span className="flex-1">{test.ok
              ? <>Connected. Airflow {test.version ?? "version unknown"}{test.api_version ? `, REST API ${test.api_version}` : ""}.</>
              : <>The connection failed: {explain(test.error ?? "no detail from the API")}</>}</span>
            <button type="button" aria-label="Dismiss" onClick={() => setTest(null)}><X className="h-3.5 w-3.5" /></button>
          </p>
          {test.ok && (test.dags_sample.length
            ? <p className="mt-1 text-foreground/80">Sample DAGs: <span className="font-mono">{test.dags_sample.join(", ")}</span></p>
            : <p className="mt-1 text-foreground/80">No DAGs visible yet.</p>)}
        </div>
      )}

      {confirm === "delete" && (
        <div role="alertdialog" aria-label="Confirm delete" className="flex flex-wrap items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs">
          <span className="flex-1">Delete {e.name}? Its DAGs, runs and settings recorded here are removed. Airflow itself is not touched.</span>
          <Button size="sm" variant="ghost" disabled={pending} onClick={() => setConfirm(null)}>Cancel</Button>
          <Button size="sm" variant="destructive" disabled={pending} onClick={doDelete}>
            {busy === "delete" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}Delete</Button>
        </div>
      )}

      {confirm === "secret" && (
        <div className="space-y-2 rounded-lg border bg-muted/30 px-3 py-3 text-xs">
          <p className="font-medium">Push setup</p>
          <p className="text-muted-foreground">The gdp_listener plugin posts each DAG and task state change to this app, signed with a secret.
            {e.has_push_secret ? " Generating a new secret replaces the current one: the plugin stops being accepted until MWAA has the new value." : ""}
            {!e.push_enabled ? " Push is off for this environment; turn it on with Edit so events are accepted." : ""}</p>
          <div className="flex flex-wrap justify-end gap-2">
            <Button size="sm" variant="ghost" disabled={pending} onClick={() => setConfirm(null)}>Cancel</Button>
            <Button size="sm" disabled={pending} onClick={doSecret}>
              {busy === "secret" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <KeyRound className="h-3.5 w-3.5" />}
              {e.has_push_secret ? "Replace push secret" : "Generate push secret"}</Button>
          </div>
        </div>
      )}

      {secret && <SecretBox env={e} secret={secret} onClose={() => setSecret(null)} />}
    </li>
  );
}

function SecretBox({ env, secret, onClose }: { env: AirflowEnv; secret: PushSecret; onClose: () => void }) {
  const url = `${window.location.origin}${secret.url_path}`;
  const values: Record<string, string> = { GDP_INGEST_URL: url, GDP_ENV_ID: env.env_id, GDP_PUSH_SECRET: secret.secret };
  return (
    <div className="space-y-3 rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-3 text-xs dark:border-amber-900 dark:bg-amber-950/30">
      <p className="flex items-start gap-2 font-medium">
        <span className="flex-1">Copy the secret now. It is shown only once; generate a new one if it is lost.</span>
        <button type="button" aria-label="Close" onClick={onClose}><X className="h-3.5 w-3.5" /></button>
      </p>
      <ol className="list-decimal space-y-2 pl-4">
        <li>
          <p>In the MWAA environment, set these (as environment variables or Airflow Variables):</p>
          <ul className="mt-1 space-y-1">
            {PUSH_ENV.map((k) => (
              <li key={k} className="flex flex-wrap items-center gap-2">
                <code className="w-36 shrink-0 font-mono">{k}</code>
                <code className="min-w-0 flex-1 break-all rounded bg-card px-1.5 py-0.5 font-mono">{values[k]}</code>
                <CopyButton text={values[k]} />
              </li>
            ))}
          </ul>
        </li>
        <li>Install the plugin from the repository folder <code className="font-mono">airflow_plugins/gdp_listener</code> into the environment&apos;s plugins.zip and update the environment.</li>
        <li>The plugin signs each event with these headers: {secret.header_names.map((h, i) => (
          <span key={h}>{i > 0 && ", "}<code className="font-mono">{h}</code></span>))}.</li>
      </ol>
      <p className="text-muted-foreground">MWAA on a private network that cannot reach {window.location.host} can skip push and rely on polling alone.</p>
    </div>
  );
}

function EnvForm({ env, onClose, onMsg }: { env?: AirflowEnv; onClose: () => void; onMsg: (m: Msg) => void }) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [name, setName] = useState(env?.name ?? "");
  const [mwaa, setMwaa] = useState(env?.mwaa_env ?? "");
  const [region, setRegion] = useState(env?.region ?? "");
  const [url, setUrl] = useState(env?.airflow_url ?? "");
  const [poll, setPoll] = useState(String(env?.poll_seconds ?? 300));
  const [enabled, setEnabled] = useState(env?.enabled ?? true);
  const [push, setPush] = useState(env?.push_enabled ?? false);
  const [error, setError] = useState("");
  const seconds = Number(poll);
  const badPoll = !Number.isInteger(seconds) || seconds < 60 || seconds > 3600;
  const badUrl = !!url.trim() && !/^https:\/\/\S+$/i.test(url.trim());
  const ready = name.trim() && mwaa.trim() && /^[a-z]{2}(-[a-z]+)+-\d$/.test(region.trim()) && !badPoll && !badUrl;
  const save = () => start(async () => {
    setError("");
    const body: EnvInput = {
      name: name.trim(), mwaa_env: mwaa.trim(), region: region.trim(), airflow_url: url.trim() || null,
      poll_seconds: seconds, enabled, push_enabled: push,
    };
    const r = env ? await updateEnv(env.env_id, body) : await createEnv(body);
    if (!r.ok) { if (toneOf(r.error) === "info") onMsg({ tone: "info", text: r.error }); else setError(explain(r.error)); return; }
    onMsg({ tone: "ok", text: env ? `${body.name} is saved.` : `${body.name} is added. Test the connection, then the first poll fills Pipelines.` });
    onClose();
    router.refresh();
  });
  return (
    <div className="space-y-3 border-b bg-muted/20 px-5 py-4">
      <p className="text-sm font-semibold">{env ? `Edit ${env.name}` : "Add an MWAA environment"}</p>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="space-y-1 text-xs font-medium">Name
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Production" className="text-xs" /></label>
        <label className="space-y-1 text-xs font-medium">MWAA environment name
          <Input value={mwaa} onChange={(e) => setMwaa(e.target.value)} placeholder="prod-airflow" className="font-mono text-xs" /></label>
        <label className="space-y-1 text-xs font-medium">AWS region
          <Input value={region} onChange={(e) => setRegion(e.target.value.toLowerCase())} placeholder="us-east-1" className="font-mono text-xs" /></label>
        <label className="space-y-1 text-xs font-medium">Airflow UI URL <span className="font-normal text-muted-foreground">(optional, for links)</span>
          <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://xxxx.airflow.us-east-1.on.aws" className="font-mono text-xs" aria-invalid={badUrl} />
          {badUrl && <span role="alert" className="block font-normal text-destructive">An https:// address.</span>}</label>
        <label className="space-y-1 text-xs font-medium">Poll every (seconds)
          <Input value={poll} onChange={(e) => setPoll(e.target.value)} inputMode="numeric" className="text-xs" aria-invalid={badPoll} />
          {badPoll && <span role="alert" className="block font-normal text-destructive">From 60 to 3600.</span>}</label>
        <div className="flex flex-col justify-end gap-2 text-xs">
          <label className="flex items-center gap-2"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />Enabled (polled)</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={push} onChange={(e) => setPush(e.target.checked)} />Accept push events from the gdp_listener plugin</label>
        </div>
      </div>
      {error && <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" disabled={pending} onClick={onClose}>Cancel</Button>
        <Button disabled={pending || !ready} onClick={save}>
          {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : env ? <Check className="h-4 w-4" /> : <CircleCheck className="h-4 w-4" />}
          {env ? "Save" : "Add environment"}</Button>
      </div>
    </div>
  );
}
