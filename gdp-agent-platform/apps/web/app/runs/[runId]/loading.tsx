export default function RunLoading() {
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <div className="h-7 w-56 animate-pulse rounded-md bg-muted" />
        <div className="h-5 w-20 animate-pulse rounded-full bg-muted" />
      </div>
      <div className="flex gap-5">
        <div className="h-96 w-56 shrink-0 animate-pulse rounded-lg bg-muted" />
        <div className="h-64 min-w-0 flex-1 animate-pulse rounded-lg bg-muted" />
      </div>
    </div>
  );
}
