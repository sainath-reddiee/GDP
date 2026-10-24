/**
 * Every screen lives under /os. The pages that came before the Data OS keep their files; their old URLs redirect here
 * and the /os URL is served by the same page (next.config.js), so links, bookmarks and server actions keep working
 * while the address bar, the rail and the persona gate (lib/os/access) all see /os paths.
 *
 * [old prefix, /os prefix]; a prefix covers its sub-pages. EXACT entries cover that path alone.
 */
const PREFIXES = [
  ["/work", "/os/work/inbox"],
  ["/dashboard/copilot", "/os/work/copilot"],
  ["/dashboard/onboarding", "/os/enable/onboarding"],
  ["/dashboard/sources", "/os/design/sources"],
  ["/dashboard/store", "/os/design/store"],
  ["/dashboard/profiling", "/os/design/profiling"],
  ["/dashboard/modeling", "/os/design/modeling"],
  ["/dashboard/sttm", "/os/design/sttm"],
  ["/dashboard/qa-store", "/os/design/qa-store"],
  ["/dashboard/dq-store", "/os/design/dq-store"],
  ["/dashboard/source-control", "/os/release/source-control"],
  ["/dashboard/quality", "/os/run/quality"],
  ["/runs", "/os/build/runs"],
  ["/gold", "/os/build/gold"],
  ["/lineage", "/os/build/lineage"],
  ["/code", "/os/build/code"],
  ["/approvals", "/os/review/approvals"],
  ["/skills", "/os/skills/registry"],
  ["/qa", "/os/release/qa"],
  ["/ops", "/os/run/ops"],
  ["/incidents", "/os/run/incidents"],
  ["/tickets", "/os/run/tickets"],
  ["/jira", "/os/run/jira"],
  ["/admin", "/os/admin/settings"],
  ["/audit", "/os/admin/audit"],
];
const EXACT = [["/dashboard", "/os/work/overview"]];

/** The /os URL for an old path (with its query), or the path itself when it already is one. */
function toOs(path) {
  const [p, q] = path.split("?");
  const e = EXACT.find(([from]) => from === p);
  if (e) return e[1] + (q ? `?${q}` : "");
  const hit = PREFIXES.find(([from]) => p === from || p.startsWith(`${from}/`));
  return hit ? hit[1] + p.slice(hit[0].length) + (q ? `?${q}` : "") : path;
}

module.exports = { PREFIXES, EXACT, toOs };
