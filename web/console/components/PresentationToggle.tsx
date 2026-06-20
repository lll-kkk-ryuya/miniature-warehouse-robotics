"use client";

import { useState } from "react";

// Capture skin (doc22 §12.4): toggles `.presenting` on <html> to hide chrome + enlarge the
// transcript for OBS/YouTube. The button itself stays visible (no `chrome` class) so the
// operator can always switch back.
export function PresentationToggle() {
  const [on, setOn] = useState(false);
  function toggle() {
    const next = !on;
    setOn(next);
    document.documentElement.classList.toggle("presenting", next);
  }
  return (
    <button
      type="button"
      onClick={toggle}
      className="rounded border border-slate-600 px-2 py-1 text-xs text-slate-300 hover:bg-surface-raised"
    >
      {on ? "通常表示" : "撮影モード"}
    </button>
  );
}
