import Link from "next/link";
import { AlertTriangle, ExternalLink, FileCode2, Network } from "lucide-react";
import type { DbtGeneration, RunModel } from "@/lib/store-types";
import { EmptyState, StatusPill } from "@/components/hub/states";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";

function Pr({ url, number, branch }: { url: string | null; number: number | null; branch: string | null }) {
  if (url) return <a href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">PR {number ?? ""} <ExternalLink className="h-3 w-3" /></a>;
  return branch ? <span className="font-mono text-xs">{branch}</span> : <span className="text-muted-foreground">-</span>;
}

/** Every run's latest model design, the run that owns it and where it was published. */
export function RunModels({ models }: { models: RunModel[] }) {
  if (!models.length) return <EmptyState icon={Network} title="No run model designs yet">A run's modeling step saves a design here.</EmptyState>;
  return (
    <section className="surface overflow-hidden" data-testid="run-models">
      <Table>
        <THead><TR><TH>Run</TH><TH>Design</TH><TH>Entities</TH><TH>Domain</TH><TH>Published</TH><TH>Saved</TH><TH /></TR></THead>
        <TBody>
          {models.map((m) => (
            <TR key={m.design_id} className="align-top">
              <TD><Link href={`/runs/${m.run_id}/domain`} className="font-medium text-primary hover:underline">{m.run_name ?? m.run_id.slice(0, 8)}</Link>
                <p className="text-[11px] text-muted-foreground">{(m.current_state ?? "").toLowerCase().replace(/_/g, " ")}</p></TD>
              <TD><span className="flex items-center gap-2"><StatusPill status={m.status} /> v{m.version}</span>
                <p className="text-[11px] text-muted-foreground">{[m.decision?.toLowerCase().replace(/_/g, " "), m.origin?.toLowerCase()].filter(Boolean).join(" · ")}</p></TD>
              <TD><span className="tabular-nums">{m.entity_count}</span>
                <p className="max-w-[260px] truncate font-mono text-[11px] text-muted-foreground" title={m.entities.map((e) => e.name).join(", ")}>
                  {m.entities.slice(0, 4).map((e) => e.name).join(", ")}{m.entities.length > 4 ? ` +${m.entities.length - 4}` : ""}</p></TD>
              <TD>{m.domain_name ?? "-"}</TD>
              <TD>{m.publication_status ? <span className="flex items-center gap-2"><StatusPill status={m.publication_status} /><Pr url={m.pr_url} number={m.pr_number} branch={m.head_branch} /></span> : <span className="text-muted-foreground">not yet</span>}</TD>
              <TD className="whitespace-nowrap text-xs text-muted-foreground">{m.created_at.slice(0, 16)}<br />{m.approved_by ? `approved by ${m.approved_by}` : m.created_by}</TD>
              <TD className="whitespace-nowrap text-xs"><Link href={`/dashboard/modeling?view=tables&run=${encodeURIComponent(m.run_id)}`} className="text-primary hover:underline">Table models</Link></TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </section>
  );
}

/** Every run's latest dbt generation; one opens in the file viewer, where edits go for approval. */
export function DbtGenerations({ generations }: { generations: DbtGeneration[] }) {
  if (!generations.length) return <EmptyState icon={FileCode2} title="No dbt generations yet">A run's dbt step stores its project here.</EmptyState>;
  return (
    <section className="surface overflow-hidden" data-testid="dbt-generations">
      <Table>
        <THead><TR><TH>Run</TH><TH>Target model</TH><TH>Generation</TH><TH>Files</TH><TH>Published</TH><TH>Generated</TH></TR></THead>
        <TBody>
          {generations.map((g) => (
            <TR key={g.generation_id} className="align-top">
              <TD><Link href={`/dashboard/modeling/dbt/${g.generation_id}`} className="font-medium text-primary hover:underline" data-testid={`dbt-${g.generation_id}`}>{g.run_name ?? g.run_id.slice(0, 8)}</Link>
                {g.sttm_changed && <p className="mt-0.5 flex items-center gap-1 text-[11px] text-amber-700 dark:text-amber-300"><AlertTriangle className="h-3 w-3" /> STTM changed since; regenerate</p>}</TD>
              <TD className="font-mono text-xs">{g.target_model ?? "-"}</TD>
              <TD><span className="flex items-center gap-2"><StatusPill status={g.generation_status} /> v{g.generation_version}</span></TD>
              <TD className="tabular-nums">{g.files_generated ?? "-"}</TD>
              <TD>{g.publication_status ? <span className="flex items-center gap-2"><StatusPill status={g.publication_status} /><Pr url={g.pr_url} number={g.pr_number} branch={g.head_branch} /></span> : <span className="text-muted-foreground">not yet</span>}</TD>
              <TD className="whitespace-nowrap text-xs text-muted-foreground">{g.created_at.slice(0, 16)}<br />{g.created_by}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </section>
  );
}
