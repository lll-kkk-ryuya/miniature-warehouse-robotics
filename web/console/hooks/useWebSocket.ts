"use client";

import { useEffect, useRef } from "react";
import { fetchConfig, wsUrl } from "@/lib/config";
import { useStore } from "@/store/useStore";
import type { ObsEvent } from "@/lib/types";

const BASE_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 15_000;

// Owns the single live WebSocket (doc22:330). Bootstraps GET /config, connects /ws?since_seq=N,
// applies each ObsEvent to the store by seq, and reconnects with exponential backoff + jitter
// (the client side of the reconnect-storm contract, doc22:235). On reconnect it resends the last
// applied seq so the server backfills the gap from events.jsonl, then continues live (overlap is
// idempotent — the store dedups by seq, doc22:232,:234,:330).
//
// Concurrency: handlers are guarded with `wsRef.current !== ws` and an effect-local `alive` flag
// so a React StrictMode double-mount (dev) can't leak a second socket or let an orphan socket's
// late `onclose` knock over the live connection.
export function useWebSocket(): void {
  const setConfig = useStore((s) => s.setConfig);
  const setStatus = useStore((s) => s.setStatus);
  const applyEvent = useStore((s) => s.applyEvent);

  const wsRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);

  useEffect(() => {
    let alive = true;

    function scheduleReconnect() {
      if (!alive) return;
      const attempt = attemptRef.current++;
      const expo = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** attempt);
      const jitter = Math.random() * expo * 0.3;
      timerRef.current = setTimeout(connect, expo + jitter);
    }

    async function connect() {
      if (!alive || wsRef.current) return; // one socket per mount; reconnect nulls wsRef first
      // Reflect "trying" up front so a cold-start where GET /config fails does not leave the UI
      // stuck on the initial "待機"/idle while it is actually retrying in the background.
      const sinceSeq = useStore.getState().lastSeq;
      setStatus(sinceSeq > 0 ? "backfilling" : "connecting");

      let config = useStore.getState().config;
      if (!config) {
        try {
          config = await fetchConfig();
        } catch {
          scheduleReconnect(); // status stays "connecting" — accurate, a retry is scheduled
          return;
        }
        if (!alive) return;
        setConfig(config);
      }
      if (!alive || wsRef.current) return;

      let ws: WebSocket;
      try {
        ws = new WebSocket(wsUrl(config, sinceSeq));
      } catch {
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        if (wsRef.current !== ws) return;
        attemptRef.current = 0;
        setStatus("open");
      };
      ws.onmessage = (ev) => {
        if (wsRef.current !== ws) return;
        try {
          applyEvent(JSON.parse(ev.data as string) as ObsEvent);
        } catch {
          /* a non-JSON frame is ignored; the gateway only ever sends ObsEvent JSON */
        }
      };
      ws.onclose = () => {
        if (wsRef.current !== ws) return; // an orphan (superseded) socket — ignore its close
        wsRef.current = null;
        setStatus("closed");
        scheduleReconnect();
      };
      ws.onerror = () => {
        if (wsRef.current !== ws) return;
        try {
          ws.close();
        } catch {
          /* close races a server-side close (overflow 1011 / cap 1013) — ignore */
        }
      };
    }

    connect();

    return () => {
      alive = false;
      if (timerRef.current) clearTimeout(timerRef.current);
      if (wsRef.current) {
        try {
          wsRef.current.close();
        } catch {
          /* ignore */
        }
        wsRef.current = null;
      }
    };
  }, [setConfig, setStatus, applyEvent]);
}
