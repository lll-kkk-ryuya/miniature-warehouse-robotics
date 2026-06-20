"use client";

import { useStore } from "@/store/useStore";
import { fmtClock, str } from "@/lib/format";
import { Panel, EmptyState } from "./Panel";
import type { ObsEvent } from "@/lib/types";

const SEVERITY_STYLE: Record<string, { color: string; icon: string }> = {
  critical: { color: "text-danger", icon: "■" },
  high: { color: "text-danger", icon: "▲" },
  warning: { color: "text-warn", icon: "▲" },
  info: { color: "text-slate-300", icon: "●" },
};

// Emergency events (/emergency/event, doc22:117). Severity carries an icon as well as colour
// (a11y non-colour cue, doc22:290). Newest first.
export function EmergencyPanel() {
  const emergencies = useStore((s) => s.emergencies);
  const recent = emergencies.slice(-30).reverse();
  return (
    <Panel title="緊急イベント">
      {recent.length === 0 ? (
        <EmptyState>緊急なし</EmptyState>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {recent.map((e) => (
            <EmergencyRow key={e.seq} event={e} />
          ))}
        </ul>
      )}
    </Panel>
  );
}

function EmergencyRow({ event }: { event: ObsEvent }) {
  const severity = str(event.payload, "severity") ?? "info";
  const style = SEVERITY_STYLE[severity] ?? SEVERITY_STYLE.info;
  return (
    <li className="rounded border border-slate-700 bg-surface-raised p-2">
      <div className="flex items-center justify-between">
        <span className={`text-sm font-semibold ${style.color}`}>
          {style.icon} {str(event.payload, "type") ?? "event"}
        </span>
        <span className="text-[10px] text-slate-500">{fmtClock(event.receive_ts)}</span>
      </div>
      <div className="mt-0.5 font-mono text-xs text-slate-400">
        {event.robot && <span>{event.robot} · </span>}
        <span>{severity}</span>
        {str(event.payload, "action_taken") && (
          <span> · 対処: {str(event.payload, "action_taken")}</span>
        )}
      </div>
    </li>
  );
}
