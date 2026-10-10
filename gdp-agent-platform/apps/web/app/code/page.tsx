import Link from "next/link";
import { Settings2 } from "lucide-react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import type { CodeRepo } from "./actions";
import { CodeExplorer, type RepoSummary } from "./code-explorer";

export default async function Code({ searchParams }: { searchParams?: { repo?: string; path?: string; line?: string; q?: string } }) {
  const [repos, summary] = await Promise.all([
    api<{ repos: CodeRepo[]; ready: boolean }>("/api/code/repos").catch(() => ({ repos: [] as CodeRepo[], ready: false })),
    api<{ repos: Record<string, RepoSummary> }>("/api/code/summary").catch(() => ({ repos: {} as Record<string, RepoSummary> })),
  ]);
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Knowledge" title="Code"
                  description="The client's own dbt and SQL code, indexed from their repositories. AI steps quote the relevant models, macros and tests from here, and cite the file and lines they used."
                  actions={
                    <Link href="/admin?section=integrations" className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-3 py-1.5 text-sm font-medium shadow-xs hover:bg-muted">
                      <Settings2 className="h-4 w-4" />Manage repositories
                    </Link>
                  } />
      {!repos.ready
        ? <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">Code repositories need the latest deploy (migration V024).</p>
        : <CodeExplorer repos={repos.repos} summary={summary.repos} initial={searchParams ?? {}} />}
    </div>
  );
}
