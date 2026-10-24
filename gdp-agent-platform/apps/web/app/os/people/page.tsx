import Link from "next/link";
import { Layers, ShieldCheck, Users } from "lucide-react";
import { api } from "@/lib/api";
import { guard, type PersonaInfo } from "@/lib/os/session";
import { AddPerson, BundleEditor, PersonaPicker, PreviewPersona } from "@/components/os/people-ui";
import { Banner, Btn, Card, Empty, Kpi, PageHeader, SourceError, Tag, pretty } from "@/components/os/ui";

export const dynamic = "force-dynamic";
export const metadata = { title: "People and personas" };

type Person = { user: string; personas: string[]; roles: string[]; matching: string[]; direct_roles: string[];
  domains: { domain_id: string; domain_name: string; role: string }[] };

async function read<T>(path: string): Promise<{ data: T | null; error: string | null }> {
  try { return { data: await api<T>(path), error: null }; }
  catch (e) { return { data: null, error: e instanceof Error ? e.message : String(e) }; }
}

/** Admin, People (super admins only): give people personas, see what each persona grants, and preview one. Raw role
 *  toggles stay in Platform settings, Access, for the rare exception. */
export default async function People() {
  await guard("people");
  const [people, personas] = await Promise.all([
    read<{ people: Person[]; default_role: string | null }>("/api/governance/people"),
    read<{ personas: PersonaInfo[]; roles: { role: string; description: string | null; retired: boolean }[] }>("/api/governance/personas"),
  ]);
  const options = (personas.data?.personas ?? []).map(({ persona_id, label, description }) => ({ persona_id, label, description }));
  const label = (id: string) => options.find((o) => o.persona_id === id)?.label ?? pretty(id);
  const list = people.data?.people ?? [];
  const withPersona = list.filter((p) => p.personas.length);
  const pending = list.filter((p) => !p.personas.length && p.roles.length);
  const viewers = list.filter((p) => !p.personas.length && !p.roles.length);

  return (
    <>
      <PageHeader eyebrow="Admin / Super admins only" title="People and personas"
                  subtitle="Give people personas instead of roles. Each persona is a bundle of governance roles; the platform grants the roles and shows each person only the personas they hold. Anyone without a persona is a Viewer."
                  actions={<Btn href="/os/admin/settings?section=access">Advanced: roles and policies</Btn>} />
      <SourceError what="People" error={people.error} />
      <SourceError what="Personas" error={personas.error} />
      {pending.length > 0 && (
        <Banner tone="amber" icon={ShieldCheck}>
          <b>{pending.length} {pending.length === 1 ? "person has" : "people have"} roles but no persona yet</b> (from before personas). Their access is unchanged; give them the persona their roles match, or the ones they should have.
        </Banner>
      )}

      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi label="People with a persona" value={withPersona.length} icon={Users} tone="green" />
        <Kpi label="Roles but no persona" value={pending.length} hint="from before personas" icon={ShieldCheck} tone={pending.length ? "amber" : "green"} />
        <Kpi label="Viewers" value={viewers.length} hint={`default role: ${people.data?.default_role ?? "VIEWER"}`} icon={Users} tone="neutral" />
        <Kpi label="Personas" value={options.length} hint="bundles of roles" icon={Layers} tone="blue" />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Card title="People" subtitle="Who does what: their personas, the roles those grant, and the domains they own" pad={false}>
          {list.length ? (
            <div className="os-divide">
              {[...pending, ...withPersona, ...viewers].map((p) => (
                <div key={p.user} className="grid gap-2 px-5 py-3.5 lg:grid-cols-[minmax(0,180px)_minmax(0,1fr)]" data-testid={`person-${p.user}`}>
                  <div className="min-w-0">
                    <div className="truncate text-[13px] font-extrabold">{p.user}</div>
                    <div className="os-faint mt-0.5 text-[10.5px] leading-snug" title={p.roles.join(", ")}>
                      {p.roles.length ? p.roles.map((r) => r.toLowerCase()).join(", ") : "no role (Viewer)"}
                    </div>
                    {p.direct_roles.length > 0 && <div className="mt-1 text-[10.5px] text-[var(--os-amber-ink)]" title="Given outside any persona, in Access">+ {p.direct_roles.map((r) => r.toLowerCase()).join(", ")}</div>}
                    {p.domains.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {p.domains.map((d) => <Link key={d.domain_id + d.role} href={`/os/knowledge/domains/${d.domain_id}`}><Tag tone="blue">{d.domain_name} · {pretty(d.role)}</Tag></Link>)}
                      </div>
                    )}
                  </div>
                  <div>
                    {!p.personas.length && p.matching.length > 0 && (
                      <div className="os-faint mb-1.5 text-[11px]">Their roles match: {p.matching.map(label).join(", ")}</div>
                    )}
                    <PersonaPicker user={p.user} held={p.personas} suggested={p.matching} options={options} testId={`personas-${p.user}`} />
                  </div>
                </div>
              ))}
            </div>
          ) : <div className="p-5"><Empty icon={Users} title="Nobody yet" /></div>}
          <div className="border-t border-[var(--os-line)] px-5 py-4"><AddPerson options={options} /></div>
        </Card>

        <Card title="Personas" subtitle="What each persona grants. Changing a bundle moves everyone who holds it; Super admin always grants SUPER_ADMIN." pad={false}
              action={<PreviewPersona persona="viewer" label="Viewer" />}>
          <div className="os-divide">
            {(personas.data?.personas ?? []).map((p) => (
              <div key={p.persona_id} className="px-5 py-3.5">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-[13px] font-extrabold">{p.label}</div>
                    <div className="os-faint text-[11px]">{p.description}</div>
                    <div className="os-faint mt-0.5 text-[10.5px]">{p.holders.length} {p.holders.length === 1 ? "person" : "people"}{p.holders.length ? `: ${p.holders.join(", ")}` : ""} · {p.effective.length} privileges</div>
                  </div>
                  {p.persona_id !== "super" && <PreviewPersona persona={p.persona_id} label={p.label} />}
                </div>
                <div className="mt-2"><BundleEditor persona={p.persona_id} roles={p.roles} all={personas.data?.roles ?? []} /></div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
