"use client";

import { useStore } from "@/store/useStore";

// Langfuse trace deep-link (doc22:64, §7). Only events that carry a gen_id get a trace_id
// (negotiation start / proposal — doc22:194); for those we surface the join key.
//
// The Langfuse base URL comes from `GET /config` at RUNTIME, never from a NEXT_PUBLIC_* build
// arg: doc22:332 requires exactly that so one static-export bundle serves dev and prod
// (`output:'export'` bakes NEXT_PUBLIC_* into the bundle, doc22:164). It must be the
// PROJECT-scoped base (e.g. https://cloud.langfuse.com/project/<id>) so `${base}/traces/<id>`
// is Langfuse's canonical trace URL. It is not a secret — the token never leaves the gateway
// (doc22:254) — and the gateway has already vetted scheme/credentials (settings.py:
// normalize_langfuse_base_url).
//
// Two independent degrades, both ending at "show the id, don't link it" (doc22:152's no-link
// state): no trace_id at all → render nothing; trace_id but no usable base (unconfigured, or an
// older/other gateway that omits the field) → copyable text the operator can paste into
// Langfuse's search.
// Re-vet the base here even though the gateway already did (settings.py:
// normalize_langfuse_base_url). This is the last hop before an href and the only hop that
// survives meeting a DIFFERENT gateway — an older build, or another deployment — so it checks
// more than the scheme: parse with the browser's own URL parser, then demand http(s), no
// embedded credentials (doc22:254), and no query/fragment that would swallow the id. It does NOT
// re-do the server's homograph-host check: `new URL` has already punycoded the host by the time
// we could look, so THAT rule only holds server-side and this stays a second line of defence,
// not a replacement. Same shape as the ingest seam's doubled guard (ingest.py:74-77).
function safeBase(raw: string): string {
  try {
    const u = new URL(raw);
    if (u.protocol !== "http:" && u.protocol !== "https:") return "";
    if (u.username || u.password) return "";
    if (u.search || u.hash) return ""; // would swallow the /traces/<id> we append
    return raw.replace(/\/+$/, "");
  } catch {
    return ""; // not a parsable absolute URL (relative, protocol-relative, malformed)
  }
}

export function TraceLink({ traceId }: { traceId: string | null }) {
  // Hook first: it must run on every render, including the `!traceId` one (rules of hooks).
  const configured = useStore((s) => s.config?.langfuse_base_url) ?? "";
  if (!traceId) return null;
  const label = `🔗 trace ${traceId.slice(0, 8)}…`;
  const base = safeBase(configured);
  if (base) {
    return (
      <a
        href={`${base}/traces/${encodeURIComponent(traceId)}`}
        target="_blank"
        rel="noreferrer"
        className="font-mono text-xs text-accent hover:underline"
      >
        {label}
      </a>
    );
  }
  return (
    <span className="font-mono text-xs text-slate-500" title={`Langfuse trace ${traceId}`}>
      {label}
    </span>
  );
}
