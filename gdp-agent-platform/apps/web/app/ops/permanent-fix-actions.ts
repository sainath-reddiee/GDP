"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";

// ---------------------------------------------------------------- types (the /api/ops/permanent-fixes contract)

export type PermanentFixStatus = "UNAVAILABLE" | "DRAFTED" | "ACCEPTED" | "REJECTED" | "PUBLISHED" | "MERGED" | "CLOSED" | "SUPERSEDED";
export type PermanentFixFile = { path: string; role: string; base_sha256: string; new_content: string; change: string };
export type PermanentFixRow = {
  fix_id: string; fix_number: number | null; issue_id: string; issue_title?: string | null; status: PermanentFixStatus;
  trigger_kind: "AUTO" | "MANUAL"; drafted_by: string; dag_id: string | null; task_id: string | null; title: string | null;
  summary: string | null; unavailable_reason: string | null; publishable: boolean | null; decided_by: string | null;
  pr_url: string | null; pr_state: string | null; created_at: string; updated_at: string;
};
export type PermanentFix = PermanentFixRow & {
  version: number; instructions: string | null; env_id: string | null; domain_id: string | null; repo_id: string | null;
  base_branch: string | null; indexed_commit: string | null; files: PermanentFixFile[] | null; diff: string | null;
  rationale: string | null; confidence: number | null; model: string | null; decided_at: string | null; decision_note: string | null;
  published_by: string | null; published_at: string | null; branch: string | null; pr_number: number | null; merged_sha: string | null;
  retire_decision: "RETIRED" | "KEPT" | null; retire_decided_by: string | null; retire_decided_at: string | null;
  context: {
    note?: string | null; models?: string[]; missing?: string[]; warnings?: string[];
    skipped?: { path: string; reason: string }[]; files_considered?: { path: string; role: string }[];
    stats?: { runs: number; dags: number; success_rate: number | null } | null;
  } | null;
  issue: { issue_id: string; title: string | null; status: string | null; fix_type: string | null };
  you_drafted: boolean; branch_name: string;
};
export type PublishPreview = { branch: string; base_branch: string; repo: string; files: string[]; diff: string; preview_token: string };

const json = (body: unknown) => JSON.stringify(body);
const enc = encodeURIComponent;

function changed(fixId?: string, issueId?: string) {
  revalidatePath("/ops/permanent-fixes");
  if (fixId) revalidatePath(`/ops/permanent-fixes/${fixId}`);
  if (issueId) revalidatePath(`/ops/fixes/${issueId}`);
  revalidatePath("/work");
}

export async function listPermanentFixes(params: { status?: string; issue_id?: string; limit?: number } = {}) {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== "").map(([k, v]) => [k, String(v)]));
  return attemptValue(() => api<{ items: PermanentFixRow[]; available: boolean }>(`/api/ops/permanent-fixes?${q}`));
}

export async function draftPermanentFix(issueId: string, instructions: string, supersede: boolean) {
  const r = await attemptValue(() => api<PermanentFix>(`/api/ops/permanent-fixes/draft/${enc(issueId)}`, {
    method: "POST", body: json({ instructions: instructions.trim() || null, supersede }),
  }));
  if (r.ok) changed(r.data.fix_id, issueId);
  return r;
}

export async function decidePermanentFix(fixId: string, accept: boolean, note: string) {
  const r = await attemptValue(() => api<PermanentFix>(`/api/ops/permanent-fixes/${enc(fixId)}/${accept ? "accept" : "reject"}`, {
    method: "POST", body: json({ note: note.trim() || null }),
  }));
  if (r.ok) changed(fixId, r.data.issue_id);
  return r;
}

export async function previewPermanentFix(fixId: string) {
  return attemptValue(() => api<PublishPreview>(`/api/ops/permanent-fixes/${enc(fixId)}/publish`, {
    method: "POST", body: json({ dry_run: true }),
  }));
}

export async function publishPermanentFix(fixId: string, previewToken: string) {
  const r = await attemptValue(() => api<{ result: { status: string; detail?: string; pull_request?: { url?: string } }; fix: PermanentFix | null }>(
    `/api/ops/permanent-fixes/${enc(fixId)}/publish`, { method: "POST", body: json({ dry_run: false, preview_token: previewToken }) }));
  if (r.ok) changed(fixId, r.data.fix?.issue_id);
  return r;
}

export async function decideKnownFix(fixId: string, retire: boolean) {
  const r = await attemptValue(() => api<PermanentFix>(`/api/ops/permanent-fixes/${enc(fixId)}/known-fix`, {
    method: "POST", body: json({ retire }),
  }));
  if (r.ok) { changed(fixId, r.data.issue_id); revalidatePath("/ops/fixes"); }
  return r;
}
