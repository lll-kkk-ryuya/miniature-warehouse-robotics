import { gatewayBase } from "./config";
import type { ObsEvent } from "./types";

// REST replay endpoints (doc22 §10:242-245). Used for history/replay only — the live stream is
// the WebSocket (TanStack Query is intentionally not used for live, doc22:330).

export async function fetchRuns(): Promise<string[]> {
  const res = await fetch(`${gatewayBase()}/runs`, { cache: "no-store" });
  if (!res.ok) throw new Error(`GET /runs -> ${res.status}`);
  const data = (await res.json()) as { runs?: string[] };
  return data.runs ?? [];
}

export interface EventsQuery {
  runId: string;
  sinceSeq?: number;
  toSeq?: number;
  kind?: string;
  limit?: number;
}

export async function fetchEvents(q: EventsQuery): Promise<ObsEvent[]> {
  const params = new URLSearchParams({ run_id: q.runId });
  if (q.sinceSeq != null) params.set("since_seq", String(q.sinceSeq));
  if (q.toSeq != null) params.set("to_seq", String(q.toSeq));
  if (q.kind) params.set("kind", q.kind);
  if (q.limit != null) params.set("limit", String(q.limit));
  const res = await fetch(`${gatewayBase()}/events?${params.toString()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`GET /events -> ${res.status}`);
  const data = (await res.json()) as { events?: ObsEvent[] };
  return data.events ?? [];
}
