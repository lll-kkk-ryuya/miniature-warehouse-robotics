"use client";

import { useStore } from "@/store/useStore";
import { fmtClock, str, num } from "@/lib/format";
import { Panel, EmptyState } from "./Panel";
import { TraceLink } from "./TraceLink";
import type { Negotiation, ObsEvent } from "@/lib/types";

const STATUS_LABEL = { active: "交渉中", agreed: "合意 ✓", aborted: "中断 ✗" } as const;
const STATUS_COLOR = { active: "text-warn", agreed: "text-ok", aborted: "text-danger" } as const;

// Ringi (稟議) flow grouped per negotiation: 開始 → バトン → 合意 / 中断 (doc22:331). The trace
// deep-link rides the gen_id-bearing nego events (doc22:194).
export function RingiFlow() {
  const negotiations = useStore((s) => s.negotiations);
  const recent = negotiations.slice(-6).reverse();
  return (
    <Panel title="稟議フロー">
      {recent.length === 0 ? (
        <EmptyState>稟議なし</EmptyState>
      ) : (
        <ul className="flex flex-col gap-3">
          {recent.map((n) => (
            <NegotiationCard key={n.id} negotiation={n} />
          ))}
        </ul>
      )}
    </Panel>
  );
}

function NegotiationCard({ negotiation }: { negotiation: Negotiation }) {
  const trace = negotiation.events.map((e) => e.trace_id).find(Boolean) ?? null;
  return (
    <li className="rounded border border-slate-700 bg-surface-raised p-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-mono text-xs text-slate-400">
          {negotiation.id}
          {negotiation.genId != null && ` · gen ${negotiation.genId}`}
        </span>
        <span className={`text-xs font-semibold ${STATUS_COLOR[negotiation.status]}`}>
          {STATUS_LABEL[negotiation.status]}
        </span>
      </div>
      <ol className="flex flex-col gap-0.5">
        {negotiation.events.map((e) => (
          <StepRow key={e.seq} event={e} />
        ))}
      </ol>
      {trace && (
        <div className="mt-1">
          <TraceLink traceId={trace} />
        </div>
      )}
    </li>
  );
}

function stepText(e: ObsEvent): string {
  switch (e.kind) {
    case "nego_start":
      return `開始（starter: ${str(e.payload, "starter") ?? "?"}）`;
    case "turn_baton": {
      const next = str(e.payload, "next");
      return `バトン → ${next ?? "?"}${num(e.payload, "turn") != null ? ` (turn ${num(e.payload, "turn")})` : ""}`;
    }
    case "proposal": {
      const a = e.payload.agreed_action as Record<string, unknown> | undefined;
      const action = a ? str(a, "action") : undefined;
      const by = a ? str(a, "by") : undefined;
      return `合意: ${action ?? "?"}${by ? ` by ${by}` : ""}`;
    }
    case "abort":
      return `中断（${str(e.payload, "reason") ?? "?"}）`;
    default:
      return e.kind;
  }
}

function StepRow({ event }: { event: ObsEvent }) {
  return (
    <li className="flex items-baseline gap-2 text-xs">
      <span className="text-[10px] text-slate-500">{fmtClock(event.receive_ts)}</span>
      <span className="text-slate-200">{stepText(event)}</span>
    </li>
  );
}
