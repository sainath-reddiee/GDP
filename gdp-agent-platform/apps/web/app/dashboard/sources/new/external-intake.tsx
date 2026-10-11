"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import { CheckCircle2, Loader2, Save } from "lucide-react";
import type { IntakeField, IntakeForm } from "@/lib/hub-types";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { ErrorNote, explain } from "@/components/hub/states";
import { registerPipeline } from "../actions";
import type { DomainOption } from "./wizard";

const visible = (f: IntakeField, answers: Record<string, string>) =>
  !f.show_if || f.show_if.in.map(String).includes(String(answers[f.show_if.key] ?? ""));

export function ExternalIntake({ form, domains }: { form: IntakeForm | null; domains: DomainOption[] }) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [pending, start] = useTransition();
  if (!form) return <ErrorNote>The intake questions could not be loaded. Check that the API is running.</ErrorNote>;
  const set = (key: string, value: string) => setAnswers((a) => ({ ...a, [key]: value }));

  const submit = () => start(async () => {
    setError(null);
    const body = Object.fromEntries(Object.entries(answers).filter(([k]) => {
      const f = form.sections.flatMap((s) => s.fields).find((x) => x.key === k);
      return k === "domain_id" || (f && visible(f, answers));
    }));
    const r = await registerPipeline(body);
    if (!r.ok) {
      const e = explain(r.error);
      setErrors(e.fields);
      setError(Object.keys(e.fields).length ? e.message : r.error);
      return;
    }
    setErrors({});
    setDone(r.data.pipeline.source_name);
  });

  if (done) {
    return (
      <div className="surface flex flex-col items-center p-10 text-center">
        <CheckCircle2 className="h-10 w-10 text-emerald-600" />
        <h3 className="mt-3 text-base font-semibold">{done} is registered</h3>
        <p className="mt-1 max-w-md text-sm text-muted-foreground">
          It is in PLATFORM_CONFIG.INGESTION_PIPELINE_CATALOG with status registered. Set it active once its pipeline is assigned.
        </p>
        <div className="mt-4 flex gap-2">
          <Link href="/dashboard/sources" className={buttonVariants()}>Back to the Source Store</Link>
          <Button variant="outline" onClick={() => { setAnswers({}); setDone(null); }}>Register another</Button>
        </div>
      </div>
    );
  }

  const field = (f: IntakeField) => {
    const id = `f-${f.key}`;
    const common = { id, value: answers[f.key] ?? "", "aria-invalid": !!errors[f.key] || undefined };
    let control;
    if (f.type === "select") {
      control = (
        <Select {...common} onChange={(e) => set(f.key, e.target.value)}>
          <option value="">Choose</option>
          {(f.options ?? []).map((o) => <option key={o} value={o}>{o.replace(/_/g, " ")}</option>)}
        </Select>
      );
    } else if (f.type === "textarea") {
      control = <Textarea {...common} placeholder={f.placeholder} onChange={(e) => set(f.key, e.target.value)} />;
    } else if (f.type === "boolean") {
      control = <input id={id} type="checkbox" checked={answers[f.key] === "true"} onChange={(e) => set(f.key, String(e.target.checked))} />;
    } else {
      control = <Input {...common} type={f.type === "password" ? "password" : f.type === "number" ? "number" : "text"}
                       autoComplete={f.type === "password" ? "new-password" : "off"} placeholder={f.placeholder}
                       onChange={(e) => set(f.key, e.target.value)} />;
    }
    return (
      <div key={f.key}>
        <Label htmlFor={id}>{f.label ?? f.key}{f.required && <span className="text-destructive"> *</span>}</Label>
        {control}
        {errors[f.key] ? <p className="mt-1 text-xs text-destructive">{errors[f.key]}</p>
          : f.help ? <p className="mt-1 text-xs text-muted-foreground">{f.help}</p> : null}
      </div>
    );
  };

  return (
    <div className="space-y-4">
      {error && <ErrorNote>{error}</ErrorNote>}
      {form.sections.map((section) => {
        const fields = section.fields.filter((f) => visible(f, answers));
        if (!fields.length) return null;
        return (
          <section key={section.title} className="surface p-5">
            <h3 className="text-sm font-semibold">{section.title}</h3>
            <div className="grid gap-x-6 md:grid-cols-2">{fields.map(field)}</div>
          </section>
        );
      })}
      {domains.length > 0 && (
        <section className="surface p-5">
          <h3 className="text-sm font-semibold">Domain</h3>
          <Select aria-label="Domain" value={answers.domain_id ?? ""} onChange={(e) => set("domain_id", e.target.value)} className="mt-3 max-w-sm">
            <option value="">Not assigned</option>
            {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
          </Select>
        </section>
      )}
      <div className="flex justify-end">
        <Button onClick={submit} disabled={pending}>{pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Register in the catalog</Button>
      </div>
    </div>
  );
}
