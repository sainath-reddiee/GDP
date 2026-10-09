"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle, ArrowLeft, ArrowRight, BookOpen, Check, CheckCircle2, ChevronDown, ChevronRight, CircleX, FileUp, Loader2,
  MessageSquareText, Plus, RefreshCw, Sparkles, Trash2, Wand2, X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select, Textarea } from "@/components/ui/input";
import { Markdown } from "@/components/copilot/markdown";
import { useScrollLock } from "@/components/use-scroll-lock";
import { cn } from "@/lib/utils";
import { CategoryIcon } from "./category-icon";
import { builderCheck, builderDraft, builderModels, builderQuestions, builderTest, createSkill, listDomains, saveAiVersion } from "./actions";
import {
  KNOWLEDGE_TYPES, pretty, STAGE_LABELS, type BuilderIssue, type BuilderMode, type BuilderQuestion, type DraftResponse,
  type SkillCategory, type SkillDraft, type SkillTest, type TestSummary,
} from "./types";

const MODES: { id: BuilderMode; title: string; body: string; icon: typeof Sparkles }[] = [
  { id: "interview", title: "Guided interview", body: "Describe the goal, answer a few clarifying questions, get a draft.", icon: MessageSquareText },
  { id: "knowledge", title: "From platform knowledge", body: "Distil approved mappings, STTM rules, quality patterns and models of a domain.", icon: BookOpen },
  { id: "document", title: "From a document", body: "Turn a standards document, runbook or contract into a playbook.", icon: FileUp },
  { id: "improve", title: "Improve a skill", body: "Propose the next version from recent failures and reviewer rejections.", icon: Wand2 },
];
const STEPS = ["Source", "Inputs", "Draft", "Test", "Save"] as const;
const TEXT_TYPES = ".md,.txt,.csv,.json,.yml,.yaml,.sql,.html";

export function SkillBuilder({ categories, skills, initialSkill, onClose }: {
  categories: SkillCategory[]; skills: string[]; initialSkill?: string | null; onClose: () => void;
}) {
  useScrollLock();
  const router = useRouter();
  const [step, setStep] = useState(initialSkill ? 1 : 0);
  const [mode, setMode] = useState<BuilderMode>(initialSkill ? "improve" : "interview");
  const [category, setCategory] = useState("");
  const [skill, setSkill] = useState(initialSkill ?? "");
  const [goal, setGoal] = useState("");
  const [questions, setQuestions] = useState<BuilderQuestion[]>([]);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [domains, setDomains] = useState<{ domain_id: string; domain_name: string; knowledge_items: number }[]>([]);
  const [domain, setDomain] = useState("");
  const [types, setTypes] = useState<string[]>(["MAPPING_PATTERN", "TRANSFORMATION_RULE", "SODA_PATTERN", "MODEL_DEFINITION", "GLOSSARY"]);
  const [days, setDays] = useState(180);
  const [docText, setDocText] = useState("");
  const [docName, setDocName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [result, setResult] = useState<DraftResponse | null>(null);
  const [draft, setDraft] = useState<SkillDraft | null>(null);
  const [accepted, setAccepted] = useState<boolean[]>([]);
  const [content, setContent] = useState("");
  const [issues, setIssues] = useState<BuilderIssue[]>([]);
  const [tests, setTests] = useState<SkillTest[]>([]);
  const [summary, setSummary] = useState<TestSummary | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"" | "questions" | "draft" | "check" | "test" | "save">("");
  const [, start] = useTransition();
  const [models, setModels] = useState<{ default: string; models: string[] } | null>(null);
  const [model, setModel] = useState("");
  useEffect(() => { builderModels().then((r) => { if (r.ok) setModels(r.data); }); }, []);
  const improving = mode === "improve" ? skill : null;

  useEffect(() => {
    if (mode === "knowledge" && !domains.length) listDomains().then((r) => { if (r.ok) setDomains(r.data.domains.filter((d) => d.active_flag)); });
  }, [mode, domains.length]);

  const run = <T,>(kind: typeof busy, fn: () => Promise<{ ok: true; data: T } | { ok: false; error: string }>, then: (d: T) => void) => {
    setBusy(kind); setError("");
    start(async () => {
      const r = await fn();
      setBusy("");
      if (r.ok) then(r.data); else setError(r.error);
    });
  };

  const makeDraft = (extra = "") => run("draft", () => builderDraft({
    mode, goal, category_id: category || null, skill_name: improving,
    answers: questions.map((q, i) => ({ question: q.question, answer: answers[i] ?? "" })).filter((a) => a.answer.trim()),
    domain_id: domain || null, knowledge_types: types, days, document_text: docText, document_name: docName,
    instructions: [instructions, extra].filter(Boolean).join("\n"), model: model || null,
  }), (d) => {
    setResult(d); setDraft(d.draft); setAccepted(d.draft.sections.map(() => true)); setContent(d.content); setIssues(d.issues);
    setTests(d.draft.tests); setSummary(null);
    setNote(d.draft.change_note || (mode === "improve" ? "Improved with AI" : `Created with AI (${MODES.find((m) => m.id === mode)?.title.toLowerCase()})`));
    setStep(2);
  });

  // re-assemble and re-check whenever the draft is edited (no AI)
  useEffect(() => {
    if (!draft) return;
    const t = setTimeout(() => run("check", () => builderCheck(draft, accepted, improving), (d) => { setContent(d.content); setIssues(d.issues); }), 600);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, accepted]);

  const errors = issues.filter((i) => i.level === "error");
  const canInputs = mode !== "improve" || Boolean(skill);
  const canDraft = mode === "interview" ? goal.trim().length >= 10 : mode === "document" ? docText.trim().length >= 200
    : mode === "improve" ? Boolean(skill) : types.length > 0;

  const save = () => run("save", async () => {
    if (!draft) return { ok: false as const, error: "Nothing to save" };
    if (improving) {
      return saveAiVersion(improving, { content, base_skill_id: result?.provenance.base_skill_id, description: draft.description,
                                        change_note: note.trim(), eval: summary });
    }
    return createSkill({ name: draft.name, description: draft.description, category_id: draft.category || "general", content,
                         change_note: note.trim(), origin: "AI", eval: summary });
  }, () => {
    const name = improving || draft!.name;
    onClose();
    router.push(`/skills/${encodeURIComponent(name)}?tab=versions`);
    router.refresh();
  });

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Skill builder">
      <button type="button" aria-label="Close" className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="relative flex h-full w-[1040px] max-w-full flex-col border-l bg-background shadow-2xl">
        <header className="border-b px-6 py-4">
          <div className="flex items-center gap-3">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-violet-500 to-primary text-white"><Sparkles className="h-4 w-4" /></span>
            <div className="min-w-0 flex-1">
              <h2 className="text-base font-semibold">{improving ? `Improve ${pretty(improving)} with AI` : "New skill with AI"}</h2>
              <p className="text-xs text-muted-foreground">Drafts are grounded in what you give it, checked, and tested before they are saved as a candidate. Production never changes here.</p>
            </div>
            <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground" title="Default comes from Admin, AI models, Model per stage (Skills)">Model
              <Select value={model} onChange={(e) => setModel(e.target.value)} className="h-8 w-52 text-xs" aria-label="Model for this builder session">
                <option value="">{models ? `Admin default (${models.default})` : "Admin default"}</option>
                {(models?.models ?? []).filter((m) => m !== models?.default).map((m) => <option key={m} value={m}>{m}</option>)}
              </Select>
            </label>
            <button type="button" onClick={onClose} aria-label="Close" className="rounded-md p-1 hover:bg-muted"><X className="h-4 w-4" /></button>
          </div>
          <ol className="mt-4 flex items-center gap-2 text-xs">
            {STEPS.map((s, i) => (
              <li key={s} className="flex items-center gap-2">
                <button type="button" disabled={i > step || (i >= 2 && !draft)} onClick={() => setStep(i)}
                        className={cn("flex items-center gap-1.5 rounded-full px-2.5 py-1 font-medium transition",
                                      i === step ? "bg-primary text-primary-foreground" : i < step ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground")}>
                  <span className="grid h-4 w-4 place-items-center rounded-full bg-background/30 text-[10px]">{i < step ? <Check className="h-3 w-3" /> : i + 1}</span>{s}
                </button>
                {i < STEPS.length - 1 && <span className="h-px w-6 bg-border" />}
              </li>
            ))}
          </ol>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-6 py-5">
          {step === 0 && (
            <div className="space-y-5">
              <div className="grid gap-3 sm:grid-cols-2">
                {MODES.map((m) => (
                  <button key={m.id} type="button" onClick={() => setMode(m.id)} aria-pressed={mode === m.id}
                          className={cn("flex items-start gap-3 rounded-2xl border bg-card p-4 text-left shadow-sm transition hover:border-primary/50",
                                        mode === m.id && "border-primary ring-2 ring-primary/30")}>
                    <span className={cn("grid h-10 w-10 shrink-0 place-items-center rounded-xl", mode === m.id ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>
                      <m.icon className="h-5 w-5" />
                    </span>
                    <span><span className="block text-sm font-semibold">{m.title}</span><span className="mt-0.5 block text-xs text-muted-foreground">{m.body}</span></span>
                  </button>
                ))}
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                {mode === "improve" ? (
                  <label className="space-y-1 text-xs font-medium">Skill to improve
                    <Select value={skill} onChange={(e) => setSkill(e.target.value)} className="h-9 text-sm">
                      <option value="">Choose a skill…</option>
                      {skills.map((n) => <option key={n} value={n}>{pretty(n)}</option>)}
                    </Select>
                  </label>
                ) : (
                  <label className="space-y-1 text-xs font-medium">Category
                    <Select value={category} onChange={(e) => setCategory(e.target.value)} className="h-9 text-sm">
                      <option value="">Let AI suggest</option>
                      {categories.map((c) => <option key={c.category_id} value={c.category_id}>{c.name}</option>)}
                    </Select>
                  </label>
                )}
              </div>
            </div>
          )}

          {step === 1 && (
            <div className="space-y-5">
              {mode !== "improve" && (
                <label className="block space-y-1 text-xs font-medium">{mode === "interview" ? "What should the skill help agents do?" : "Goal (optional)"}
                  <Textarea value={goal} onChange={(e) => setGoal(e.target.value)} rows={4}
                            placeholder="e.g. Decide freshness and volume checks for daily batch feeds, with thresholds that depend on the feed's history." />
                </label>
              )}
              {mode === "interview" && (
                <div className="space-y-3">
                  <Button size="sm" variant="outline" disabled={goal.trim().length < 10 || busy === "questions"}
                          onClick={() => run("questions", () => builderQuestions(goal, category, model), (d) => { setQuestions(d.questions); setAnswers({}); })}>
                    {busy === "questions" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <MessageSquareText className="h-3.5 w-3.5" />}
                    {questions.length ? "Ask different questions" : "Ask me clarifying questions"}
                  </Button>
                  {questions.map((q, i) => (
                    <div key={i} className="rounded-xl border bg-card p-3 shadow-sm">
                      <p className="text-sm font-medium">{q.question}</p>
                      <p className="mb-2 text-[11px] text-muted-foreground">{q.why}</p>
                      {!!q.options?.length && (
                        <div className="mb-2 flex flex-wrap gap-1.5">
                          {q.options.map((o) => (
                            <button key={o} type="button" onClick={() => setAnswers({ ...answers, [i]: o })}
                                    className={cn("rounded-full border px-2.5 py-0.5 text-[11px]", answers[i] === o ? "border-primary bg-primary/10 text-primary" : "hover:border-primary/50")}>{o}</button>
                          ))}
                        </div>
                      )}
                      <Input value={answers[i] ?? ""} onChange={(e) => setAnswers({ ...answers, [i]: e.target.value })} placeholder="Your answer (optional)" className="h-8 text-xs" />
                    </div>
                  ))}
                </div>
              )}
              {mode === "knowledge" && (
                <div className="grid gap-4 md:grid-cols-[1fr_1fr]">
                  <label className="space-y-1 text-xs font-medium">Domain
                    <Select value={domain} onChange={(e) => setDomain(e.target.value)} className="h-9 text-sm">
                      <option value="">All domains</option>
                      {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name} ({d.knowledge_items})</option>)}
                    </Select>
                  </label>
                  <label className="space-y-1 text-xs font-medium">Learned within
                    <Select value={String(days)} onChange={(e) => setDays(Number(e.target.value))} className="h-9 text-sm">
                      {[30, 90, 180, 365, 3650].map((d) => <option key={d} value={d}>{d === 3650 ? "Any time" : `Last ${d} days`}</option>)}
                    </Select>
                  </label>
                  <div className="md:col-span-2">
                    <p className="mb-1.5 text-xs font-medium">Knowledge to distil</p>
                    <div className="flex flex-wrap gap-1.5">
                      {KNOWLEDGE_TYPES.map((t) => {
                        const on = types.includes(t);
                        return <button key={t} type="button" aria-pressed={on} onClick={() => setTypes(on ? types.filter((x) => x !== t) : [...types, t])}
                                       className={cn("rounded-full border px-2.5 py-0.5 text-[11px]", on ? "border-primary bg-primary/10 text-primary" : "text-muted-foreground hover:border-primary/50")}>
                          {pretty(t)}</button>;
                      })}
                    </div>
                  </div>
                </div>
              )}
              {mode === "document" && (
                <div className="space-y-3">
                  <label className="flex cursor-pointer flex-col items-center gap-2 rounded-2xl border-2 border-dashed bg-muted/20 p-6 text-center text-sm hover:border-primary/50">
                    <FileUp className="h-6 w-6 text-muted-foreground" />
                    <span className="font-medium">{docName || "Choose a text document"}</span>
                    <span className="text-xs text-muted-foreground">Markdown, text, CSV, JSON, YAML, SQL or HTML. For PDF or Word, paste the text below.</span>
                    <input type="file" accept={TEXT_TYPES} className="sr-only" onChange={async (e) => {
                      const f = e.target.files?.[0];
                      if (!f) return;
                      if (f.size > 2_000_000) { setError("Keep documents under 2 MB"); return; }
                      setDocName(f.name); setDocText(await f.text());
                    }} />
                  </label>
                  <label className="block space-y-1 text-xs font-medium">Document text
                    <Textarea value={docText} onChange={(e) => setDocText(e.target.value)} rows={10} className="font-mono text-[11px]" placeholder="Or paste the text here" />
                  </label>
                  <p className="text-[11px] text-muted-foreground">{docText.length.toLocaleString()} characters{docText.length > 60000 ? ": only the first 60,000 are used" : ""}. Instructions inside the document are ignored.</p>
                </div>
              )}
              {mode === "improve" && (
                <div className="rounded-2xl border bg-card p-4 text-sm shadow-sm">
                  <p className="font-medium">AI reads the production version of {pretty(skill)}, plus:</p>
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
                    <li>failed steps in runs that loaded it (last 60 days)</li>
                    <li>reviewer rejections recorded in knowledge for its category (last 60 days)</li>
                    <li>your instructions below</li>
                  </ul>
                </div>
              )}
              <label className="block space-y-1 text-xs font-medium">{mode === "improve" ? "What should change?" : "Extra instructions (optional)"}
                <Textarea value={instructions} onChange={(e) => setInstructions(e.target.value)} rows={3}
                          placeholder={mode === "improve" ? "e.g. Reviewers keep rejecting uniqueness checks on surrogate keys; tighten that rule." : "Tone, scope or anything to avoid"} />
              </label>
            </div>
          )}

          {step === 2 && draft && result && (
            <DraftStep draft={draft} setDraft={setDraft} accepted={accepted} setAccepted={setAccepted} issues={issues}
                       checking={busy === "check"} result={result} categories={categories} locked={Boolean(improving)}
                       onRedraft={(fb) => makeDraft(fb)} redrafting={busy === "draft"} />
          )}

          {step === 3 && (
            <TestStep tests={tests} setTests={setTests} summary={summary} busy={busy === "test"}
                      baseline={improving ? `production ${pretty(improving)}` : "no skill"}
                      onRun={() => run("test", () => builderTest(content, tests, improving, model), setSummary)} />
          )}

          {step === 4 && draft && (
            <div className="space-y-4">
              <div className="rounded-2xl border bg-card p-5 shadow-sm">
                <div className="flex items-start gap-3">
                  <CategoryIcon icon={categories.find((c) => c.category_id === draft.category)?.icon} color={categories.find((c) => c.category_id === draft.category)?.color} size="lg" />
                  <div className="min-w-0 flex-1">
                    <p className="text-base font-semibold">{pretty(draft.name)}</p>
                    <p className="font-mono text-xs text-muted-foreground">{draft.name}</p>
                    <p className="mt-1 text-sm text-muted-foreground">{draft.description}</p>
                  </div>
                </div>
                <dl className="mt-4 grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
                  <div><dt className="text-muted-foreground">Saved as</dt><dd className="font-medium">{improving ? "New version, candidate" : "New skill v0.1.0, candidate"}</dd></div>
                  <div><dt className="text-muted-foreground">Sections</dt><dd className="font-medium">{accepted.filter(Boolean).length} of {draft.sections.length}</dd></div>
                  <div><dt className="text-muted-foreground">Tests</dt><dd className="font-medium">{summary ? `${summary.candidate_passed}/${summary.total} passed (${summary.baseline_label}: ${summary.baseline_passed})` : "Not run"}</dd></div>
                  <div><dt className="text-muted-foreground">Suggested stages</dt><dd className="font-medium">{draft.stages.map((s) => STAGE_LABELS[s] ?? s).join(", ") || "None"}</dd></div>
                </dl>
                {!summary && <p className="mt-3 flex items-center gap-1.5 rounded-lg bg-warning/10 px-3 py-2 text-xs text-warning"><AlertTriangle className="h-3.5 w-3.5" />Tests were not run. You can still save; reviewers will see that it was not tested.</p>}
              </div>
              <label className="block space-y-1 text-xs font-medium">Change note (shown in the version history and to the approver)
                <Input value={note} onChange={(e) => setNote(e.target.value)} />
              </label>
              <p className="text-xs text-muted-foreground">After saving: try it on a run from the run header (Skills), compare it with production, then promote it.
                {!improving && " New skills are not loaded by any stage until you bind them in Admin, Skills per stage."}</p>
            </div>
          )}
        </div>

        <footer className="flex items-center gap-2 border-t px-6 py-3">
          {error && <p role="alert" className="max-w-[60%] text-xs text-destructive">{error}</p>}
          {busy === "test" && <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Running tests: answering each task twice and judging, usually one to two minutes…</p>}
          {busy === "draft" && <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Drafting with AI, usually about a minute…</p>}
          <span className="ml-auto" />
          {step > 0 && <Button size="sm" variant="ghost" onClick={() => setStep(step - 1)} disabled={Boolean(busy && busy !== "check")}><ArrowLeft className="h-3.5 w-3.5" />Back</Button>}
          {step === 0 && <Button size="sm" disabled={!canInputs} onClick={() => setStep(1)}>Next<ArrowRight className="h-3.5 w-3.5" /></Button>}
          {step === 1 && (
            <Button size="sm" disabled={!canDraft || busy === "draft"} onClick={() => makeDraft()}>
              {busy === "draft" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}{draft ? "Draft again" : "Draft the skill"}
            </Button>
          )}
          {step === 1 && draft && <Button size="sm" variant="outline" onClick={() => setStep(2)}>Keep current draft<ArrowRight className="h-3.5 w-3.5" /></Button>}
          {step === 2 && <Button size="sm" disabled={errors.length > 0 || busy === "check"} onClick={() => setStep(3)}>Next: test<ArrowRight className="h-3.5 w-3.5" /></Button>}
          {step === 3 && <Button size="sm" disabled={busy === "test"} onClick={() => setStep(4)}>{summary ? "Next: save" : "Skip tests"}<ArrowRight className="h-3.5 w-3.5" /></Button>}
          {step === 4 && (
            <Button size="sm" disabled={busy === "save" || errors.length > 0 || note.trim().length < 3} onClick={save}>
              {busy === "save" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Save as candidate
            </Button>
          )}
        </footer>
      </div>
    </div>
  );
}

function DraftStep({ draft, setDraft, accepted, setAccepted, issues, checking, result, categories, locked, onRedraft, redrafting }: {
  draft: SkillDraft; setDraft: (d: SkillDraft) => void; accepted: boolean[]; setAccepted: (a: boolean[]) => void; issues: BuilderIssue[];
  checking: boolean; result: DraftResponse; categories: SkillCategory[]; locked: boolean; onRedraft: (feedback: string) => void; redrafting: boolean;
}) {
  const [feedback, setFeedback] = useState("");
  const [preview, setPreview] = useState<Record<number, boolean>>({});
  const p = result.provenance;
  const setSection = (i: number, patch: Partial<SkillDraft["sections"][number]>) =>
    setDraft({ ...draft, sections: draft.sections.map((s, j) => (j === i ? { ...s, ...patch } : s)) });
  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
      <div className="space-y-4">
        <div className="grid gap-3 rounded-2xl border bg-card p-4 shadow-sm md:grid-cols-2">
          <label className="space-y-1 text-xs font-medium">Name
            <Input value={draft.name} disabled={locked} onChange={(e) => setDraft({ ...draft, name: e.target.value.toUpperCase().replace(/[^A-Z0-9_-]/g, "-") })} className="h-9 font-mono" />
          </label>
          <label className="space-y-1 text-xs font-medium">Title
            <Input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} className="h-9" />
          </label>
          <label className="space-y-1 text-xs font-medium md:col-span-2">Description (agents use it to decide when the skill applies)
            <Textarea value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} rows={2} />
          </label>
          {!locked && (
            <label className="space-y-1 text-xs font-medium">Category
              <Select value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })} className="h-9">
                {categories.map((c) => <option key={c.category_id} value={c.category_id}>{c.name}</option>)}
              </Select>
            </label>
          )}
        </div>
        {draft.sections.map((s, i) => (
          <section key={i} className={cn("rounded-2xl border bg-card shadow-sm", !accepted[i] && "opacity-60")}>
            <div className="flex items-center gap-2 border-b px-4 py-2">
              <label className="flex items-center gap-1.5 text-xs">
                <input type="checkbox" checked={accepted[i] ?? true} onChange={() => setAccepted(accepted.map((a, j) => (j === i ? !a : a)))} />
                Keep
              </label>
              <Input value={s.heading} onChange={(e) => setSection(i, { heading: e.target.value })} className="h-7 flex-1 border-0 text-sm font-semibold shadow-none focus-visible:ring-0" aria-label="Section heading" />
              <button type="button" onClick={() => setPreview({ ...preview, [i]: !preview[i] })} className="text-[11px] text-primary hover:underline">{preview[i] ? "Edit" : "Preview"}</button>
            </div>
            <div className="p-3">
              {preview[i] ? <div className="text-sm"><Markdown text={s.markdown} /></div>
                : <Textarea value={s.markdown} onChange={(e) => setSection(i, { markdown: e.target.value })} rows={Math.min(14, Math.max(4, s.markdown.split("\n").length + 1))} className="font-mono text-[12px]" />}
            </div>
          </section>
        ))}
        {draft.references.length > 0 && (
          <section className="rounded-2xl border bg-card p-4 shadow-sm">
            <h4 className="mb-2 text-xs font-semibold">Reference files</h4>
            {draft.references.map((r, i) => (
              <details key={r.path} className="mb-1.5 rounded-lg border">
                <summary className="cursor-pointer px-3 py-1.5 font-mono text-xs">{r.path}</summary>
                <Textarea value={r.markdown} rows={8} className="rounded-none border-0 font-mono text-[11px]"
                          onChange={(e) => setDraft({ ...draft, references: draft.references.map((x, j) => (j === i ? { ...x, markdown: e.target.value } : x)) })} />
              </details>
            ))}
          </section>
        )}
      </div>
      <aside className="space-y-4 lg:sticky lg:top-0 lg:self-start">
        <section className="rounded-2xl border bg-card p-4 shadow-sm">
          <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold">Checks {checking && <Loader2 className="h-3 w-3 animate-spin" />}</h4>
          {issues.length === 0 ? <p className="flex items-center gap-1.5 text-xs text-success"><CheckCircle2 className="h-3.5 w-3.5" />All checks pass</p> : (
            <ul className="space-y-1.5">
              {issues.map((i, k) => (
                <li key={k} className={cn("flex gap-1.5 text-xs", i.level === "error" ? "text-destructive" : "text-warning")}>
                  {i.level === "error" ? <CircleX className="mt-0.5 h-3.5 w-3.5 shrink-0" /> : <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />}{i.message}
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="rounded-2xl border bg-card p-4 text-xs shadow-sm">
          <h4 className="mb-2 font-semibold">Based on</h4>
          <ul className="space-y-1 text-muted-foreground">
            {p.mode === "knowledge" && <li>{p.knowledge_items} knowledge items; {p.used_knowledge?.length ?? 0} cited</li>}
            {p.mode === "document" && <li>{p.document || "Pasted text"}{p.truncated ? " (first 60,000 characters)" : ""}</li>}
            {p.mode === "improve" && <li>Production v{(p.base_version ?? "").split("+")[0]} and {p.evidence ?? 0} pieces of evidence</li>}
            {p.mode === "interview" && <li>Your goal and answers</li>}
            <li>Model {result.model}{result.tokens ? `, ${result.tokens.toLocaleString()} tokens` : ""}</li>
          </ul>
          {result.feedback.length > 0 && (
            <details className="mt-2"><summary className="cursor-pointer text-primary">Evidence used</summary>
              <ul className="mt-1 list-disc space-y-1 pl-4 text-muted-foreground">{result.feedback.map((f, i) => <li key={i}>{f}</li>)}</ul>
            </details>
          )}
        </section>
        <section className="rounded-2xl border bg-card p-4 shadow-sm">
          <h4 className="mb-2 text-xs font-semibold">Not quite right?</h4>
          <Textarea value={feedback} onChange={(e) => setFeedback(e.target.value)} rows={3} className="text-xs" placeholder="Tell AI what to change and draft again" />
          <Button size="sm" variant="outline" className="mt-2 w-full" disabled={redrafting || feedback.trim().length < 5} onClick={() => onRedraft(feedback)}>
            {redrafting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}Draft again with this
          </Button>
        </section>
      </aside>
    </div>
  );
}

function TestStep({ tests, setTests, summary, busy, baseline, onRun }: {
  tests: SkillTest[]; setTests: (t: SkillTest[]) => void; summary: TestSummary | null; busy: boolean; baseline: string; onRun: () => void;
}) {
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const valid = useMemo(() => tests.filter((t) => t.input.trim() && t.expectation.trim()).length, [tests]);
  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button size="sm" disabled={busy || valid === 0} onClick={onRun}>
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}{busy ? "Running…" : summary ? "Run tests again" : `Run ${valid} test${valid === 1 ? "" : "s"}`}
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">Each test is answered twice: by an agent following the draft, and by one using {baseline}. A judge model marks
        each answer against its expectation. Three AI calls in total.</p>
      {summary && (
        <div className="grid gap-3 md:grid-cols-3">
          <Score label="This draft" passed={summary.candidate_passed} total={summary.total} avg={summary.candidate_avg} tone="primary" />
          <Score label={`Baseline (${summary.baseline_label})`} passed={summary.baseline_passed} total={summary.total} avg={summary.baseline_avg} />
          <div className={cn("rounded-2xl border p-4 shadow-sm", summary.verdict === "better" ? "border-emerald-300 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/30"
            : summary.verdict === "worse" ? "border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/30" : "bg-card")}>
            <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Verdict</p>
            <p className="mt-1 text-lg font-semibold capitalize">{summary.verdict}</p>
            <p className="text-[11px] text-muted-foreground">{summary.ms ? `${Math.round(summary.ms / 1000)} s` : ""}{summary.model ? ` · ${summary.model}` : ""}</p>
          </div>
        </div>
      )}
      <div className="space-y-2">
        {tests.map((t, i) => {
          const row = summary?.rows.find((r) => r.index === i);
          return (
            <div key={i} className="rounded-2xl border bg-card p-3 shadow-sm">
              <div className="flex items-center gap-2">
                {row && (row.candidate_pass ? <CheckCircle2 className="h-4 w-4 text-success" /> : <CircleX className="h-4 w-4 text-destructive" />)}
                <Input value={t.title ?? ""} onChange={(e) => setTests(tests.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)))}
                       placeholder="Test name" className="h-8 flex-1 text-sm font-medium" />
                {row && <span className="text-[11px] text-muted-foreground">baseline {row.baseline_pass ? "passed" : "failed"}</span>}
                <button type="button" aria-label="Remove test" onClick={() => setTests(tests.filter((_, j) => j !== i))} className="rounded p-1 text-muted-foreground hover:text-destructive"><Trash2 className="h-3.5 w-3.5" /></button>
              </div>
              <div className="mt-2 grid gap-2 md:grid-cols-2">
                <label className="space-y-1 text-[11px] text-muted-foreground">Task the agent gets
                  <Textarea value={t.input} rows={3} className="text-xs" onChange={(e) => setTests(tests.map((x, j) => (j === i ? { ...x, input: e.target.value } : x)))} />
                </label>
                <label className="space-y-1 text-[11px] text-muted-foreground">A correct answer…
                  <Textarea value={t.expectation} rows={3} className="text-xs" onChange={(e) => setTests(tests.map((x, j) => (j === i ? { ...x, expectation: e.target.value } : x)))} />
                </label>
              </div>
              {row && (
                <div className="mt-2">
                  <p className="text-xs"><span className="font-medium">Judge:</span> {row.reason}</p>
                  <button type="button" onClick={() => setOpen({ ...open, [i]: !open[i] })} className="mt-1 flex items-center gap-1 text-[11px] text-primary">
                    {open[i] ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}Answers
                  </button>
                  {open[i] && (
                    <div className="mt-1 grid gap-2 md:grid-cols-2">
                      <div className="rounded-lg bg-muted/40 p-2 text-[11px]"><p className="mb-1 font-semibold">This draft</p><Markdown text={row.candidate_answer ?? ""} /></div>
                      <div className="rounded-lg bg-muted/40 p-2 text-[11px]"><p className="mb-1 font-semibold">Baseline</p><Markdown text={row.baseline_answer ?? ""} /></div>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <Button size="sm" variant="outline" disabled={tests.length >= 8} onClick={() => setTests([...tests, { title: "", input: "", expectation: "" }])}><Plus className="h-3.5 w-3.5" />Add test</Button>
    </div>
  );
}

function Score({ label, passed, total, avg, tone }: { label: string; passed: number; total: number; avg: number; tone?: "primary" }) {
  const pct = total ? Math.round((passed / total) * 100) : 0;
  return (
    <div className="rounded-2xl border bg-card p-4 shadow-sm">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={cn("mt-1 text-2xl font-semibold tabular-nums", tone === "primary" && "text-primary")}>{passed}/{total}</p>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted"><div className={cn("h-full rounded-full", tone === "primary" ? "bg-primary" : "bg-muted-foreground/50")} style={{ width: `${pct}%` }} /></div>
      <p className="mt-1 text-[11px] text-muted-foreground">{pct}% passed · average score {avg.toFixed(2)}</p>
    </div>
  );
}
