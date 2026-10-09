"use client";

import { useMemo, useState } from "react";
import { Input, Label, Select, Textarea } from "@/components/ui/input";

/** One entry per check kind the platform runs and renders to SodaCL. */
export const KINDS: { kind: string; label: string; group: string; column: "required" | "optional" | "none"; hint: string }[] = [
  { kind: "row_count", label: "Row count", group: "Volume", column: "none", hint: "The dataset is not empty, or stays inside a range." },
  { kind: "change_over_time", label: "Row count change", group: "Volume", column: "none", hint: "Row count moves less than a percentage since the last scan (Soda Library)." },
  { kind: "not_null", label: "Missing values", group: "Completeness", column: "required", hint: "No missing values; add your own missing markers like N/A." },
  { kind: "missing_percent", label: "Missing percent", group: "Completeness", column: "required", hint: "Up to a percentage of rows may be missing." },
  { kind: "unique", label: "Duplicates", group: "Uniqueness", column: "optional", hint: "No duplicates on one column or a combination." },
  { kind: "duplicate_percent", label: "Duplicate percent", group: "Uniqueness", column: "required", hint: "Up to a percentage of duplicate values." },
  { kind: "accepted_values", label: "Valid values", group: "Validity", column: "required", hint: "Only values from a list." },
  { kind: "regex", label: "Valid regex", group: "Validity", column: "required", hint: "Values match a regular expression." },
  { kind: "format", label: "Valid format", group: "Validity", column: "required", hint: "Built-in Soda formats such as email or uuid." },
  { kind: "range", label: "Valid range", group: "Validity", column: "required", hint: "Numbers inside a minimum and maximum." },
  { kind: "max_length", label: "Max length", group: "Validity", column: "required", hint: "Text no longer than a length." },
  { kind: "avg", label: "Average", group: "Numeric metrics", column: "required", hint: "avg(column) meets a threshold." },
  { kind: "min", label: "Minimum", group: "Numeric metrics", column: "required", hint: "min(column) meets a threshold." },
  { kind: "max", label: "Maximum", group: "Numeric metrics", column: "required", hint: "max(column) meets a threshold." },
  { kind: "sum", label: "Sum", group: "Numeric metrics", column: "required", hint: "sum(column) meets a threshold." },
  { kind: "stddev", label: "Standard deviation", group: "Numeric metrics", column: "required", hint: "stddev(column) meets a threshold." },
  { kind: "freshness", label: "Freshness", group: "Timeliness", column: "optional", hint: "The newest timestamp is recent enough." },
  { kind: "schema", label: "Schema", group: "Schema", column: "none", hint: "Required, forbidden and typed columns." },
  { kind: "reference", label: "Reference", group: "Consistency", column: "required", hint: "Values exist in another table." },
  { kind: "failed_rows", label: "Failed rows (SQL)", group: "Custom SQL", column: "none", hint: "Rows matching a condition or query are failures." },
  { kind: "metric", label: "User-defined metric", group: "Custom SQL", column: "none", hint: "Your own SQL aggregate with a threshold." },
];
const FORMATS = ["email", "phone number", "uuid", "credit card number", "ipv4", "date eu", "date us", "date iso 8601"];
const OPS = ["<", "<=", ">", ">=", "=", "!="];

export type Draft = { target_column: string; severity: "FAIL" | "WARN"; requirement: string; definition: Record<string, unknown> };

const list = (v: unknown) => (Array.isArray(v) ? v.map(String).join(", ") : "");
const split = (v: string) => v.split(",").map((s) => s.trim()).filter(Boolean);
const num = (v: string) => (v.trim() === "" || Number.isNaN(Number(v)) ? undefined : Number(v));

function Threshold({ value, onChange }: { value: unknown; onChange: (t: Record<string, unknown>) => void }) {
  const t = (value && typeof value === "object" ? value : { op: "<", value: "" }) as Record<string, unknown>;
  const between = Array.isArray(t.between);
  const b = (t.between as number[] | undefined) ?? ["", ""];
  return (
    <div className="flex flex-wrap items-end gap-2">
      <div className="space-y-1">
        <Label>Threshold</Label>
        <Select value={between ? "between" : String(t.op ?? "<")}
                onChange={(e) => onChange(e.target.value === "between" ? { between: [b[0] ?? 0, b[1] ?? 0] } : { op: e.target.value, value: t.value ?? 0 })}>
          {OPS.map((o) => <option key={o} value={o}>{o}</option>)}
          <option value="between">between</option>
        </Select>
      </div>
      {between ? (
        <>
          <Input className="w-28" type="number" aria-label="Low" value={String(b[0] ?? "")}
                 onChange={(e) => onChange({ between: [num(e.target.value) ?? 0, b[1]] })} />
          <span className="pb-2 text-xs text-muted-foreground">and</span>
          <Input className="w-28" type="number" aria-label="High" value={String(b[1] ?? "")}
                 onChange={(e) => onChange({ between: [b[0], num(e.target.value) ?? 0] })} />
        </>
      ) : (
        <Input className="w-32" type="number" aria-label="Value" value={String(t.value ?? "")}
               onChange={(e) => onChange({ op: t.op ?? "<", value: num(e.target.value) ?? 0 })} />
      )}
    </div>
  );
}

/** Guided fields for one check. The kind can only be changed when adding a new check. */
export function CheckEditor({
  draft, onChange, columns, kindLocked = false,
}: {
  draft: Draft;
  onChange: (d: Draft) => void;
  columns: string[];
  kindLocked?: boolean;
}) {
  const d = draft.definition;
  const kind = String(d.kind ?? "row_count");
  const meta = KINDS.find((k) => k.kind === kind) ?? KINDS[0];
  const set = (patch: Record<string, unknown>) => onChange({ ...draft, definition: { ...d, ...patch } });
  const [typesText, setTypesText] = useState(() =>
    Object.entries((d.types as Record<string, string>) ?? {}).map(([c, t]) => `${c}: ${t}`).join("\n"));
  const groups = useMemo(() => Array.from(new Set(KINDS.map((k) => k.group))), []);
  const [sqlMode, setSqlMode] = useState<"condition" | "query">(d.query ? "query" : kind === "metric" ? "condition" : "condition");

  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-3">
        <div className="space-y-1">
          <Label htmlFor="ce_kind">Check type</Label>
          <Select id="ce_kind" value={kind} disabled={kindLocked}
                  onChange={(e) => onChange({ ...draft, definition: { kind: e.target.value } })}>
            {groups.map((g) => (
              <optgroup key={g} label={g}>
                {KINDS.filter((k) => k.group === g).map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}
              </optgroup>
            ))}
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="ce_col">Column{meta.column === "optional" ? " (optional)" : ""}</Label>
          <Input id="ce_col" list="ce_columns" disabled={meta.column === "none" || kindLocked}
                 value={meta.column === "none" ? "" : draft.target_column}
                 placeholder={meta.column === "none" ? "Dataset level" : "COLUMN_NAME"}
                 onChange={(e) => onChange({ ...draft, target_column: e.target.value.toUpperCase() })} />
          <datalist id="ce_columns">{columns.map((c) => <option key={c} value={c} />)}</datalist>
        </div>
        <div className="space-y-1">
          <Label htmlFor="ce_sev">Severity</Label>
          <Select id="ce_sev" value={draft.severity} onChange={(e) => onChange({ ...draft, severity: e.target.value as Draft["severity"] })}>
            <option value="FAIL">Fail (blocks)</option>
            <option value="WARN">Warn (alerts)</option>
          </Select>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">{meta.hint}</p>

      {kind === "row_count" && (
        <div className="flex flex-wrap gap-3">
          <Field label="More than" value={d.gt} onChange={(v) => set({ gt: num(v), min: undefined, max: undefined })} />
          <Field label="or between min" value={d.min} onChange={(v) => set({ min: num(v) })} />
          <Field label="and max" value={d.max} onChange={(v) => set({ max: num(v) })} />
        </div>
      )}
      {kind === "change_over_time" && (
        <div className="flex flex-wrap gap-3">
          <Field label="Max decrease %" value={d.max_decrease_percent ?? 20} onChange={(v) => set({ max_decrease_percent: num(v) })} />
          <Field label="Max increase %" value={d.max_increase_percent ?? 50} onChange={(v) => set({ max_increase_percent: num(v) })} />
        </div>
      )}
      {kind === "not_null" && (
        <Field wide label="Also treat as missing (comma separated)" text value={list(d.missing_values)}
               onChange={(v) => set({ missing_values: split(v).length ? split(v) : undefined })} />
      )}
      {(kind === "missing_percent" || kind === "duplicate_percent") && (
        <Field label="Max percent" value={d.max_percent ?? 1} onChange={(v) => set({ max_percent: num(v) })} />
      )}
      {kind === "unique" && (
        <Field wide label="Columns that must be unique together (comma separated)" text
               value={list(d.columns) || draft.target_column}
               onChange={(v) => set({ columns: split(v.toUpperCase()) })} />
      )}
      {kind === "accepted_values" && (
        <Field wide label="Valid values (comma separated)" text value={list(d.values)} onChange={(v) => set({ values: split(v) })} />
      )}
      {kind === "regex" && (
        <div className="flex flex-wrap gap-3">
          <Field wide label="Pattern" text mono value={d.pattern} onChange={(v) => set({ pattern: v })} />
          <Field label="Allowed invalid %" value={d.max_invalid_percent} onChange={(v) => set({ max_invalid_percent: num(v) })} />
        </div>
      )}
      {kind === "format" && (
        <div className="space-y-1">
          <Label>Format</Label>
          <Select value={String(d.format ?? "email")} onChange={(e) => set({ format: e.target.value })}>
            {FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}
          </Select>
        </div>
      )}
      {kind === "range" && (
        <div className="flex flex-wrap gap-3">
          <Field label="Min" value={d.min} onChange={(v) => set({ min: num(v) })} />
          <Field label="Max" value={d.max} onChange={(v) => set({ max: num(v) })} />
        </div>
      )}
      {kind === "max_length" && <Field label="Max length" value={d.max} onChange={(v) => set({ max: num(v) })} />}
      {["avg", "min", "max", "sum", "stddev"].includes(kind) && (
        <Threshold value={d.threshold} onChange={(t) => set({ threshold: t })} />
      )}
      {kind === "freshness" && (
        <Field label="Newer than (30m, 6h, 1d)" text value={d.threshold ?? "1d"} onChange={(v) => set({ threshold: v.trim() })} />
      )}
      {kind === "schema" && (
        <div className="grid gap-3 md:grid-cols-2">
          <Field wide label="Required columns" text value={list(d.required)} onChange={(v) => set({ required: split(v.toUpperCase()) })} />
          <Field wide label="Forbidden columns" text value={list(d.forbidden)} onChange={(v) => set({ forbidden: split(v.toUpperCase()) })} />
          <div className="space-y-1 md:col-span-2">
            <Label>Column types (one per line, COLUMN: TYPE)</Label>
            <Textarea rows={3} className="font-mono text-xs" value={typesText}
                      onChange={(e) => {
                        setTypesText(e.target.value);
                        const types: Record<string, string> = {};
                        for (const line of e.target.value.split("\n")) {
                          const [c, t] = line.split(":").map((s) => s.trim());
                          if (c && t) types[c.toUpperCase()] = t.toUpperCase();
                        }
                        set({ types: Object.keys(types).length ? types : undefined });
                      }} />
          </div>
        </div>
      )}
      {kind === "reference" && (
        <div className="flex flex-wrap gap-3">
          <Field wide label="Reference table (DATABASE.SCHEMA.TABLE)" text mono value={d.reference_table}
                 onChange={(v) => set({ reference_table: v.trim().toUpperCase() })} />
          <Field label="Reference column" text mono value={d.reference_column}
                 onChange={(v) => set({ reference_column: v.trim().toUpperCase() || undefined })} />
        </div>
      )}
      {(kind === "failed_rows" || kind === "metric") && (
        <div className="space-y-2">
          <div role="tablist" className="flex gap-1">
            {(["condition", "query"] as const).map((m) => (
              <button key={m} type="button" role="tab" aria-selected={sqlMode === m}
                      onClick={() => {
                        setSqlMode(m);
                        const key = kind === "metric" ? "expression" : "condition";
                        set(m === "query" ? { [key]: undefined } : { query: undefined });
                      }}
                      className={`rounded-md px-3 py-1 text-xs font-medium ${sqlMode === m ? "bg-primary text-primary-foreground" : "hover:bg-muted"}`}>
                {m === "query" ? "SQL query" : kind === "metric" ? "Expression" : "Fail condition"}
              </button>
            ))}
          </div>
          {kind === "metric" && (
            <Field label="Metric name" text value={d.name ?? "custom metric"} onChange={(v) => set({ name: v })} />
          )}
          {sqlMode === "condition" ? (
            <Field wide label={kind === "metric" ? "Aggregate expression, e.g. AVG(TOTAL - DISCOUNT)" : "Rows where this is true fail, e.g. TOTAL < 0"}
                   text mono value={kind === "metric" ? d.expression : d.condition}
                   onChange={(v) => set(kind === "metric" ? { expression: v } : { condition: v })} />
          ) : (
            <div className="space-y-1">
              <Label>{kind === "metric" ? "Query returning one number" : "Query returning the failing rows"}</Label>
              <Textarea rows={5} className="font-mono text-xs" value={String(d.query ?? "")} onChange={(e) => set({ query: e.target.value })}
                        placeholder="SELECT * FROM DB.SCHEMA.TABLE WHERE ..." />
              <p className="text-xs text-muted-foreground">One read-only SELECT. Writes, DDL and multiple statements are refused.</p>
            </div>
          )}
          {kind === "failed_rows" && <Field label="Allowed failing rows" value={d.max_failed} onChange={(v) => set({ max_failed: num(v) })} />}
          {kind === "metric" && <Threshold value={d.threshold} onChange={(t) => set({ threshold: t })} />}
        </div>
      )}

      <div className="space-y-1">
        <Label htmlFor="ce_need">Business need</Label>
        <Textarea id="ce_need" rows={2} value={draft.requirement} onChange={(e) => onChange({ ...draft, requirement: e.target.value })}
                  placeholder="What this check protects; shown as the check name in SodaCL" />
      </div>
    </div>
  );
}

function Field({ label, value, onChange, text = false, mono = false, wide = false }: {
  label: string; value: unknown; onChange: (v: string) => void; text?: boolean; mono?: boolean; wide?: boolean;
}) {
  return (
    <div className={`space-y-1 ${wide ? "min-w-[16rem] flex-1" : ""}`}>
      <Label>{label}</Label>
      <Input type={text ? "text" : "number"} className={`${mono ? "font-mono text-xs" : ""} ${wide ? "" : "w-36"}`}
             value={value === undefined || value === null ? "" : String(value)} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

export function draftFrom(check?: { target_column: string | null; severity: string; client_requirement: string | null;
                                    check_definition?: Record<string, unknown> | string | null }): Draft {
  let def: Record<string, unknown> = { kind: "row_count", gt: 0 };
  if (check?.check_definition) {
    def = typeof check.check_definition === "string" ? JSON.parse(check.check_definition) : { ...check.check_definition };
  }
  return {
    target_column: check?.target_column ?? "",
    severity: check?.severity === "WARN" ? "WARN" : "FAIL",
    requirement: check?.client_requirement ?? "",
    definition: def,
  };
}

/** Drops empty values so the stored definition stays clean. */
export function cleanDefinition(def: Record<string, unknown>) {
  return Object.fromEntries(Object.entries(def).filter(([, v]) => v !== undefined && v !== "" && !(Array.isArray(v) && v.length === 0)));
}
