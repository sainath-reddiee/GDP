"use server";

import { api } from "@/lib/api";
import type { SearchKind, SearchResponse } from "@/lib/os/search";

/** Search the platform by meaning (the Home search bar). */
export async function searchPlatform(q: string, kinds: SearchKind[] = [], limit = 24): Promise<{ ok: true; data: SearchResponse } | { ok: false; error: string }> {
  try {
    const p = new URLSearchParams({ q: q.slice(0, 500), limit: String(limit) });
    if (kinds.length) p.set("kinds", kinds.join(","));
    return { ok: true, data: await api<SearchResponse>(`/api/search?${p}`) };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}
