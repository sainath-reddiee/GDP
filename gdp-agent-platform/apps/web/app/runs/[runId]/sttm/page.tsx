import { redirect } from "next/navigation";

/** Transformations and joins (the STTM) are step 2 of Mapping. */
export default function SttmPage({ params }: { params: { runId: string } }) {
  redirect(`/runs/${params.runId}/modeling?tab=transformations`);
}
