"use client";

import { useState, useTransition } from "react";
import { Archive, ArchiveRestore } from "lucide-react";
import { setRunsArchived } from "@/app/runs/actions";
import { Button } from "@/components/ui/button";

export function ArchiveToggle({ runId, archived }: { runId: string; archived: boolean }) {
  const [pending, start] = useTransition();
  const [error, setError] = useState("");
  const run = () =>
    start(async () => {
      setError("");
      const result = await setRunsArchived([runId], !archived);
      if (!result.ok) setError(result.error);
      else if (result.data.skipped.length) setError(result.data.skipped[0].reason);
    });
  return (
    <span className="inline-flex items-center gap-2">
      <Button size="sm" variant="outline" disabled={pending} onClick={run}>
        {archived ? <ArchiveRestore className="h-3.5 w-3.5" /> : <Archive className="h-3.5 w-3.5" />}
        {pending ? (archived ? "Restoring…" : "Archiving…") : archived ? "Restore run" : "Archive run"}
      </Button>
      {error && <span role="alert" className="text-xs text-destructive">{error}</span>}
    </span>
  );
}
