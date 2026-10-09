export const HUB_TABS = [
  { id: "overview", label: "Overview" },
  { id: "items", label: "All items" },
  { id: "inbox", label: "Review inbox" },
  { id: "feed", label: "Learning feed" },
  { id: "health", label: "Duplicates and stale" },
] as const;
export type HubTab = (typeof HUB_TABS)[number]["id"];
