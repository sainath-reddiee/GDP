"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { Archive, Loader2, Plus, Save, Send, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import {
  publishFix, retireFix, saveFix, saveSkill, type FixInput, type FixSkill, type KnownFix, type SkillStep,
} from "../resolve-actions";

const field = "space-y-1 text-xs font-medium";

/** Write or edit a draft known fix. A PROCEDURE fix calls one CORE.FIX_* procedure with the failure id; a SKILL fix
 *  runs the steps of a multi-step fix in order. Only a draft is editable. */
export function FixForm({ initial, issueId, skills, editable }: {
  initial: FixInput; issueId: string | null; skills: FixSkill[]; editable: boolean;
}) {
  const router = useRouter();
  const [v, setV] = useState<FixInput>(initial);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const set = (p: Partial<FixInput>) => setV((x) => ({ ...x, ...p }));
  const ro = !editable || pending;

  const save = () => start(async () => {
    setError("");
    const r = await saveFix(issueId, v);
    if (!r.ok) { setError(r.error); return; }
    if (!issueId) router.push(`/ops/fixes/${encodeURIComponent(r.data.issue_id)}`);
    else router.refresh();
  });

  return (
    <form className="surface space-y-3 p-4" data-testid="fix-form" onSubmit={(e) => { e.preventDefault(); save(); }}>
      <label className={field}>Title<Input value={v.title ?? ""} onChange={(e) => set({ title: e.target.value })} disabled={ro} required
                                           placeholder="Staging table missing after a source schema change" className="text-sm" /></label>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className={field}>DAG (blank: any)<Input value={v.dag_id ?? ""} onChange={(e) => set({ dag_id: e.target.value })} disabled={ro} className="text-xs" /></label>
        <label className={field}>Task (blank: any)<Input value={v.task_id ?? ""} onChange={(e) => set({ task_id: e.target.value })} disabled={ro} className="text-xs" /></label>
        <label className={field}>Exception class (blank: any)<Input value={v.exception_class ?? ""} onChange={(e) => set({ exception_class: e.target.value })} disabled={ro} className="text-xs" /></label>
      </div>
      <label className={field}>Match text
        <Textarea value={v.match_text ?? ""} onChange={(e) => set({ match_text: e.target.value })} disabled={ro} rows={3} className="font-mono text-xs" required />
        <span className="block font-normal text-muted-foreground">What a new failure is compared with. Prefilled from the solved incident; keep the error's stable words.</span>
      </label>
      <label className={field}>Error signature (exact match, optional)
        <Input value={v.error_signature ?? ""} onChange={(e) => set({ error_signature: e.target.value })} disabled={ro} className="font-mono text-xs" />
      </label>
      <fieldset className="space-y-2">
        <legend className="text-xs font-medium">The fix</legend>
        <div className="flex gap-4 text-sm">
          {(["PROCEDURE", "SKILL"] as const).map((t) => (
            <label key={t} className="flex items-center gap-2">
              <input type="radio" name="fix_type" checked={(v.fix_type ?? "PROCEDURE") === t} disabled={ro} onChange={() => set({ fix_type: t })} />
              {t === "PROCEDURE" ? "One procedure" : "Several steps (skill)"}
            </label>
          ))}
        </div>
        {(v.fix_type ?? "PROCEDURE") === "PROCEDURE" ? (
          <label className={field}>Fix procedure
            <Input value={v.procedure_fqn ?? ""} onChange={(e) => set({ procedure_fqn: e.target.value })} disabled={ro} placeholder="CORE.FIX_RECREATE_STAGE" className="font-mono text-xs" />
            <span className="block font-normal text-muted-foreground">Must be named CORE.FIX_&lt;NAME&gt; and take the failure id. It is reviewed like code.</span>
          </label>
        ) : (
          <label className={field}>Skill
            <select value={v.skill_id ?? ""} onChange={(e) => set({ skill_id: e.target.value || null })} disabled={ro} className="h-9 w-full rounded-lg border bg-card px-2 text-xs">
              <option value="">Choose a multi-step fix</option>
              {skills.map((s) => <option key={s.skill_id} value={s.skill_id}>{s.skill_name} ({s.steps?.length ?? 0} steps)</option>)}
            </select>
          </label>
        )}
      </fieldset>
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" className="mt-1 h-4 w-4 accent-primary" checked={v.approval_required ?? true} disabled={ro}
               onChange={(e) => set({ approval_required: e.target.checked })} />
        <span><span className="font-medium">Ask for human approval before it runs</span>
          <span className="block text-xs text-muted-foreground">Leave on until the fix has proven itself. A pipeline set to "Always ask" asks regardless.</span></span>
      </label>
      <label className={field}>Notes<Textarea value={v.notes ?? ""} onChange={(e) => set({ notes: e.target.value })} disabled={ro} rows={2} className="text-xs" /></label>
      {editable && (
        <Button type="submit" size="sm" disabled={pending}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}{issueId ? "Save draft" : "Create draft"}</Button>
      )}
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    </form>
  );
}

/** Publish (another person than the author) or retire a fix. */
export function FixActions({ fix, canPublish, me }: { fix: KnownFix; canPublish: boolean; me: string }) {
  const router = useRouter();
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [pending, start] = useTransition();
  const own = (fix.created_by ?? "").toUpperCase() === me.toUpperCase();
  const act = (fn: () => Promise<{ ok: true; data: KnownFix } | { ok: false; error: string }>, done: (k: KnownFix) => string) => start(async () => {
    setError("");
    const r = await fn();
    if (!r.ok) { setError(r.error); return; }
    setNote(done(r.data));
    router.refresh();
  });
  if (!canPublish) return null;
  return (
    <div className="surface space-y-2 p-4">
      <h2 className="text-sm font-semibold">Review</h2>
      {fix.status === "DRAFT" && (own
        ? <p className="text-xs text-muted-foreground">You wrote this fix, so another reviewer publishes it.</p>
        : <Button size="sm" disabled={pending} onClick={() => act(() => publishFix(fix.issue_id),
            (k) => `Published. Search ${k.search_refresh ?? "refreshes within minutes"}: the next matching failure can run it.`)}>
            {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}Publish
          </Button>)}
      {fix.status !== "RETIRED" && (
        <Button size="sm" variant="outline" disabled={pending} onClick={() => act(() => retireFix(fix.issue_id), () => "Retired: it is never run again.")}>
          <Archive className="h-3.5 w-3.5" />Retire
        </Button>
      )}
      {note && <p role="status" className="text-xs text-success">{note}</p>}
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

/** Write a multi-step fix: ordered steps, each a CORE.FIX_* procedure with an optional JSON input. */
export function SkillEditor({ skill, editable, onSaved }: { skill?: FixSkill; editable: boolean; onSaved?: () => void }) {
  const router = useRouter();
  const [name, setName] = useState(skill?.skill_name ?? "");
  const [description, setDescription] = useState(skill?.description ?? "");
  const [steps, setSteps] = useState<SkillStep[]>(skill?.steps?.length ? skill.steps : [{ step_no: 1, action: "", note: "" }]);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const ro = !editable || pending;
  const put = (i: number, p: Partial<SkillStep>) => setSteps((s) => s.map((x, j) => (j === i ? { ...x, ...p } : x)));
  const save = () => start(async () => {
    setError("");
    const body = { skill_name: name.trim(), description: description.trim(),
      steps: steps.map((s, i) => ({ step_no: i + 1, action: s.action.trim(), note: s.note,
        input: typeof s.input === "string" && s.input.trim() ? s.input.trim() : s.input ?? null })) };
    const r = await saveSkill(skill?.skill_id ?? null, body);
    if (!r.ok) { setError(r.error); return; }
    onSaved?.();
    router.refresh();
  });
  return (
    <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); save(); }} data-testid="skill-editor">
      <div className="grid gap-2 sm:grid-cols-2">
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Skill name" aria-label="Skill name" disabled={ro} className="text-xs" />
        <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What it repairs" aria-label="Skill description" disabled={ro} className="text-xs" />
      </div>
      <ol className="space-y-1.5">
        {steps.map((s, i) => (
          <li key={i} className="flex flex-wrap items-center gap-2">
            <span className="w-6 text-right text-xs tabular-nums text-muted-foreground">{i + 1}.</span>
            <Input value={s.action} onChange={(e) => put(i, { action: e.target.value })} placeholder="CORE.FIX_..." aria-label={`Step ${i + 1} procedure`} disabled={ro} className="w-64 font-mono text-xs" />
            <Input value={typeof s.input === "string" ? s.input : s.input ? JSON.stringify(s.input) : ""} onChange={(e) => put(i, { input: e.target.value })}
                   placeholder='Input JSON (optional)' aria-label={`Step ${i + 1} input`} disabled={ro} className="w-56 font-mono text-xs" />
            <Input value={s.note ?? ""} onChange={(e) => put(i, { note: e.target.value })} placeholder="Note" aria-label={`Step ${i + 1} note`} disabled={ro} className="min-w-[10rem] flex-1 text-xs" />
            {editable && steps.length > 1 && <Button type="button" size="sm" variant="ghost" aria-label={`Remove step ${i + 1}`} onClick={() => setSteps((x) => x.filter((_, j) => j !== i))}><Trash2 className="h-3.5 w-3.5" /></Button>}
          </li>
        ))}
      </ol>
      {editable && (
        <div className="flex gap-2">
          <Button type="button" size="sm" variant="outline" onClick={() => setSteps((x) => [...x, { step_no: x.length + 1, action: "", note: "" }])}><Plus className="h-3.5 w-3.5" />Step</Button>
          <Button type="submit" size="sm" disabled={pending || !name.trim() || steps.some((s) => !s.action.trim())}>
            {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}Save skill
          </Button>
        </div>
      )}
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    </form>
  );
}
