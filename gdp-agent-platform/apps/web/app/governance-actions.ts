"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";

export type ChangeRequest = {
  request_id: string; privilege: string; title: string | null; summary: string | null; run_id: string | null;
  method: string; path: string; payload: unknown; requested_by: string; approver_role: string; allow_self: boolean;
  status: string; decided_by: string | null; decision_note: string | null; result: { status_code?: number; body?: unknown } | null;
  created_at: string; decided_at: string | null; can_decide: boolean;
};
export type GovRole = { role: string; description: string | null; system: boolean; privileges: string[]; inherits: string[]; members: string[] };
export type GovPrivilege = { privilege: string; group: string; description: string };
export type GovPolicy = GovPrivilege & { requires_approval: boolean; approver_role: string | null; four_eyes: boolean; allow_self: boolean; active: boolean };
export type GovUser = { user: string; roles: string[] };
export type GovSettings = { DEFAULT_ROLE: string; SUPER_SELF_APPROVE: boolean; ENFORCE: boolean };
export type GovEvent = { event_id: string; event_type: string; actor: string; target: string; detail: unknown; created_at: string };

function done() {
  revalidatePath("/approvals");
  revalidatePath("/admin");
}

export async function approveRequest(id: string, note: string) {
  const r = await attemptValue(() => api<{ status: string; status_code: number; result: unknown }>(
    `/api/governance/requests/${id}/approve`, { method: "POST", body: JSON.stringify({ note }) }));
  revalidatePath("/", "layout");
  return r;
}

export async function rejectRequest(id: string, note: string) {
  const r = await attemptValue(() => api(`/api/governance/requests/${id}/reject`, { method: "POST", body: JSON.stringify({ note }) }));
  done();
  return r;
}

export async function cancelRequest(id: string) {
  const r = await attemptValue(() => api(`/api/governance/requests/${id}/cancel`, { method: "POST" }));
  done();
  return r;
}

export async function setUserRoles(user: string, roles: string[]) {
  const r = await attemptValue(() => api(`/api/governance/users/${encodeURIComponent(user)}`, {
    method: "PUT", body: JSON.stringify({ roles }),
  }));
  done();
  return r;
}

export async function saveRole(role: { role: string; description?: string | null; privileges: string[]; inherits: string[] }, create: boolean) {
  const r = await attemptValue(() => api(create ? "/api/governance/roles" : `/api/governance/roles/${role.role}`, {
    method: create ? "POST" : "PUT", body: JSON.stringify(role),
  }));
  done();
  return r;
}

export async function deleteRole(role: string) {
  const r = await attemptValue(() => api(`/api/governance/roles/${role}`, { method: "DELETE" }));
  done();
  return r;
}

export async function savePolicy(privilege: string, body: {
  requires_approval: boolean; approver_role: string; four_eyes: boolean; allow_self: boolean; active: boolean;
}) {
  const r = await attemptValue(() => api(`/api/governance/policies/${privilege}`, { method: "PUT", body: JSON.stringify(body) }));
  done();
  return r;
}

export async function saveSettings(body: Partial<GovSettings>) {
  const r = await attemptValue(() => api("/api/governance/settings", { method: "PUT", body: JSON.stringify(body) }));
  done();
  return r;
}
