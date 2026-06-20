"use client";

import { useEffect, useRef } from "react";
import { VList, type VListHandle } from "virtua";
import { useStore } from "@/store/useStore";
import { fmtClock, str } from "@/lib/format";
import { Panel, EmptyState } from "./Panel";
import type { ObsEvent } from "@/lib/types";

// Bot1/Bot2 negotiation conversation, newest at the bottom. Virtualized (doc22:331) so a long
// run stays smooth; sticks to the bottom as new turns arrive.
export function ConversationTimeline() {
  const conversation = useStore((s) => s.conversation);
  const ref = useRef<VListHandle>(null);

  useEffect(() => {
    if (conversation.length > 0) {
      ref.current?.scrollToIndex(conversation.length - 1, { align: "end" });
    }
  }, [conversation.length]);

  return (
    <Panel title="会話タイムライン" scroll={false}>
      {conversation.length === 0 ? (
        <EmptyState>交渉なし（通常サイクル）</EmptyState>
      ) : (
        <VList ref={ref} style={{ height: "100%" }} className="p-3">
          {conversation.map((e) => (
            <SpeechRow key={e.seq} event={e} />
          ))}
        </VList>
      )}
    </Panel>
  );
}

function SpeechRow({ event }: { event: ObsEvent }) {
  const speaker = str(event.payload, "speaker") ?? event.robot ?? "?";
  const text = str(event.payload, "text") ?? "";
  const isBot1 = speaker.includes("1");
  return (
    <div className="mb-2 flex flex-col">
      <div className="flex items-baseline gap-2">
        <span className={`font-mono text-xs ${isBot1 ? "text-accent-bot1" : "text-accent-bot2"}`}>
          {speaker}
        </span>
        <span className="text-[10px] text-slate-500">{fmtClock(event.receive_ts)}</span>
      </div>
      <p className="whitespace-pre-wrap break-words text-sm leading-snug text-slate-100">{text}</p>
    </div>
  );
}
