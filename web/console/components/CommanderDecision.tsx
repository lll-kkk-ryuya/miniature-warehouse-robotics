"use client";

import { useEffect, useRef } from "react";
import { useStore } from "@/store/useStore";
import { fmtClock, str } from "@/lib/format";
import { Panel, EmptyState } from "./Panel";
import type { ObsEvent } from "@/lib/types";

// Commander LLM thinking log (/llm/reasoning) + issued commands (/llm/command), newest last
// (doc22:331). These carry no gen_id so there is no Langfuse deep-link in v1 (doc22:194); a
// /llm/situation additive (S2.5) would add the join.
export function CommanderDecision() {
  const log = useStore((s) => s.commanderLog);
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [log.length]);

  return (
    <Panel title="司令官の判断">
      {log.length === 0 ? (
        <EmptyState>判断待ち</EmptyState>
      ) : (
        <ul className="flex flex-col gap-2">
          {log.map((e) => (
            <DecisionRow key={e.seq} event={e} />
          ))}
          <div ref={endRef} />
        </ul>
      )}
    </Panel>
  );
}

function DecisionRow({ event }: { event: ObsEvent }) {
  if (event.kind === "reasoning") {
    return (
      <li className="border-l-2 border-slate-600 pl-2">
        <TimeTag ts={event.receive_ts} label="💭 reasoning" />
        <p className="whitespace-pre-wrap break-words text-sm text-slate-200">
          {str(event.payload, "text")}
        </p>
      </li>
    );
  }
  // command
  const reasoning = str(event.payload, "reasoning");
  const commands = Array.isArray(event.payload.commands)
    ? (event.payload.commands as Record<string, unknown>[])
    : [];
  return (
    <li className="rounded border border-slate-700 bg-surface-raised p-2">
      <TimeTag ts={event.receive_ts} label="⚡ command" />
      {reasoning && <p className="mb-1 text-xs text-slate-400">{reasoning}</p>}
      <ul className="flex flex-col gap-0.5">
        {commands.map((c, i) => (
          <li key={i} className="font-mono text-xs text-slate-100">
            {str(c, "bot") ?? "?"} → {str(c, "action") ?? "?"}
            {str(c, "destination") ? ` ${str(c, "destination")}` : ""}
            {typeof c.duration === "number" ? ` ${c.duration}s` : ""}
          </li>
        ))}
      </ul>
    </li>
  );
}

function TimeTag({ ts, label }: { ts: number; label: string }) {
  return (
    <div className="mb-0.5 flex items-baseline gap-2">
      <span className="text-xs font-semibold text-accent">{label}</span>
      <span className="text-[10px] text-slate-500">{fmtClock(ts)}</span>
    </div>
  );
}
