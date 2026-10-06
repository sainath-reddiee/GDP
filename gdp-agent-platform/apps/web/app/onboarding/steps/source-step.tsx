"use client";

import { Cloud, Database, FileSpreadsheet, Globe, HardDrive, Server, Snowflake, Workflow } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input, Label } from "@/components/ui/input";
import type { SourceDetails, SourceOrigin } from "../intent-types";
import type { SourceConnection } from "@/lib/types";
import { cn } from "@/lib/utils";

export const EXTERNAL_CONNECTORS = [
  { id: "postgres", label: "PostgreSQL", icon: Database },
  { id: "sqlserver", label: "SQL Server", icon: Server },
  { id: "oracle", label: "Oracle", icon: HardDrive },
  { id: "object_storage", label: "S3 / ADLS / GCS files", icon: Cloud },
  { id: "salesforce", label: "Salesforce", icon: Workflow },
  { id: "spreadsheet", label: "CSV / Excel upload", icon: FileSpreadsheet },
  { id: "rest_api", label: "REST API", icon: Globe },
] as const;

function OriginCard({
  active, onClick, icon: Icon, title, body, tag,
}: { active: boolean; onClick: () => void; icon: typeof Snowflake; title: string; body: string; tag?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "flex flex-1 items-start gap-3 rounded-xl border p-4 text-left transition-all",
        active ? "border-primary bg-primary/5 ring-2 ring-primary/30" : "hover:border-foreground/30 hover:bg-muted/40",
      )}
    >
      <span className={cn("rounded-lg p-2", active ? "bg-primary text-primary-foreground" : "bg-muted")}>
        <Icon className="h-5 w-5" />
      </span>
      <span className="min-w-0">
        <span className="flex items-center gap-2 text-sm font-semibold">{title}{tag && <Badge variant="outline" className="text-[10px]">{tag}</Badge>}</span>
        <span className="mt-0.5 block text-xs text-muted-foreground">{body}</span>
      </span>
    </button>
  );
}

export function SourceStep({
  runName, setRunName, origin, setOrigin, details, setDetails,
  connections = [], connectionId = "", onPickConnection,
}: {
  runName: string;
  setRunName: (v: string) => void;
  origin: SourceOrigin | "";
  setOrigin: (v: SourceOrigin) => void;
  details: SourceDetails;
  setDetails: (v: SourceDetails) => void;
  connections?: SourceConnection[];
  connectionId?: string;
  onPickConnection?: (c: SourceConnection) => void;
}) {
  return (
    <div className="space-y-5">
      <div>
        <Label htmlFor="run_name" className="mt-0">Run name</Label>
        <Input id="run_name" value={runName} onChange={(e) => setRunName(e.target.value)} placeholder="CRM customer onboard" maxLength={256} />
      </div>

      {connections.length > 0 && onPickConnection && (
        <div>
          <p className="mb-1 text-sm font-medium">Use a registered source</p>
          <p className="mb-2 text-xs text-muted-foreground">
            Registered once, reused by every run. Picking one fills in the catalog and schema for you.
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            {connections.map((c) => (
              <button
                key={c.source_system_id}
                type="button"
                aria-pressed={connectionId === c.source_system_id}
                onClick={() => onPickConnection(c)}
                className={cn(
                  "flex items-start gap-3 rounded-lg border px-3 py-2 text-left",
                  connectionId === c.source_system_id ? "border-primary bg-primary/5" : "hover:bg-muted/50",
                )}
              >
                <Snowflake className="mt-0.5 h-4 w-4 text-muted-foreground" />
                <span className="min-w-0">
                  <span className="block text-sm font-medium">{c.source_system_name}</span>
                  <span className="block truncate font-mono text-[11px] text-muted-foreground">
                    {c.database_name}.{c.schema_name}
                  </span>
                  <span className="block text-[11px] text-muted-foreground">
                    {c.runs} run{c.runs === 1 ? "" : "s"}{c.last_run_at ? `, last ${c.last_run_at.slice(0, 10)}` : ""}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      <div>
        <p className="mb-2 text-sm font-medium">Is this source Snowflake-native?</p>
        <div className="flex flex-col gap-3 sm:flex-row">
          <OriginCard
            active={origin === "snowflake"}
            onClick={() => setOrigin("snowflake")}
            icon={Snowflake}
            title="Yes, Snowflake-native"
            body="Usually a share. Snowflake databases this role can read also count."
          />
          <OriginCard
            active={origin === "external"}
            onClick={() => setOrigin("external")}
            icon={Globe}
            title="No, it's an external source"
            body="An operational DB, files, SaaS app or API outside Snowflake."
            tag="coming soon"
          />
        </div>
      </div>

      {origin === "external" && (
        <div className="rounded-xl border border-dashed p-4">
          <p className="text-sm font-semibold">External connector</p>
          <p className="text-xs text-muted-foreground">
            Placeholder: external sources will land into Snowflake first. You can save the plan, but runs can&apos;t be created yet.
          </p>
          <div className="mt-3 grid gap-2 sm:grid-cols-3">
            {EXTERNAL_CONNECTORS.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                type="button"
                onClick={() => setDetails({ ...details, external_connector: id })}
                className={cn(
                  "flex items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm",
                  details.external_connector === id ? "border-primary bg-primary/5" : "hover:bg-muted/50",
                )}
              >
                <Icon className="h-4 w-4 text-muted-foreground" /> {label}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
