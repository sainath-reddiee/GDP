import { guard } from "@/lib/os/session";
import { PageHeader } from "@/components/os/ui";
import { ToastProvider } from "@/app/admin/admin-ui";
import { IntegrationsSection, type IntegrationView } from "@/app/admin/integrations-section";
import { loadIntegrations } from "@/app/admin/integrations-data";

export const dynamic = "force-dynamic";
export const metadata = { title: "Integrations" };

const VIEWS: IntegrationView[] = ["repos", "jira", "airflow", "incidents", "cases", "datadog"];

// The Data OS Administrator's integrations (INTEGRATION.MANAGE): code repositories, Jira, Airflow (MWAA), Datadog,
// incident routing and cases, the same screen super admins have in Platform settings. Setting up GitHub publishing
// deploys a procedure and stores a token, so it stays with super admins (ADMIN.DEPLOY).
export default async function EnableIntegrations({ searchParams }: { searchParams: { view?: string } }) {
  await guard("integrations");
  const data = await loadIntegrations();
  const view = VIEWS.find((v) => v === searchParams.view) ?? "repos";
  return (
    <ToastProvider>
      <PageHeader eyebrow="Enable / Data OS Administrator" title="Integrations"
                  subtitle="Connections to the client's tools, configured once and used by every run: code repositories, Jira, Airflow, Datadog, incident routing and cases." />
      {!data ? <p className="os-muted text-[13px]">The integrations could not be read from the API.</p>
        : "notReady" in data ? <p className="os-muted text-[13px]">Code repositories need the latest deploy (migration V024).</p>
          : <IntegrationsSection {...data} view={view} base="/os/enable/integrations" />}
    </ToastProvider>
  );
}
