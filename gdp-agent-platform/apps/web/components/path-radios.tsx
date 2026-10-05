"use client";

import type { OnboardingPath } from "@/app/onboarding/intent-types";

export function PathRadios({
  value, onChange, name = "modeling_path",
}: {
  value: OnboardingPath | "";
  onChange: (path: OnboardingPath) => void;
  name?: string;
}) {
  return (
    <fieldset className="rounded-lg border bg-card px-4 py-3">
      <legend className="px-1 text-sm font-medium">How should this source be modeled?</legend>
      <div className="mt-2 flex flex-wrap gap-x-8 gap-y-2">
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input
            type="radio"
            name={name}
            checked={value === "map_existing"}
            onChange={() => onChange("map_existing")}
          />
          Map to existing models
        </label>
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input
            type="radio"
            name={name}
            checked={value === "profile_suggest"}
            onChange={() => onChange("profile_suggest")}
          />
          New source — profile, then suggest
        </label>
      </div>
    </fieldset>
  );
}
