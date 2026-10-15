import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  // dark: variants apply under the .dark class that components/theme sets (from the saved choice or the OS setting)
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        secondary: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--secondary-foreground))" },
        destructive: { DEFAULT: "hsl(var(--destructive))", foreground: "hsl(var(--destructive-foreground))" },
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
        sidebar: { DEFAULT: "hsl(var(--sidebar))", foreground: "hsl(var(--sidebar-foreground))" },
        success: "hsl(var(--success))",
        warning: "hsl(var(--warning))",
        // agent work vs. a human approval (globals.css --agent, --human)
        agent: "hsl(var(--agent) / <alpha-value>)",
        human: "hsl(var(--human) / <alpha-value>)",
      },
      borderRadius: { lg: "var(--radius)", md: "calc(var(--radius) - 2px)", sm: "calc(var(--radius) - 4px)" },
      boxShadow: {
        xs: "var(--shadow-xs)",
        card: "var(--shadow-card)",
        hover: "var(--shadow-hover)",
        pop: "var(--shadow-pop)",
        // the defaults every page already uses, softened to the same family
        sm: "var(--shadow-card)",
        md: "var(--shadow-hover)",
      },
      fontFamily: {
        sans: ['"Inter Variable"', "Inter", '"Segoe UI"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', '"Cascadia Code"', "ui-monospace", "SFMono-Regular", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
