/** The commit and time of this web build (set in next.config.js); "dev" builds show the time the server started. */
export const BUILD_SHA = process.env.NEXT_PUBLIC_BUILD_SHA ?? "unknown";
export const BUILD_TIME = process.env.NEXT_PUBLIC_BUILD_TIME ?? "";

export function buildLabel() {
  const at = BUILD_TIME ? BUILD_TIME.slice(0, 16).replace("T", " ") : "";
  return `Build ${BUILD_SHA}${at ? ` · ${at} UTC` : ""}`;
}
