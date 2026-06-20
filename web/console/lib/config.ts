import type { RuntimeConfig } from "./types";

// Resolve where the gateway lives. In prod the SPA is served BY web_bridge at :8646 → same-origin
// (relative URLs). Any other origin is a dev `next dev` server (:3000 by default, but this holds
// for any dev port) → reach the gateway at :8646 on the same host (doc22:333). The one thing that
// can't come from GET /config is how to reach /config itself in dev; NEXT_PUBLIC_GATEWAY_URL is a
// dev-only override for non-default setups (never a secret, doc22:175,:332). NB: assumes the
// documented direct :8646 deployment; a reverse-proxied prod on another port uses the override.
export function gatewayBase(): string {
  if (typeof window === "undefined") return "";
  const override = process.env.NEXT_PUBLIC_GATEWAY_URL;
  if (override) return override.replace(/\/+$/, "");
  const { protocol, hostname, port } = window.location;
  if (port === "8646") return ""; // served by the gateway → same-origin (relative)
  return `${protocol}//${hostname}:8646`; // dev cross-origin → gateway on the same host
}

export async function fetchConfig(): Promise<RuntimeConfig> {
  const res = await fetch(`${gatewayBase()}/config`, { cache: "no-store" });
  if (!res.ok) throw new Error(`GET /config -> ${res.status}`);
  return (await res.json()) as RuntimeConfig;
}

/** Build the absolute WebSocket URL for /ws?since_seq=N (doc22:241). */
export function wsUrl(config: RuntimeConfig, sinceSeq: number): string {
  const path = config.ws_path || "/ws";
  // doc22:168 allows an absolute ws URL in the dev cross-origin case; honor it if the server ever
  // returns one (the current server always returns the relative "/ws", settings.py:browser_config).
  if (/^wss?:\/\//i.test(path)) return `${path}?since_seq=${sinceSeq}`;
  if (/^https?:\/\//i.test(path)) {
    return `${path.replace(/^http/i, "ws")}?since_seq=${sinceSeq}`;
  }
  const base = gatewayBase();
  const httpOrigin = base || (typeof window !== "undefined" ? window.location.origin : "");
  const wsOrigin = httpOrigin.replace(/^http/i, "ws");
  return `${wsOrigin}${path}?since_seq=${sinceSeq}`;
}
