const { execSync } = require("node:child_process");

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
    ];
  },
};
