import { redirect } from "next/navigation";

/** Landing now runs inside the Source stage ("Validate & land"). */
export default function LandingPage({ params }: { params: { runId: string } }) {
  redirect(`/runs/${params.runId}/source`);
}
