"use client";

import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";

/** Error boundary for the lifecycle hubs: one failing hub never takes the navigation down with it. */
export default function HubError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="surface flex flex-col items-center px-6 py-12 text-center">
      <span className="grid h-11 w-11 place-items-center rounded-2xl bg-rose-50 text-rose-600"><AlertTriangle className="h-5 w-5" /></span>
      <h2 className="mt-3 text-base font-semibold">This hub could not load</h2>
      <p className="mt-1 max-w-md text-sm text-muted-foreground">{error.message || "Something went wrong."}</p>
      <Button className="mt-4" variant="outline" onClick={reset}>Try again</Button>
    </div>
  );
}
