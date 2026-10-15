"use client";

import { useEffect, useRef, useState } from "react";

/** A number that counts up from zero the first time it scrolls into view. Null shows "-"; reduced motion shows the
 * final value at once. Server-rendered as the final value, so the page reads correctly before hydration. */
export function CountUp({ value, duration = 900, className }: { value: number | null | undefined; duration?: number; className?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [shown, setShown] = useState<number | null>(value ?? null);

  useEffect(() => {
    const el = ref.current;
    if (value == null || !el) { setShown(value ?? null); return; }
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || typeof IntersectionObserver === "undefined") {
      setShown(value);
      return;
    }
    setShown(0);
    let raf = 0;
    const io = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) return;
      io.disconnect();
      const start = performance.now();
      const tick = (now: number) => {
        const k = Math.min(1, (now - start) / duration);
        setShown(Math.round(value * (1 - Math.pow(1 - k, 3))));
        if (k < 1) raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
    }, { threshold: 0.2 });
    io.observe(el);
    return () => { io.disconnect(); cancelAnimationFrame(raf); };
  }, [value, duration]);

  return <span ref={ref} className={className}>{shown == null ? "-" : shown.toLocaleString()}</span>;
}
