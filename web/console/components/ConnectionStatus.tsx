"use client";

import { useStore } from "@/store/useStore";
import type { ConnectionStatus as Status } from "@/lib/types";

const LABEL: Record<Status, string> = {
  idle: "待機",
  connecting: "接続中…",
  backfilling: "再同期中…",
  open: "● LIVE",
  closed: "切断（再接続中…）",
};

const COLOR: Record<Status, string> = {
  idle: "text-slate-400",
  connecting: "text-warn",
  backfilling: "text-warn",
  open: "text-ok",
  closed: "text-danger",
};

export function ConnectionStatus() {
  const status = useStore((s) => s.status);
  const lastSeq = useStore((s) => s.lastSeq);
  return (
    <span className="flex items-center gap-2 font-mono text-sm">
      <span className={COLOR[status]}>{LABEL[status]}</span>
      <span className="text-slate-500">seq {lastSeq}</span>
    </span>
  );
}
