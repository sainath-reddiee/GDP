"use client";

import { useEffect, useState } from "react";
import { Check, ChevronDown, Cloud, Copy, Download, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { loadSodaCloudCi, type SodaCloudCi } from "@/app/dashboard/dq-store/actions";

/** Publishing merged suites to Soda Cloud: a GitHub Actions workflow and scan script that a person commits once,
 *  with the repository secrets it needs. The platform never holds Soda Cloud keys. */
export function SodaCloudCard() {
  const [ci, setCi] = useState<SodaCloudCi | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState(0);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!open || ci) return;
    void loadSodaCloudCi().then((r) => (r.ok ? setCi(r.data) : setError(r.error)));
  }, [open, ci]);
  const files = ci ? Object.entries(ci.files) : [];
  const [path, content] = files[tab] ?? ["", ""];
  const copy = async () => {
    try { await navigator.clipboard.writeText(content); setCopied(true); setTimeout(() => setCopied(false), 1400); } catch { /* blocked */ }
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([content], { type: "text/plain" }));
    Object.assign(document.createElement("a"), { href: url, download: path.split("/").pop() }).click();
    URL.revokeObjectURL(url);
  };
  return (
    <section className="surface p-4" data-testid="soda-cloud">
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 text-left" aria-expanded={open}>
        <Cloud className="h-4 w-4" />
        <h3 className="whitespace-nowrap text-sm font-semibold">Publish to Soda Cloud</h3>
        <span className="hidden min-w-0 truncate text-xs text-muted-foreground sm:inline">from CI, once a suite&apos;s pull request is merged</span>
        <ChevronDown className={cn("ml-auto h-4 w-4 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="mt-3 space-y-3">
          <ol className="list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
            <li>Commit the two files below to the repository the suites are published to (once).</li>
            <li>Add the repository secrets listed below (Soda Cloud keys from Profile, API Keys in Soda Cloud).</li>
            <li>Every merged suite pull request then scans the changed suites with Soda Core and sends the checks and results to Soda Cloud. Run it by hand from the Actions tab too.</li>
          </ol>
          {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : !ci ? (
            <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />Loading the files...</p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Secrets</span>
                {ci.secrets.map((s) => <code key={s} className="rounded border bg-muted/50 px-1.5 py-px font-mono text-[11px]">{s}</code>)}
              </div>
              <div className="overflow-hidden rounded-lg border border-slate-800 bg-slate-950 text-[12px]">
                <div className="flex items-center gap-1 border-b border-slate-800 bg-slate-900/80 pl-1.5 pr-1">
                  <div className="flex min-w-0 flex-1 gap-0.5 overflow-x-auto" role="tablist" aria-label="CI files">
                    {files.map(([p], i) => (
                      <button key={p} type="button" role="tab" aria-selected={i === tab} onClick={() => setTab(i)}
                              className={cn("shrink-0 border-b-2 px-3 py-2 font-mono text-xs", i === tab ? "border-blue-400 text-slate-100" : "border-transparent text-slate-400 hover:text-slate-200")}>
                        {p}
                      </button>
                    ))}
                  </div>
                  <button type="button" onClick={copy} className="inline-flex h-7 items-center gap-1.5 rounded px-2 text-xs text-slate-400 hover:bg-slate-800 hover:text-slate-100">
                    {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}{copied ? "Copied" : "Copy"}
                  </button>
                  <button type="button" onClick={download} className="inline-flex h-7 items-center gap-1.5 rounded px-2 text-xs text-slate-400 hover:bg-slate-800 hover:text-slate-100">
                    <Download className="h-3.5 w-3.5" /> Download
                  </button>
                </div>
                <pre className="max-h-[320px] overflow-auto p-3 font-mono leading-[1.55] text-slate-200">{content}</pre>
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}
