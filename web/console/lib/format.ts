// Small display helpers. Producer timestamps are display-only — seq is the order (doc22:160).

export function str(p: Record<string, unknown>, key: string): string | undefined {
  const v = p[key];
  return typeof v === "string" ? v : undefined;
}

export function num(p: Record<string, unknown>, key: string): number | undefined {
  const v = p[key];
  return typeof v === "number" ? v : undefined;
}

/** receive_ts (epoch seconds) → HH:MM:SS (24h, ja-JP locale, the viewer's own timezone). Display
 * only — seq is the order (doc22:160). */
export function fmtClock(ts: number): string {
  if (!Number.isFinite(ts)) return "--:--:--";
  return new Date(ts * 1000).toLocaleTimeString("ja-JP", { hour12: false });
}

const MODE_LABEL: Record<string, string> = {
  none: "Mode A",
  simple: "Mode B",
  "open-rmf": "Mode C",
};

/** Display label for a traffic_mode string (doc22:170, §18#10 is display-side only). */
export function modeLabel(mode: string): string {
  return MODE_LABEL[mode] ? `${MODE_LABEL[mode]}（${mode}）` : mode;
}

/** Mode C (open-rmf) hides conversation / ringi (doc22 §12.1) — no character LLM is launched. */
export function isOpenRmf(mode: string): boolean {
  return mode === "open-rmf";
}
