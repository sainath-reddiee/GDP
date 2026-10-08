"use client";

import { useState } from "react";
import { BookOpen, Database, KeyRound, Network, PlugZap, Snowflake } from "lucide-react";
import { cn } from "@/lib/utils";
import { CopyButton } from "./oracle-ui";

type Section = "oracle" | "network" | "snowflake" | "connect";

function Code({ children }: { children: string }) {
  return (
    <div className="space-y-1">
      <pre className="max-h-64 overflow-auto whitespace-pre rounded-lg border bg-muted/50 p-2.5 font-mono text-[11px] leading-relaxed">{children}</pre>
      <CopyButton text={children} />
    </div>
  );
}

/** What has to be true outside the platform before the connection works, written for the people who do it
 *  (the Oracle DBA, the network team, the Snowflake admin), with the values from the form filled in. */
export function SetupGuide({ user, owner, host, port, tls, runtime, passwordEnv, sourceName, onClose }: {
  user: string; owner: string; host: string; port: string; tls: boolean; runtime: "snowflake" | "api_host";
  passwordEnv: string; sourceName: string; onClose?: () => void;
}) {
  const [section, setSection] = useState<Section>("oracle");
  const u = (user || "ETL_READER").toUpperCase();
  const o = (owner || user || "HR").toUpperCase();
  const h = host || "<oracle host>";
  const p = port || (tls ? "1522" : "1521");
  const env = passwordEnv || `ORACLE_${sourceName || "HR"}_PASSWORD`;
  const sameUser = u === o;

  const oracleSql = `-- Run as a DBA in the database (PDB) that holds the data
CREATE USER ${u} IDENTIFIED BY "<a strong password>";
GRANT CREATE SESSION TO ${u};
${sameUser ? `-- ${u} reads its own schema: no further grants needed.` : `
-- Oracle 23ai and later: read everything in ${o}
GRANT SELECT ANY TABLE ON SCHEMA ${o} TO ${u};

-- Oracle 12c to 21c: grant each table, view and materialized view
BEGIN
  FOR x IN (SELECT object_name FROM all_objects
             WHERE owner = '${o}'
               AND object_type IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW')) LOOP
    EXECUTE IMMEDIATE 'GRANT READ ON "${o}"."' || x.object_name || '" TO ${u}';
  END LOOP;
END;
/`}`;

  const sections: [Section, string, typeof Database][] = [
    ["oracle", "Oracle user", Database], ["network", "Network", Network],
    ["snowflake", runtime === "snowflake" ? "Snowflake" : "API server", runtime === "snowflake" ? Snowflake : KeyRound],
    ["connect", "Connect", PlugZap],
  ];

  return (
    <aside className="space-y-3 rounded-2xl border bg-card p-4 text-xs">
      <div className="flex items-center gap-2">
        <BookOpen className="h-4 w-4 text-primary" />
        <p className="text-sm font-semibold">Setup guide</p>
        {onClose && <button type="button" onClick={onClose} className="ml-auto text-[11px] text-muted-foreground hover:text-foreground">Hide</button>}
      </div>
      <ol className="grid grid-cols-4 gap-1">
        {sections.map(([id, label, Icon], i) => (
          <li key={id}>
            <button type="button" onClick={() => setSection(id)} aria-current={section === id ? "step" : undefined}
                    className={cn("flex w-full flex-col items-center gap-1 rounded-lg border px-1 py-1.5 text-[10px] font-medium",
                      section === id ? "border-primary bg-primary/5 text-primary" : "text-muted-foreground hover:bg-muted/50")}>
              <Icon className="h-3.5 w-3.5" /><span>{i + 1}. {label}</span>
            </button>
          </li>
        ))}
      </ol>

      {section === "oracle" && (
        <div className="space-y-2">
          <p><span className="font-medium">Who:</span> the Oracle DBA, once.</p>
          <p>A dedicated read-only user is all the platform needs: it never writes to Oracle. Reads use consistent snapshots and are tagged <span className="font-mono">AgenticPipeline</span> in V$SESSION, so the DBA can see them.</p>
          <Code>{oracleSql}</Code>
          <p className="text-muted-foreground">Tip: put the user on a profile whose password does not expire unexpectedly. The connection test warns 14 days before expiry anyway.</p>
        </div>
      )}

      {section === "network" && (
        <div className="space-y-2">
          <p><span className="font-medium">Who:</span> the network or cloud team.</p>
          {runtime === "snowflake" ? (
            <>
              <p>Snowflake connects out to <span className="font-mono">{h}:{p}</span>. The address must be reachable from Snowflake:</p>
              <ul className="list-disc space-y-1 pl-4">
                <li>A public endpoint (or public IP) for the listener, with the firewall allowing Snowflake&apos;s outbound addresses for this account. Ask Snowflake support for the egress ranges of the account&apos;s region.</li>
                <li>Or private connectivity (AWS PrivateLink / Azure Private Link) to the database network.</li>
                <li>Oracle Autonomous Database: use TLS on 1522 and add Snowflake to the database&apos;s access control list.</li>
              </ul>
              <p>Snowflake only allows this one host and port (a network rule); nothing else is opened.</p>
            </>
          ) : (
            <>
              <p>The API server connects to <span className="font-mono">{h}:{p}</span>, so it only needs the same network access your own tools have: office network, VPN or a peered VPC.</p>
              <p>Quick check from the API server:</p>
              <Code>{`# PowerShell\nTest-NetConnection ${h} -Port ${p}\n\n# Linux / macOS\nnc -vz ${h} ${p}`}</Code>
            </>
          )}
          {tls && <p className="rounded-lg bg-success/5 p-2">TLS is on: traffic is encrypted end to end. Plain TCP listeners usually use 1521, TLS listeners 1522 or 2484.</p>}
        </div>
      )}

      {section === "snowflake" && runtime === "snowflake" && (
        <div className="space-y-2">
          <p><span className="font-medium">Who:</span> a Snowflake admin, once per account.</p>
          <p>When you choose <span className="font-medium">Enter the password</span>, the wizard creates for this source:</p>
          <ul className="list-disc space-y-0.5 pl-4">
            <li>a PASSWORD secret holding the Oracle login (the password goes straight into it),</li>
            <li>a network rule allowing only {h}:{p}, and an external access integration,</li>
            <li>the procedure that reads Oracle (runs as its owner, uses the secret).</li>
          </ul>
          <p>The platform role needs CREATE SECRET and CREATE NETWORK RULE in the platform database (normally already granted) and CREATE INTEGRATION on the account. If it lacks them, the wizard shows the exact SQL for an admin. To let the role create integrations itself:</p>
          <Code>{"-- as ACCOUNTADMIN\nGRANT CREATE INTEGRATION ON ACCOUNT TO ROLE <platform role>;"}</Code>
          <p>The procedure uses the <span className="font-mono">oracledb</span> and <span className="font-mono">pyarrow</span> packages from the Snowflake Anaconda channel; an ORGADMIN accepts the Anaconda terms once (Admin &gt; Billing &amp; Terms).</p>
          <p className="text-muted-foreground">Prefer your own objects? Choose <span className="font-medium">Use an existing secret</span> and <span className="font-medium">Use an existing integration</span>; the wizard checks they allow this host and secret.</p>
        </div>
      )}

      {section === "snowflake" && runtime === "api_host" && (
        <div className="space-y-2">
          <p><span className="font-medium">Who:</span> whoever runs the API server.</p>
          <p>The password is read from the environment variable <span className="font-mono">{env}</span> of the API process. It is never stored by the platform. Set it where the API starts, then restart the API:</p>
          <Code>{`# PowerShell, in the window that starts the API\n$env:${env} = "<the Oracle password>"\n\n# Linux / macOS\nexport ${env}='<the Oracle password>'`}</Code>
          <p className="text-muted-foreground">For a service, set it in the service definition or your secret manager. Use the Check button in step 2 to confirm the API can see it (only its presence is checked, never its value).</p>
        </div>
      )}

      {section === "connect" && (
        <div className="space-y-2">
          <p><span className="font-medium">Who:</span> you, in this wizard.</p>
          <ol className="list-decimal space-y-1 pl-4">
            <li><span className="font-medium">Connection:</span> host, port, service name (or SID), the read-only user and the schema owner. Pasting a connect string fills these in.</li>
            <li><span className="font-medium">Access:</span> {runtime === "snowflake"
              ? <>choose <span className="font-medium">Enter the password</span> and type it (it goes straight into a new Snowflake secret), or pick an existing secret.</>
              : <>name the environment variable that holds the password and press Check.</>}</li>
            <li><span className="font-medium">Verify:</span> the platform runs ten checks (network, login, password expiry, privileges, statistics, column types, latency) and explains any failure with its fix.</li>
          </ol>
          <p>Then browse tables, preview data, profile in Oracle, and land into Snowflake with full refresh, append or merge.</p>
        </div>
      )}
    </aside>
  );
}
