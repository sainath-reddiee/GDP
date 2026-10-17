import Link from "next/link";
import { api, ApiError, whoami } from "@/lib/api";
import { can } from "@/lib/types";
import { FixStatusPill } from "@/components/ops/status-pill";
import { PermanentFixCard } from "@/components/ops/permanent-fix-card";
import type { PermanentFixRow } from "../../permanent-fix-actions";
import type { FixInput, FixSkill, KnownFix } from "../../resolve-actions";
import { FixActions, FixForm } from "../fix-form";

type Detail = { issue: KnownFix; skill: FixSkill | null; runs: { resolution_id: string; failure_id: string; outcome: string; actor: string | null;
  started_at: string; dag_id: string; task_id: string | null; run_id: string | null }[] };

function Crumb() {
  return <p className="eyebrow"><Link href="/ops/fixes" className="hover:underline">Known fixes</Link></p>;
}

/** A known fix: `new` (optionally `?incident=` to start from a solved incident) or an existing one. */
export default async function FixPage({ params, searchParams }: { params: { id: string }; searchParams?: { incident?: string } }) {
  const me = await whoami();
  if (!can(me, "OPS.VIEW")) return <div className="space-y-3"><Crumb /><p className="text-sm text-muted-foreground">Known fixes need the OPS.VIEW privilege.</p></div>;
  const skills = await api<FixSkill[]>("/api/ops/resolve/skills").catch(() => [] as FixSkill[]);

  if (params.id === "new") {
    let initial: FixInput = { fix_type: "PROCEDURE", approval_required: true };
    let note = "";
    if (searchParams?.incident) {
      try {
        initial = await api<FixInput>(`/api/ops/resolve/fixes/draft?incident_id=${encodeURIComponent(searchParams.incident)}`);
        note = "Prefilled from the solved incident: its error signature, DAG, task and match text.";
      } catch (e) {
        if (!(e instanceof ApiError)) throw e;
        note = `Could not prefill from the incident: ${e.message}`;
      }
    }
    return (
      <div className="max-w-3xl space-y-4">
        <Crumb />
        <h1 className="text-xl font-semibold">New known fix</h1>
        <p className="text-sm text-muted-foreground">Saved as a draft. Another person reviews and publishes it; until then it never runs.{note ? ` ${note}` : ""}</p>
        <FixForm initial={initial} issueId={null} skills={skills} editable={can(me, "OPS.OPERATE")} />
      </div>
    );
  }

  let data: Detail;
  try {
    data = await api<Detail>(`/api/ops/resolve/fixes/${encodeURIComponent(params.id)}`);
  } catch (e) {
    if (e instanceof ApiError) return <div className="space-y-3"><Crumb /><p role="alert" className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{e.message}</p></div>;
    throw e;
  }
  const k = data.issue;
  const proposals = await api<{ items: PermanentFixRow[] }>(`/api/ops/permanent-fixes?issue_id=${encodeURIComponent(k.issue_id)}&limit=1`)
    .catch(() => ({ items: [] as PermanentFixRow[] }));
  return (
    <div className="space-y-4">
      <Crumb />
      <h1 className="flex flex-wrap items-center gap-2 text-xl font-semibold">{k.title} <FixStatusPill status={k.status} /></h1>
      <p className="text-sm text-muted-foreground">
        v{k.version_no} · written by {k.created_by}{k.published_by ? ` · published by ${k.published_by}` : ""}
        {k.source_incident_id && <> · from <Link href={`/incidents/${encodeURIComponent(k.source_incident_id)}`} className="text-primary hover:underline">this incident</Link></>}
      </p>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <FixForm initial={k} issueId={k.issue_id} skills={skills} editable={k.status === "DRAFT" && can(me, "OPS.OPERATE")} />
        <div className="space-y-4">
          <FixActions fix={k} canPublish={can(me, "OPS.FIX_PUBLISH")} me={me?.user ?? ""} />
          <PermanentFixCard issueId={k.issue_id} published={k.status === "PUBLISHED"} latest={proposals.items[0] ?? null}
                            canDraft={can(me, "OPS.OPERATE") && can(me, "AI.USE")} runs30d={k.runs_30d} />
          <section className="surface p-4">
            <h2 className="mb-2 text-sm font-semibold">Runs</h2>
            {data.runs.length === 0 ? <p className="text-xs text-muted-foreground">Not run yet.</p> : (
              <ul className="divide-y text-xs">
                {data.runs.map((r) => (
                  <li key={r.resolution_id} className="flex items-center gap-2 py-1.5">
                    <span className={r.outcome === "SUCCESS" ? "font-semibold text-success" : "font-semibold text-destructive"}>{r.outcome.toLowerCase()}</span>
                    <Link href={`/ops/resolutions/${encodeURIComponent(r.failure_id)}`} className="min-w-0 flex-1 truncate hover:text-primary">{r.dag_id} · {r.task_id}</Link>
                    <span className="text-muted-foreground">{r.started_at?.slice(0, 16).replace("T", " ")}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
