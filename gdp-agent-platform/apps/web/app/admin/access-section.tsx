"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Check, Loader2, Plus, Trash2, UserPlus, Users2, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  deleteRole, roleMember, savePolicy, saveRole, saveSettings, setUserRoles,
  type GovEvent, type GovPolicy, type GovPrivilege, type GovRole, type GovSettings, type GovUser,
} from "../governance-actions";

type Tab = "users" | "roles" | "policies" | "settings" | "log";

function useAction() {
  const router = useRouter();
  const [busy, start] = useTransition();
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const run = (fn: () => Promise<{ ok: true; data: unknown } | { ok: false; error: string }>, success: string) => start(async () => {
    setMsg(null);
    const r = await fn();
    if (!r.ok) setMsg({ ok: false, text: r.error });
    else { setMsg({ ok: true, text: success }); router.refresh(); }
  });
  return { busy, msg, run };
}

function Message({ msg }: { msg: { ok: boolean; text: string } | null }) {
  if (!msg) return null;
  return <p role={msg.ok ? "status" : "alert"} className={cn("text-xs", msg.ok ? "text-success" : "text-destructive")}>{msg.text}</p>;
}

function Users({ users, roles }: { users: GovUser[]; roles: GovRole[] }) {
  const { busy, msg, run } = useAction();
  const [edit, setEdit] = useState<Record<string, string[]>>({});
  const [newUser, setNewUser] = useState("");
  const all = users;
  const current = (u: GovUser) => edit[u.user] ?? u.roles;
  const toggle = (u: GovUser, role: string) => {
    const now = current(u);
    setEdit({ ...edit, [u.user]: now.includes(role) ? now.filter((r) => r !== role) : [...now, role] });
  };
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-2">
        <label className="space-y-1 text-xs">Add a user by Snowflake name
          <Input value={newUser} onChange={(e) => setNewUser(e.target.value.toUpperCase())} placeholder="JANE.DOE" className="w-64 font-mono" />
        </label>
        <Button size="sm" variant="outline" disabled={busy || !newUser.trim()}
                onClick={() => run(() => setUserRoles(newUser.trim(), ["VIEWER"]), `${newUser} added as viewer`)}>
          <UserPlus className="h-3.5 w-3.5" />Add as viewer
        </Button>
        <Message msg={msg} />
      </div>
      <div className="overflow-x-auto rounded-xl border">
        <table className="w-full min-w-[760px] text-sm">
          <thead className="bg-muted/50 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
            <tr><th className="px-3 py-2">User</th><th className="px-3 py-2">Roles</th><th className="w-28 px-3 py-2" /></tr>
          </thead>
          <tbody>
            {all.map((u) => {
              const changed = edit[u.user] !== undefined && edit[u.user].slice().sort().join() !== u.roles.slice().sort().join();
              return (
                <tr key={u.user} className="border-t align-top">
                  <td className="px-3 py-2 font-mono text-xs">{u.user}{!u.roles.length && <span className="ml-2 text-[10px] text-muted-foreground">default role</span>}</td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {roles.map((r) => {
                        const on = current(u).includes(r.role);
                        return (
                          <button key={r.role} type="button" onClick={() => toggle(u, r.role)} aria-pressed={on} title={r.description ?? ""}
                                  className={cn("rounded-full border px-2 py-0.5 text-[11px]", on ? "border-primary bg-primary text-primary-foreground" : "bg-card text-muted-foreground hover:border-primary/40")}>
                            {r.role.toLowerCase().replace(/_/g, " ")}
                          </button>
                        );
                      })}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-right">
                    {changed && (
                      <Button size="sm" disabled={busy} onClick={() => run(() => setUserRoles(u.user, current(u)), `${u.user} updated`)}>
                        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Save
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const pretty = (role: string) => role.toLowerCase().replace(/_/g, " ");

function Roles({ roles, privileges, users }: { roles: GovRole[]; privileges: GovPrivilege[]; users: GovUser[] }) {
  const { busy, msg, run } = useAction();
  const [selected, setSelected] = useState(roles.find((r) => r.role !== "SUPER_ADMIN")?.role ?? roles[0]?.role ?? "");
  const [creating, setCreating] = useState(false);
  const blank: GovRole = { role: "", description: "", system: false, privileges: [], inherits: ["VIEWER"], members: [] };
  const base = creating ? blank : roles.find((r) => r.role === selected) ?? blank;
  const [draft, setDraft] = useState<GovRole>(base);
  const [member, setMember] = useState("");
  const [query, setQuery] = useState("");
  const groups = useMemo(() => Array.from(new Set(privileges.map((p) => p.group))), [privileges]);
  const byName = useMemo(() => new Map(roles.map((r) => [r.role, r])), [roles]);

  // keep the editor in step with the server after a save (router.refresh) unless the user is mid-edit
  const dirty = creating || draft.description !== base.description
    || [...draft.privileges].sort().join() !== [...base.privileges].sort().join()
    || [...draft.inherits].sort().join() !== [...base.inherits].sort().join();
  useEffect(() => {
    if (!creating) setDraft(roles.find((r) => r.role === selected) ?? blank);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roles, selected]);

  const pick = (name: string) => { setCreating(false); setSelected(name); setDraft(roles.find((r) => r.role === name) ?? blank); };
  const startNew = () => { setCreating(true); setSelected(""); setDraft(blank); };
  const isSuper = draft.role === "SUPER_ADMIN";

  // what the draft would hold through inheritance (from each inherited role's effective privileges)
  const via = useMemo(() => {
    const out: Record<string, string> = {};
    for (const parent of draft.inherits) for (const p of byName.get(parent)?.effective ?? []) out[p] ??= parent;
    return out;
  }, [draft.inherits, byName]);
  const own = new Set(draft.privileges);
  const total = new Set([...draft.privileges, ...Object.keys(via)]);
  const viewOnly = !isSuper && Array.from(total).every((p) => ["AUDIT.VIEW", "ADMIN.VIEW", "APPROVAL.VIEW"].includes(p));
  const togglePriv = (p: string) => setDraft({ ...draft, privileges: own.has(p) ? draft.privileges.filter((x) => x !== p) : [...draft.privileges, p] });
  const toggleGroup = (g: string, on: boolean) => {
    const ps = privileges.filter((p) => p.group === g && !via[p.privilege]).map((p) => p.privilege);
    setDraft({ ...draft, privileges: on ? Array.from(new Set([...draft.privileges, ...ps])) : draft.privileges.filter((p) => !ps.includes(p)) });
  };
  const toggleInherit = (r: string) => setDraft({ ...draft, inherits: draft.inherits.includes(r) ? draft.inherits.filter((x) => x !== r) : [...draft.inherits, r] });
  const shown = roles.filter((r) => !query || `${r.role} ${r.description ?? ""}`.toLowerCase().includes(query.toLowerCase()));

  return (
    <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
      <div className="space-y-2">
        <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a role" aria-label="Find a role" />
        <div className="space-y-1">
          {shown.map((r) => (
            <button key={r.role} type="button" onClick={() => pick(r.role)}
                    className={cn("w-full rounded-lg border px-3 py-2 text-left transition",
                                  r.role === selected && !creating ? "border-primary bg-primary/5 ring-1 ring-primary/30" : "border-transparent hover:bg-muted")}>
              <span className="flex items-center gap-1.5">
                <span className="truncate text-sm font-medium capitalize">{pretty(r.role)}</span>
                {r.role === "SUPER_ADMIN" && <Badge variant="destructive" className="px-1.5 py-0 text-[9px]">all</Badge>}
                {r.read_only && <Badge variant="outline" className="px-1.5 py-0 text-[9px]">view only</Badge>}
                {r.approves && <Badge variant="outline" className="border-violet-300 px-1.5 py-0 text-[9px] text-violet-700">approver</Badge>}
                <span className="ml-auto text-[10px] text-muted-foreground" title="members">{r.members.length} <Users2 className="inline h-3 w-3" /></span>
              </span>
              <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">{r.description}</span>
              <span className="block text-[10px] text-muted-foreground">{r.role === "SUPER_ADMIN" ? "every privilege" : `${r.effective?.length ?? r.privileges.length} privileges`}{r.system ? " · system" : ""}</span>
            </button>
          ))}
        </div>
        <Button size="sm" variant="outline" className="w-full" onClick={startNew}><Plus className="h-3.5 w-3.5" />New role</Button>
      </div>

      <div className="relative space-y-4 rounded-xl border bg-card p-4 pb-16">
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1 space-y-1">
            {creating ? (
              <label className="block space-y-1 text-xs">Role name
                <Input value={draft.role} onChange={(e) => setDraft({ ...draft, role: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_") })}
                       className="w-64 font-mono" placeholder="FINANCE_REVIEWER" />
              </label>
            ) : (
              <h3 className="flex flex-wrap items-center gap-2 font-mono text-lg font-semibold">
                {draft.role}
                {draft.system && <Badge variant="outline">system</Badge>}
                {viewOnly && <Badge variant="outline" className="border-sky-300 text-sky-700">view only</Badge>}
              </h3>
            )}
            <p className="text-xs text-muted-foreground">
              {isSuper ? "Holds every privilege and can approve any request. Cannot be edited."
                : viewOnly ? "Can open every page but cannot change, run, request or use AI for anything."
                : `${total.size} privileges: ${own.size} granted directly, ${total.size - own.size} inherited.`}
            </p>
          </div>
          <label className="w-full space-y-1 text-xs sm:w-96">Description
            <Input value={draft.description ?? ""} disabled={isSuper} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
          </label>
        </div>

        {!creating && (
          <div className="rounded-lg border bg-muted/20 p-3">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Members</p>
            <div className="flex flex-wrap items-center gap-1.5">
              {draft.members.map((m) => (
                <span key={m} className="inline-flex items-center gap-1 rounded-full border bg-card px-2 py-0.5 font-mono text-[11px]">
                  {m}
                  <button type="button" aria-label={`Remove ${m}`} disabled={busy} onClick={() => run(() => roleMember(draft.role, m, "remove"), `${m} removed from ${draft.role}`)}
                          className="rounded-full hover:bg-muted"><X className="h-3 w-3" /></button>
                </span>
              ))}
              {draft.members.length === 0 && <span className="text-xs text-muted-foreground">No members yet.</span>}
              <span className="ml-auto flex items-center gap-1">
                <Input list="known-users" value={member} onChange={(e) => setMember(e.target.value.toUpperCase())} placeholder="SNOWFLAKE.USER"
                       className="h-8 w-48 font-mono text-xs" aria-label="Add member" />
                <datalist id="known-users">{users.map((u) => <option key={u.user} value={u.user} />)}</datalist>
                <Button size="sm" variant="outline" disabled={busy || !member.trim()}
                        onClick={() => { run(() => roleMember(draft.role, member.trim(), "add"), `${member} added to ${draft.role}`); setMember(""); }}>
                  <UserPlus className="h-3.5 w-3.5" />Add
                </Button>
              </span>
            </div>
          </div>
        )}

        {!isSuper && (
          <>
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Inherits</p>
              <p className="mb-2 text-[11px] text-muted-foreground">Everything the chosen roles can do, this role can do too (like GRANT ROLE ... TO ROLE in Snowflake).</p>
              <div className="flex flex-wrap gap-1.5">
                {roles.filter((r) => r.role !== draft.role && r.role !== "SUPER_ADMIN").map((r) => {
                  const on = draft.inherits.includes(r.role);
                  return (
                    <button key={r.role} type="button" onClick={() => toggleInherit(r.role)} aria-pressed={on}
                            className={cn("inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[11px] capitalize transition",
                                          on ? "border-violet-500 bg-violet-500 text-white" : "bg-card text-muted-foreground hover:border-violet-300")}>
                      {on && <Check className="h-3 w-3" />}{pretty(r.role)}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              {groups.map((g) => {
                const items = privileges.filter((p) => p.group === g);
                const ownable = items.filter((p) => !via[p.privilege]);
                const allOn = ownable.length > 0 && ownable.every((p) => own.has(p.privilege));
                return (
                  <div key={g} className="rounded-lg border p-3">
                    <div className="mb-1.5 flex items-center justify-between">
                      <p className="text-xs font-semibold">{g}</p>
                      {ownable.length > 0 && (
                        <button type="button" onClick={() => toggleGroup(g, !allOn)} className="text-[10px] text-primary hover:underline">
                          {allOn ? "clear" : "select all"}
                        </button>
                      )}
                    </div>
                    {items.map((p) => {
                      const inherited = via[p.privilege];
                      return (
                        <label key={p.privilege} className={cn("flex items-start gap-2 rounded px-1 py-1 text-xs", inherited ? "opacity-80" : "hover:bg-muted/40")}>
                          <input type="checkbox" className="mt-0.5" checked={own.has(p.privilege) || Boolean(inherited)}
                                 disabled={Boolean(inherited) && !own.has(p.privilege)} onChange={() => togglePriv(p.privilege)} />
                          <span className="min-w-0">
                            <span className="font-mono">{p.privilege}</span>
                            {inherited && !own.has(p.privilege) && (
                              <span className="ml-1.5 rounded-full bg-violet-100 px-1.5 py-0 text-[9px] text-violet-700 dark:bg-violet-950 dark:text-violet-300">via {pretty(inherited)}</span>
                            )}
                            <span className="block text-[11px] text-muted-foreground">{p.description}</span>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          </>
        )}

        {(dirty || msg || (!creating && !draft.system)) && !isSuper && (
          <div className="absolute inset-x-0 bottom-0 flex flex-wrap items-center gap-2 rounded-b-xl border-t bg-card/95 px-4 py-2 backdrop-blur">
            {dirty ? <span className="text-xs font-medium text-warning">Unsaved changes</span> : <Message msg={msg} />}
            <span className="ml-auto flex gap-2">
              {!creating && !draft.system && !dirty && (
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => run(() => deleteRole(draft.role), `${draft.role} deleted`)}>
                  <Trash2 className="h-3.5 w-3.5" />Delete role
                </Button>
              )}
              {dirty && <Button size="sm" variant="ghost" disabled={busy} onClick={() => (creating ? pick(roles[0]?.role ?? "") : setDraft(base))}>Discard</Button>}
              {dirty && (
                <Button size="sm" disabled={busy || !draft.role}
                        onClick={() => run(() => saveRole({ role: draft.role, description: draft.description, privileges: draft.privileges, inherits: draft.inherits }, creating),
                                           creating ? `${draft.role} created` : `${draft.role} saved`)}>
                  {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}{creating ? "Create role" : "Save role"}
                </Button>
              )}
            </span>
          </div>
        )}
        {dirty && msg && <Message msg={msg} />}
      </div>
    </div>
  );
}

function Policies({ policies, roles }: { policies: GovPolicy[]; roles: GovRole[] }) {
  const { busy, msg, run } = useAction();
  const [edits, setEdits] = useState<Record<string, GovPolicy>>({});
  const row = (p: GovPolicy) => edits[p.privilege] ?? p;
  const set = (p: GovPolicy, patch: Partial<GovPolicy>) => setEdits({ ...edits, [p.privilege]: { ...row(p), ...patch } });
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        People who hold a privilege act directly. Anyone else who may raise requests sends the change to the approver role.
        Four eyes makes even privilege holders ask. Changes here go through approval too.
      </p>
      <Message msg={msg} />
      <div className="overflow-x-auto rounded-xl border">
        <table className="w-full min-w-[860px] text-sm">
          <thead className="bg-muted/50 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
            <tr><th className="px-3 py-2">Action</th><th className="px-3 py-2">Approval</th><th className="px-3 py-2">Approver role</th>
              <th className="px-3 py-2">Four eyes</th><th className="px-3 py-2">Self approve</th><th className="w-24 px-3 py-2" /></tr>
          </thead>
          <tbody>
            {policies.map((p) => {
              const r = row(p);
              const changed = edits[p.privilege] !== undefined;
              return (
                <tr key={p.privilege} className="border-t">
                  <td className="px-3 py-2"><span className="font-mono text-xs">{p.privilege}</span><span className="block text-[11px] text-muted-foreground">{p.description}</span></td>
                  <td className="px-3 py-2"><input type="checkbox" aria-label="Requires approval" checked={r.requires_approval && r.active}
                                                   onChange={(e) => set(p, { requires_approval: e.target.checked, active: e.target.checked, approver_role: r.approver_role ?? "SUPER_ADMIN" })} /></td>
                  <td className="px-3 py-2">
                    <select className="h-8 rounded-lg border bg-card px-2 text-xs" value={r.approver_role ?? ""} onChange={(e) => set(p, { approver_role: e.target.value })} aria-label="Approver role">
                      <option value="">none</option>
                      {roles.map((x) => <option key={x.role} value={x.role}>{x.role}</option>)}
                    </select>
                  </td>
                  <td className="px-3 py-2"><input type="checkbox" aria-label="Four eyes" checked={r.four_eyes} onChange={(e) => set(p, { four_eyes: e.target.checked })} /></td>
                  <td className="px-3 py-2"><input type="checkbox" aria-label="Allow self approval" checked={r.allow_self} onChange={(e) => set(p, { allow_self: e.target.checked })} /></td>
                  <td className="px-3 py-2 text-right">
                    {changed && (
                      <Button size="sm" disabled={busy || !r.approver_role}
                              onClick={() => run(() => savePolicy(p.privilege, { requires_approval: r.requires_approval, approver_role: r.approver_role ?? "SUPER_ADMIN", four_eyes: r.four_eyes, allow_self: r.allow_self, active: r.active }),
                                                 `${p.privilege} saved`)}>Save</Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Settings({ settings, roles }: { settings: GovSettings; roles: GovRole[] }) {
  const { busy, msg, run } = useAction();
  const [s, setS] = useState(settings);
  return (
    <div className="max-w-xl space-y-3 rounded-xl border p-4 text-sm">
      <label className="flex items-center justify-between gap-3">Enforce roles and approvals
        <input type="checkbox" checked={s.ENFORCE} onChange={(e) => setS({ ...s, ENFORCE: e.target.checked })} />
      </label>
      <label className="flex items-center justify-between gap-3">Super admins may approve their own requests
        <input type="checkbox" checked={s.SUPER_SELF_APPROVE} onChange={(e) => setS({ ...s, SUPER_SELF_APPROVE: e.target.checked })} />
      </label>
      <label className="flex items-center justify-between gap-3">Role for signed-in users without one
        <select className="h-8 rounded-lg border bg-card px-2 text-xs" value={s.DEFAULT_ROLE ?? ""} onChange={(e) => setS({ ...s, DEFAULT_ROLE: e.target.value })}>
          <option value="">none (no access)</option>
          {roles.map((r) => <option key={r.role} value={r.role}>{r.role}</option>)}
        </select>
      </label>
      <div className="flex items-center gap-2 border-t pt-3">
        <Button size="sm" disabled={busy} onClick={() => run(() => saveSettings(s), "Settings saved")}>Save settings</Button>
        <Message msg={msg} />
      </div>
    </div>
  );
}

function Log({ events }: { events: GovEvent[] }) {
  if (!events.length) return <p className="text-sm text-muted-foreground">No governance events yet.</p>;
  return (
    <div className="overflow-x-auto rounded-xl border">
      <table className="w-full min-w-[700px] text-xs">
        <thead className="bg-muted/50 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
          <tr><th className="px-3 py-2">When</th><th className="px-3 py-2">Event</th><th className="px-3 py-2">By</th><th className="px-3 py-2">Target</th><th className="px-3 py-2">Detail</th></tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.event_id} className="border-t align-top">
              <td className="whitespace-nowrap px-3 py-1.5">{e.created_at.slice(0, 16)}</td>
              <td className="px-3 py-1.5"><Badge variant="outline">{e.event_type.toLowerCase()}</Badge></td>
              <td className="px-3 py-1.5 font-mono">{e.actor}</td>
              <td className="max-w-[12rem] truncate px-3 py-1.5 font-mono" title={e.target}>{e.target}</td>
              <td className="max-w-[24rem] truncate px-3 py-1.5 font-mono text-muted-foreground" title={JSON.stringify(e.detail)}>{JSON.stringify(e.detail)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AccessSection({ users, roles, privileges, policies, settings, events }: {
  users: GovUser[]; roles: GovRole[]; privileges: GovPrivilege[]; policies: GovPolicy[]; settings: GovSettings; events: GovEvent[];
}) {
  const [tab, setTab] = useState<Tab>("users");
  return (
    <div className="space-y-4">
      <div role="tablist" className="flex flex-wrap gap-1 border-b">
        {([["users", `Users · ${users.length}`], ["roles", `Roles · ${roles.length}`], ["policies", "Approval policies"], ["settings", "Settings"], ["log", "Log"]] as const).map(([k, l]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                  className={cn("-mb-px border-b-2 px-3 py-2 text-sm", tab === k ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground")}>
            {l}
          </button>
        ))}
      </div>
      {tab === "users" && <Users users={users} roles={roles} />}
      {tab === "roles" && <Roles roles={roles} privileges={privileges} users={users} />}
      {tab === "policies" && <Policies policies={policies} roles={roles} />}
      {tab === "settings" && <Settings settings={settings} roles={roles} />}
      {tab === "log" && <Log events={events} />}
    </div>
  );
}
