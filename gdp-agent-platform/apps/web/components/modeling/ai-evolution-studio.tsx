"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import {
  ArrowRight,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Code2,
  GitPullRequest,
  ListChecks,
  Loader2,
  Network,
  Play,
  Sparkles,
  Wand2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { DmIssue, DmSpec } from "@/lib/model-types";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { explain } from "@/components/hub/states";
import { useToast } from "@/components/ui/toast";
import {
  applyEvolveWorkflow,
  evolveModelWithAi,
  type EvolveModelResult,
  type EvolveWorkflowResult,
} from "@/app/dashboard/modeling/model-actions";

const QUICK_PROMPTS = [
  "Add a new LEASE_AGREEMENT fact table linked to the primary entity with LEASE_ID (PK), LEASE_START_DATE, LEASE_END_DATE, and MONTHLY_RENT_AMOUNT",
  "Add SCD2 effective dates (EFFECTIVE_START_TS, EFFECTIVE_END_TS, IS_CURRENT) and audit columns",
  "Enrich business definitions on columns and mark personal/contact fields (email, phone, tax ID) as PII",
  "Add business rules and glossary terms to Domain Knowledge (Enterprise Memory)",
];

const STAGE_ICONS = {
  design: Network,
  build: Code2,
  validate: ListChecks,
  publish: GitPullRequest,
} as const;

export function AiEvolutionStudio({
  modelId,
  domainName,
  spec,
  defaultExpanded = true,
  onPreviewSpec,
  onWorkflowApplied,
}: {
  modelId: string;
  domainName: string;
  spec?: DmSpec | null;
  defaultExpanded?: boolean;
  onPreviewSpec?: (next: DmSpec, issues: DmIssue[], summaryNotice: string, rationale: string) => void;
  onWorkflowApplied?: (result: EvolveWorkflowResult) => void;
}) {
  const toast = useToast();
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [instructions, setInstructions] = useState("");
  const [saveKnowledge, setSaveKnowledge] = useState(true);
  const [createRun, setCreateRun] = useState(true);
  const [recommendation, setRecommendation] = useState<EvolveModelResult | null>(null);
  const [workflow, setWorkflow] = useState<EvolveWorkflowResult | null>(null);
  const [previewTab, setPreviewTab] = useState<"changes" | "knowledge" | "ddl" | "dbt">("changes");
  const [pendingRecommend, startRecommend] = useTransition();
  const [pendingApply, startApply] = useTransition();

  const handleRecommend = () =>
    startRecommend(async () => {
      const text = instructions.trim();
      if (text.length < 3) return;
      setWorkflow(null);
      const r = await evolveModelWithAi(modelId, {
        spec: spec ?? null,
        instructions: text,
        save_knowledge: false,
      });
      if (!r.ok) {
        toast("error", explain(r.error).message);
        return;
      }
      setRecommendation(r.data);
      setPreviewTab("changes");
    });

  const handleStartWorkflow = () =>
    startApply(async () => {
      if (!recommendation) return;
      const r = await applyEvolveWorkflow(modelId, {
        spec: recommendation.spec,
        instructions: instructions.trim(),
        rationale: recommendation.rationale,
        knowledge_proposals: recommendation.knowledge_proposals,
        save_knowledge: saveKnowledge,
        create_run: createRun,
      });
      if (!r.ok) {
        toast("error", explain(r.error).message);
        return;
      }
      setWorkflow(r.data);
      toast(
        "ok",
        `Saved Draft v${r.data.version}${r.data.run_name ? ` · workflow "${r.data.run_name}" started` : ""}`,
      );
      onWorkflowApplied?.(r.data);
    });

  return (
    <section
      className="rounded-2xl border border-primary/30 bg-gradient-to-br from-primary/[0.05] via-card to-card p-4 shadow-sm"
      data-testid="ai-evolution-studio"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-xl bg-primary text-primary-foreground shadow-sm">
            <Sparkles className="h-4 w-4" />
          </span>
          <div>
            <h3 className="flex flex-wrap items-center gap-2 text-sm font-semibold">
              Evolve {domainName} with AI
            </h3>
            <p className="text-xs text-muted-foreground">
              Describe model or knowledge changes in plain English, review the AI recommendation, and start the workflow.
            </p>
          </div>
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setExpanded((v) => !v)}
          data-testid="toggle-ai-studio"
        >
          {expanded ? (
            <>
              Hide <ChevronUp className="h-4 w-4" />
            </>
          ) : (
            <>
              Evolve with AI <ChevronDown className="h-4 w-4" />
            </>
          )}
        </Button>
      </div>

      {expanded && (
        <div className="mt-3 space-y-3 border-t border-border/60 pt-3">
          {/* Custom human input */}
          <div className="space-y-2 rounded-xl border bg-card p-3">
            <Textarea
              id="ai-custom-input"
              data-testid="evolve-instructions"
              rows={2}
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder={`Describe what to add or evolve in ${domainName} (e.g. Add a LEASE_AGREEMENT fact table with LEASE_ID, LEASE_START_DATE, MONTHLY_RENT_AMOUNT, and a rule that rent > 0)…`}
              className="text-sm"
            />
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] font-medium text-muted-foreground">Suggestions:</span>
              {QUICK_PROMPTS.map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setInstructions(p)}
                  className="rounded-full border bg-muted/40 px-2.5 py-0.5 text-left text-[11px] text-muted-foreground transition hover:border-primary/40 hover:bg-primary/10 hover:text-foreground"
                >
                  + {p.slice(0, 60)}&hellip;
                </button>
              ))}
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
              <div className="flex flex-wrap items-center gap-4 text-xs">
                <label className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={saveKnowledge}
                    onChange={(e) => setSaveKnowledge(e.target.checked)}
                  />
                  Update Domain Knowledge
                </label>
                <label className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={createRun}
                    onChange={(e) => setCreateRun(e.target.checked)}
                  />
                  Create workflow run
                </label>
              </div>
              <Button
                size="sm"
                onClick={handleRecommend}
                disabled={pendingRecommend || pendingApply || instructions.trim().length < 3}
                data-testid="run-evolve-ai"
              >
                {pendingRecommend ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Wand2 className="h-3.5 w-3.5" />
                )}
                {pendingRecommend ? "Analyzing…" : "Get AI Recommendation"}
              </Button>
            </div>
          </div>

          {/* Compact AI Recommendation Card */}
          {recommendation && (
            <div
              className="space-y-2.5 rounded-xl border border-primary/40 bg-card p-3.5 shadow-sm"
              data-testid="ai-recommendation-card"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="inline-flex items-center gap-1 rounded-full bg-primary/15 px-2 py-0.5 text-[11px] font-semibold text-primary">
                      <Sparkles className="h-3 w-3" /> AI Recommendation
                    </span>
                    <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 dark:text-emerald-300">
                      +{recommendation.counts.ADDITIVE ?? 0} additive
                    </span>
                    <span className="rounded-full bg-slate-500/15 px-2 py-0.5 text-[11px] font-semibold text-slate-700 dark:text-slate-300">
                      ~{recommendation.counts.NON_FUNCTIONAL ?? 0} docs
                    </span>
                    {(recommendation.counts.BREAKING ?? 0) > 0 && (
                      <span className="rounded-full bg-rose-500/15 px-2 py-0.5 text-[11px] font-semibold text-rose-700 dark:text-rose-300">
                        !{recommendation.counts.BREAKING} breaking
                      </span>
                    )}
                    {recommendation.knowledge_proposals.length > 0 && (
                      <span className="rounded-full border px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                        {recommendation.knowledge_proposals.length} rule(s)
                      </span>
                    )}
                  </div>
                  <p className="line-clamp-2 text-xs text-muted-foreground">
                    {recommendation.rationale}
                  </p>
                </div>
                <Button
                  size="sm"
                  onClick={handleStartWorkflow}
                  disabled={pendingApply}
                  data-testid="accept-evolve-workflow"
                >
                  {pendingApply ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Play className="h-3.5 w-3.5" />
                  )}
                  Start workflow
                </Button>
              </div>

              {/* Preview tabs */}
              <div className="flex flex-wrap gap-1 border-b border-border/60 text-xs">
                {(
                  [
                    ["changes", `Changes (${recommendation.changes.length})`],
                    ["knowledge", `Knowledge (${recommendation.knowledge_proposals.length})`],
                    ["ddl", "DDL"],
                    ["dbt", "dbt Tests"],
                  ] as const
                ).map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setPreviewTab(id)}
                    className={cn(
                      "rounded-t-lg px-2.5 py-1 font-medium transition",
                      previewTab === id
                        ? "border-b-2 border-primary bg-primary/5 text-primary"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {previewTab === "changes" && (
                <div className="max-h-36 space-y-1 overflow-y-auto pr-1 text-xs">
                  {recommendation.changes.length === 0 ? (
                    <p className="text-muted-foreground">
                      Model structure ready ({recommendation.spec.design.entities.length} entities).
                    </p>
                  ) : (
                    recommendation.changes.map((c, i) => (
                      <div
                        key={i}
                        className="flex items-center gap-2 rounded-md border bg-background/60 px-2.5 py-1"
                      >
                        <span
                          className={cn(
                            "rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase",
                            c.class === "BREAKING"
                              ? "bg-rose-100 text-rose-700"
                              : c.class === "ADDITIVE"
                                ? "bg-emerald-100 text-emerald-700"
                                : "bg-slate-100 text-slate-700",
                          )}
                        >
                          {c.class.toLowerCase()}
                        </span>
                        <span className="font-mono text-[11px]">{c.message}</span>
                      </div>
                    ))
                  )}
                </div>
              )}

              {previewTab === "knowledge" && (
                <div className="max-h-36 space-y-1 overflow-y-auto pr-1 text-xs">
                  {recommendation.knowledge_proposals.length === 0 ? (
                    <p className="text-muted-foreground">No additional domain rules extracted.</p>
                  ) : (
                    recommendation.knowledge_proposals.map((k, i) => (
                      <div key={i} className="rounded-md border bg-background/60 px-2.5 py-1.5">
                        <div className="flex items-center gap-2">
                          <BookOpen className="h-3.5 w-3.5 text-primary" />
                          <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold text-primary">
                            {k.knowledge_type}
                          </span>
                          <span className="font-semibold">{k.title}</span>
                        </div>
                        <p className="mt-0.5 text-muted-foreground">{k.content}</p>
                      </div>
                    ))
                  )}
                </div>
              )}

              {previewTab === "ddl" && (
                <pre className="max-h-40 overflow-auto rounded-md border bg-muted/40 p-2.5 font-mono text-[11px]">
                  {recommendation.ddl_preview || "-- DDL preview will be rendered on save"}
                </pre>
              )}

              {previewTab === "dbt" && (
                <pre className="max-h-40 overflow-auto rounded-md border bg-muted/40 p-2.5 font-mono text-[11px]">
                  {recommendation.dbt_preview || "# dbt schema preview will be rendered on save"}
                </pre>
              )}
            </div>
          )}

          {/* Active 4-Stage Workflow Track (Design -> Build -> Validate -> Publish) */}
          {workflow && (
            <div
              className="space-y-2.5 rounded-xl border border-emerald-500/40 bg-emerald-500/[0.06] p-3.5"
              data-testid="evolve-workflow-track"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
                  <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                  Workflow started: {workflow.run_name ?? `${workflow.domain_name} Draft v${workflow.version}`}
                </p>
                {workflow.run_id && (
                  <Link
                    href={`/os/design?view=selected&run=${encodeURIComponent(workflow.run_id)}`}
                    className="inline-flex h-7 items-center gap-1 rounded-lg bg-primary px-2.5 text-xs font-semibold text-primary-foreground shadow-sm hover:bg-primary/90"
                    data-testid="open-evolved-workflow"
                  >
                    Open workflow <ArrowRight className="h-3 w-3" />
                  </Link>
                )}
              </div>

              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                {workflow.workflow_stages.map((st) => {
                  const Icon = STAGE_ICONS[st.key] ?? Network;
                  const isActive = st.status === "ACTIVE";
                  return (
                    <Link
                      key={st.key}
                      href={st.href}
                      data-testid={`workflow-stage-${st.key}`}
                      className={cn(
                        "group flex flex-col justify-between rounded-lg border bg-card p-2.5 transition hover:border-primary/50 hover:shadow-sm",
                        isActive && "border-primary/60 ring-1 ring-primary/20",
                      )}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="inline-flex items-center gap-1.5 text-xs font-bold">
                          <span
                            className={cn(
                              "grid h-5 w-5 place-items-center rounded-full text-[11px]",
                              isActive
                                ? "bg-primary text-primary-foreground"
                                : "bg-muted text-muted-foreground",
                            )}
                          >
                            {st.step}
                          </span>
                          {st.label}
                        </span>
                        <Icon className="h-3.5 w-3.5 text-muted-foreground group-hover:text-primary" />
                      </div>
                      <p className="mt-1 text-[11px] text-muted-foreground">{st.detail}</p>
                    </Link>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
