"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import type { RunState } from "@/lib/types";
import type { ActionResult } from "@/lib/api";
import { Loader2 } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { reviewRun, transitionRun } from "./actions";

const DECISIONS = ["APPROVE", "REQUEST_CHANGES", "REJECT", "REOPEN"];

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
  SOURCE_REGISTERED: { slug: "source", label: "Select objects and validate access" },
  ACCESS_APPROVED: { slug: "access", label: "Start landing" },
  LANDING_PENDING: { slug: "landing", label: "Start landing" },
  LANDING_COMPLETE: { slug: "profile", label: "Start profiling" },
  PROFILING_COMPLETE: { slug: "mapping", label: "Generate mapping candidates" },
  DOMAIN_IDENTIFIED: { slug: "mapping", label: "Generate mapping candidates" },
  MAPPING_REVIEW: { slug: "mapping", label: "Approve the mapping pack" },
  MAPPING_APPROVED: { slug: "sttm", label: "Generate the STTM" },
  STTM_REVIEW: { slug: "sttm", label: "Approve the STTM and continue to Soda" },
  STTM_APPROVED: { slug: "soda", label: "Generate Soda checks" },
  SODA_REVIEW: { slug: "soda", label: "Review Soda checks" },
  SODA_APPROVED: { slug: "dbt", label: "Generate dbt" },
  VALIDATION_PENDING: { slug: "validation", label: "Run validation" },
  VALIDATION_FAILED: { slug: "validation", label: "Inspect validation" },
  DBT_REVIEW: { slug: "review", label: "Review generated code" },
};

export function RunConsole({ state }: { state: RunState }) {
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const human = state.allowed_transitions.filter((t) => t.actor === "HUMAN");
  const system = state.allowed_transitions.filter(
    (t) => t.actor !== "HUMAN" && t.to_state !== "FAILED" &&
      (state.current_state === "FAILED" || !PROCEDURE_OWNED.has(t.to_state)),
  );
  const next = NEXT_STEP[state.current_state];

  const run = (fn: () => Promise<ActionResult>) =>
    start(async () => {
      setError("");
      const result = await fn();
      if (!result.ok) setError(result.error);
    });

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>{state.current_stage ?? "Run"} — {state.current_state}</CardTitle>
          <CardDescription>Only the transitions Snowflake allows from this state are offered.</CardDescription>
        </CardHeader>
        <CardContent>
          {state.failure_reason && <p className="mb-3 text-sm text-destructive">Failure: {state.failure_reason}</p>}
          {error && <p role="alert" className="mb-3 text-sm text-destructive">{error}</p>}
          {next && (
            <Link href={`/runs/${state.run_id}/${next.slug}`} className={buttonVariants({ className: "mb-3" })}>
              Next: {next.label}
            </Link>
          )}
          <div className="flex flex-wrap gap-2">
            {system.map((t) => (
              <Button
                key={t.to_state}
                variant={t.to_state === "CANCELLED" || next ? "outline" : "default"}
                disabled={pending}
                onClick={() => run(() => transitionRun(state.run_id, t.to_state))}
              >
                {pending && <Loader2 className="h-4 w-4 animate-spin" />}
                {t.to_state}
              </Button>
            ))}
            {system.length === 0 && human.length === 0 && !next && (
              <p className="text-sm text-muted-foreground">No actions available.</p>
            )}
          </div>
        </CardContent>
      </Card>

      {human.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Human review</CardTitle>
            <CardDescription>Approval requires a business justification. The reviewer is your Snowflake user.</CardDescription>
          </CardHeader>
          <CardContent>
            <form action={(form) => run(() => reviewRun(state.run_id, form))}>
              <Label htmlFor="to_state">Move to</Label>
              <Select id="to_state" name="to_state">
                {human.map((t) => <option key={t.to_state} value={t.to_state}>{t.to_state}</option>)}
              </Select>
              <Label htmlFor="decision">Decision</Label>
              <Select id="decision" name="decision">
                {DECISIONS.map((d) => <option key={d}>{d}</option>)}
              </Select>
              <Label htmlFor="justification">Business justification</Label>
              <Textarea id="justification" name="justification" rows={3} />
              <Label htmlFor="comments">Comments</Label>
              <Input id="comments" name="comments" />
              <Button type="submit" className="mt-4" disabled={pending}>
                {pending && <Loader2 className="h-4 w-4 animate-spin" />}
                {pending ? "Submitting…" : "Submit review"}
              </Button>
            </form>
          </CardContent>
        </Card>
      )}
    </>
  );
}
