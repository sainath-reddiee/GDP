import type { ReactNode } from "react";
import { Lock } from "lucide-react";
import type { RunState } from "@/lib/types";
import { STAGES } from "@/lib/stages";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/** Renders children only when the backend reports the stage as reachable. */
export function StageGate({ state, stage, children }: { state: RunState; stage: string; children: ReactNode }) {
  const status = state.stages.find((s) => s.stage === stage)?.status ?? "LOCKED";
  if (status !== "LOCKED") return <>{children}</>;
  const index = STAGES.findIndex((s) => s.stage === stage);
  const previous = index > 0 ? STAGES[index - 1].label : "the previous stage";
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Lock className="h-4 w-4" /> Stage locked</CardTitle>
        <CardDescription>
          This stage opens after {previous} is complete. The run is currently at {state.current_state}.
        </CardDescription>
      </CardHeader>
    </Card>
  );
}
