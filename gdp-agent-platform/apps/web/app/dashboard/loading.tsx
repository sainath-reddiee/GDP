/** Shown at once while a dashboard page (the Command Center, the Store hub or any store) loads its data, so a click
 * answers immediately instead of holding the previous page until everything has arrived. */
export default function DashboardLoading() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading">
      <div className="space-y-2">
        <div className="h-3 w-24 animate-pulse rounded bg-muted" />
        <div className="h-8 w-64 animate-pulse rounded-md bg-muted" />
        <div className="h-4 w-full max-w-2xl animate-pulse rounded bg-muted/70" />
      </div>
      <div className="h-11 animate-pulse rounded-xl bg-muted/70" />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-28 animate-pulse rounded-2xl bg-muted" />)}
      </div>
      <div className="h-72 animate-pulse rounded-2xl bg-muted/80" />
    </div>
  );
}
