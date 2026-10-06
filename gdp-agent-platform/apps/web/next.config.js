/** @type {import('next').NextConfig} */
module.exports = {
  // External file sources are uploaded through a server action to the API, which stages them in Snowflake.
  experimental: { serverActions: { bodySizeLimit: "200mb" } },
};
