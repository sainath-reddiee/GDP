"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { Check, Eye, Loader2, Plus, Save, Sparkles, UserPlus } from "lucide-react";
import { assignPersonas, editPersona, startPreview } from "@/app/os/people/actions";
import { cn } from "@/lib/utils";

// Admin, People (super admins only): who holds which persona, what each persona grants, and a preview.

export type PersonaOption = { persona_id: string; label: string; description: string | null };
type Outcome = { tone: "done" | "fail"; text: string } | null;

function Line({ outcome }: { outcome: Outcome }) {
  if (!outcome) return null;
  return <div role={outcome.tone === "fail" ? "alert" : "status"} className={cn("mt-2 text-[11.5px]", outcome.tone === "fail" ? "text-[var(--os-red-ink)]" : "text-[var(--os-green-ink)]")}>{outcome.text}</div>;
}

/** One person's personas, as toggles, saved together. */
export function PersonaPicker({ user, held, suggested, options, testId }: {
  user: string; held: string[]; suggested: string[]; options: PersonaOption[]; testId?: string;
}) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [picked, setPicked] = useState<string[]>(held);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const changed = picked.length !== held.length || picked.some((p) => !held.includes(p));
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const save = (personas: string[]) => start(async () => {
    setOutcome(null);
    const r = await assignPersonas(user, personas);
    if (!r.ok) { setOutcome({ tone: "fail", text: r.error }); return; }
    setOutcome({ tone: "done", text: personas.length ? `Saved: ${user} now holds ${r.data.roles.length} role${r.data.roles.length === 1 ? "" : "s"}.` : `Saved: ${user} has no persona (Viewer).` });
    router.refresh();
  });
  return (
    <div data-testid={testId}>
      <div className="flex flex-wrap gap-1.5">
        {options.map((o) => {
          const on = picked.includes(o.persona_id);
          return (
            <button key={o.persona_id} type="button" aria-pressed={on} title={o.description ?? undefined} onClick={() => toggle(o.persona_id)} disabled={pending}
                    className={cn("os-tag cursor-pointer border transition", on ? "border-[var(--os-forest)] bg-[var(--os-forest)] text-white" : "border-[var(--os-line)] bg-transparent")}>
              {on && <Check className="h-3 w-3" />}{o.label}
            </button>
          );
        })}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {changed && <button type="button" className="os-btn os-btn-primary" disabled={pending} onClick={() => save(picked)}>
          {pending ? <Loader2 className="h-3.5 w-3.5 os-spin" /> : <Save className="h-3.5 w-3.5" />} Save personas</button>}
        {!held.length && suggested.length > 0 && !changed && (
          <button type="button" className="os-btn os-btn-mint" disabled={pending} onClick={() => { setPicked(suggested); save(suggested); }}
                  title="Their roles already match these personas">
            <Sparkles className="h-3.5 w-3.5" /> Give the matching persona{suggested.length === 1 ? "" : "s"}
          </button>
        )}
      </div>
      <Line outcome={outcome} />
    </div>
  );
}

/** Give a person who has no role yet their first personas. */
export function AddPerson({ options }: { options: PersonaOption[] }) {
  const [user, setUser] = useState("");
  const name = user.trim().toUpperCase();
  return (
    <div data-testid="add-person">
      <label className="os-label" htmlFor="new-user">Snowflake user</label>
      <input id="new-user" value={user} onChange={(e) => setUser(e.target.value)} maxLength={256} placeholder="e.g. JDOE" className="os-input mb-2 max-w-xs" />
      {name ? <PersonaPicker key={name} user={name} held={[]} suggested={[]} options={options} />
        : <p className="os-faint text-[11.5px]"><UserPlus className="mr-1 inline h-3 w-3" />Name the user, then pick their personas.</p>}
    </div>
  );
}

/** What a persona grants: role toggles, saved for everyone who holds it. */
export function BundleEditor({ persona, roles, all }: { persona: string; roles: string[]; all: { role: string; description: string | null; retired: boolean }[] }) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [picked, setPicked] = useState<string[]>(roles);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const changed = picked.length !== roles.length || picked.some((r) => !roles.includes(r));
  const choices = all.filter((r) => !r.retired && (persona === "super" || r.role !== "SUPER_ADMIN"));
  return (
    <div data-testid={`bundle-${persona}`}>
      <div className="flex flex-wrap gap-1.5">
        {choices.map((r) => {
          const on = picked.includes(r.role);
          return (
            <button key={r.role} type="button" aria-pressed={on} title={r.description ?? undefined} disabled={pending || persona === "super"}
                    onClick={() => setPicked((p) => (on ? p.filter((x) => x !== r.role) : [...p, r.role]))}
                    className={cn("os-tag cursor-pointer border font-mono text-[10px]", on ? "border-[var(--os-emerald)] bg-[var(--os-green-bg)] text-[var(--os-green-ink)]" : "border-[var(--os-line)] bg-transparent opacity-70")}>
              {on ? <Check className="h-3 w-3" /> : <Plus className="h-3 w-3" />}{r.role.toLowerCase()}
            </button>
          );
        })}
      </div>
      {changed && (
        <button type="button" className="os-btn os-btn-primary mt-2" disabled={pending || !picked.length}
                onClick={() => start(async () => {
                  setOutcome(null);
                  const r = await editPersona(persona, picked);
                  if (!r.ok) { setOutcome({ tone: "fail", text: r.error }); return; }
                  setOutcome({ tone: "done", text: "Saved; everyone holding this persona now has these roles." });
                  router.refresh();
                })}>
          {pending ? <Loader2 className="h-3.5 w-3.5 os-spin" /> : <Save className="h-3.5 w-3.5" />} Save roles
        </button>
      )}
      <Line outcome={outcome} />
    </div>
  );
}

/** See the platform as a persona (super admins): the same banner and the decisions off, as the header used to do. */
export function PreviewPersona({ persona, label }: { persona: string; label: string }) {
  const router = useRouter();
  const [pending, start] = useTransition();
  return (
    <button type="button" className="os-btn" disabled={pending} data-testid={`preview-${persona}`}
            onClick={() => start(async () => { await startPreview(persona); router.push("/os"); router.refresh(); })}>
      {pending ? <Loader2 className="h-3.5 w-3.5 os-spin" /> : <Eye className="h-3.5 w-3.5" />} See as {label}
    </button>
  );
}
