import { redirect } from "next/navigation";

/** Onboarding now starts in the Sources hub: explore, profile in place, then start modeling. */
export default function Onboarding() {
  redirect("/sources");
}
