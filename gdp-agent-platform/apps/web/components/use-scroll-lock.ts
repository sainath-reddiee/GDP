"use client";

import { useEffect } from "react";

// Overlays can stack (a drawer opening a dialog), so the page stays locked until the last one closes.
let locks = 0;
let saved = { overflow: "", paddingRight: "" };

/** Stop the page behind an open drawer or dialog from scrolling. */
export function useScrollLock(active = true) {
  useEffect(() => {
    if (!active) return;
    const root = document.documentElement;
    if (locks === 0) {
      saved = { overflow: root.style.overflow, paddingRight: root.style.paddingRight };
      // keep the layout from jumping sideways when the scrollbar disappears
      const gap = window.innerWidth - root.clientWidth;
      root.style.overflow = "hidden";
      root.dataset.overlay = "open"; // lets floating launchers (copilot) step aside
      if (gap > 0) root.style.paddingRight = `${gap}px`;
    }
    locks += 1;
    return () => {
      locks -= 1;
      if (locks === 0) {
        root.style.overflow = saved.overflow;
        root.style.paddingRight = saved.paddingRight;
        delete root.dataset.overlay;
      }
    };
  }, [active]);
}
