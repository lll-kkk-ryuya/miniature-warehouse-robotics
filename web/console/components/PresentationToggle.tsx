"use client";

import { useCallback, useEffect, useState } from "react";

// Capture skin (doc22 §12.4): toggles `.presenting` on <html> to hide chrome + enlarge the
// transcript for OBS/YouTube. The button is rendered OUTSIDE the `.chrome` region so it stays
// visible while presenting; Escape is also wired as a second way back.
export function PresentationToggle() {
  const [on, setOn] = useState(false);

  const set = useCallback((next: boolean) => {
    setOn(next);
    document.documentElement.classList.toggle("presenting", next);
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") set(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [set]);

  return (
    <button
      type="button"
      onClick={() => set(!on)}
      className="rounded border border-slate-600 px-2 py-1 text-xs text-slate-300 hover:bg-surface-raised"
    >
      {on ? "通常表示" : "撮影モード"}
    </button>
  );
}
