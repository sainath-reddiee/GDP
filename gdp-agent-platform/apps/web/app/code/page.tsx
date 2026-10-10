import Link from "next/link";
import { Settings2 } from "lucide-react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import type { CodeRepo, CodeUsage } from "./actions";
import { CodeExplorer, type RepoSummary } from "./code-explorer";

type Params = { repo?: string; path?: string; line?: string; q?: string; tab?: string; node?: string };

export default async function Code({ searchParams }: { searchParams?: Params }) {
  const [repos, summary, usage] = await Promise.all([
    api<{ repos: CodeRepo[]; ready: boolean }>("/api/code/repos").catch(() => ({ repos: [] as CodeRepo[], ready: false })),
    api<{ repos: Record<string, RepoSummary> }>("/api/code/summary").catch(() => ({ repos: {} as Record<string, RepoSummary> })),
    api<CodeUsage>("/api/code/usage?days=30").catch(() => null),
  ]);
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Knowledge" title="Code"
                  description="The client's dbt, SQL and Python code, indexed from their repositories: models, tests, lineage and what each change affects. AI steps quote it with file and line citations, and the dbt workspace builds on it."
                  actions={
                    <Link href="/admin?section=integrations" className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-3 py-1.5 text-sm font-medium shadow-xs hover:bg-muted">
                      <Settings2 className="h-4 w-4" />Manage repositories
                    </Link>
                  } />
      {!repos.ready
        ? <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">Code repositories need the latest deploy (migration V024).</p>
        : <CodeExplorer repos={repos.repos} summary={summary.repos} usage={usage} initial={searchParams ?? {}} />}
    </div>
  );
}
