import { api } from "@/lib/api";
import type { AuditEvent } from "@/lib/types";
import { AuditTable } from "@/components/audit-table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type Review = { review_id: string; stage: string; decision: string; business_justification: string | null; reviewer: string; reviewed_at: string };

export default async function RunAudit({ params }: { params: { runId: string } }) {
  const { events, reviews } = await api<{ events: AuditEvent[]; reviews: Review[] }>(`/api/runs/${params.runId}/audit`);
  return (
    <>
      <AuditTable events={events} />
      <Card>
        <CardHeader><CardTitle>Review decisions</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-sm">
          {reviews.length === 0 && <p className="text-muted-foreground">No human decisions yet.</p>}
          {reviews.map((r) => (
            <div key={r.review_id}>
              <strong>{r.stage}</strong> {r.decision} by {r.reviewer} at {r.reviewed_at.slice(0, 19)}
              {r.business_justification && <div className="text-muted-foreground">{r.business_justification}</div>}
            </div>
          ))}
        </CardContent>
      </Card>
    </>
  );
}
