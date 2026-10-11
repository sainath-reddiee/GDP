import Link from "next/link";
import { notFound } from "next/navigation";
import { ApiError, api } from "@/lib/api";
import type { SttmEntry, SttmVersion } from "@/lib/hub-types";
import { PageHeader } from "@/components/page-header";
import { StatusPill } from "@/components/hub/states";
import { SttmEditor } from "./editor";

export default async function SttmDetail({ params, searchParams }: { params: { catalogId: string }; searchParams: { version?: string } }) {
  const qs = searchParams.version && /^\d+$/.test(searchParams.version) ? `?version=${searchParams.version}` : "";
  const data = await api<{ mapping: SttmEntry; versions: SttmVersion[] }>(`/api/hub/sttm/${encodeURIComponent(params.catalogId)}${qs}`)
    .catch((e) => { if (e instanceof ApiError && e.status === 404) notFound(); throw e; });
  const m = data.mapping;
  const latest = data.versions[0]?.version ?? m.version;
  return (
    <div className="space-y-6">
      <p className="eyebrow"><Link href="/dashboard/sttm" className="hover:underline">STTM Catalog</Link></p>
      <PageHeader title={<span className="font-mono">{m.target_entity}</span>}
                  description={<>From {m.source_system ?? "source"} ({m.source_entities.join(", ") || "no source tables"}){m.domain_name ? ` · domain ${m.domain_name}` : ""}{m.run_id ? <> · <Link className="text-primary hover:underline" href={`/runs/${m.run_id}/sttm`}>run</Link></> : null}</>}
                  actions={<><StatusPill status={m.status} /><a className="text-sm font-medium text-primary hover:underline" href={`/bff/sttm-export/${m.catalog_id}?version=${m.version}`}>Export v{m.version} as dbt</a></>} />
      <SttmEditor entry={m} versions={data.versions} latest={latest} />
    </div>
  );
}
