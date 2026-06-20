"use client";

import { useWebSocket } from "@/hooks/useWebSocket";

// Mounts the single WebSocket lifecycle once (doc22:329,:330). The connection + store survive
// navigation because this lives in the root layout, not in a page.
export function ConnectionProvider({ children }: { children: React.ReactNode }) {
  useWebSocket();
  return <>{children}</>;
}
