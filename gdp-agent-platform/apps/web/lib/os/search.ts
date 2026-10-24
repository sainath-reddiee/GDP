// Search by meaning across the platform (GET /api/search, apps/api/app/search_api.py): workflows, domains, sources,
// runs, target tables and domain knowledge, ranked by Cortex Search (keyword + vector) fused with keyword scoring and
// the platform's synonyms. mode "keyword" when the search service is not deployed; "filter" for a query that is only
// intent ("waiting on me", "failed runs").

export type SearchKind = "WORKFLOW" | "DOMAIN" | "SOURCE" | "RUN" | "TABLE" | "KNOWLEDGE";
export type SearchResult = {
  kind: SearchKind; id: string; title: string; subtitle: string | null; domain_id: string | null; domain_name: string | null;
  status: string | null; href: string; score: number; why: string[]; match: "both" | "semantic" | "keyword";
};
export type SearchResponse = {
  query: string; mode: "hybrid" | "keyword" | "filter" | "none";
  intent: { text: string; terms: string[]; kinds: SearchKind[]; mine: boolean; failed: boolean };
  results: SearchResult[]; counts: Partial<Record<SearchKind, number>>;
};

export const KIND_LABEL: Record<SearchKind, string> = {
  WORKFLOW: "Workflows", DOMAIN: "Domains", SOURCE: "Sources", RUN: "Runs", TABLE: "Tables", KNOWLEDGE: "Knowledge",
};
export const KIND_ORDER: SearchKind[] = ["WORKFLOW", "RUN", "DOMAIN", "SOURCE", "TABLE", "KNOWLEDGE"];
export const MODE_LABEL: Record<SearchResponse["mode"], string> = {
  hybrid: "Semantic + keyword", keyword: "Keyword and synonyms", filter: "Filter", none: "",
};
