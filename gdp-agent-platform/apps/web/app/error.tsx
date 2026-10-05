"use client";

export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div className="mx-auto max-w-xl space-y-3 py-16">
      <p className="text-sm font-medium text-destructive">The workspace API did not respond</p>
      <p className="text-sm text-muted-foreground">{error.message}</p>
      <button
        type="button"
        onClick={reset}
        className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground"
      >
        Try again
      </button>
    </div>
  );
}
