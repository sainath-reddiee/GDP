import type { RunStatusFilter } from "./types";

export const STATUS_FILTERS: { value: RunStatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "draft", label: "Drafts" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
  { value: "archived", label: "Archived" },
];

export function parseStatusFilter(value?: string): RunStatusFilter {
  const v = (value ?? "all").toLowerCase();
  return (STATUS_FILTERS.some((f) => f.value === v) ? v : "all") as RunStatusFilter;
}
