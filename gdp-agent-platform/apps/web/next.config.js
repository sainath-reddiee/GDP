const { execSync } = require("node:child_process");
const { EXACT, PREFIXES } = require("./os-routes");

/** The commit this build was made from, shown in the app (account menu, sidebar) and at /bff/version. */
function buildSha() {
  try {
    return execSync("git rev-parse --short HEAD", { stdio: ["ignore", "pipe", "ignore"] }).toString().trim() || "unknown";
  } catch {
    return "unknown";
  }
}

/** @type {import('next').NextConfig} */
module.exports = {
  env: {
    NEXT_PUBLIC_BUILD_SHA: process.env.NEXT_PUBLIC_BUILD_SHA ?? buildSha(),
    NEXT_PUBLIC_BUILD_TIME: new Date().toISOString(),
  },
  // External file sources are uploaded through a server action to the API, which stages them in Snowflake.
  experimental: { serverActions: { bodySizeLimit: "200mb" } },
  // The lifecycle hubs live under /dashboard; old links keep working (the query string is carried over).
  async redirects() {
    return [
      { source: "/sources", destination: "/dashboard/sources", permanent: false },
      { source: "/onboarding", destination: "/dashboard/onboarding", permanent: false },
      // the Store hub and short names for its stores (every store keeps its own route)
      { source: "/store", destination: "/dashboard/store", permanent: false },
      { source: "/dashboard/stores", destination: "/dashboard/store", permanent: false },
      { source: "/dashboard/qa", destination: "/dashboard/qa-store", permanent: false },
      { source: "/dashboard/data-quality", destination: "/dashboard/dq-store", permanent: false },
      { source: "/dashboard/mapping", destination: "/dashboard/sttm", permanent: false },
      // every screen lives under /os (os-routes.js): an old URL lands on its /os address
      ...EXACT.map(([from, to]) => ({ source: from, destination: to, permanent: false })),
      ...PREFIXES.flatMap(([from, to]) => [
        { source: from, destination: to, permanent: false },
        { source: `${from}/:path*`, destination: `${to}/:path*`, permanent: false },
      ]),
    ];
  },
  // ...and the /os address is served by the page that has always rendered it
  async rewrites() {
    return [
      ...EXACT.map(([from, to]) => ({ source: to, destination: from })),
      ...PREFIXES.flatMap(([from, to]) => [
        { source: to, destination: from },
        { source: `${to}/:path*`, destination: `${from}/:path*` },
      ]),
    ];
  },
};
