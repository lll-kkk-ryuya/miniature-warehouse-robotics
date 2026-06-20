"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchEvents, fetchRuns } from "@/lib/api";
import { fmtClock, str } from "@/lib/format";
import type { ObsEvent } from "@/lib/types";

// Runs picker + read-only replay (doc22:329,:243). A selected run is replayed from /events
// (doc22:242). v1 uses client state; a ?run_id deep-link + scrub into the live panels is a
// follow-up (kept out to avoid static-export useSearchParams/Suspense plumbing).
export default function RunsPage() {
  const [runs, setRuns] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [events, setEvents] = useState<ObsEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchRuns()
      .then(setRuns)
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!selected) return;
    let stale = false; // ignore a superseded fetch if the selection changed mid-flight
    setError(null);
    setEvents([]); // clear the old run's table immediately so header + body never disagree
    fetchEvents({ runId: selected, limit: 1000 })
      .then((rows) => {
        if (!stale) setEvents(rows);
      })
      .catch((e) => {
        if (!stale) setError(String(e));
      });
    return () => {
      stale = true;
    };
  }, [selected]);

  return (
    <main className="flex h-full">
      <aside className="w-64 shrink-0 overflow-auto border-r border-slate-700 p-3">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-200">記録 (runs)</h2>
          <Link href="/live" className="text-xs text-accent hover:underline">
            ← live
          </Link>
        </div>
        {error && <p className="mb-2 text-xs text-danger">{error}</p>}
        <ul className="flex flex-col gap-1">
          {runs.map((r) => (
            <li key={r}>
              <button
                type="button"
                onClick={() => setSelected(r)}
                className={`w-full rounded px-2 py-1 text-left font-mono text-xs ${
                  selected === r
                    ? "bg-surface-raised text-slate-100"
                    : "text-slate-400 hover:bg-surface-raised"
                }`}
              >
                {r}
              </button>
            </li>
          ))}
          {runs.length === 0 && !error && (
            <li className="text-xs text-slate-500">記録なし</li>
          )}
        </ul>
      </aside>

      <section className="min-w-0 flex-1 overflow-auto p-3">
        {!selected ? (
          <p className="text-sm text-slate-500">run を選択してください</p>
        ) : (
          <>
            <h3 className="mb-2 font-mono text-sm text-slate-300">
              {selected} <span className="text-slate-500">· {events.length} events</span>
            </h3>
            <table className="w-full border-collapse text-xs">
              <thead className="text-slate-500">
                <tr className="border-b border-slate-700 text-left">
                  <th className="py-1 pr-3">seq</th>
                  <th className="py-1 pr-3">time</th>
                  <th className="py-1 pr-3">kind</th>
                  <th className="py-1">summary</th>
                </tr>
              </thead>
              <tbody className="font-mono text-slate-200">
                {events.map((e) => (
                  <tr key={e.seq} className="border-b border-slate-800">
                    <td className="py-0.5 pr-3 text-slate-500">{e.seq}</td>
                    <td className="py-0.5 pr-3 text-slate-500">{fmtClock(e.receive_ts)}</td>
                    <td className="py-0.5 pr-3 text-accent">{e.kind}</td>
                    <td className="py-0.5 text-slate-300">{summarize(e)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>
    </main>
  );
}

function summarize(e: ObsEvent): string {
  switch (e.kind) {
    case "speech":
      return `${str(e.payload, "speaker") ?? "?"}: ${str(e.payload, "text") ?? ""}`;
    case "reasoning":
      return str(e.payload, "text") ?? "";
    case "command":
      return str(e.payload, "reasoning") ?? "command";
    case "emergency":
      return `${str(e.payload, "type") ?? ""} (${str(e.payload, "severity") ?? ""})`;
    case "proposal":
      return `合意 ${e.negotiation_id ?? ""}`;
    case "malformed":
      return String(e.payload.raw ?? "").slice(0, 80);
    default:
      return e.robot ?? "";
  }
}
