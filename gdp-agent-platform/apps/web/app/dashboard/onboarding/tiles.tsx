"use client";

import Link from "next/link";
import { useEffect, useRef, useState, useTransition } from "react";
import { AppWindow, CheckCircle2, FileJson, FileText, Loader2, Upload, XCircle } from "lucide-react";
import type { OnboardingCheck } from "@/lib/hub-types";
import { Modal } from "@/components/hub/overlay";
import { ErrorNote } from "@/components/hub/states";
import { Button, buttonVariants } from "@/components/ui/button";
import { checkOnboarding, importPack } from "./actions";

const ROADMAP = [
  "Register an upstream application and its owning team",
  "Discover the application's data products and events",
  "Generate ingestion contracts and pipeline entries from the catalog",
  "Track application SLAs alongside data quality gates",
];

/** Domain onboarding from files: a domain_pack.json and its data contract, validated before anything is registered. */
export function DomainImportCard({ openImport = false }: { openImport?: boolean }) {
  const [open, setOpen] = useState(openImport);
  // "+ New" while already on this page changes only the query, so follow it after mount too
  useEffect(() => { if (openImport) setOpen(true); }, [openImport]);
  return (
    <article className="surface flex flex-wrap items-center gap-4 p-5">
      <div className="min-w-0 flex-1">
        <h2 className="text-base font-semibold">domain_pack.json and its data contract</h2>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          The contract can be YAML or the platform&apos;s markdown contract. Both are validated before anything is registered.
        </p>
      </div>
      <Button onClick={() => setOpen(true)} data-testid="import-domain"><Upload className="h-4 w-4" /> Choose files</Button>
      <ImportModal open={open} onClose={() => setOpen(false)} />
    </article>
  );
}

/** Application onboarding: not available yet; shows what is coming. */
export function ApplicationCard() {
  return (
    <article className="surface flex flex-col border-dashed p-5">
      <h2 className="flex items-center gap-2 text-base font-semibold">
        <AppWindow className="h-4 w-4 text-muted-foreground" /> Application onboarding is not available yet
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Upstream applications will onboard as sources with their owners, data products and contracts.
      </p>
      <ul className="mt-4 space-y-1.5 text-sm text-muted-foreground">
        {ROADMAP.map((r) => <li key={r} className="flex gap-2"><span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-muted-foreground/60" />{r}</li>)}
      </ul>
    </article>
  );
}

function ImportModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [pack, setPack] = useState<{ name: string; data: Record<string, unknown> } | null>(null);
  const [contract, setContract] = useState<{ name: string; text: string } | null>(null);
  const [check, setCheck] = useState<OnboardingCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ domain_id: string; domain_name: string; targets: number; columns: number; knowledge: number } | null>(null);
  const [pending, start] = useTransition();
  const packRef = useRef<HTMLInputElement>(null);
  const contractRef = useRef<HTMLInputElement>(null);

  const reset = () => { setPack(null); setContract(null); setCheck(null); setError(null); setDone(null); };
  const close = () => { reset(); onClose(); };
  const readPack = async (file: File | undefined) => {
    setCheck(null); setError(null);
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("not an object");
      setPack({ name: file.name, data });
    } catch {
      setPack(null);
      setError(`${file.name} is not valid JSON`);
    }
  };
  const readContract = async (file: File | undefined) => {
    setCheck(null);
    if (!file) return;
    if (file.size > 500_000) return setError("The contract is larger than 500 KB");
    setContract({ name: file.name, text: await file.text() });
  };
  const validate = () => start(async () => {
    setError(null);
    const r = await checkOnboarding(pack?.data ?? null, contract?.name ?? null, contract?.text ?? null);
    if (r.ok) setCheck(r.data); else setError(r.error);
  });
  const doImport = () => start(async () => {
    if (!pack) return;
    const r = await importPack(pack.data);
    if (r.ok) setDone(r.data); else setError(r.error);
  });

  const picker = (label: string, hint: string, file: string | undefined, onPick: () => void, Icon: typeof FileJson) => (
    <button type="button" onClick={onPick} className="flex w-full items-center gap-3 rounded-xl border border-dashed border-border p-4 text-left transition hover:border-primary/40 hover:bg-accent/30">
      <Icon className="h-6 w-6 text-primary" />
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium">{file ?? label}</span>
        <span className="block text-xs text-muted-foreground">{file ? "Click to replace" : hint}</span>
      </span>
    </button>
  );

  return (
    <Modal open={open} onClose={close} wide title="Import a domain" description="Validated first: nothing is registered until you import."
           footer={done ? <Button onClick={close}>Close</Button> : <>
             <Button variant="outline" onClick={validate} disabled={pending || (!pack && !contract)}>{pending ? <Loader2 className="h-4 w-4 animate-spin" /> : null} Validate</Button>
             <Button onClick={doImport} disabled={pending || !pack || !check?.pack?.ok || (check.contract ? !check.contract.ok : false)}>Import domain</Button>
           </>}>
      {done ? (
        <div className="flex flex-col items-center py-6 text-center">
          <CheckCircle2 className="h-10 w-10 text-emerald-600" />
          <p className="mt-3 text-base font-semibold">{done.domain_name} is registered</p>
          <p className="text-sm text-muted-foreground">{done.targets} targets, {done.columns} columns, {done.knowledge} knowledge items</p>
          <Link href={`/os/knowledge/domains/${done.domain_id}`} className={buttonVariants({ className: "mt-4" })}>Open the domain</Link>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="grid gap-3 md:grid-cols-2">
            {picker("domain_pack.json", "Domain, targets, columns and knowledge", pack?.name, () => packRef.current?.click(), FileJson)}
            {picker("Data contract", "contract.yaml or the markdown contract", contract?.name, () => contractRef.current?.click(), FileText)}
          </div>
          <input ref={packRef} type="file" accept=".json,application/json" className="hidden" onChange={(e) => readPack(e.target.files?.[0])} />
          <input ref={contractRef} type="file" accept=".yaml,.yml,.md,.markdown,text/plain" className="hidden" onChange={(e) => readContract(e.target.files?.[0])} />
          {error && <ErrorNote>{error}</ErrorNote>}
          {check && (
            <div className="grid gap-3 md:grid-cols-2">
              {check.pack && (
                <Result ok={check.pack.ok} title={`Pack: ${check.pack.domain}`} problems={check.pack.problems}
                        lines={[`${check.pack.summary.targets} targets, ${check.pack.summary.columns} columns, ${check.pack.summary.knowledge} knowledge items`,
                                check.pack.exists ? "Updates the existing domain of this name" : "Creates a new domain"]} />
              )}
              {check.contract && (
                <Result ok={check.contract.ok} title={`Contract (${check.contract.kind})`} problems={check.contract.problems}
                        lines={Object.entries(check.contract.summary).map(([k, v]) => `${k.replace(/_/g, " ")}: ${String(v)}`)} />
              )}
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

function Result({ ok, title, lines, problems }: { ok: boolean; title: string; lines: string[]; problems: string[] }) {
  return (
    <div className={ok ? "rounded-xl border border-emerald-200 bg-emerald-50/60 p-3" : "rounded-xl border border-rose-200 bg-rose-50/60 p-3"}>
      <p className="flex items-center gap-1.5 text-sm font-semibold">
        {ok ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <XCircle className="h-4 w-4 text-rose-600" />} {title}
        <span className="ml-auto text-xs font-medium">{ok ? "valid" : "needs changes"}</span>
      </p>
      <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">{lines.map((l) => <li key={l}>{l}</li>)}</ul>
      {problems.length > 0 && <ul className="mt-2 space-y-0.5 text-xs text-rose-700">{problems.slice(0, 10).map((p) => <li key={p}>• {p}</li>)}</ul>}
    </div>
  );
}
