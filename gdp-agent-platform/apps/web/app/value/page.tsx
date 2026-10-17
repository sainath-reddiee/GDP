import Link from "next/link";
import { api, ApiError, whoami } from "@/lib/api";
import { can } from "@/lib/types";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/page-header";
import type { ValueReport } from "@/lib/value-types";
import { ValueView } from "./value-view";

const WINDOWS = [30, 90, 180];

/** The value ledger: what the platform's work is worth, measured from what it records, priced with editable baselines. */
export default async function ValuePage({ searchParams }: { searchParams?: { days?: string } }) {
  const me = await whoami();
  const days = WINDOWS.includes(Number(searchParams?.days)) ? Number(searchParams?.days) : 30;
  const header = (
    <PageHeader eyebrow="Work" title="Value"
                description="Hours the platform saved, what that is worth after AI cost, how much faster failures get repaired, and how much the platform knows. Counts are measured; minutes per item are labelled assumptions you can tune."
                actions={<div className="flex gap-1" role="group" aria-label="Window">
                  {WINDOWS.map((d) => (
                    <Link key={d} href={`/value?days=${d}`} aria-current={d === days ? "page" : undefined}
                          className={cn("rounded-full border px-3 py-1 text-xs font-medium", d === days ? "border-primary bg-primary/10 text-primary" : "hover:bg-muted")}>
                      {d} days
                    </Link>
                  ))}
                </div>} />
  );
  if (!can(me, "AUDIT.VIEW")) {
    return <div className="space-y-5">{header}<p className="text-sm text-muted-foreground">The value ledger needs the AUDIT.VIEW privilege.</p></div>;
  }
  let data: ValueReport | null = null;
  let error = "";
  try {
    data = await api<ValueReport>(`/api/value?days=${days}`);
  } catch (e) {
    if (!(e instanceof ApiError)) throw e;
    error = e.message;
  }
  return (
    <div className="space-y-5">
      {header}
      {data ? <ValueView data={data} canEdit={can(me, "CONFIG.EDIT")} />
        : <p role="alert" className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">The value ledger did not load: {error}</p>}
    </div>
  );
}
