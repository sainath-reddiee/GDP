// npm run doctor: why does localhost:3000 not show the latest code? Checks the checkout, the dependencies, the
// build and what the running server reports, and says what to run next. Read-only; works on Windows and Linux.
import { execSync } from "node:child_process";
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const url = process.argv[2] ?? process.env.AIP_WEB_URL ?? "http://localhost:3000";
const problems = [];
const git = (cmd) => { try { return execSync(`git ${cmd}`, { cwd: web, stdio: ["ignore", "pipe", "ignore"] }).toString().trim(); } catch { return ""; } };
const line = (ok, text) => console.log(`${ok ? "  ok  " : "  !!  "}${text}`);

console.log(`\nWeb app folder: ${web}\n`);

// 1. checkout
const branch = git("rev-parse --abbrev-ref HEAD");
const head = git("rev-parse --short HEAD");
git("fetch --quiet origin main");
const main = git("rev-parse --short origin/main");
const behind = Number(git("rev-list --count HEAD..origin/main") || 0);
const dirty = git("status --porcelain");
line(!!head, `Checkout: branch ${branch || "?"} at ${head || "?"}${main ? ` (origin/main is ${main})` : ""}`);
if (behind) { line(false, `${behind} commit(s) behind origin/main`); problems.push("git checkout main && git pull origin main"); }
if (dirty) { line(false, "Uncommitted local changes (they can block git pull):"); console.log(dirty.split("\n").slice(0, 10).map((l) => `        ${l}`).join("\n")); }

// 2. the source has the new shell
const sidebar = join(web, "components", "sidebar.tsx");
const newShell = existsSync(sidebar) && readFileSync(sidebar, "utf8").includes("NAV_GROUPS");
line(newShell, newShell ? "Source has the new shell (asset stores sidebar)" : "Source is the old sidebar: this folder does not have the latest code");
if (!newShell) problems.push("pull the latest main into THIS folder (check you are not running a different copy of the repo)");

// 3. dependencies
const font = existsSync(join(web, "node_modules", "@fontsource-variable", "inter"));
line(font, font ? "Dependencies installed" : "New dependency missing (@fontsource-variable/inter)");
if (!font) problems.push("npm install");

// 4. build
const buildId = join(web, ".next", "BUILD_ID");
if (existsSync(buildId)) {
  const built = statSync(buildId).mtime;
  const committed = new Date(Number(git("log -1 --format=%ct") || 0) * 1000);
  const stale = built < committed;
  line(!stale, `Production build from ${built.toISOString().slice(0, 16)}${stale ? `, older than the last commit (${committed.toISOString().slice(0, 16)})` : ""}`);
  if (stale) problems.push("npm run build   (npm run start / next start serves the last build, not the current code)");
} else {
  line(true, "No production build (fine for npm run dev)");
}

// 5. what is actually answering on the port
try {
  const res = await fetch(`${url}/bff/version`, { redirect: "manual" });
  if (res.status === 200) {
    const v = await res.json();
    const match = v.web === head;
    line(match, `${url} runs web build ${v.web} (built ${v.built_at ?? "?"})${match ? "" : `, but this folder is at ${head}`}`);
    if (!match) problems.push("stop the running server and start it again from this folder after building");
    line(v.api_reachable, v.api_reachable ? `API reachable, running commit ${v.api}` : "The web server cannot reach the API");
    if (v.api_reachable && v.api && v.api !== "unknown" && v.api !== head) problems.push("restart the API (uvicorn) so it loads the latest code");
  } else {
    line(false, `${url} answers but has no /bff/version (HTTP ${res.status}): it is serving an OLD build`);
    problems.push(`stop whatever is running on ${url} (an old server keeps the port; a new one then starts on 3001)`);
  }
} catch {
  line(false, `Nothing answers on ${url}`);
}

console.log(problems.length ? `\nDo this, in order:\n${[...new Set(problems)].map((p, i) => `  ${i + 1}. ${p}`).join("\n")}\n  then hard refresh the browser (Ctrl+Shift+R)\n`
  : "\nEverything matches. If the page still looks old, hard refresh (Ctrl+Shift+R) or open a private window.\n");
