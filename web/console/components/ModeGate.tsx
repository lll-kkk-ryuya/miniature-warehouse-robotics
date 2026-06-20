"use client";

import { useStore } from "@/store/useStore";
import { isOpenRmf } from "@/lib/format";

// Mode C (open-rmf) launches no character LLM, so /character/* and /negotiation/* are never
// published (doc22 §12.1). Wrapping the conversation / ringi panels in <ModeGate> hides them in
// Mode C instead of showing permanently-empty panels.
export function ModeGate({ children }: { children: React.ReactNode }) {
  const mode = useStore((s) => s.run.mode);
  if (isOpenRmf(mode)) return null;
  return <>{children}</>;
}
