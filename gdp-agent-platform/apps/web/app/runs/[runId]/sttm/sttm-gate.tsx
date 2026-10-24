"use client";

import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DecisionBar } from "@/components/decision-bar";

export function SttmGate({
  runId, currentState, lineCount,
}: {
  runId: string;
  currentState: string;
  lineCount: number;
}) {
  const router = useRouter();

  if (currentState === "STTM_APPROVED" || currentState === "SODA_PENDING") {
    return (
      <Card className="border-primary/30">
        <CardHeader>
          <CardTitle>Data Mapping approved</CardTitle>
          <CardDescription>Data Quality and dbt both open from this contract. Run them in either order.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Button onClick={() => router.push(`/os/build/runs/${runId}/soda`)}>Open Data Quality</Button>
          <Button variant="outline" onClick={() => router.push(`/os/build/runs/${runId}/dbt`)}>Open dbt</Button>
        </CardContent>
      </Card>
    );
  }

  if (currentState !== "STTM_REVIEW" || lineCount === 0) return null;

  return (
    <DecisionBar runId={runId} gate="Transformations and joins (Data Mapping)" role="Data Mapping approver" approveTo="STTM_APPROVED"
                 approveLabel="Approve Data Mapping" sendBack={["STTM_PENDING", "MAPPING_REVIEW"]}
                 defaultNote="Data Mapping matches the approved mappings and is ready for Data Quality checks."
                 detail="Approving opens QA, Data Quality and code generation in parallel." afterApprove={`/os/build/runs/${runId}/soda`} />
  );
}
