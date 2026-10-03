import { notFound } from "next/navigation";
import { getRun } from "@/lib/api";
import { bySlug } from "@/lib/stages";
import { StageGate } from "@/components/stage-gate";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default async function StagePage({ params }: { params: { runId: string; stage: string } }) {
  const meta = bySlug(params.stage);
  if (!meta) notFound();
  const state = await getRun(params.runId);
  const status = state.stages.find((s) => s.stage === meta.stage)?.status;
  return (
    <StageGate state={state} stage={meta.stage}>
      <Card>
        <CardHeader>
          <CardTitle>{meta.label}</CardTitle>
          <CardDescription>
            Stage status {status}. The {meta.label.toLowerCase()} workspace is delivered in build phase {meta.phase};
            use the overview to move the run until then.
          </CardDescription>
        </CardHeader>
      </Card>
    </StageGate>
  );
}
