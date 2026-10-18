"use client";

import { useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Check, Copy, Download, FileJson, FileText } from "lucide-react";
import { cn } from "@/lib/utils";
import { CONTRACT_TEMPLATE, PACK_TEMPLATE } from "./templates";

// The answer to "what format do you need from us to onboard a domain?", kept in step with docs/onboarding-inputs.md.

/* ------------------------------------------------------------------ code sample */

type Sample = { file: string; lang: "json" | "yaml"; text: string; icon: typeof FileJson; note: ReactNode };

const SAMPLES: Sample[] = [
  { file: "domain_pack.json", lang: "json", text: PACK_TEMPLATE, icon: FileJson,
    note: <>Only <Code>domain.name</Code> is required; each table needs <Code>table</Code> and each column <Code>name</Code>. Optional{" "}
      <Code>domain.silver_database</Code> and <Code>silver_schema</Code> say where the tables live (default: the platform database, a schema named after the domain).</> },
  { file: "contract.yaml", lang: "yaml", text: CONTRACT_TEMPLATE, icon: FileText,
    note: <><Code>domain</Code> and at least one table with columns are required. The platform&apos;s markdown contract (.md) is accepted too.</> },
];

function Code({ children }: { children: ReactNode }) {
  return <code className="rounded bg-muted px-1 py-px font-mono text-[11px] text-foreground">{children}</code>;
}

const C = { key: "text-sky-300", str: "text-emerald-300", lit: "text-amber-300", dim: "text-slate-500" };

function highlight(line: string, lang: Sample["lang"]): ReactNode[] {
  const out: ReactNode[] = [];
  if (lang === "yaml") {
    if (/^\s*#/.test(line)) return [<span key="c" className={C.dim}>{line}</span>];
    const m = line.match(/^(\s*-?\s*)([\w.]+)(:)(.*)$/);
    if (!m) return [line];
    out.push(m[1], <span key="k" className={C.key}>{m[2]}</span>, <span key="p" className={C.dim}>{m[3]}</span>);
    const value = m[4];
    out.push(<span key="v" className={/^\s*"/.test(value) ? C.str : /^\s*(true|false|\d+)\s*$/.test(value) ? C.lit : "text-slate-200"}>{value}</span>);
    return out;
  }
  const re = /("(?:[^"\\]|\\.)*")(\s*:)?|\b(true|false|null)\b|-?\b\d+(?:\.\d+)?\b|[{}[\],]/g;
  let last = 0;
  for (const m of line.matchAll(re)) {
    const at = m.index ?? 0;
    if (at > last) out.push(line.slice(last, at));
    const cls = m[1] ? (m[2] ? C.key : C.str) : m[3] || /\d/.test(m[0]) ? C.lit : C.dim;
    out.push(<span key={at} className={cls}>{m[1] ?? m[0]}</span>);
    if (m[2]) out.push(<span key={`${at}:`} className={C.dim}>{m[2]}</span>);
    last = at + m[0].length;
  }
  out.push(line.slice(last));
  return out;
}

/** An editor-style viewer for the starter files: tabs, line numbers, copy and download. */
export function TemplateViewer() {
  const [tab, setTab] = useState(0);
  const [copied, setCopied] = useState(false);
  const sample = SAMPLES[tab];
  const lines = sample.text.replace(/\n$/, "").split("\n");
  const copy = async () => {
    try { await navigator.clipboard.writeText(sample.text); setCopied(true); setTimeout(() => setCopied(false), 1400); } catch { /* clipboard blocked */ }
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([sample.text], { type: sample.lang === "json" ? "application/json" : "text/yaml" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: sample.file });
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="min-w-0 space-y-2" data-testid="template-viewer">
      <div className="overflow-hidden rounded-lg border border-slate-800 bg-slate-950 text-[12.5px] shadow-card">
        <div className="flex items-center gap-1 border-b border-slate-800 bg-slate-900/80 pl-1.5 pr-1">
          <div className="flex min-w-0 flex-1 gap-0.5 overflow-x-auto" role="tablist" aria-label="Starter files">
            {SAMPLES.map((s, i) => (
              <button key={s.file} type="button" role="tab" aria-selected={i === tab} onClick={() => setTab(i)}
                      className={cn("relative inline-flex shrink-0 items-center gap-1.5 px-3 py-2 font-mono text-xs transition-colors",
                        i === tab ? "text-slate-100" : "text-slate-400 hover:text-slate-200")}>
                <s.icon className="h-3.5 w-3.5" /> {s.file}
                {i === tab && <motion.span layoutId="template-tab" className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-blue-400" transition={{ type: "spring", stiffness: 500, damping: 38 }} />}
              </button>
            ))}
          </div>
          <button type="button" onClick={copy} aria-label={`Copy ${sample.file}`}
                  className="inline-flex h-7 items-center gap-1.5 rounded px-2 text-xs text-slate-400 transition hover:bg-slate-800 hover:text-slate-100">
            <AnimatePresence mode="wait" initial={false}>
              <motion.span key={copied ? "y" : "n"} initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.6, opacity: 0 }} transition={{ duration: 0.12 }}>
                {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
              </motion.span>
            </AnimatePresence>
            {copied ? "Copied" : "Copy"}
          </button>
          <button type="button" onClick={download} aria-label={`Download ${sample.file}`} data-testid="template-download"
                  className="inline-flex h-7 items-center gap-1.5 rounded px-2 text-xs text-slate-400 transition hover:bg-slate-800 hover:text-slate-100">
            <Download className="h-3.5 w-3.5" /> Download
          </button>
        </div>
        <AnimatePresence mode="wait" initial={false}>
          <motion.pre key={sample.file} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.15 }}
                      className="max-h-[360px] overflow-auto py-3 font-mono leading-[1.6] text-slate-200">
            {lines.map((l, i) => (
              <div key={i} className="flex hover:bg-slate-900/70">
                <span className="w-10 shrink-0 select-none pr-3 text-right text-slate-600">{i + 1}</span>
                <span className="whitespace-pre pr-4">{highlight(l, sample.lang)}</span>
              </div>
            ))}
          </motion.pre>
        </AnimatePresence>
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">{sample.note}</p>
    </div>
  );
}

/* ------------------------------------------------------------------ chips and the full matrix */

export function Formats({ items, className }: { items: string[]; className?: string }) {
  return (
    <span className={cn("flex flex-wrap gap-1", className)}>
      {items.map((f) => <span key={f} className="rounded border bg-muted/50 px-1.5 py-px font-mono text-[11px] text-foreground/80">{f}</span>)}
    </span>
  );
}

const MATRIX: { what: string; formats: string[]; limit: string; where: string }[] = [
  { what: "Domain pack", formats: [".json"], limit: "", where: "Onboarding, Import a pack" },
  { what: "Data contract", formats: [".yaml", ".yml", ".md"], limit: "500 KB", where: "Onboarding, Import a pack" },
  { what: "Contract or model description for AI drafting", formats: ["yaml", "md", "txt", "sql", "csv", "json"], limit: "200,000 characters", where: "Onboarding, Draft from a contract" },
  { what: "Data models", formats: ["Erwin .xml", ".sql DDL", ".xlsx", ".csv", "pack .json"], limit: "25 MB", where: "Model Workbench, Import" },
  { what: "Business documents", formats: [".pdf", ".docx", ".pptx", ".xlsx", ".html", ".md", ".txt", ".csv", ".json", ".yaml", ".xml", ".sql"], limit: "10 files, 50 MB each", where: "Domain, Sources tab" },
  { what: "Scans, diagrams, screenshots (OCR)", formats: [".png", ".jpg", ".tiff", ".webp", ".gif", ".bmp"], limit: "50 MB", where: "Domain, Sources tab" },
  { what: "Confluence", formats: ["page or space link"], limit: "20 pages per space", where: "Domain, Sources tab" },
  { what: "Reference material", formats: ["data models", "MDM schemas", "STTM sheets", "dbt .zip/.yml", "sample rows"], limit: "10 files, 50 MB each", where: "Domain, References tab" },
  { what: "Live Snowflake schema as reference", formats: ["DATABASE.SCHEMA"], limit: "", where: "Domain, References tab" },
  { what: "Examples of correct output", formats: [".csv", ".tsv", ".txt", ".xlsx"], limit: "10 MB", where: "Domain, Data model, Examples" },
];

/** Everything a domain can be onboarded from, in one table. */
export function FormatsMatrix() {
  return (
    <section className="surface overflow-hidden" data-testid="formats-matrix" aria-labelledby="formats-title">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b px-5 py-3.5">
        <h2 id="formats-title" className="text-sm font-semibold">What a domain can be onboarded from</h2>
        <span className="text-xs text-muted-foreground">Any combination works; people review what the platform proposes before it is used.</span>
      </header>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
            <tr><th className="px-5 py-2 font-semibold">Material</th><th className="px-3 py-2 font-semibold">Formats</th><th className="px-3 py-2 font-semibold">Limit</th><th className="px-5 py-2 font-semibold">Where</th></tr>
          </thead>
          <tbody className="divide-y">
            {MATRIX.map((r, i) => (
              <motion.tr key={r.what} initial={{ opacity: 0, y: 4 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-40px" }}
                         transition={{ delay: i * 0.03, duration: 0.25 }} className="align-top transition-colors hover:bg-accent/30">
                <td className="px-5 py-2.5 font-medium">{r.what}</td>
                <td className="px-3 py-2.5"><Formats items={r.formats} /></td>
                <td className="whitespace-nowrap px-3 py-2.5 text-xs text-muted-foreground">{r.limit}</td>
                <td className="whitespace-nowrap px-5 py-2.5 text-xs text-muted-foreground">{r.where}</td>
              </motion.tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="border-t px-5 py-2.5 text-xs text-muted-foreground">Old Office formats (.doc, .xls, .ppt) are stored but not read: ask for the modern format.</p>
    </section>
  );
}
