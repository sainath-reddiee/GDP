import { redirect } from "next/navigation";

/** Onboarding is a lifecycle hub now. */
export default function Onboarding() {
  redirect("/dashboard/onboarding");
}
