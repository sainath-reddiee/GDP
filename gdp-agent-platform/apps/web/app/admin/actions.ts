"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";

export async function deployPlatform() {
  const result = await attemptValue(() => api<{ ok: boolean; log: string[] }>("/api/admin/apply", { method: "POST" }));
  revalidatePath("/", "layout");
  return result;
}
