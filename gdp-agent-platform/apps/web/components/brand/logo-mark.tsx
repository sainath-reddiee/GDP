import { useId } from "react";
import { cn } from "@/lib/utils";

/**
 * The product mark (also app/icon.svg, the favicon): three stacked data layers (Bronze, Silver, Gold) with a check on
 * the top one for human approval, and a loop that carries each decision back to the top, starting from the approval
 * dot: a knowledge-driven engine that learns from every approval.
 */
export function LogoMark({ size = "md", className }: { size?: "sm" | "md" | "lg"; className?: string }) {
  const id = useId().replace(/:/g, "");
  const box = size === "sm" ? "h-9 w-9" : size === "lg" ? "h-12 w-12" : "h-10 w-10";
  return (
    <svg viewBox="0 0 32 32" aria-hidden className={cn("shrink-0", box, className)}>
      <defs>
        <linearGradient id={`lm-${id}`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#1d4ed8" />
          <stop offset="1" stopColor="#1d4ed8" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill={`url(#lm-${id})`} />
      <path d="M8.5 19.2 L17 23.4 L25.5 19.2" fill="none" stroke="#fff" strokeOpacity=".55" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M8.5 15.2 L17 19.4 L25.5 15.2" fill="none" stroke="#fff" strokeOpacity=".8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M17 6.8 L25.5 11 L17 15.2 L8.5 11 Z" fill="#fff" />
      <path d="M14.2 11 L16.2 12.7 L19.9 9.5" fill="none" stroke="#1e3a8a" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M6.6 23.6 C3.4 18.8 3.9 11 9.6 7.2" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M7.4 5.9 L9.9 7 L8.9 9.5" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="7" cy="24.2" r="2.3" fill="#b45309" stroke="#1e3a8a" strokeWidth="1" />
    </svg>
  );
}
