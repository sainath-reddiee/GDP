"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";

export type TagType = "PROFILE" | "RUN" | "MODEL";
export type TagInfo = { tag: string; count: number; color: string; description: string | null };

export async function loadTags(entityType?: TagType) {
  return attemptValue(() => api<{ tags: TagInfo[]; ready: boolean }>(`/api/tags${entityType ? `?entity_type=${entityType}` : ""}`));
}

export async function loadEntityTags(entityType: TagType, key: string) {
  return attemptValue(() => api<{ tags: string[] }>(`/api/tags/${entityType}/${encodeURIComponent(key)}`));
}

export async function saveEntityTags(entityType: TagType, key: string, tags: string[]) {
  const r = await attemptValue(() => api<{ tags: string[] }>(`/api/tags/${entityType}/${encodeURIComponent(key)}`, {
    method: "PUT", body: JSON.stringify({ tags }),
  }));
  if (r.ok) {
    revalidatePath(entityType === "RUN" ? "/runs" : "/sources");
  }
  return r;
}
