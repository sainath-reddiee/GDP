"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import type { RunState } from "@/lib/types";
import type { ActionResult } from "@/lib/api";
import { ArrowRight, Bot, Loader2, TriangleAlert } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { DecisionBar } from "@/components/decision-bar";
import { transitionRun } from "./actions";
import { labelFor, pipelineOf } from "@/lib/stages";
import { GATES, stateLabel } from "@/lib/run-labels";

/** States entered only through their stage action (mirrors PROCEDURE_OWNED_STATES in the API). */
const PROCEDURE_OWNED = new Set([
  "SOURCE_REGISTERED", "ACCESS_VALIDATION", "ACCESS_APPROVED", "LANDING_PENDING", "LANDING_RUNNING", "LANDING_COMPLETE",
  "PROFILING_PENDING", "PROFILING_RUNNING", "PROFILING_COMPLETE", "DOMAIN_IDENTIFIED",
  "MAPPING_PENDING", "MAPPING_REVIEW", "STTM_PENDING", "STTM_REVIEW", "SODA_PENDING", "SODA_REVIEW",
  "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING", "VALIDATION_RUNNING", "VALIDATION_PASSED",
  "VALIDATION_FAILED", "DBT_REVIEW", "COMPLETED",
]);

const NEXT_STEP: Record<string, { slug: string; label: string }> = {
  CREATED: { slug: "source", label: "Register the source" },
  SOURCE_REGISTERED: { slug: "source", label: "Validate & land the source" },
  ACCESS_APPROVED: { slug: "source", label: "Resume landing" },
  LANDING_PENDING: { slug: "source", label: "Resume landing" },
  LANDING_COMPLETE: { slug: "profile", label: "Start profiling" },
  PROFILING_COMPLETE: { slug: "modeling?tab=mapping", label: "Mapping: generate the field mapping" },
  DOMAIN_IDENTIFIED: { slug: "modeling?tab=mapping", label: "Mapping: generate the field mapping" },
  MAPPING_REVIEW: { slug: "modeling?tab=mapping", label: "Mapping: approve the field mapping" },
  MAPPING_APPROVED: { slug: "modeling?tab=transformations", label: "Mapping: generate transformations and joins" },
  STTM_REVIEW: { slug: "modeling?tab=transformations", label: "Mapping: approve transformations and joins" },
  STTM_APPROVED: { slug: "soda", label: "Start Data Quality or Silver code generation" },
  SODA_REVIEW: { slug: "soda", label: "Approve the Data Quality pack" },
  SODA_APPROVED: { slug: "dbt", label: "Generate or review the Silver code" },
  DBT_PENDING: { slug: "dbt", label: "Generate the Silver code" },
  DBT_GENERATING: { slug: "dbt", label: "Review the generated Silver code" },
  VALIDATION_PENDING: { slug: "validation", label: "Run validation" },
  VALIDATION_FAILED: { slug: "validation", label: "Inspect validation" },
  DBT_REVIEW: { slug: "review", label: "Review generated code" },
};

/** Where the run is, in plain words, and the one thing to do next. A run at a gate gets the decision bar; cancelling
 *  lives in the run's menu (components/run-menu), and other moves the state machine allows are offered by name. */
export function RunConsole({ state, onGatePage = false }: { state: RunState; onGatePage?: boolean }) {
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const human = state.allowed_transitions.filter((t) => t.actor === "HUMAN");
  const system = state.allowed_transitions.filter(
    (t) => t.actor !== "HUMAN" && t.to_state !== "FAILED" && t.to_state !== "CANCELLED" &&
      (state.current_state === "FAILED" || !PROCEDURE_OWNED.has(t.to_state)),
  );
  const pipeline = pipelineOf(state.run?.pipeline);
  const gold = pipeline === "GOLD";
  const step = NEXT_STEP[state.current_state];
  // a Gold run: the code is gold, and it picks its gold table before mapping
  const next = step && gold ? {
    ...step,
    label: ["PROFILING_COMPLETE", "DOMAIN_IDENTIFIED"].includes(state.current_state) && !state.run?.target_model
      ? "Modeling: design the gold model and pick the table to build" : step.label.replace(/Silver code/g, "Gold code"),
  } : step;
  const gate = GATES[state.current_state];
  const approveTo = human.find((t) => t.to_state.endsWith("_APPROVED"))?.to_state ?? gate?.approveTo;
  const sendBack = human.filter((t) => !t.to_state.endsWith("_APPROVED")).map((t) => t.to_state);
  const lanesOpen = ["STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW"].includes(state.current_state);

  const run = (fn: () => Promise<ActionResult>) =>
    start(async () => {
      setError("");
      const result = await fn();
      if (!result.ok) setError(result.error);
    });

  return (
    <>
      <Card>
        <CardContent className="flex flex-wrap items-center gap-4 p-5">
          <span className={gate ? "grid h-10 w-10 place-items-center rounded-xl bg-human/10 text-human" : "grid h-10 w-10 place-items-center rounded-xl bg-agent/10 text-agent"}>
            {state.failure_reason ? <TriangleAlert className="h-5 w-5 text-destructive" /> : <Bot className="h-5 w-5" />}
          </span>
          <div className="min-w-[14rem] flex-1">
            <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
              {state.current_stage ? labelFor(state.current_stage, pipeline) : "Run"}
            </p>
            <p className="text-lg font-semibold" title={state.current_state}>{stateLabel(state.current_state)}</p>
            {state.failure_reason && <p className="text-sm text-destructive">Failure: {state.failure_reason}</p>}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {lanesOpen ? (
              <>
                <Link href={`/runs/${state.run_id}/soda`} className={buttonVariants()}>Open Data Quality <ArrowRight className="h-4 w-4" /></Link>
                <Link href={`/runs/${state.run_id}/dbt`} className={buttonVariants({ variant: "outline" })}>Open {labelFor("DBT", pipeline)}</Link>
              </>
            ) : next && !gate && (
              <Link href={`/runs/${state.run_id}/${next.slug}`} className={buttonVariants()}>
                {next.label} <ArrowRight className="h-4 w-4" />
              </Link>
            )}
            {system.map((t) => (
              <Button key={t.to_state} variant="outline" disabled={pending} title={t.to_state}
                      onClick={() => run(() => transitionRun(state.run_id, t.to_state))}>
                {pending && <Loader2 className="h-4 w-4 animate-spin" />}
                Move to: {stateLabel(t.to_state)}
              </Button>
            ))}
          </div>
          {error && <p role="alert" className="basis-full text-sm text-destructive">{error}</p>}
        </CardContent>
      </Card>

      {human.length > 0 && approveTo && (
        <DecisionBar runId={state.run_id} gate={gate?.name ?? stateLabel(state.current_state)} role={gate?.role}
                     approveTo={approveTo} sendBack={sendBack}
                     reviewHref={gate && !onGatePage ? `/runs/${state.run_id}/${gate.slug}` : undefined} />
      )}
    </>
  );
}
