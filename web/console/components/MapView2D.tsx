"use client";

import { useStore } from "@/store/useStore";
import { KNOWN_LOCATIONS, WAREHOUSE_BOUNDS } from "@/lib/locations";
import { Panel } from "./Panel";

// Lightweight 2D top-down map: the 9 KNOWN_LOCATIONS + the two robots, from the (coalesced)
// snapshot only. Deliberately NOT the raw ROS graph — /scan, /map and costmap are out of scope
// for the browser (doc22:25 — bandwidth / safety / Jetson cost).
const W = 100;
const H = 100;
const PAD = 8;

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

function project(x: number, y: number): { cx: number; cy: number } {
  const { minX, maxX, minY, maxY } = WAREHOUSE_BOUNDS;
  // clamp so an out-of-bounds robot pins to the map edge instead of clipping off-screen.
  const cx = clamp(PAD + ((x - minX) / (maxX - minX)) * (W - 2 * PAD), PAD, W - PAD);
  const cy = clamp(H - PAD - ((y - minY) / (maxY - minY)) * (H - 2 * PAD), PAD, H - PAD); // y inverted
  return { cx, cy };
}

export function MapView2D() {
  const snapshots = useStore((s) => s.snapshots);
  return (
    <Panel title="2Dマップ" scroll={false}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" className="h-full w-full">
        <rect x={0} y={0} width={W} height={H} fill="#0b0f17" />
        {KNOWN_LOCATIONS.map((loc) => {
          const { cx, cy } = project(loc.x, loc.y);
          return (
            <g key={loc.key}>
              <rect x={cx - 1.6} y={cy - 1.6} width={3.2} height={3.2} rx={0.5} fill="#334155" />
              <text x={cx} y={cy - 2.6} fontSize={2.4} fill="#64748b" textAnchor="middle">
                {loc.key}
              </text>
            </g>
          );
        })}
        {Object.entries(snapshots).map(([name, r]) => {
          if (!r.position) return null;
          const { cx, cy } = project(r.position.x, r.position.y);
          const color = name.includes("1") ? "#38bdf8" : "#f59e0b";
          return (
            <g key={name}>
              <circle cx={cx} cy={cy} r={2.4} fill={color} />
              <text x={cx} y={cy + 4.6} fontSize={2.8} fill={color} textAnchor="middle">
                {name}
              </text>
            </g>
          );
        })}
      </svg>
    </Panel>
  );
}
