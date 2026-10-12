import type { DiffFile } from "@/components/diff-view";

/** Copilot workspace (/dashboard/copilot), as served by apps/api/app/copilot_agent_api.py. */

export type StepStatus = "PENDING" | "RUNNING" | "DONE" | "BLOCKED" | "SKIPPED";
export type PlanStep = { no: number; key: string; title: string; status: StepStatus; note: string };

export type FormField = { name: string; label: string; type: "select" | "text" | "confirm"; options?: string[] };

export type ChangeKind = "INGESTION" | "MODEL" | "STTM" | "DBT" | "QA" | "DQ" | "GIT" | "JIRA";
export type ChangeStatus = "PROPOSED" | "APPLIED" | "PENDING_APPROVAL" | "FAILED" | "DISCARDED";
export type Change = {
  change_id: string; seq: number; change_key: string; kind: ChangeKind; title: string; status: ChangeStatus;
  request_id?: string | null; result?: unknown; operations?: { method: string; path: string }[]; updated_at?: string;
};

export type ColumnRow = {
  name: string; datatype?: string; nullable?: boolean; is_pk?: boolean; description?: string;
  state: "added" | "changed" | "removed" | "same"; changed: string[];
};
export type SttmRow = {
  target_column: string; state: "added" | "changed" | "removed";
  before: Record<string, string | null> | null; after: Record<string, string | null> | null;
};
type TabBase = { change_id: string; title: string; kind: ChangeKind };
export type Workbench = {
  model: (TabBase & { entity: string; columns: ColumnRow[]; counts: Record<string, number> })[];
  sttm: (TabBase & { lines: SttmRow[] })[];
  dbt: (TabBase & { files: DiffFile[] })[];
  quality: (TabBase & { files: DiffFile[] })[];
};

/** One stored or streamed chat item. */
export type ChatItem =
  | { id: string; kind: "USER"; text: string; revise?: { title: string } }
  | { id: string; kind: "ANSWER"; answers: Record<string, unknown> }
  | { id: string; kind: "THOUGHT"; text: string }
  | { id: string; kind: "TOOL"; tool: string; ok: boolean; error?: string; summary?: unknown }
  | { id: string; kind: "CHANGE"; title: string; change_kind: ChangeKind }
  | { id: string; kind: "ASK"; question: string; fields: FormField[] }
  | { id: string; kind: "REPLY"; text: string }
  | { id: string; kind: "ERROR"; text: string };

export type StoredEvent = { event_id: string; seq: number; kind: string; content: Record<string, unknown> };

export type CopilotSession = {
  session: { session_id: string; title: string; status: string; jira_key?: string | null; updated_at?: string };
  plan: PlanStep[]; events: StoredEvent[]; changes: Change[]; workbench: Workbench;
};

export type SessionRow = {
  session_id: string; title: string; status: string; jira_key?: string | null; updated_at?: string;
  proposed?: number; changes?: number;
};

/** Stored events as chat items (the same shapes the stream produces). */
export function toItems(events: StoredEvent[]): ChatItem[] {
  return events.map((e): ChatItem => {
    const c = (e.content ?? {}) as Record<string, any>;
    const id = e.event_id;
    switch (e.kind) {
      case "USER": return { id, kind: "USER", text: String(c.text ?? ""), revise: c.revise };
      case "ANSWER": return { id, kind: "ANSWER", answers: c.answers ?? {} };
      case "THOUGHT": return { id, kind: "THOUGHT", text: String(c.text ?? "") };
      case "TOOL": return { id, kind: "TOOL", tool: String(c.tool ?? ""), ok: !!c.ok, error: c.error, summary: c.summary };
      case "CHANGE": return { id, kind: "CHANGE", title: String(c.title ?? ""), change_kind: c.kind };
      case "ASK": return { id, kind: "ASK", question: String(c.question ?? ""), fields: c.fields ?? [] };
      case "ERROR": return { id, kind: "ERROR", text: String(c.text ?? "") };
      default: return { id, kind: "REPLY", text: String(c.text ?? "") };
    }
  });
}
