"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";

export type Pack = Record<string, unknown>;

/** AI drafts a pack from a contract document; nothing is saved. */
export async function draftPack(text: string, standard: "GDP" | "GENERIC") {
  return attemptValue(() =>
    api<{ pack: Pack; problems: string[]; model: string | null }>("/api/domains/draft", {
      method: "POST", body: JSON.stringify({ text, standard }),
    }),
  );
}

export async function exportPack(domainId: string) {
  return attemptValue(() => api<{ pack: Pack }>(`/api/domains/${domainId}/export`));
}

export async function importPack(pack: Pack) {
  const result = await attemptValue(() =>
    api<{ domain_id: string; domain_name: string; targets: number; columns: number; knowledge: number;
          inactive_targets: string[] }>("/api/domains/import", { method: "POST", body: JSON.stringify({ pack }) }),
  );
  if (result.ok) {
    revalidatePath("/domains");
    revalidatePath("/sources");
  }
  return result;
}
