// Langfuse trace deep-link (doc22:64, §7). Only events that carry a gen_id get a trace_id
// (negotiation start / proposal — doc22:194); for those we surface the join key. A clickable
// deep-link needs the Langfuse base URL, which the gateway does not expose to the browser
// (no secret on the wire, doc22:254); NEXT_PUBLIC_LANGFUSE_URL is an optional dev convenience.
// Without it the trace_id is shown as a copyable value the operator can search in Langfuse.
export function TraceLink({ traceId }: { traceId: string | null }) {
  if (!traceId) return null;
  const base = process.env.NEXT_PUBLIC_LANGFUSE_URL;
  const label = `🔗 trace ${traceId.slice(0, 8)}…`;
  if (base) {
    return (
      <a
        href={`${base.replace(/\/+$/, "")}/traces/${traceId}`}
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
