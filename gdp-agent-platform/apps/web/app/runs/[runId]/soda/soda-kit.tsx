"use client";

import { useState } from "react";
import { Check, Copy, Download } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";

const ORDER = ["README.md", "configuration.yml", "checks.yml", "ds_config.yml", "contract.yaml", "sc_config.yml", ".github/workflows/soda-scan.yml"];
const ABOUT: Record<string, string> = {
  "configuration.yml": "Soda v3 data source, credentials as ${VAR}",
  "checks.yml": "SodaCL v3 checks",
  "ds_config.yml": "Soda v4 data source, credentials as ${env.VAR}",
  "contract.yaml": "Soda v4 data contract",
  "sc_config.yml": "Soda Cloud keys (optional)",
  ".github/workflows/soda-scan.yml": "CI: scan on push and nightly",
  "README.md": "Setup and troubleshooting",
};

type Flavour = "v3" | "v4";
const STEPS: Record<Flavour, { title: string; cmd: string; note?: string }[]> = {
  v3: [
    { title: "Install", cmd: "pip install soda-core-snowflake", note: "Soda Library users: pip install -i https://pypi.cloud.soda.io soda-snowflake" },
    { title: "Set credentials", cmd: "export SNOWFLAKE_USER=...\nexport SNOWFLAKE_PASSWORD=...", note: "PowerShell: $env:SNOWFLAKE_USER = \"...\". Never put secrets in the YAML." },
    { title: "Validate the connection", cmd: "soda test-connection -d snowflake_dq -c configuration.yml -V" },
    { title: "Run the scan", cmd: "soda scan -d snowflake_dq -c configuration.yml checks.yml" },
    { title: "Keep a result file", cmd: "soda scan -d snowflake_dq -c configuration.yml -srf results.json checks.yml" },
  ],
  v4: [
    { title: "Install", cmd: "pip install soda-snowflake" },
    { title: "Set credentials", cmd: "export SNOWFLAKE_USER=...\nexport SNOWFLAKE_PASSWORD=...", note: "ds_config.yml reads them as ${env.SNOWFLAKE_USER}." },
    { title: "Validate the connection", cmd: "soda data-source test -ds ds_config.yml" },
    { title: "Verify the contract", cmd: "soda contract verify --data-source ds_config.yml --contract contract.yaml", note: "Exit codes: 0 pass, 1 fail, 2 warnings only, 3 could not run, 4 could not reach Soda Cloud." },
    { title: "Publish to Soda Cloud (optional)", cmd: "soda cloud test -sc sc_config.yml\nsoda contract publish --contract contract.yaml --soda-cloud sc_config.yml" },
  ],
};

const EDGES = [
  ["Authentication failed", "Check SNOWFLAKE_USER and SNOWFLAKE_PASSWORD, or the private key path and passphrase for key-pair auth (commented in the config)."],
  ["Object does not exist or not authorized", "The role needs USAGE on the database and schema and SELECT on the table, or the model has not been built yet."],
  ["No active warehouse", "Grant the role USAGE on the warehouse named in the config."],
  ["Network policy blocks the IP", "Run from an allowed network or ask the Snowflake admin to allow the runner's IP (CI runners change IPs)."],
  ["Invalid identifier", "Snowflake stores names upper case; quoted mixed-case columns must match exactly."],
  ["change for row_count is rejected", "Change-over-time checks need Soda Library with Soda Cloud history; Soda Core open source skips them."],
];

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <Button type="button" size="sm" variant="ghost" aria-label="Copy"
            onClick={() => { navigator.clipboard?.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500); }}>
      {done ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
    </Button>
  );
}

export function SodaKit({ runId, files }: { runId: string; files: Record<string, string> }) {
  const names = ORDER.filter((n) => n in files).concat(Object.keys(files).filter((n) => !ORDER.includes(n)));
  const [file, setFile] = useState(names.includes("checks.yml") ? "checks.yml" : names[0]);
  const [flavour, setFlavour] = useState<Flavour>("v3");
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg border bg-muted/30 p-4">
        <div className="max-w-2xl space-y-1 text-sm">
          <p className="font-medium">Run these checks in your own Snowflake account</p>
          <p className="text-muted-foreground">
            The kit holds the approved and proposed checks (rejected ones are left out) for both Soda v3 (SodaCL) and Soda v4
            (data contracts), plus a CI workflow. Account, warehouse, role and table are filled in; every secret is an
            environment variable placeholder.
          </p>
        </div>
        <a href={`/bff/soda-kit/${runId}`} download className={buttonVariants()}>
          <Download className="h-4 w-4" />Download kit (.zip)
        </a>
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="space-y-3">
          <div role="tablist" className="inline-flex rounded-md border p-0.5">
            {(["v3", "v4"] as const).map((f) => (
              <button key={f} type="button" role="tab" aria-selected={flavour === f} onClick={() => setFlavour(f)}
                      className={`rounded px-3 py-1 text-xs font-medium ${flavour === f ? "bg-primary text-primary-foreground" : "hover:bg-muted"}`}>
                {f === "v3" ? "Soda v3 · checks.yml" : "Soda v4 · contract"}
              </button>
            ))}
          </div>
          <ol className="space-y-3">
            {STEPS[flavour].map((s, i) => (
              <li key={s.title} className="flex gap-3">
                <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">{i + 1}</span>
                <div className="min-w-0 flex-1 space-y-1">
                  <p className="text-sm font-medium">{s.title}</p>
                  <div className="flex items-start gap-1 rounded-md bg-muted/50">
                    <pre className="min-w-0 flex-1 overflow-x-auto p-2 font-mono text-xs">{s.cmd}</pre>
                    <CopyButton text={s.cmd} />
                  </div>
                  {s.note && <p className="text-xs text-muted-foreground">{s.note}</p>}
                </div>
              </li>
            ))}
          </ol>
          <details className="rounded-lg border p-3 text-sm">
            <summary className="cursor-pointer font-medium">Troubleshooting and edge cases</summary>
            <dl className="mt-3 space-y-2">
              {EDGES.map(([t, d]) => (
                <div key={t}>
                  <dt className="text-xs font-semibold">{t}</dt>
                  <dd className="text-xs text-muted-foreground">{d}</dd>
                </div>
              ))}
            </dl>
          </details>
        </div>

        <div className="min-w-0 overflow-hidden rounded-lg border">
          <div className="flex flex-wrap gap-1 border-b bg-muted/40 p-1">
            {names.map((n) => (
              <button key={n} type="button" onClick={() => setFile(n)} title={ABOUT[n]}
                      className={`rounded px-2 py-1 font-mono text-[11px] ${file === n ? "bg-background shadow-sm" : "text-muted-foreground hover:bg-background/60"}`}>
                {n.split("/").pop()}
              </button>
            ))}
          </div>
          <div className="flex items-center justify-between border-b px-3 py-1.5">
            <span className="text-xs text-muted-foreground">{ABOUT[file] ?? file}</span>
            <CopyButton text={files[file] ?? ""} />
          </div>
          <pre className="max-h-[560px] overflow-auto p-3 font-mono text-xs leading-relaxed">{files[file]}</pre>
        </div>
      </div>
    </div>
  );
}
