import { RunsList, type RunsSearch } from "../runs/runs-list";

/** The Gold Pipeline: Silver (read in place) to Gold runs, on the same engine and steps as Silver runs. */
export default function GoldRuns({ searchParams }: { searchParams: RunsSearch }) {
  return <RunsList pipeline="GOLD" searchParams={searchParams} />;
}
