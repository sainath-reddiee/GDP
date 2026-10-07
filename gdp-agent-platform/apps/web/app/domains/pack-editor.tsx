"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { FileJson, Loader2, PackagePlus, Sparkles, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { draftPack, exportPack, importPack, type Pack } from "./actions";

type Mode = "draft" | "json";

const EXAMPLE = JSON.stringify({
  domain: { name: "ORDERS", description: "Customer orders", standard: "GENERIC",
            signals: { tables: { ORDER: 3, SALES: 2 }, columns: { ORDER: 3, QTY: 2 } } },
  targets: [{ schema: "ORDERS", table: "ORDER_FACT", grain: "one row per order line",
              columns: [{ name: "ORDER_ID", type: "VARCHAR", nullable: false, business_key: true },
                        { name: "ORDER_DATE", type: "DATE", nullable: false }] }],
  knowledge: [{ key: "orders.glossary.order_id", type: "GLOSSARY", title: "ORDER_ID",
                content: "Order number", content_json: { target_column: "ORDER_ID", synonyms: ["ORD_NO", "ORDERNUM"] } }],
}, null, 2);

/** Add or update a domain's knowledge pack from the UI: AI draft from any contract, review the JSON, import. */
export function PackEditor({ domains }: { domains: { domain_id: string; domain_name: string; label: string }[] }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<Mode>("draft");
  const [contract, setContract] = useState("");
  const [standard, setStandard] = useState<"GDP" | "GENERIC">("GENERIC");
  const [json, setJson] = useState("");
  const [problems, setProblems] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, start] = useTransition();

  const parsed = useMemo(() => {
    if (!json.trim()) return { pack: null as Pack | null, error: "" };
    try {
      return { pack: JSON.parse(json) as Pack, error: "" };
    } catch (e) {
      return { pack: null, error: (e as Error).message };
    }
  }, [json]);

  const summary = useMemo(() => {
    const p = parsed.pack as { domain?: { name?: string }; targets?: { columns?: unknown[] }[]; knowledge?: unknown[] } | null;
    if (!p) return null;
    const targets = p.targets ?? [];
    return `${p.domain?.name ?? "unnamed"}: ${targets.length} target${targets.length === 1 ? "" : "s"}, `
      + `${targets.reduce((n, t) => n + (t.columns?.length ?? 0), 0)} columns, ${(p.knowledge ?? []).length} knowledge items`;
  }, [parsed.pack]);

  const draft = () => start(async () => {
    setError(""); setNotice(""); setProblems([]);
    const r = await draftPack(contract, standard);
    if (!r.ok) { setError(r.error); return; }
    setJson(JSON.stringify(r.data.pack, null, 2));
    setProblems(r.data.problems);
    setMode("json");
    setNotice("Draft ready. Review every table, column and rule below before importing; the AI may have missed or misread parts of the document.");
  });

  const load = (domainId: string) => start(async () => {
    if (!domainId) return;
    setError(""); setNotice(""); setProblems([]);
    const r = await exportPack(domainId);
    if (!r.ok) { setError(r.error); return; }
    setJson(JSON.stringify(r.data.pack, null, 2));
    setMode("json");
  });

  const save = () => start(async () => {
    if (!parsed.pack) return;
    setError(""); setNotice("");
    const r = await importPack(parsed.pack);
    if (!r.ok) { setError(r.error); return; }
    const inactive = r.data.inactive_targets.length
      ? ` ${r.data.inactive_targets.join(", ")} registered as knowledge only until it has columns.` : "";
    setNotice(`Imported ${r.data.domain_name}: ${r.data.targets} targets, ${r.data.columns} columns, ${r.data.knowledge} knowledge items.${inactive}`);
    setProblems([]);
    router.refresh();
  });

  const upload = (file: File | undefined) => {
    if (!file) return;
    file.text().then((text) => {
      if (file.name.toLowerCase().endsWith(".json")) { setJson(text); setMode("json"); } else { setContract(text); setMode("draft"); }
    });
  };

  if (!open) {
    return (
      <Card className="flex flex-wrap items-center gap-3 p-4">
        <PackagePlus className="h-5 w-5 text-primary" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">Add a knowledge pack</p>
          <p className="text-xs text-muted-foreground">Onboard a new business domain without a code change: draft one from a contract with AI, or import pack JSON.</p>
        </div>
        <Button size="sm" onClick={() => setOpen(true)}>Add pack</Button>
      </Card>
    );
  }

  return (
    <Card className="space-y-4 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <PackagePlus className="h-5 w-5 text-primary" />
        <h3 className="text-base font-semibold">Add or update a knowledge pack</h3>
        <div className="ml-auto flex rounded-lg border p-0.5 text-xs">
          {([["draft", "Draft from a contract"], ["json", "Pack JSON"]] as const).map(([value, label]) => (
            <button key={value} type="button" onClick={() => setMode(value)} aria-pressed={mode === value}
                    className={cn("rounded-md px-3 py-1", mode === value ? "bg-primary text-primary-foreground" : "hover:bg-muted")}>
              {label}
            </button>
          ))}
        </div>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>Close</Button>
      </div>

      {mode === "draft" ? (
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">
            Paste the data contract, model document, DDL or spreadsheet export. The AI extracts the target tables, columns,
            keys, PII flags, glossary and rules into a pack you review before anything is saved.
          </p>
          <textarea value={contract} onChange={(e) => setContract(e.target.value)} rows={10}
                    aria-label="Contract document"
                    className="w-full rounded-md border bg-card p-3 font-mono text-xs" placeholder="Paste the contract here…" />
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-xs">
              Is this domain part of GDP?
              <select value={standard} onChange={(e) => setStandard(e.target.value as "GDP" | "GENERIC")}
                      className="h-8 rounded-md border bg-card px-2 text-xs">
                <option value="GENERIC">No</option>
                <option value="GDP">Yes, GDP</option>
              </select>
            </label>
            <label className="flex cursor-pointer items-center gap-1 text-xs text-primary">
              <Upload className="h-3.5 w-3.5" /> Load a file
              <input type="file" accept=".md,.txt,.sql,.csv,.json" className="hidden" onChange={(e) => upload(e.target.files?.[0])} />
            </label>
            <Button size="sm" className="ml-auto" disabled={pending || contract.trim().length < 20} onClick={draft}>
              {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
              {pending ? "Drafting…" : "Draft pack with AI"}
            </Button>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-xs">
              Edit an existing domain
              <select defaultValue="" onChange={(e) => load(e.target.value)} className="h-8 rounded-md border bg-card px-2 text-xs">
                <option value="">Choose…</option>
                {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.label}</option>)}
              </select>
            </label>
            <button type="button" className="flex items-center gap-1 text-xs text-primary" onClick={() => setJson(EXAMPLE)}>
              <FileJson className="h-3.5 w-3.5" /> Start from an example
            </button>
            <label className="flex cursor-pointer items-center gap-1 text-xs text-primary">
              <Upload className="h-3.5 w-3.5" /> Load a .json file
              <input type="file" accept=".json" className="hidden" onChange={(e) => upload(e.target.files?.[0])} />
            </label>
          </div>
          <textarea value={json} onChange={(e) => setJson(e.target.value)} rows={18} spellCheck={false}
                    aria-label="Pack JSON" className="w-full rounded-md border bg-card p-3 font-mono text-xs" />
          {parsed.error && <p className="text-xs text-destructive">JSON: {parsed.error}</p>}
          {summary && <p className="text-xs text-muted-foreground">{summary}</p>}
          <div className="flex justify-end">
            <Button size="sm" disabled={pending || !parsed.pack} onClick={save}>
              {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <PackagePlus className="h-4 w-4" />}
              Import pack
            </Button>
          </div>
        </div>
      )}

      {problems.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-warning">
          {problems.slice(0, 12).map((p) => <li key={p}>{p}</li>)}
        </ul>
      )}
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
      {notice && <p className="text-xs text-success">{notice}</p>}
    </Card>
  );
}
