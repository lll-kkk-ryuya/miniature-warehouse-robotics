// Wire types the console consumes from warehouse_web_bridge (doc22 §5 ObsEvent / §5.1 /config).
// The gateway passes each producer payload through verbatim (decoded JSON), so payload shapes
// are intentionally loose — components narrow by `kind` and read fields defensively.

export type ObsKind =
  | "reasoning"
  | "command"
  | "speech"
  | "turn_baton"
  | "nego_start"
  | "proposal"
  | "abort"
  | "snapshot"
  | "emergency"
  | "run_header"
  | "malformed";

/** The ObsEvent envelope (doc22:141-156). `seq` is the sole ordering / apply key (doc22:160). */
export interface ObsEvent {
  schema_version: number;
  seq: number;
  receive_ts: number;
  source_topic: string;
  kind: ObsKind;
  run_id: string | null;
  gen_id: number | null;
  negotiation_id: string | null;
  robot: string | null;
  trace_id: string | null;
  persona_source: "canned" | "live" | null;
  payload: Record<string, unknown>;
}

/** Browser-facing runtime config from `GET /config` (doc22:166-172) — never carries a secret. */
export interface RuntimeConfig {
  ws_path: string;
  mode: string; // "none" | "simple" | "open-rmf"
  lan: boolean;
  token_required: boolean;
}

export type ConnectionStatus = "idle" | "connecting" | "backfilling" | "open" | "closed";

export interface RobotSnapshot {
  position: { x: number; y: number };
  velocity?: { linear: number; angular: number };
  heading?: number;
  status: string;
  battery: number;
  obstacle_distance?: number | null;
}

export interface RunHeaderInfo {
  run_id: string | null;
  mode: string;
  provider: string | null;
  scenario: string | null;
}

export type NegotiationStatus = "active" | "agreed" | "aborted";

/** One ringi (negotiation) grouped from nego_start / turn_baton / proposal / abort. */
export interface Negotiation {
  id: string;
  genId: number | null;
  status: NegotiationStatus;
  startedSeq: number;
  events: ObsEvent[];
}
