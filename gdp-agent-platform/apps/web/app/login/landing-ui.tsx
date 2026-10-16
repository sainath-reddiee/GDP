"use client";

import type { ReactNode } from "react";
import { motion } from "framer-motion";
import { ArrowDown } from "lucide-react";
import { cn } from "@/lib/utils";

/** Shared pieces of the landing page sections (app/login/landing.tsx, engine-sections.tsx). */

export const reveal = {
  initial: { opacity: 0, y: 18 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, margin: "-80px" },
  transition: { duration: 0.6, ease: [0.2, 0.8, 0.2, 1] },
} as const;

/** A numbered section: rule, § number and label, a serif title with a short lede beside it, then the content. */
export function Section({ id, n, label, title, body, children, className, next }: {
  id: string; n: string; label: string; title: ReactNode; body?: ReactNode; children: ReactNode; className?: string;
  /** the next chapter of the story, linked at the end of this one */
  next?: { href: string; label: string };
}) {
  return (
    <section id={id} className={cn("mx-auto max-w-[1200px] 2xl:max-w-[1360px] scroll-mt-20 px-5 sm:px-8", className)}>
      <motion.div {...reveal} className="flex items-center gap-4 border-t border-[color:var(--ink)] pt-4">
        <span className="lp-label !text-[color:var(--ink)]">§ {n}</span>
        <span className="lp-label">{label}</span>
      </motion.div>
      <div className="mt-8 grid gap-6 lg:grid-cols-12">
        <motion.h2 {...reveal} className="lp-display text-[40px] leading-[1.02] text-[color:var(--ink)] sm:text-[56px] lg:col-span-7">{title}</motion.h2>
        {body && <motion.p {...reveal} className="self-end text-[16px] leading-relaxed text-[color:var(--ink-2)] lg:col-span-5">{body}</motion.p>}
      </div>
      <div className="mt-12">{children}</div>
      {next && <NextChapter {...next} />}
    </section>
  );
}

/** "Next: …" at the end of a chapter, so the page reads as one story from top to bottom. */
export function NextChapter({ href, label, dark }: { href: string; label: string; dark?: boolean }) {
  return (
    <div className="mt-14 flex justify-end">
      <a href={href} className={`lp-link inline-flex items-center gap-2 text-[14px] font-medium ${dark ? "text-[#f3f1ea]" : "text-[color:var(--ink)]"}`}>
        <span className={`lp-label ${dark ? "!text-[#9b978c]" : ""}`}>Next</span> {label} <ArrowDown className="h-4 w-4" />
      </a>
    </div>
  );
}
