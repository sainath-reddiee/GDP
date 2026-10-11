/** @type {import('next').NextConfig} */
module.exports = {
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
