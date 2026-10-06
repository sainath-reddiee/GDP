import { redirect } from "next/navigation";

/** Access validation now runs inside the Source stage ("Validate & land"). */
export default function AccessPage({ params }: { params: { runId: string } }) {
  redirect(`/runs/${params.runId}/source`);
}
