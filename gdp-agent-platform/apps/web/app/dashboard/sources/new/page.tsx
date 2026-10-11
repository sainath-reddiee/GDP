import { redirect } from "next/navigation";

/** Source onboarding moved to the Onboarding page (Source section); old links keep their branch. */
export default function NewSource({ searchParams }: { searchParams: { branch?: string } }) {
  redirect(`/dashboard/onboarding?type=source${searchParams.branch ? `&branch=${encodeURIComponent(searchParams.branch)}` : ""}`);
}
