import { ApiError } from "@/lib/api";

/** A store action's outcome. A governed write that was turned into a change request (HTTP 202) is not an error:
 *  `pending` carries the "sent for approval" message so the UI can say so. */
export type StoreResult<T> = { ok: true; data: T } | { ok: false; error: string; pending?: boolean };

export async function storeAction<T>(fn: () => Promise<T>): Promise<StoreResult<T>> {
  try {
    return { ok: true, data: await fn() };
  } catch (e) {
    if (e instanceof ApiError) return { ok: false, error: e.message, pending: e.status === 202 };
    throw e;
  }
}
