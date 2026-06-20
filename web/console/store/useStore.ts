import { create } from "zustand";
import type {
  ConnectionStatus,
  Negotiation,
  ObsEvent,
  RobotSnapshot,
  RunHeaderInfo,
  RuntimeConfig,
} from "@/lib/types";

// Bound the append-only arrays so a long run can't grow the tab unboundedly (the timeline is
// virtualized; deep history is re-fetched via /events). The live invariant that matters is
// seq-ordering, not retaining every event.
const MAX_CONVERSATION = 2000;
const MAX_COMMANDER = 1000;
const MAX_EMERGENCIES = 500;
const MAX_NEGOTIATIONS = 200;
const MAX_NEG_EVENTS = 100; // per-negotiation step cap (defensive; see groupKeylessNego)

function capPush<T>(arr: T[], item: T, max: number): T[] {
  const next = arr.length >= max ? arr.slice(arr.length - max + 1) : arr.slice();
  next.push(item);
  return next;
}

// nego_start / proposal carry a key on the wire (gen_id; proposal also negotiation_id). But
// /negotiation/turn ({turn,next}) and /negotiation/abort ({reason,bot,event_id}) carry NEITHER
// (negotiation_messages.py / doc03:108), so they reach us with negotiation_id=null & gen_id=null.
// Negotiations are sequential in practice (the commander fires one start_negotiation per
// deadlock), so a keyless turn/abort belongs to the most-recent still-open negotiation. This is
// a heuristic until the additive /negotiation/turn negotiation_id lands (doc22 §14, S2.5).
function keyedNegId(e: ObsEvent): string | null {
  if (e.negotiation_id) return e.negotiation_id;
  if (e.gen_id != null) return `gen:${e.gen_id}`;
  return null;
}

function applyNego(list: Negotiation[], e: ObsEvent): Negotiation[] {
  const status: Negotiation["status"] =
    e.kind === "proposal" ? "agreed" : e.kind === "abort" ? "aborted" : "active";
  const key = keyedNegId(e);

  let idx = -1;
  if (key) {
    idx = list.findIndex((n) => n.id === key);
  } else {
    // keyless (turn_baton / abort): attach to the most-recent still-active negotiation.
    for (let i = list.length - 1; i >= 0; i--) {
      if (list[i].status === "active") {
        idx = i;
        break;
      }
    }
  }

  if (idx === -1) {
    const created: Negotiation = {
      id: key ?? `orphan:${e.seq}`,
      genId: e.gen_id,
      status,
      startedSeq: e.seq,
      events: [e],
    };
    return capPush(list, created, MAX_NEGOTIATIONS);
  }

  const next = list.slice();
  const cur = next[idx];
  next[idx] = {
    ...cur,
    genId: cur.genId ?? e.gen_id,
    status: status === "active" ? cur.status : status, // active never overwrites agreed/aborted
    events: capPush(cur.events, e, MAX_NEG_EVENTS),
  };
  return next;
}

export interface StoreState {
  config: RuntimeConfig | null;
  status: ConnectionStatus;
  runId: string | null; // envelope run_id of the run currently in view (run-boundary key)
  lastSeq: number;
  run: RunHeaderInfo;
  snapshots: Record<string, RobotSnapshot>;
  snapshotTs: string | null;
  conversation: ObsEvent[];
  negotiations: Negotiation[];
  commanderLog: ObsEvent[];
  emergencies: ObsEvent[];
  malformedCount: number;

  setConfig: (c: RuntimeConfig) => void;
  setStatus: (s: ConnectionStatus) => void;
  applyEvent: (e: ObsEvent) => void;
}

const EMPTY_RUN: RunHeaderInfo = { run_id: null, mode: "none", provider: null, scenario: null };

// run-scoped state cleared on a run boundary (excludes config / connection — doc22:309 (c)).
const RUN_RESET = {
  lastSeq: 0,
  snapshots: {} as Record<string, RobotSnapshot>,
  snapshotTs: null,
  conversation: [] as ObsEvent[],
  negotiations: [] as Negotiation[],
  commanderLog: [] as ObsEvent[],
  emergencies: [] as ObsEvent[],
  malformedCount: 0,
};

function applyKind(s: StoreState, e: ObsEvent): Partial<StoreState> {
  const p = e.payload ?? {};
  switch (e.kind) {
    case "snapshot": {
      const robots = p.robots;
      const isObj = !!robots && typeof robots === "object" && !Array.isArray(robots);
      return {
        snapshots: isObj ? (robots as Record<string, RobotSnapshot>) : s.snapshots,
        snapshotTs: typeof p.timestamp === "string" ? p.timestamp : s.snapshotTs,
      };
    }
    case "speech":
      return { conversation: capPush(s.conversation, e, MAX_CONVERSATION) };
    case "reasoning":
    case "command":
      return { commanderLog: capPush(s.commanderLog, e, MAX_COMMANDER) };
    case "emergency":
      return { emergencies: capPush(s.emergencies, e, MAX_EMERGENCIES) };
    case "run_header":
      return {
        run: {
          run_id: (p.run_id as string) ?? e.run_id,
          mode: (p.mode as string) ?? s.run.mode,
          provider: (p.provider as string) ?? null,
          scenario: (p.scenario as string) ?? null,
        },
      };
    case "nego_start":
    case "turn_baton":
    case "proposal":
    case "abort":
      return { negotiations: applyNego(s.negotiations, e) };
    case "malformed":
      return { malformedCount: s.malformedCount + 1 };
    default:
      return {};
  }
}

function reduce(s: StoreState, e: ObsEvent): Partial<StoreState> | null {
  if (typeof e.seq !== "number") return null;
  // run boundary: per-run seq restarts low (doc22:143,:160,:309). A new envelope run_id clears
  // run-scoped state so the new run's low seqs are not all dropped by dedup, and old-run data
  // does not bleed in. Detection precedes dedup.
  const isNewRun = e.run_id != null && s.runId != null && e.run_id !== s.runId;
  if (!isNewRun && e.seq <= s.lastSeq) return null; // dedup within a run (backfill overlap)

  const base: StoreState = isNewRun ? { ...s, ...RUN_RESET, run: EMPTY_RUN } : s;
  const patch = applyKind(base, e);
  const resetRun = isNewRun
    ? { run: { ...EMPTY_RUN, mode: s.config?.mode ?? "none" } }
    : {};
  return {
    ...(isNewRun ? RUN_RESET : {}),
    ...resetRun,
    ...patch,
    lastSeq: e.seq,
    runId: e.run_id ?? base.runId,
  };
}

export const useStore = create<StoreState>((set) => ({
  config: null,
  status: "idle",
  runId: null,
  lastSeq: 0,
  run: EMPTY_RUN,
  snapshots: {},
  snapshotTs: null,
  conversation: [],
  negotiations: [],
  commanderLog: [],
  emergencies: [],
  malformedCount: 0,

  setConfig: (c) =>
    set((s) => ({
      config: c,
      // until /run/header lands (S2.5) the mode comes from /config (doc22:303).
      run: s.run.run_id ? s.run : { ...s.run, mode: c.mode },
    })),

  setStatus: (status) => set({ status }),

  applyEvent: (e) => set((s) => reduce(s, e) ?? {}),
}));
