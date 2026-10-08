"use client";

import { useEffect, useState, useTransition } from "react";
import {
  AlertTriangle, CheckCircle2, CircleDashed, ClipboardPaste, Copy, Loader2, Lock, MinusCircle, ShieldCheck, Wand2, XCircle,
} from "lucide-react";
import type { CheckStatus, OracleCheck } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { oracleParse } from "./actions";

export type OracleFields = Record<string, string>;

export function fmtRows(n: number | null | undefined) {
  if (n == null) return "–";
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function fmtBytes(n: number | null | undefined) {
  if (n == null) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v >= 10 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

export function fmtDuration(seconds: number | null | undefined) {
  if (seconds == null) return "–";
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const m = Math.floor(seconds / 60);
  return m < 60 ? `${m}m ${Math.round(seconds % 60)}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** "3 min ago" for UTC "YYYY-MM-DD HH:MM:SS" timestamps written by the platform. */
export function timeAgo(utc: string | null | undefined) {
  if (!utc) return "never";
  const at = Date.parse(utc.replace(" ", "T") + (utc.endsWith("Z") ? "" : "Z"));
  if (Number.isNaN(at)) return utc;
  const s = Math.max(0, (Date.now() - at) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

const TONE: Record<string, string> = {
  ok: "bg-success/10 text-success border-success/20",
  warn: "bg-warning/10 text-warning border-warning/30",
  fail: "bg-destructive/10 text-destructive border-destructive/20",
  skip: "bg-muted text-muted-foreground border-transparent",
  none: "bg-muted text-muted-foreground border-transparent",
};

export function HealthPill({ status, label }: { status: "ok" | "warn" | "fail" | null | undefined; label?: string }) {
  const text = label ?? (status === "ok" ? "Healthy" : status === "warn" ? "Needs attention" : status === "fail" ? "Unreachable" : "Not checked");
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium", TONE[status ?? "none"])}>
      <span className={cn("h-1.5 w-1.5 rounded-full", status === "ok" ? "bg-success" : status === "warn" ? "bg-warning"
        : status === "fail" ? "bg-destructive" : "bg-muted-foreground/50")} />
      {text}
    </span>
  );
}

function CheckIcon({ status }: { status: CheckStatus | "running" }) {
  if (status === "running") return <Loader2 className="h-4 w-4 animate-spin text-primary" />;
  if (status === "ok") return <CheckCircle2 className="h-4 w-4 text-success" />;
  if (status === "warn") return <AlertTriangle className="h-4 w-4 text-warning" />;
  if (status === "fail") return <XCircle className="h-4 w-4 text-destructive" />;
  return <MinusCircle className="h-4 w-4 text-muted-foreground/60" />;
}

const PLANNED = ["Connection details", "Network reaches the listener", "Login", "Account and password", "Database",
  "Schema objects", "Read data", "Optimizer statistics", "Column types", "Round-trip latency"];

/** The diagnostics as a checklist; while running, planned steps show as pending. Results are revealed in order. */
export function Checklist({ checks, running }: { checks: OracleCheck[] | null; running: boolean }) {
  const [shown, setShown] = useState(0);
  useEffect(() => {
    if (!checks) { setShown(0); return; }
    setShown(0);
    const id = setInterval(() => setShown((n) => (n >= checks.length ? n : n + 1)), 90);
    return () => clearInterval(id);
  }, [checks]);
  if (!checks) {
    return (
      <ol className="divide-y rounded-xl border">
        {PLANNED.map((label, i) => (
          <li key={label} className="flex items-center gap-3 px-4 py-2.5 text-sm">
            {running && i === 0 ? <CheckIcon status="running" /> : <CircleDashed className="h-4 w-4 text-muted-foreground/50" />}
            <span className={running ? "" : "text-muted-foreground"}>{label}</span>
          </li>
        ))}
      </ol>
    );
  }
  return (
    <ol className="divide-y rounded-xl border">
      {checks.slice(0, shown).map((c) => (
        <li key={c.id} className={cn("px-4 py-2.5 text-sm", c.status === "fail" && "bg-destructive/5", c.status === "warn" && "bg-warning/5")}>
          <div className="flex items-start gap-3">
            <span className="mt-0.5"><CheckIcon status={c.status} /></span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-medium">{c.label}</span>
                <span className="text-xs text-muted-foreground">{c.detail}</span>
                {c.code && <span className="rounded bg-muted px-1.5 font-mono text-[10px] text-muted-foreground">{c.code}</span>}
              </div>
              {c.fix && c.status !== "ok" && <p className="mt-0.5 text-xs">{c.fix}</p>}
              {c.error && c.status === "fail" && (
                <details className="mt-1 text-[11px] text-muted-foreground">
                  <summary className="cursor-pointer">Technical detail</summary>
                  <pre className="mt-1 whitespace-pre-wrap break-all font-mono">{c.error}</pre>
                </details>
              )}
            </div>
            {c.ms != null && c.status === "ok" && <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">{c.ms} ms</span>}
          </div>
        </li>
      ))}
      {shown < checks.length && (
        <li className="flex items-center gap-3 px-4 py-2.5 text-sm text-muted-foreground"><CheckIcon status="running" /> Checking…</li>
      )}
    </ol>
  );
}

export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: readonly (readonly [T, string])[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg border bg-muted/40 p-0.5">
      {options.map(([v, text]) => (
        <button key={v} type="button" role="radio" aria-checked={value === v} onClick={() => onChange(v)}
                className={cn("rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                  value === v ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>
          {text}
        </button>
      ))}
    </div>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <Button size="sm" variant="outline" type="button"
            onClick={() => { navigator.clipboard.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); }); }}>
      <Copy className="h-3.5 w-3.5" /> {done ? "Copied" : label}
    </Button>
  );
}

/** Problems with the connection fields before anything is sent. */
export function connectionProblems(c: OracleFields): string[] {
  const out: string[] = [];
  if (!c.host) out.push("host is required");
  else if (!/^[A-Za-z0-9.-]{1,253}$/.test(c.host)) out.push("host must be a host name or IP address, without scheme or port");
  const port = Number(c.port || 0);
  if (!(port > 0 && port < 65536)) out.push("port must be 1-65535");
  if (!(c.service_name || c.sid)) out.push("a service name or SID is required");
  if (!c.user) out.push("user is required");
  else if (!/^[A-Za-z][A-Za-z0-9_$#]{0,127}$/.test(c.user)) out.push("user must be an Oracle user name");
  if (c.schema_owner && !/^[A-Za-z][A-Za-z0-9_$#]{0,127}$/.test(c.schema_owner)) out.push("schema owner must be an Oracle schema name");
  return out;
}

/** Host, port, protocol, service/SID, user, owner, with a paste-a-connect-string helper and auto-correction of
 *  common slips (scheme or port typed into the host). */
export function ConnectionFields({ value, onChange, disabled }: {
  value: OracleFields; onChange: (v: OracleFields) => void; disabled?: boolean;
}) {
  const [paste, setPaste] = useState("");
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteError, setPasteError] = useState("");
  const [pending, start] = useTransition();
  const bySid = !!value.sid || (value.sid !== undefined && value.service_name === undefined);
  const tls = (value.protocol ?? "tcp") === "tcps";
  const set = (patch: OracleFields) => onChange({ ...value, ...patch });

  const setHost = (raw: string) => {
    let host = raw.trim().replace(/^(tcps?|https?|jdbc:oracle:thin:@?)\/*:?\/*/i, "");
    const patch: OracleFields = {};
    const withPort = host.match(/^([^:/]+):(\d{2,5})(?:\/(.+))?$/);
    if (withPort) {
      host = withPort[1];
      patch.port = withPort[2];
      if (withPort[3] && !value.sid) patch.service_name = withPort[3];
    }
    set({ ...patch, host });
  };

  const parse = () => start(async () => {
    setPasteError("");
    const r = await oracleParse(paste);
    if (!r.ok) { setPasteError(r.error); return; }
    const d = r.data;
    const next: OracleFields = { ...value, host: String(d.host), port: String(d.port), protocol: String(d.protocol ?? "tcp") };
    delete next.service_name; delete next.sid;
    if (d.service_name) next.service_name = String(d.service_name); else next.sid = String(d.sid);
    if (d.ssl_server_dn_match !== undefined) next.ssl_server_dn_match = d.ssl_server_dn_match ? "true" : "false";
    onChange(next);
    setPaste(""); setPasteOpen(false);
  });

  return (
    <fieldset disabled={disabled} className="space-y-4">
      <div className="rounded-xl border border-dashed p-3">
        {!pasteOpen ? (
          <button type="button" onClick={() => setPasteOpen(true)} className="flex items-center gap-2 text-xs font-medium text-primary">
            <ClipboardPaste className="h-3.5 w-3.5" /> Paste a connect string, JDBC URL or TNS entry instead
          </button>
        ) : (
          <div className="space-y-2">
            <div className="flex gap-2">
              <Input autoFocus value={paste} onChange={(e) => setPaste(e.target.value)} aria-label="Connect string"
                     placeholder="db.acme.com:1521/ORCLPDB1  ·  jdbc:oracle:thin:@//host:1521/svc  ·  (DESCRIPTION=…)"
                     className="font-mono text-xs" />
              <Button type="button" size="sm" disabled={!paste || pending} onClick={parse}>
                {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wand2 className="h-3.5 w-3.5" />} Fill in
              </Button>
            </div>
            <p className="text-[11px] text-muted-foreground">Any user name or password in the text is ignored; credentials are set in the next step.</p>
            {pasteError && <p className="text-xs text-destructive">{pasteError}</p>}
          </div>
        )}
      </div>

      <div className="grid gap-3 sm:grid-cols-[1fr_110px]">
        <label className="block text-xs font-medium">Host
          <Input value={value.host ?? ""} onChange={(e) => setHost(e.target.value)} placeholder="db.acme.com or 10.0.4.12" className="mt-1" />
        </label>
        <label className="block text-xs font-medium">Port
          <Input value={value.port ?? ""} inputMode="numeric" onChange={(e) => set({ port: e.target.value.replace(/[^0-9]/g, "") })}
                 placeholder={tls ? "1522" : "1521"} className="mt-1" />
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <div className="flex items-center gap-2 text-xs font-medium">
          Security
          <Segmented label="Protocol" value={tls ? "tcps" : "tcp"}
                     options={[["tcp", "TCP"], ["tcps", "TLS (TCPS)"]] as const}
                     onChange={(v) => set({ protocol: v, port: value.port === (v === "tcps" ? "1521" : "1522") || !value.port ? (v === "tcps" ? "1522" : "1521") : value.port })} />
        </div>
        {tls && (
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={(value.ssl_server_dn_match ?? "true") !== "false"}
                   onChange={(e) => set({ ssl_server_dn_match: e.target.checked ? "true" : "false" })} />
            Verify the server certificate matches the host
          </label>
        )}
      </div>
      {tls && (
        <p className="flex items-start gap-2 rounded-lg bg-muted/50 p-2 text-[11px] text-muted-foreground">
          <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" />
          Encrypted in transit. Oracle Autonomous Database works without a wallet when mutual TLS is not required (port 1522).
        </p>
      )}

      <div>
        <div className="mb-1 flex items-center gap-2 text-xs font-medium">
          Connect by
          <Segmented label="Connect by" value={bySid ? "sid" : "service"} options={[["service", "Service name"], ["sid", "SID"]] as const}
                     onChange={(v) => {
                       const next = { ...value };
                       delete next.service_name; delete next.sid;
                       onChange({ ...next, [v === "sid" ? "sid" : "service_name"]: "" });
                     }} />
        </div>
        <Input aria-label={bySid ? "SID" : "Service name"} value={(bySid ? value.sid : value.service_name) ?? ""}
               onChange={(e) => set({ [bySid ? "sid" : "service_name"]: e.target.value.trim() })}
               placeholder={bySid ? "ORCL" : "ORCLPDB1"} className="font-mono" />
        <p className="mt-1 text-[11px] text-muted-foreground">
          {bySid ? "The instance identifier, for older databases without service names." : "Pluggable databases and Autonomous Database use a service name."}
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-xs font-medium">User
          <Input value={value.user ?? ""} onChange={(e) => set({ user: e.target.value.trim().toUpperCase() })} placeholder="ETL_READER" className="mt-1 font-mono" />
          <span className="mt-1 block text-[11px] font-normal text-muted-foreground">A read-only user is enough: CREATE SESSION and SELECT on the tables.</span>
        </label>
        <label className="block text-xs font-medium">Schema owner
          <Input value={value.schema_owner ?? ""} onChange={(e) => set({ schema_owner: e.target.value.trim().toUpperCase() })}
                 placeholder={value.user || "HR"} className="mt-1 font-mono" />
          <span className="mt-1 block text-[11px] font-normal text-muted-foreground">Whose tables to read; defaults to the user.</span>
        </label>
      </div>
    </fieldset>
  );
}

export function SecretNote() {
  return (
    <p className="flex items-start gap-2 text-[11px] text-muted-foreground">
      <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      The password is never stored by the platform. It goes straight into a Snowflake secret, or stays in an environment
      variable on the API host.
    </p>
  );
}
