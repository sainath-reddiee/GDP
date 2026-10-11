// Generates app/theme-compat.css: dark-mode equivalents for the light Tailwind tints the pages use directly
// (bg-emerald-50, text-amber-700, ring-slate-200, bg-white, ...). Run `npm run theme:compat` after adding a new
// opacity or hover variant of a palette tint; plain tints are covered for the whole palette.
//
// Every base rule is `.dark :where(.cls)`: the same specificity as the utility it replaces, so it wins by coming
// later, while an explicit `dark:` variant (`.dark .dark\:cls`) still wins over it.
import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const colors = createRequire(import.meta.url)("tailwindcss/colors");
const HUES = ["red", "orange", "amber", "yellow", "lime", "green", "emerald", "teal", "cyan", "sky", "blue", "indigo",
  "violet", "purple", "fuchsia", "pink", "rose"];
const GREYS = ["slate", "gray", "zinc", "neutral", "stone"];

const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)).join(" ");
const tint = (hue, alpha) => `rgb(${rgb(colors[hue]["500"])} / ${+alpha.toFixed(3)})`;
const solid = (hue, shade) => `rgb(${rgb(colors[hue][shade])})`;
const esc = (cls) => cls.replace(/([:/.[\]])/g, "\\$1");

const BG = { 50: 0.12, 100: 0.18, 200: 0.26, 300: 0.34 };
const BORDER = { 50: 0.2, 100: 0.25, 200: 0.32, 300: 0.42 };
const TEXT = { 600: "400", 700: "300", 800: "200", 900: "100", 950: "100" };
const GREY_TEXT = { 500: "400", 600: "400", 700: "300", 800: "200", 900: "100", 950: "50" };

/** utility (without variant or opacity) -> declaration body for a given opacity multiplier, or null. */
function rule(util, mult = 1) {
  if (util === "bg-white") return `background-color: hsl(var(--card) / ${mult});`;
  const m = util.match(/^(bg|text|border|ring|from|to|via|divide)-([a-z]+)-(\d+)$/);
  if (!m) return null;
  const [, kind, hue, shade] = m;
  const grey = GREYS.includes(hue);
  if (!grey && !HUES.includes(hue)) return null;
  if (kind === "bg") {
    if (grey) return { 50: `background-color: hsl(var(--muted) / ${0.5 * mult});`, 100: `background-color: hsl(var(--muted) / ${mult});`,
      200: `background-color: hsl(var(--foreground) / ${0.1 * mult});`, 300: `background-color: hsl(var(--foreground) / ${0.16 * mult});` }[shade] ?? null;
    return BG[shade] ? `background-color: ${tint(hue, BG[shade] * mult)};` : null;
  }
  if (kind === "text") {
    const to = (grey ? GREY_TEXT : TEXT)[shade];
    return to ? `color: ${solid(grey ? "slate" : hue, to)};` : null;
  }
  if (kind === "border" || kind === "divide") {
    const prop = kind === "border" ? "border-color" : "border-color";
    if (grey) return +shade <= 300 ? `${prop}: hsl(var(--border));` : null;
    return BORDER[shade] ? `${prop}: ${tint(hue, BORDER[shade] * mult)};` : null;
  }
  if (kind === "ring") {
    if (grey) return +shade <= 300 ? `--tw-ring-color: hsl(var(--border));` : null;
    return BORDER[shade] ? `--tw-ring-color: ${tint(hue, BORDER[shade] * mult)};` : null;
  }
  if (+shade > 200) return null;
  const c = grey ? `hsl(var(--muted) / ${mult})` : tint(hue, (BG[shade] ?? 0.12) * mult);
  if (kind === "from") return `--tw-gradient-from: ${c} var(--tw-gradient-from-position); --tw-gradient-to: transparent var(--tw-gradient-to-position); --tw-gradient-stops: var(--tw-gradient-from), var(--tw-gradient-to);`;
  if (kind === "via") return `--tw-gradient-to: transparent var(--tw-gradient-to-position); --tw-gradient-stops: var(--tw-gradient-from), ${c} var(--tw-gradient-via-position), var(--tw-gradient-to);`;
  return `--tw-gradient-to: ${c} var(--tw-gradient-to-position);`;
}

function selector(cls, util, variant) {
  const base = `.${esc(cls)}`;
  const sel = util.startsWith("divide-") ? `${base} > :not([hidden]) ~ :not([hidden])` : base;
  if (variant === "hover") return `.dark ${sel}:hover`;
  if (variant === "focus") return `.dark ${sel}:focus`;
  return `.dark :where(${sel})`;
}

// the whole palette, plain
const out = new Map();
const plain = ["bg-white"];
for (const hue of [...HUES, ...GREYS])
  for (const kind of ["bg", "text", "border", "ring", "from", "to", "via", "divide"])
    for (const shade of ["50", "100", "200", "300", "500", "600", "700", "800", "900", "950"]) plain.push(`${kind}-${hue}-${shade}`);
for (const util of plain) {
  const body = rule(util);
  if (body) out.set(util, `${selector(util, util)} { ${body} }`);
}

// opacity and hover/focus variants actually used in the code
function* files(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* files(p);
    else if (/\.(tsx?|jsx?)$/.test(name)) yield p;
  }
}
const found = new Set();
for (const dir of ["app", "components", "lib"])
  for (const f of files(join(root, dir)))
    for (const m of readFileSync(f, "utf8").matchAll(/(?<![\w:-])((?:hover|focus):)?((?:bg|text|border|ring|from|to|via|divide)-(?:white|[a-z]+-\d+))(?:\/(\d+))?(?![\w-])/g))
      found.add(m[0]);
for (const cls of [...found].sort()) {
  const m = cls.match(/^(?:(hover|focus):)?([a-z]+-(?:white|[a-z]+-\d+))(?:\/(\d+))?$/);
  if (!m || (!m[1] && !m[3])) continue;
  const [, variant, util, pct] = m;
  if (util === "bg-white" && pct && +pct < 50) continue;  // translucent white on dark chrome (sidebar) stays as is
  const body = rule(util, pct ? +pct / 100 : 1);
  if (body) out.set(cls, `${selector(cls, util, variant)} { ${body} }`);
}

const header = `/* Generated by scripts/theme-compat.mjs (npm run theme:compat). Do not edit by hand.
   Dark-mode equivalents for light Tailwind tints used directly in pages: tinted backgrounds become translucent,
   dark text becomes light, light borders and rings become subtle. Explicit dark: variants still take precedence. */
`;
writeFileSync(join(root, "app/theme-compat.css"), header + [...out.values()].join("\n") + "\n");
console.log(`theme-compat.css: ${out.size} rules`);
