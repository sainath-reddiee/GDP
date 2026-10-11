"use client";

import { usePathname, useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import { GitCompareArrows, Loader2, Pencil, RotateCcw, Save } from "lucide-react";
import type { SttmDiff, SttmEntry, SttmLine, SttmVersion } from "@/lib/hub-types";
import { ErrorNote } from "@/components/hub/states";
import { Button } from "@/components/ui/button";
import { Input, Select, Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { compare, saveEdits, type SttmEdit } from "../actions";

const TYPES = ["DIRECT", "TRANSFORM", "DERIVED", "LOOKUP", "CONSTANT", "UNMAPPED"];
type Draft = Partial<Pick<SttmLine, "transformation" | "mapping_type" | "business_rule">>;

export function SttmEditor({ entry, versions, latest }: { entry: SttmEntry; versions: SttmVersion[]; latest: number }) {
  const router = useRouter();
  const path = usePathname();
  const editable = entry.version === latest;
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [diff, setDiff] = useState<SttmDiff | null>(null);
  const [from, setFrom] = useState(versions[1]?.version ?? entry.version);
  const [to, setTo] = useState(entry.version);
  const [pending, start] = useTransition();

  const lines = useMemo(() => entry.lines.filter((l) =>
    !filter || `${l.target_column} ${l.source_table ?? ""} ${l.source_column ?? ""}`.toLowerCase().includes(filter.toLowerCase())), [entry.lines, filter]);
  const changed = Object.keys(drafts).filter((k) => Object.keys(drafts[k]).length);
  const value = <K extends keyof Draft>(l: SttmLine, k: K) => (drafts[l.target_column]?.[k] !== undefined ? drafts[l.target_column]![k] : l[k]) ?? "";
  const set = (l: SttmLine, k: keyof Draft, v: string) => setDrafts((d) => ({ ...d, [l.target_column]: { ...d[l.target_column], [k]: v } }));

  const save = () => start(async () => {
    setError(null);
    const edits: SttmEdit[] = changed.map((c) => ({ target_column: c, ...drafts[c] } as SttmEdit));
    const r = await saveEdits(entry.catalog_id, entry.version, edits, note);
    if (!r.ok) return setError(r.error);
    setDrafts({}); setNote(""); setEditing(null);
    router.push(`${path}?version=${r.data.version}`);
  });
  const runCompare = () => start(async () => {
    setError(null);
    const r = await compare(entry.catalog_id, from, to);
    if (r.ok) setDiff(r.data); else setError(r.error);
  });

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_20rem]">
      <div className="min-w-0 space-y-3">
        {error && <ErrorNote>{error}</ErrorNote>}
        {!editable && (
          <p className="rounded-lg bg-muted px-3 py-2 text-sm text-muted-foreground">
            Viewing v{entry.version}. Open the latest version (v{latest}) to edit.
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Input aria-label="Filter lines" placeholder="Filter columns" className="max-w-xs" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <span className="text-xs text-muted-foreground">{entry.lines.length} lines{entry.change_note ? ` · ${entry.change_note}` : ""}</span>
          {changed.length > 0 && (
            <div className="ml-auto flex items-center gap-2">
              <Input aria-label="Change note" placeholder="What changed and why" className="w-64" value={note} onChange={(e) => setNote(e.target.value)} />
              <Button variant="ghost" size="sm" onClick={() => setDrafts({})}><RotateCcw className="h-3.5 w-3.5" /> Discard</Button>
              <Button size="sm" onClick={save} disabled={pending}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Save {changed.length} as v{latest + 1}</Button>
            </div>
          )}
        </div>
        <div className="surface overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted/60 text-left text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              <tr><th className="px-3 py-2">Target column</th><th className="px-3 py-2">Source</th><th className="px-3 py-2">Type</th><th className="px-3 py-2">Transformation</th><th className="px-3 py-2">Business rule</th><th /></tr>
            </thead>
            <tbody>
              {lines.map((l) => {
                const open = editing === l.target_column;
                const dirty = !!drafts[l.target_column];
                return (
                  <tr key={l.target_column} className={cn("border-t border-border/60 align-top", dirty && "bg-amber-50/50")}>
                    <td className="px-3 py-2"><div className="font-mono text-xs font-medium">{l.target_column}</div><div className="text-[11px] text-muted-foreground">{l.target_datatype}</div></td>
                    <td className="px-3 py-2 font-mono text-[11px]">{l.source_table ? `${l.source_table}.${l.source_column ?? ""}` : <span className="text-muted-foreground">none</span>}</td>
                    <td className="px-3 py-2">
                      {open ? (
                        <Select aria-label="Mapping type" className="h-8 w-32 text-xs" value={String(value(l, "mapping_type"))} onChange={(e) => set(l, "mapping_type", e.target.value)}>
                          {TYPES.map((t) => <option key={t}>{t}</option>)}
                        </Select>
                      ) : <span className="text-xs">{String(value(l, "mapping_type")).toLowerCase()}</span>}
                    </td>
                    <td className="min-w-[13rem] px-3 py-2">
                      {open ? <Textarea aria-label="Transformation" className="min-h-16 font-mono text-xs" value={String(value(l, "transformation"))} onChange={(e) => set(l, "transformation", e.target.value)} />
                        : <code className="whitespace-pre-wrap break-all text-[11px]">{String(value(l, "transformation")) || <span className="text-muted-foreground">as is</span>}</code>}
                    </td>
                    <td className="min-w-[9rem] px-3 py-2">
                      {open ? <Input aria-label="Business rule" className="text-xs" value={String(value(l, "business_rule"))} onChange={(e) => set(l, "business_rule", e.target.value)} />
                        : <span className="text-xs text-muted-foreground">{String(value(l, "business_rule")) || "-"}</span>}
                    </td>
                    <td className="px-2 py-2">
                      {editable && (
                        <button aria-label={`Edit ${l.target_column}`} onClick={() => setEditing(open ? null : l.target_column)} className="rounded-md p-1 text-muted-foreground hover:bg-muted">
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <aside className="space-y-4">
        <section className="surface p-4">
          <h3 className="text-sm font-semibold">Version history</h3>
          <ol className="mt-3 space-y-2">
            {versions.map((v) => (
              <li key={v.version}>
                <button onClick={() => router.push(`${path}?version=${v.version}`)}
                        className={cn("w-full rounded-lg border px-3 py-2 text-left text-xs transition",
                          v.version === entry.version ? "border-primary/40 bg-accent" : "border-border/70 hover:bg-muted/50")}>
                  <span className="font-semibold">v{v.version}</span>
                  <span className="ml-2 text-muted-foreground">{v.origin.toLowerCase().replace(/_/g, " ")}</span>
                  <span className="block truncate text-muted-foreground">{v.change_note ?? `${v.line_count} lines`}</span>
                  <span className="block text-[10px] text-muted-foreground">{v.created_by} · {v.created_at.slice(0, 16)}</span>
                </button>
              </li>
            ))}
          </ol>
        </section>
        {versions.length > 1 && (
          <section className="surface p-4">
            <h3 className="text-sm font-semibold">Compare versions</h3>
            <div className="mt-3 flex items-center gap-2">
              <Select aria-label="From version" className="h-8 text-xs" value={from} onChange={(e) => setFrom(Number(e.target.value))}>
                {versions.map((v) => <option key={v.version} value={v.version}>v{v.version}</option>)}
              </Select>
              <span className="text-xs text-muted-foreground">to</span>
              <Select aria-label="To version" className="h-8 text-xs" value={to} onChange={(e) => setTo(Number(e.target.value))}>
                {versions.map((v) => <option key={v.version} value={v.version}>v{v.version}</option>)}
              </Select>
              <Button size="sm" variant="outline" onClick={runCompare} disabled={pending || from === to}><GitCompareArrows className="h-3.5 w-3.5" /></Button>
            </div>
            {diff && (
              <div className="mt-3 space-y-2 text-xs">
                <p className="font-medium">{diff.summary}</p>
                {diff.added.map((l) => <p key={`a${l.target_column}`} className="rounded bg-emerald-50 px-2 py-1 text-emerald-800">+ {l.target_column}</p>)}
                {diff.removed.map((l) => <p key={`r${l.target_column}`} className="rounded bg-rose-50 px-2 py-1 text-rose-800">- {l.target_column}</p>)}
                {diff.changed.map((c) => (
                  <div key={`c${c.target_column}`} className="rounded border border-border/70 p-2">
                    <p className="font-mono font-medium">{c.target_column}</p>
                    {c.fields.map((f) => (
                      <div key={f} className="mt-1">
                        <p className="text-[10px] uppercase text-muted-foreground">{f.replace(/_/g, " ")}</p>
                        <p className="break-all rounded bg-rose-50 px-1.5 py-0.5 font-mono text-rose-800">{String(c.from[f] ?? "none")}</p>
                        <p className="mt-0.5 break-all rounded bg-emerald-50 px-1.5 py-0.5 font-mono text-emerald-800">{String(c.to[f] ?? "none")}</p>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </section>
        )}
      </aside>
    </div>
  );
}
