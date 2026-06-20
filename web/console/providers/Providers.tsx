"use client";

import { ConnectionProvider } from "./ConnectionProvider";

// Single client-boundary mounted once by app/layout.tsx (doc22:329). The run mode (doc22's
// ModeProvider) lives in the Zustand store (`run.mode`), read by <ModeGate> / components, so a
// separate context would be redundant — kept here as the one place to add cross-cutting
// providers later.
export function Providers({ children }: { children: React.ReactNode }) {
  return <ConnectionProvider>{children}</ConnectionProvider>;
}
