export default function RunLoading() {
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <div className="h-7 w-56 animate-pulse rounded-md bg-muted" />
        <div className="h-5 w-20 animate-pulse rounded-full bg-muted" />
      </div>
      <div className="h-16 animate-pulse rounded-xl bg-muted" />
      <div className="h-64 animate-pulse rounded-lg bg-muted" />
    </div>
  );
}
