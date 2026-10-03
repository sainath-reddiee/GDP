"use client";

import { useState, useTransition } from "react";
import { Button } from "@/components/ui/button";
import { Label, Textarea } from "@/components/ui/input";
import { importSoda } from "../pipeline-actions";

export function ImportSodaForm({ runId }: { runId: string }) {
  const [text, setText] = useState('[{"attribute":"EMAIL_ADDRESS","check_type":"CUSTOM","severity":"WARN","requirement":"Email should look valid"}]');
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  return (
    <div>
      <Label htmlFor="client_rows">Client expectation rows (JSON array)</Label>
      <Textarea id="client_rows" rows={5} value={text} onChange={(e) => setText(e.target.value)} />
      <Button
        className="mt-3"
        variant="outline"
        disabled={pending}
        onClick={() => start(async () => {
          setError("");
          const result = await importSoda(runId, text);
          if (!result.ok) setError(result.error);
        })}
      >
        {pending ? "Importing…" : "Import client checks"}
      </Button>
      {error && <p role="alert" className="mt-2 text-sm text-destructive">{error}</p>}
    </div>
  );
}
