import type { ModelEntity, ModelRelationship } from "@/lib/hub-types";
import type { DiffFile } from "@/components/diff-view";

/** Copilot workspace (/dashboard/copilot), as served by apps/api/app/copilot_agent_api.py. */

export type StepStatus = "PENDING" | "RUNNING" | "DONE" | "BLOCKED" | "SKIPPED";
export type PlanStep = { no: number; key: string; title: string; status: StepStatus; note: string };

export type FormField = {
  name: string; label: string; type: "select" | "multiselect" | "text" | "textarea" | "number" | "confirm";
  options?: string[]; default?: string; required?: boolean; help?: string;
};

export type ChangeKind = "INGESTION" | "MODEL" | "STTM" | "DBT" | "QA" | "DQ" | "GIT" | "JIRA";
export type ChangeStatus = "PROPOSED" | "PUBLISHING" | "APPLIED" | "PENDING_APPROVAL" | "FAILED" | "DISCARDED";
export type Change = {
  change_id: string; seq: number; change_key: string; kind: ChangeKind; title: string; status: ChangeStatus;
  request_id?: string | null; result?: { detail?: string } | null; operations?: { method: string; path: string }[];
  updated_at?: string;
};

export type Attachment = {
  attachment_id: string; file_name: string; size_bytes: number; chars: number; note?: string | null; mime_type?: string;
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
/** A drafted QA query with the outcome of its read-only dry run. */
export type QaQueryPreview = {
  query_name: string; sql: string; severity?: string | null; description?: string | null; expectation?: string | null;
  state: "added" | "changed";
  dry_run?: { ok?: boolean; outcome?: string; detail?: string; rows_returned?: number; failing?: number } | null;
};
/** What publishing a draft would meet now, for the signed-in person (GET .../publish-check). */
export type PublishCheck = {
  status: "ok" | "approval" | "forbidden" | "unknown"; reason: string; approver_role: string | null; privileges: string[];
};
export type ChangeMark = "added" | "changed" | "removed";
/** The model tab's ER diagram: entities after the change (removed columns kept) and what to highlight. */
export type ModelDiagram = {
  entities: ModelEntity[]; relationships: ModelRelationship[]; hidden: number;
  highlight: { entities: Record<string, ChangeMark>; columns: Record<string, ChangeMark> };
};
export type Workbench = {
  model: (TabBase & { entity: string; columns: ColumnRow[]; counts: Record<string, number>; diagram?: ModelDiagram })[];
  sttm: (TabBase & { lines: SttmRow[] })[];
  dbt: (TabBase & { files: DiffFile[] })[];
  quality: (TabBase & { files: DiffFile[]; queries?: QaQueryPreview[] })[];
};

export type PublishResult = { change_id: string; title: string; kind: ChangeKind; status: ChangeStatus; request_id?: string | null; detail?: string };

/** One stored or streamed chat item. */
export type ChatItem =
  | { id: string; kind: "USER"; text: string; revise?: { title: string }; jira_keys?: string[]; attachments?: { file_name: string }[] }
  | { id: string; kind: "ANSWER"; answers: Record<string, unknown> }
  | { id: string; kind: "THOUGHT"; text: string }
  | { id: string; kind: "TOOL"; tool: string; ok: boolean; error?: string; summary?: unknown }
  | { id: string; kind: "CHANGE"; title: string; change_kind: ChangeKind; change_id?: string }
  | { id: string; kind: "ASK"; question: string; fields: FormField[] }
  | { id: string; kind: "REPLY"; text: string }
  | { id: string; kind: "PUBLISH"; results: PublishResult[] }
  | { id: string; kind: "ERROR"; text: string };

export type StoredEvent = { event_id: string; seq: number; kind: string; content: Record<string, unknown> };

export type CopilotSession = {
  session: { session_id: string; title: string; status: string; jira_key?: string | null; updated_at?: string };
  plan: PlanStep[]; events: StoredEvent[]; changes: Change[]; workbench: Workbench; attachments: Attachment[];
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
      case "USER": return { id, kind: "USER", text: String(c.text ?? ""), revise: c.revise, jira_keys: c.jira_keys, attachments: c.attachments };
      case "ANSWER": return { id, kind: "ANSWER", answers: c.answers ?? {} };
      case "THOUGHT": return { id, kind: "THOUGHT", text: String(c.text ?? "") };
      case "TOOL": return { id, kind: "TOOL", tool: String(c.tool ?? ""), ok: !!c.ok, error: c.error, summary: c.summary };
      case "CHANGE": return { id, kind: "CHANGE", title: String(c.title ?? ""), change_kind: c.kind, change_id: c.change_id };
      case "ASK": return { id, kind: "ASK", question: String(c.question ?? ""), fields: c.fields ?? [] };
      case "PUBLISH": return { id, kind: "PUBLISH", results: c.results ?? [] };
      case "ERROR": return { id, kind: "ERROR", text: String(c.text ?? "") };
      default: return { id, kind: "REPLY", text: String(c.text ?? "") };
    }
  });
}

/** Reads one server-sent event stream, calling `on` for each event's JSON payload. */
export async function readStream(res: Response, on: (e: Record<string, any>) => void) {
  const reader = res.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder();
  let buffer = "";
  const emit = (block: string) => {
    const data = block.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("\n");
    if (!data) return;
    let parsed: Record<string, any>;
    try { parsed = JSON.parse(data); } catch { return; /* a partial or foreign event: skip it */ }
    on(parsed);   // outside the try: a bug in the handler must not vanish silently
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n?/g, "\n");
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      emit(buffer.slice(0, cut));
      buffer = buffer.slice(cut + 2);
    }
  }
  buffer = (buffer + decoder.decode()).replace(/\r\n?/g, "\n");
  if (buffer.trim()) emit(buffer);
}

export const KIND_LABEL: Record<ChangeKind, string> = {
  INGESTION: "Source onboarding", MODEL: "Data model", STTM: "Modeling (STTM)", DBT: "Silver code", QA: "QA pack", DQ: "Data quality",
  GIT: "Pull request", JIRA: "Jira",
};

export const TOOL_LABEL: Record<string, string> = {
  fetch_jira_issue: "Read Jira issue", search_jira: "Searched Jira", list_domains: "Listed domains",
  fetch_domain_context: "Searched domain knowledge", answer_question: "Asked domain knowledge",
  inspect_source: "Inspected source schema", find_table_models: "Found table models", get_table_model: "Read table model",
  update_table_model: "Drafted model change", generate_dbt_models: "Rendered dbt", find_sttm_mappings: "Found mappings",
  get_sttm_mapping: "Read mapping", generate_sttm_mapping: "Drafted mapping change", register_qa_sql_pack: "Drafted QA queries",
  register_soda_checks: "Drafted data quality checks", onboard_source_config: "Validated source onboarding",
  sync_jira_status: "Drafted Jira update", list_attachments: "Listed attachments", read_attachment: "Read attachment",
  list_runs: "Listed runs", list_ingestion_sources: "Listed ingestion sources", onboarding_requirements: "Loaded onboarding checklist",
  landing_targets: "Read landing targets", find_domain_models: "Found domain models", get_domain_model: "Read domain model",
  update_domain_model: "Drafted domain model change", create_git_pr: "Drafted pull request", search_code: "Searched the code",
  code_impact: "Checked code impact",
};
