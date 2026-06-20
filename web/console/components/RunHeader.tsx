"use client";

import Link from "next/link";
import { useStore } from "@/store/useStore";
import { modeLabel } from "@/lib/format";
import { ConnectionStatus } from "./ConnectionStatus";
import { PresentationToggle } from "./PresentationToggle";

// Top bar: run identity, mode, provider, the canned/live badge (doc22:277 — content is canned
// until the live persona #288 lands), connection state, malformed counter, LAN/token indicator.
export function RunHeader() {
  const run = useStore((s) => s.run);
  const config = useStore((s) => s.config);
  const malformed = useStore((s) => s.malformedCount);
  const conversation = useStore((s) => s.conversation);

  const personaSource = conversation.length
    ? conversation[conversation.length - 1].persona_source
    : null;

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-slate-700 bg-surface-panel px-4 py-2">
      <h1 className="text-base font-semibold text-slate-100">倉庫観測コンソール</h1>
      <span className="rounded bg-surface-raised px-2 py-0.5 font-mono text-xs text-slate-300">
        {modeLabel(run.mode)}
      </span>
      {run.run_id && (
        <span className="font-mono text-xs text-slate-400">run {run.run_id}</span>
      )}
      {run.provider && <span className="text-xs text-slate-400">provider: {run.provider}</span>}
      <PersonaBadge source={personaSource} />
      <div className="ml-auto flex items-center gap-3">
        {/* chrome hides in presentation mode; the toggle stays OUTSIDE it so there is always an
            in-app way back (doc22 §12.4). */}
        <div className="flex items-center gap-3 chrome">
          {malformed > 0 && (
            <span className="font-mono text-xs text-warn" title="malformed events">
              ⚠ {malformed}
            </span>
          )}
          {config?.lan && (
            <span className="rounded bg-warn/20 px-1.5 py-0.5 text-xs text-warn">LAN公開</span>
          )}
          <ConnectionStatus />
          <Link href="/runs" className="text-xs text-accent hover:underline">
            runs
          </Link>
        </div>
        <PresentationToggle />
      </div>
    </header>
  );
}

function PersonaBadge({ source }: { source: "canned" | "live" | null }) {
  if (source === "live") {
    return <span className="rounded bg-ok/20 px-1.5 py-0.5 text-xs text-ok">live会話</span>;
  }
  if (source === "canned") {
    return (
      <span
        className="rounded bg-slate-600/40 px-1.5 py-0.5 text-xs text-slate-300"
        title="台本（#288 のlive persona land前）"
      >
        canned会話
      </span>
    );
  }
  return null;
}
