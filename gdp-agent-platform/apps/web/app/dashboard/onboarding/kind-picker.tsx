"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { AppWindow, Boxes, Database } from "lucide-react";
import { ChoiceStep, RailStep, Reveal, type Choice } from "./rail";

export type Kind = "source" | "domain" | "application";

const KINDS: Choice<Kind>[] = [
  { key: "source", label: "Source", icon: Database },
  { key: "domain", label: "Domain", icon: Boxes },
  { key: "application", label: "Application", icon: AppWindow, soon: true },
];

/** Step 1: what is being onboarded. The choice settles at once and the page follows in the address bar, so the steps
 *  below load for that kind (and Back returns to the choice). */
export function KindPicker({ value }: { value: Kind | null }) {
  const router = useRouter();
  const [shown, setShown] = useState(value);
  const [pending, start] = useTransition();
  useEffect(() => setShown(value), [value]);
  const pick = (k: Kind | null) => {
    setShown(k);
    start(() => router.push(k ? `/dashboard/onboarding?type=${k}` : "/dashboard/onboarding", { scroll: false }));
  };
  return (
    <>
      <ChoiceStep n={1} title="What are you onboarding" choices={KINDS} value={shown} onChange={pick} testId="onboard-kind" depth={0} />
      {pending && shown && shown !== value && (
        <Reveal>
          <RailStep n={2} title="Loading" last>
            <div className="grid gap-2 sm:grid-cols-2" role="status" aria-label="Loading the next step">
              {[0, 1].map((i) => <div key={i} className="h-[62px] animate-pulse rounded-lg border bg-muted/40" />)}
            </div>
          </RailStep>
        </Reveal>
      )}
    </>
  );
}
