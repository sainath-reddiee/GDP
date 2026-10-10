import Link from "next/link";
import { api, ApiError, whoami } from "@/lib/api";
import { can } from "@/lib/types";
import type { CaseDetail } from "../actions";
import { CaseView } from "./case-view";

function Crumb() {
  return <p className="eyebrow"><Link href="/qa?tab=cases" className="hover:underline">QA cases</Link></p>;
}

/** One case. `?opened=new|existing` (set by the intake buttons) repeats what opening it did. */
export default async function CasePage({ params, searchParams }: { params: { id: string }; searchParams?: { opened?: string } }) {
  let id = params.id;
  try { id = decodeURIComponent(params.id); } catch { /* already decoded */ }
  const me = await whoami();
  let detail: CaseDetail;
  try {
    detail = await api<CaseDetail>(`/api/cases/${encodeURIComponent(id)}`);
  } catch (e) {
    if (!(e instanceof ApiError)) throw e;
    return (
      <div className="space-y-3">
        <Crumb />
        <p role="alert" className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
          {e.status === 404 ? "This case does not exist or is outside your domains." : `The case did not load: ${e.message}`}
        </p>
      </div>
    );
  }
  const opened = searchParams?.opened === "new" ? "new" : searchParams?.opened === "existing" ? "existing" : null;
  return <CaseView key={id} initial={detail} canWork={can(me, "CASE.WORK")} canResolve={can(me, "CASE.RESOLVE")}
                   canAI={can(me, "AI.USE")} canDbt={can(me, "DBT.EDIT")} canJira={can(me, "JIRA.WRITE")} opened={opened} />;
}
