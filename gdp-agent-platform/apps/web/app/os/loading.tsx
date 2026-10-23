/** While an /os page reads the platform: its skeleton, inside the Agentic Data OS shell. */
export default function Loading() {
  const block = "os-card animate-pulse";
  return (
    <div aria-busy="true" aria-label="Loading">
      <div className="mb-5 space-y-2">
        <div className="h-3 w-48 rounded bg-[var(--os-line)]" />
        <div className="h-7 w-80 rounded bg-[var(--os-line)]" />
        <div className="h-3 w-[28rem] max-w-full rounded bg-[var(--os-line-soft)]" />
      </div>
      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => <div key={i} className={`${block} h-[94px]`} />)}
      </div>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className={`${block} h-[420px]`} />
        <div className={`${block} h-[420px]`} />
      </div>
    </div>
  );
}
