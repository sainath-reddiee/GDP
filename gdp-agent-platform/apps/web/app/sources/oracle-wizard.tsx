"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import {
  ArrowLeft, ArrowRight, BookOpen, CheckCircle2, Eye, EyeOff, KeyRound, Loader2, Network, RefreshCw, Server, ShieldAlert,
  Snowflake, XCircle,
} from "lucide-react";
import type { OracleTest } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  oracleCheckIntegration, oracleConnection, oracleEnvCheck, oracleIntegrations, oracleSecrets, oracleSecretUser,
  oracleSetup, oracleTest, registerExternalSource, type IntegrationCheck, type SetupResult,
} from "./actions";
import type { ManagedSource } from "./connect-source";
import { SetupGuide } from "./oracle-guide";
import { Checklist, ConnectionFields, connectionProblems, CopyButton, SecretNote, type OracleFields } from "./oracle-ui";

type Step = 1 | 2 | 3;
type Runtime = "snowflake" | "api_host";

function StepDots({ step }: { step: Step }) {
  const steps = ["Connection", "Access", "Verify"];
  return (
    <ol className="flex items-center gap-2 text-xs">
      {steps.map((label, i) => {
        const n = (i + 1) as Step;
        return (
          <li key={label} className="flex items-center gap-2">
            <span className={cn("flex h-6 w-6 items-center justify-center rounded-full border text-[11px] font-semibold",
              n < step ? "border-success bg-success text-white" : n === step ? "border-primary bg-primary text-primary-foreground" : "text-muted-foreground")}>
              {n < step ? <CheckCircle2 className="h-3.5 w-3.5" /> : n}
            </span>
            <span className={n === step ? "font-semibold" : "text-muted-foreground"}>{label}</span>
            {i < steps.length - 1 && <span className="mx-1 h-px w-8 bg-border" />}
          </li>
        );
      })}
    </ol>
  );
}

function Choice({ active, onClick, icon: Icon, title, body, tag }: {
  active: boolean; onClick: () => void; icon: typeof Snowflake; title: string; body: string; tag?: string;
}) {
  return (
    <button type="button" aria-pressed={active} onClick={onClick}
            className={cn("flex items-start gap-3 rounded-xl border p-3 text-left transition-all",
              active ? "border-primary bg-primary/5 ring-2 ring-primary/20" : "hover:border-primary/40 hover:bg-muted/30")}>
      <span className={cn("rounded-lg p-1.5", active ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>
        <Icon className="h-4 w-4" />
      </span>
      <span>
        <span className="flex items-center gap-2 text-sm font-semibold">{title}
          {tag && <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">{tag}</span>}
        </span>
        <span className="mt-0.5 block text-[11px] text-muted-foreground">{body}</span>
      </span>
    </button>
  );
}

function Radio({ checked, onChange, children }: { checked: boolean; onChange: () => void; children: React.ReactNode }) {
  return (
    <label className={cn("flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm",
      checked ? "border-primary bg-primary/5" : "hover:bg-muted/30")}>
      <input type="radio" checked={checked} onChange={onChange} className="accent-primary" />
      {children}
    </label>
  );
}

/** Connect an Oracle database in three steps: where it is, how the platform gets in, and a live verification. */
export function OracleWizard({ onDone, onCancel }: {
  onDone: (source: ManagedSource) => void; onCancel: () => void;
}) {
  const [step, setStep] = useState<Step>(1);
  const [name, setName] = useState("");
  const [fields, setFields] = useState<OracleFields>({ port: "1521", protocol: "tcp", service_name: "" });
  const [runtime, setRuntime] = useState<Runtime>("snowflake");
  const [secretMode, setSecretMode] = useState<"new" | "existing">("new");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [capsLock, setCapsLock] = useState(false);
  const [secrets, setSecrets] = useState<{ name: string; comment: string | null }[] | null>(null);
  const [secretsError, setSecretsError] = useState("");
  const [secret, setSecret] = useState("");
  const [secretUser, setSecretUser] = useState<string | null>(null);
  const [integrationMode, setIntegrationMode] = useState<"new" | "existing">("new");
  const [integrations, setIntegrations] = useState<{ name: string; enabled: boolean }[] | null>(null);
  const [eaiNew, setEaiNew] = useState("");
  const [eaiExisting, setEaiExisting] = useState("");
  const [eaiCheck, setEaiCheck] = useState<IntegrationCheck | null>(null);
  const [passwordEnv, setPasswordEnv] = useState("");
  const [envPresent, setEnvPresent] = useState<boolean | null>(null);
  const [walletDir, setWalletDir] = useState("");
  const [registered, setRegistered] = useState<ManagedSource | null>(null);
  const [savedFields, setSavedFields] = useState("");
  const [phase, setPhase] = useState<"idle" | "register" | "setup" | "test" | "done">("idle");
  const [setup, setSetup] = useState<SetupResult | null>(null);
  const [test, setTest] = useState<OracleTest | null>(null);
  const [error, setError] = useState("");
  const [showGuide, setShowGuide] = useState(true);
  const [pending, start] = useTransition();

  const nameOk = /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(name);
  const problems = connectionProblems(fields);
  const owner = fields.schema_owner || fields.user || "";
  const target = `${fields.host || "host"}:${fields.port || "1521"}`;
  const defaultEai = `${name || "SOURCE"}_ORACLE_ACCESS`;

  useEffect(() => {
    if (runtime !== "snowflake") return;
    if (secretMode === "existing" && secrets === null) {
      oracleSecrets().then((r) => {
        if (r.ok) { setSecrets(r.data.secrets); setSecretsError(r.data.error ?? ""); } else setSecretsError(r.error);
      });
    }
    if (integrationMode === "existing" && integrations === null) {
      oracleIntegrations().then((r) => r.ok && setIntegrations(r.data.integrations));
    }
  }, [runtime, secretMode, integrationMode, secrets, integrations]);

  useEffect(() => {
    setSecretUser(null);
    if (!secret) return;
    oracleSecretUser(secret).then((r) => r.ok && setSecretUser(r.data.username));
  }, [secret]);

  useEffect(() => {
    setEaiCheck(null);
    if (integrationMode !== "existing" || !eaiExisting || !fields.host) return;
    oracleCheckIntegration({ name: eaiExisting, host: fields.host, port: Number(fields.port || 1521),
                             secret: secretMode === "existing" ? secret || undefined : undefined })
      .then((r) => r.ok && setEaiCheck(r.data));
  }, [integrationMode, eaiExisting, fields.host, fields.port, secretMode, secret]);

  const accessProblems = useMemo(() => {
    const out: string[] = [];
    if (runtime === "api_host") {
      if (!/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(passwordEnv)) out.push("name the environment variable that holds the password");
      return out;
    }
    if (secretMode === "new" && !password) out.push("enter the Oracle password");
    if (secretMode === "existing" && !secret) out.push("choose the secret");
    if (integrationMode === "existing") {
      if (!eaiExisting) out.push("choose the integration");
      if (secretMode === "new") out.push("an existing integration can only use a secret it already allows: choose an existing secret too, or let the platform create network access");
      if (eaiCheck && !eaiCheck.usable) out.push(`${eaiExisting} does not allow ${eaiCheck.allows_host === false ? target : "this secret"}`);
    } else if (!/^[A-Za-z_][A-Za-z0-9_$]{0,254}$/.test(eaiNew || defaultEai)) out.push("integration name must be a Snowflake identifier");
    return out;
  }, [runtime, passwordEnv, secretMode, password, secret, integrationMode, eaiExisting, eaiCheck, eaiNew, defaultEai, target]);

  const creates = runtime === "snowflake" ? [
    secretMode === "new" ? `Secret EXT_${name || "NAME"}.ORACLE_LOGIN (type PASSWORD)` : null,
    integrationMode === "new" ? `Network rule allowing ${target} (egress only)` : null,
    integrationMode === "new" ? `External access integration ${eaiNew || defaultEai}` : null,
    `Procedure EXT_${name || "NAME"}.ORACLE_RUN (runs as owner, reads Oracle, lands Parquet)`,
  ].filter(Boolean) as string[] : [];

  const config = (): Record<string, string> => {
    const c: Record<string, string> = { runtime };
    for (const [k, v] of Object.entries(fields)) if (v) c[k] = v;
    if (runtime === "api_host") {
      c.password_env = passwordEnv;
      if (walletDir) c.wallet_dir = walletDir;
    }
    return c;
  };

  const verify = () => start(async () => {
    setError(""); setSetup(null); setTest(null);
    setStep(3);
    let source = registered;
    const now = JSON.stringify(config());
    setPhase("register");
    if (!source) {
      const r = await registerExternalSource({ source_system_name: name, connector: "oracle", config: config() });
      if (!r.ok) { setError(r.error); setPhase("idle"); return; }
      source = { id: r.data.source_system_id, name: r.data.source_system_name, connector: "oracle",
                 database: r.data.landing.database, schema: r.data.landing.schema };
      setRegistered(source);
      setSavedFields(now);
    } else if (now !== savedFields) {
      const r = await oracleConnection(source.id, config());
      if (!r.ok) { setError(r.error); setPhase("idle"); return; }
      setSavedFields(now);
    }
    if (runtime === "snowflake") {
      setPhase("setup");
      const r = await oracleSetup(source.id, {
        secret_mode: secretMode, password: secretMode === "new" ? password : undefined,
        secret: secretMode === "existing" ? secret : undefined, integration_mode: integrationMode,
        external_access_integration: integrationMode === "new" ? (eaiNew || defaultEai) : eaiExisting,
      });
      if (!r.ok) { setError(r.error); setPhase("idle"); return; }
      setSetup(r.data);
      if (!r.data.ready) { setPhase("idle"); return; }
      setPassword("");
      if (secretMode === "new") { setSecretMode("existing"); setSecret(r.data.secret ?? ""); }
      if (integrationMode === "new") { setIntegrationMode("existing"); setEaiExisting(r.data.integration ?? ""); setIntegrations(null); }
    }
    setPhase("test");
    const t = await oracleTest(source.id);
    if (t.ok) setTest(t.data); else setError(t.error);
    setPhase("done");
  });

  const failedSql = setup && !setup.ready
    ? setup.log.map((l) => l.sql.replace("'<oracle password>'", "'<the Oracle password>'")).join(";\n\n") + ";" : "";

  return (
    <div className={cn("grid gap-5", showGuide && "lg:grid-cols-[minmax(0,1fr)_340px]")}>
    <div className="min-w-0 space-y-5">
      <div className="flex items-center gap-3">
        <StepDots step={step} />
        {!showGuide && (
          <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setShowGuide(true)}>
            <BookOpen className="h-3.5 w-3.5" /> Setup guide
          </Button>
        )}
      </div>

      {step === 1 && (
        <div className="space-y-4">
          <label className="block text-xs font-medium">Source name
            <Input value={name} onChange={(e) => setName(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_"))}
                   placeholder="HR_ORACLE" maxLength={64} className="mt-1 font-mono" disabled={!!registered} />
            <span className="mt-1 block text-[11px] font-normal text-muted-foreground">
              Landed tables go to <span className="font-mono">EXT_{name || "NAME"}</span> in the platform database.
            </span>
          </label>
          <ConnectionFields value={fields} onChange={setFields} disabled={!!registered && pending} />
        </div>
      )}

      {step === 2 && (
        <div className="space-y-5">
          <div>
            <p className="mb-2 text-xs font-medium">Where the extraction runs</p>
            <div className="grid gap-2 sm:grid-cols-2">
              <Choice active={runtime === "snowflake"} onClick={() => setRuntime("snowflake")} icon={Snowflake} tag="Recommended"
                      title="Inside Snowflake" body={`Snowflake connects to ${target} directly. Needs Oracle reachable from Snowflake: public endpoint, allow-listed Snowflake egress IPs or PrivateLink.`} />
              <Choice active={runtime === "api_host"} onClick={() => setRuntime("api_host")} icon={Server}
                      title="On this platform's server" body="For an Oracle only your network or VPN reaches. The API server reads Oracle and uploads to Snowflake." />
            </div>
          </div>

          {runtime === "snowflake" ? (
            <>
              <section className="space-y-2">
                <p className="flex items-center gap-2 text-sm font-semibold"><KeyRound className="h-4 w-4 text-primary" /> Oracle password</p>
                <div className="grid gap-2 sm:grid-cols-2">
                  <Radio checked={secretMode === "new"} onChange={() => setSecretMode("new")}>
                    <span><span className="block font-medium">Enter the password</span>
                      <span className="block text-[11px] text-muted-foreground">Saved straight into a new Snowflake secret</span></span>
                  </Radio>
                  <Radio checked={secretMode === "existing"} onChange={() => setSecretMode("existing")}>
                    <span><span className="block font-medium">Use an existing secret</span>
                      <span className="block text-[11px] text-muted-foreground">One your admin already created</span></span>
                  </Radio>
                </div>
                {secretMode === "new" ? (
                  <div>
                    <div className="relative">
                      <Input type={showPw ? "text" : "password"} autoComplete="new-password" value={password} aria-label="Oracle password"
                             onChange={(e) => setPassword(e.target.value)} placeholder={`Password of ${fields.user || "the Oracle user"}`}
                             onKeyUp={(e) => setCapsLock(e.getModifierState("CapsLock"))} className="pr-10" />
                      <button type="button" aria-label={showPw ? "Hide password" : "Show password"} onClick={() => setShowPw((v) => !v)}
                              className="absolute right-2 top-2 text-muted-foreground hover:text-foreground">
                        {showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </button>
                    </div>
                    {capsLock && <p className="mt-1 text-[11px] text-warning">Caps Lock is on; Oracle passwords are case-sensitive.</p>}
                  </div>
                ) : (
                  <div className="space-y-1">
                    <select value={secret} onChange={(e) => setSecret(e.target.value)} aria-label="Existing secret"
                            className="h-9 w-full rounded-md border bg-card px-2 font-mono text-sm">
                      <option value="">{secrets === null ? "Loading secrets…" : secrets.length ? "Choose a PASSWORD secret" : "No PASSWORD secrets visible to this role"}</option>
                      {(secrets ?? []).map((s) => <option key={s.name} value={s.name}>{s.name}{s.comment ? `  ·  ${s.comment}` : ""}</option>)}
                    </select>
                    {secretsError && <p className="text-[11px] text-warning">{secretsError}</p>}
                    {secretUser && fields.user && secretUser.toUpperCase() !== fields.user.toUpperCase() && (
                      <p className="flex flex-wrap items-center gap-2 text-[11px] text-warning">
                        <ShieldAlert className="h-3.5 w-3.5" /> This secret is for {secretUser}, but the connection user is {fields.user}.
                        <button type="button" className="font-medium text-primary underline" onClick={() => setFields({ ...fields, user: secretUser.toUpperCase() })}>
                          Use {secretUser.toUpperCase()}
                        </button>
                      </p>
                    )}
                    {secretUser && fields.user && secretUser.toUpperCase() === fields.user.toUpperCase() && (
                      <p className="flex items-center gap-1 text-[11px] text-success"><CheckCircle2 className="h-3.5 w-3.5" /> Secret user matches {fields.user}</p>
                    )}
                  </div>
                )}
              </section>

              <section className="space-y-2">
                <p className="flex items-center gap-2 text-sm font-semibold"><Network className="h-4 w-4 text-primary" /> Network access from Snowflake</p>
                <div className="grid gap-2 sm:grid-cols-2">
                  <Radio checked={integrationMode === "new"} onChange={() => setIntegrationMode("new")}>
                    <span><span className="block font-medium">Create access</span>
                      <span className="block text-[11px] text-muted-foreground">Allows only {target}</span></span>
                  </Radio>
                  <Radio checked={integrationMode === "existing"} onChange={() => setIntegrationMode("existing")}>
                    <span><span className="block font-medium">Use an existing integration</span>
                      <span className="block text-[11px] text-muted-foreground">Checked for this host and secret</span></span>
                  </Radio>
                </div>
                {integrationMode === "new" ? (
                  <label className="block text-xs font-medium">Integration name
                    <Input value={eaiNew} onChange={(e) => setEaiNew(e.target.value.toUpperCase())} placeholder={defaultEai} className="mt-1 font-mono" />
                  </label>
                ) : (
                  <div className="space-y-2">
                    <select value={eaiExisting} onChange={(e) => setEaiExisting(e.target.value)} aria-label="Existing integration"
                            className="h-9 w-full rounded-md border bg-card px-2 font-mono text-sm">
                      <option value="">{integrations === null ? "Loading integrations…" : "Choose an external access integration"}</option>
                      {(integrations ?? []).map((i) => <option key={i.name} value={i.name} disabled={!i.enabled}>{i.name}{i.enabled ? "" : " (disabled)"}</option>)}
                    </select>
                    {eaiExisting && !eaiCheck && <p className="flex items-center gap-2 text-[11px] text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" /> Checking what it allows…</p>}
                    {eaiCheck && (
                      <ul className="grid gap-1 text-[11px] sm:grid-cols-3">
                        {[["Enabled", eaiCheck.enabled], [`Allows ${target}`, eaiCheck.allows_host],
                          ["Allows the secret", eaiCheck.allows_secret]].map(([label, ok]) => (
                          <li key={String(label)} className={cn("flex items-center gap-1 rounded-md border px-2 py-1",
                            ok === null ? "text-muted-foreground" : ok ? "text-success" : "text-destructive")}>
                            {ok === null ? <span className="h-3.5 w-3.5" /> : ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
                            {String(label)}{ok === null ? (String(label).startsWith("Allows the secret") ? " (choose a secret)" : " (checked by the test)") : ""}
                          </li>
                        ))}
                      </ul>
                    )}
                    {eaiCheck?.note && <p className="text-[11px] text-muted-foreground">{eaiCheck.note}</p>}
                  </div>
                )}
              </section>

              <section className="rounded-xl bg-muted/40 p-3">
                <p className="mb-1 text-xs font-semibold">Snowflake objects for this source</p>
                <ul className="space-y-0.5 text-[11px] text-muted-foreground">
                  {creates.map((c) => <li key={c}>• {c}</li>)}
                  {secretMode === "existing" && secret && <li>• Uses secret <span className="font-mono">{secret}</span></li>}
                  {integrationMode === "existing" && eaiExisting && <li>• Uses integration <span className="font-mono">{eaiExisting}</span></li>}
                </ul>
                <p className="mt-2 text-[11px] text-muted-foreground">Needs CREATE SECRET, NETWORK RULE and INTEGRATION for what is created. Without them, the exact SQL is shown for an admin.</p>
              </section>
            </>
          ) : (
            <section className="space-y-3">
              <label className="block text-xs font-medium">Password environment variable on the API server
                <div className="mt-1 flex gap-2">
                  <Input value={passwordEnv} onChange={(e) => { setPasswordEnv(e.target.value.toUpperCase()); setEnvPresent(null); }}
                         placeholder={`ORACLE_${name || "HR"}_PASSWORD`} className="font-mono" />
                  <Button type="button" size="sm" variant="outline" disabled={!passwordEnv}
                          onClick={() => oracleEnvCheck(passwordEnv).then((r) => r.ok && setEnvPresent(r.data.present))}>Check</Button>
                </div>
              </label>
              {envPresent === true && <p className="flex items-center gap-1 text-xs text-success"><CheckCircle2 className="h-3.5 w-3.5" /> {passwordEnv} is set on the API server.</p>}
              {envPresent === false && (
                <p className="rounded-lg border border-warning/30 bg-warning/5 p-2 text-xs">
                  {passwordEnv} is not set on the API server. Set it where the API runs (for example in its .env file), restart the API, then verify.
                  You can continue; the check will tell you when it is missing.
                </p>
              )}
              {fields.protocol === "tcps" && (
                <label className="block text-xs font-medium">Wallet folder (optional, for mutual TLS)
                  <Input value={walletDir} onChange={(e) => setWalletDir(e.target.value)} placeholder="C:\oracle\wallet_hr or /opt/oracle/wallet_hr" className="mt-1 font-mono" />
                  <span className="mt-1 block text-[11px] font-normal text-muted-foreground">The unzipped wallet (ewallet.pem) on the API server. Leave empty for TLS without a wallet.</span>
                </label>
              )}
            </section>
          )}
          <SecretNote />
        </div>
      )}

      {step === 3 && (
        <div className="space-y-4">
          <ol className="flex flex-wrap gap-2 text-xs">
            {([["register", "Register source"], ...(runtime === "snowflake" ? [["setup", "Create Snowflake access"]] : []),
               ["test", "Check the connection"]] as [string, string][]).map(([id, label]) => {
              const order = ["register", "setup", "test", "done"];
              const done = order.indexOf(phase) > order.indexOf(id) || (phase === "done");
              const active = phase === id;
              const failed = id === "setup" && setup && !setup.ready;
              return (
                <li key={id} className={cn("flex items-center gap-1.5 rounded-full border px-2.5 py-1",
                  failed ? "border-destructive/30 text-destructive" : done ? "border-success/30 text-success" : active ? "border-primary text-primary" : "text-muted-foreground")}>
                  {failed ? <XCircle className="h-3.5 w-3.5" /> : done ? <CheckCircle2 className="h-3.5 w-3.5" /> : active ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                  {label}
                </li>
              );
            })}
          </ol>

          {setup && !setup.ready && (
            <section className="space-y-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
              <p className="text-sm font-semibold">Snowflake access needs an admin</p>
              <p className="text-xs">{setup.detail}</p>
              <ol className="space-y-1 font-mono text-[11px]">
                {setup.log.map((l, i) => (
                  <li key={i} className={l.ok ? "text-muted-foreground" : "text-destructive"}>
                    {l.ok ? "✓" : "✗"} {l.sql.split("\n")[0].slice(0, 160)}{l.error ? `  →  ${l.error}` : ""}
                  </li>
                ))}
              </ol>
              {setup.grants && setup.grants.length > 0 && (
                <pre className="whitespace-pre-wrap rounded-lg bg-card p-2 font-mono text-[11px]">{setup.grants.join(";\n")};</pre>
              )}
              <div className="flex flex-wrap gap-2">
                <CopyButton text={setup.grants?.length ? setup.grants.join(";\n") + ";" : failedSql} label="Copy SQL for an admin" />
                <Button size="sm" variant="ghost" onClick={() => setStep(2)}><ArrowLeft className="h-3.5 w-3.5" /> Use existing objects instead</Button>
              </div>
            </section>
          )}

          {(phase === "test" || test) && <Checklist checks={test?.checks ?? null} running={phase === "test"} />}

          {test && (
            <div className={cn("flex items-start gap-3 rounded-xl border p-4",
              test.status === "fail" ? "border-destructive/30 bg-destructive/5" : test.status === "warn" ? "border-warning/30 bg-warning/5" : "border-success/30 bg-success/5")}>
              {test.status === "fail" ? <XCircle className="h-5 w-5 text-destructive" /> : <CheckCircle2 className={cn("h-5 w-5", test.status === "warn" ? "text-warning" : "text-success")} />}
              <div className="text-sm">
                <p className="font-semibold">{test.status === "fail" ? "Not connected yet" : `Connected to ${owner} on Oracle ${test.server.version ?? ""}`}</p>
                <p className="text-xs text-muted-foreground">
                  {test.status === "fail" ? "Fix the failed step and run the checks again, or save the source and finish later."
                    : `${test.server.visible_tables ?? 0} tables and ${test.server.visible_views ?? 0} views are ready to browse, profile and land.`}
                </p>
              </div>
            </div>
          )}
        </div>
      )}

      {error && <p role="alert" className="rounded-lg bg-destructive/10 p-2 text-sm text-destructive">{error}</p>}

      <div className="flex flex-wrap items-center gap-2 border-t pt-4">
        {step === 1 && (
          <>
            <span className="text-[11px] text-muted-foreground">
              {!nameOk ? "Name the source (letters, digits, _)" : problems[0] ? `Fix: ${problems[0]}` : "Next: how the platform gets in"}
            </span>
            <Button variant="ghost" className="ml-auto" onClick={onCancel}>Cancel</Button>
            <Button disabled={!nameOk || problems.length > 0} onClick={() => setStep(2)}>Continue <ArrowRight className="h-4 w-4" /></Button>
          </>
        )}
        {step === 2 && (
          <>
            <Button variant="ghost" onClick={() => setStep(1)}><ArrowLeft className="h-4 w-4" /> Back</Button>
            <span className="text-[11px] text-muted-foreground">{accessProblems[0] ? `Fix: ${accessProblems[0]}` : ""}</span>
            <Button className="ml-auto" disabled={accessProblems.length > 0 || pending} onClick={verify}>
              {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />} Connect and verify
            </Button>
          </>
        )}
        {step === 3 && (
          <>
            <Button variant="ghost" disabled={pending} onClick={() => setStep(registered ? 2 : 1)}><ArrowLeft className="h-4 w-4" /> Back</Button>
            {registered && test && (
              <Button variant="outline" disabled={pending} onClick={() => start(async () => {
                setPhase("test"); setTest(null);
                const t = await oracleTest(registered.id);
                if (t.ok) setTest(t.data); else setError(t.error);
                setPhase("done");
              })}>
                <RefreshCw className="h-4 w-4" /> Run checks again
              </Button>
            )}
            {registered && (
              <Button className="ml-auto" variant={test && test.status !== "fail" ? "default" : "outline"} disabled={pending}
                      onClick={() => onDone(registered)}>
                {test && test.status !== "fail" ? "Open source" : "Save and finish later"} <ArrowRight className="h-4 w-4" />
              </Button>
            )}
          </>
        )}
      </div>
    </div>
    {showGuide && (
      <div className="lg:sticky lg:top-0 lg:self-start">
        <SetupGuide user={fields.user ?? ""} owner={fields.schema_owner ?? ""} host={fields.host ?? ""} port={fields.port ?? ""}
                    tls={fields.protocol === "tcps"} runtime={runtime} passwordEnv={passwordEnv} sourceName={name}
                    onClose={() => setShowGuide(false)} />
      </div>
    )}
    </div>
  );
}
