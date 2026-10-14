import { RunsList, type RunsSearch } from "./runs-list";

/** The Silver Pipeline: source to Silver (pre-MDM) runs. */
export default function Runs({ searchParams }: { searchParams: RunsSearch }) {
  return <RunsList pipeline="SILVER" searchParams={searchParams} />;
}
