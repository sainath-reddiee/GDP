"use client";

import Link from "next/link";
import { useEffect, useState, useTransition } from "react";
import { Check, LifeBuoy, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getCaseSettings, saveCaseSettings, type CaseSettings } from "../qa/cases/actions";
import { SEVERITY_IDS } from "../qa/cases/case-filters";

type Msg = { tone: "ok" | "info" | "error"; text: string } | null;
type Sev = (typeof SEVERITY_IDS)[number];
const MAX_SLA = 24 * 90;

const FieldError = ({ children }: { children: React.ReactNode }) => (
  <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">{children}</p>
);

/** Admin, Integrations, Cases: automatic AI triage and the SLA per severity. */
export function CasesTab({ may, onMsg }: { may: boolean; onMsg: (m: Msg) => void }) {
  const [state, setState] = useState<CaseSettings | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    getCaseSettings().then((r) => {
      if (!live) return;
      if (r.ok) { setState(r.data); setError(""); } else setError(r.status === 404 ? "Case settings need the latest API deploy." : r.error);
    }, (e: unknown) => { if (live) setError(e instanceof Error ? e.message : "Could not load the case settings."); });
    return () => { live = false; };
  }, [tick]);
  if (!state) {
    return (
      <section className="surface p-5 text-sm">
        {error ? <FieldError>Case settings did not load: {error}</FieldError>
          : <p className="flex items-center gap-2 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading case settings</p>}
      </section>
    );
  }
  return <CasesForm key={JSON.stringify(state)} state={state} may={may} onMsg={onMsg} onSaved={() => setTick((n) => n + 1)} />;
}

function CasesForm({ state, may, onMsg, onSaved }: { state: CaseSettings; may: boolean; onMsg: (m: Msg) => void; onSaved: () => void }) {
  const [auto, setAuto] = useState(!!state.auto_triage);
  const [sevs, setSevs] = useState<string[]>(state.auto_triage_severities ?? ["P1", "P2"]);
  const [sla, setSla] = useState<Record<Sev, string>>(() =>
    Object.fromEntries(SEVERITY_IDS.map((s) => [s, String(state.sla_hours?.[s] ?? "")])) as Record<Sev, string>);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const bad = (s: Sev) => { const n = Number(sla[s]); return !sla[s].trim() || !Number.isInteger(n) || n < 1 || n > MAX_SLA; };
  const anyBad = SEVERITY_IDS.some(bad);
  const badSev = auto && !sevs.length;
  const toggle = (s: string) => setSevs((v) => (v.includes(s) ? v.filter((x) => x !== s) : SEVERITY_IDS.filter((x) => x === s || v.includes(x))));
  const save = () => start(async () => {
    setError("");
    try {
      const r = await saveCaseSettings({
        auto_triage: auto, auto_triage_severities: sevs,
        sla_hours: { P1: Number(sla.P1), P2: Number(sla.P2), P3: Number(sla.P3), P4: Number(sla.P4) },
      });
      if (!r.ok) {
        if (r.status === 202 || /approval|request/i.test(r.error)) onMsg({ tone: "info", text: r.error }); else setError(r.error);
        return;
      }
      onMsg({ tone: "ok", text: "Case settings saved." });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the case settings.");
    }
  });
  return (
    <section className="surface overflow-hidden">
      <header className="flex flex-wrap items-center gap-3 border-b px-5 py-4">
        <span className="grid h-10 w-10 place-items-center rounded-xl bg-sky-50 text-sky-600 ring-1 ring-inset ring-sky-100"><LifeBuoy className="h-5 w-5" /></span>
        <div className="min-w-[14rem] flex-1">
          <h3 className="text-base font-semibold">Case settings</h3>
          <p className="text-xs text-muted-foreground">Automatic AI triage and response times for <Link href="/qa?tab=cases" className="text-primary hover:underline">QA cases</Link>.</p>
        </div>
      </header>
      <fieldset disabled={!may || pending} className="grid gap-4 p-5 md:grid-cols-2">
        <div className="space-y-3">
          <label className="flex items-start gap-2 text-xs">
            <input type="checkbox" className="mt-0.5" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
            <span>Triage new cases with AI <span className="block text-muted-foreground">The worker triages a case when it opens, for the severities below. Each triage is a billed model call; unchanged cases reuse the cached analysis.</span></span>
          </label>
          <div className="space-y-1 text-xs font-medium" role="group" aria-label="Severities triaged automatically">
            <span>Severities triaged automatically</span>
            <span className="flex flex-wrap gap-3 font-normal">
              {SEVERITY_IDS.map((s) => (
                <label key={s} className="flex items-center gap-1.5">
                  <input type="checkbox" checked={sevs.includes(s)} onChange={() => toggle(s)} disabled={!auto} />{s}
                </label>
              ))}
            </span>
            {badSev && <span role="alert" className="block font-normal text-destructive">Pick at least one severity, or turn automatic triage off.</span>}
          </div>
        </div>
        <div className="space-y-1 text-xs font-medium" role="group" aria-label="SLA hours per severity">
          <span>SLA (hours to resolve)</span>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {SEVERITY_IDS.map((s) => (
              <label key={s} className="space-y-1 font-normal">{s}
                <Input value={sla[s]} onChange={(e) => setSla((v) => ({ ...v, [s]: e.target.value }))} inputMode="numeric" className="text-xs" aria-invalid={bad(s)} />
              </label>
            ))}
          </div>
          <span className="block font-normal text-muted-foreground">Applies to cases opened after saving.</span>
          {anyBad && <span role="alert" className="block font-normal text-destructive">Each SLA is a whole number of hours from 1 to {MAX_SLA}.</span>}
        </div>
        {error && <div className="md:col-span-2"><FieldError>{error}</FieldError></div>}
        {may ? (
          <div className="flex justify-end md:col-span-2">
            <Button onClick={save} disabled={pending || anyBad || badSev}>
              {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}Save settings</Button>
          </div>
        ) : <p className="text-xs text-muted-foreground md:col-span-2">Changing these needs the INTEGRATION.MANAGE privilege.</p>}
      </fieldset>
    </section>
  );
}
