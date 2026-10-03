"use client";

import { useState, useTransition } from "react";
import { Loader2 } from "lucide-react";
import type { ActionResult } from "@/lib/api";
import { Button, type ButtonProps } from "@/components/ui/button";

/** Button for a long-running stage action; shows progress and the backend's error message. */
export function StageAction({ action, label, pendingLabel, ...props }:
  { action: () => Promise<ActionResult>; label: string; pendingLabel: string } & ButtonProps) {
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  return (
    <div>
      <Button
        {...props}
        disabled={pending || props.disabled}
        onClick={() => start(async () => {
          setError("");
          const result = await action();
          if (!result.ok) setError(result.error);
        })}
      >
        {pending && <Loader2 className="h-4 w-4 animate-spin" />}
        {pending ? pendingLabel : label}
      </Button>
      {pending && (
        <p className="mt-2 text-xs text-muted-foreground">
          Working in Snowflake — this tab stays on the step until it finishes.
        </p>
      )}
      {error && <p role="alert" className="mt-2 text-sm text-destructive">{error}</p>}
    </div>
  );
}
