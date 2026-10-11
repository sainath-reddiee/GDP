"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";

export type SyncResult = {
  checked: number; open?: number; merged: { kind: string; name?: string; version: number }[];
  closed: { kind: string; name?: string; version: number }[]; errors: { error: string }[]; detail?: string;
};

/** Read every open pull request's state from GitHub now (or one kind's). */
export async function syncPullRequests(kind?: string) {
  const r = await attemptValue(() => api<SyncResult>("/api/source-control/sync", {
    method: "POST", body: JSON.stringify({ force: true, ...(kind ? { kind } : {}) }),
  }));
  revalidatePath("/dashboard/source-control");
  return r;
}
