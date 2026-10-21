import { redirect } from "next/navigation";

/** Field mapping is step 1 of Mapping. */
export default function MappingPage({ params }: { params: { runId: string } }) {
  redirect(`/runs/${params.runId}/modeling?tab=mapping`);
}
