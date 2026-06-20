"use client";

import { useStore } from "@/store/useStore";
import { Panel, EmptyState } from "./Panel";
import type { RobotSnapshot } from "@/lib/types";

// Battery band thresholds 10/20/30% (doc22:381 → doc12:250-252). Non-color cue (icon + label)
// alongside the colour for a11y (doc22:290).
function batteryBand(pct: number): { color: string; icon: string; label: string } {
  if (pct < 10) return { color: "text-danger", icon: "▼", label: "危機" };
  if (pct < 20) return { color: "text-warn", icon: "▽", label: "低" };
  if (pct < 30) return { color: "text-amber-400", icon: "△", label: "注意" };
  return { color: "text-ok", icon: "▲", label: "良好" };
}

export function SituationFleet() {
  const snapshots = useStore((s) => s.snapshots);
  const ts = useStore((s) => s.snapshotTs);
  const robots = Object.entries(snapshots).sort(([a], [b]) => a.localeCompare(b));
  return (
    <Panel
      title="ロボット状態"
      accessory={ts && <span className="font-mono text-[10px] text-slate-500">{ts}</span>}
    >
      {robots.length === 0 ? (
        <EmptyState>状態未受信</EmptyState>
      ) : (
        <ul className="flex flex-col gap-2">
          {robots.map(([name, r]) => (
            <RobotRow key={name} name={name} robot={r} />
          ))}
        </ul>
      )}
    </Panel>
  );
}

function RobotRow({ name, robot }: { name: string; robot: RobotSnapshot }) {
  const band = batteryBand(robot.battery);
  const pos = robot.position;
  return (
    <li className="rounded border border-slate-700 bg-surface-raised p-2">
      <div className="flex items-center justify-between">
        <span className={`font-mono text-sm ${name.includes("1") ? "text-accent-bot1" : "text-accent-bot2"}`}>
          {name}
        </span>
        <span className={`font-mono text-sm ${band.color}`} title={band.label}>
          {band.icon} {robot.battery}% <span className="text-[10px]">{band.label}</span>
        </span>
      </div>
      <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono text-xs text-slate-400">
        <span>状態: {robot.status}</span>
        {pos && (
          <span>
            pos ({pos.x?.toFixed(2)}, {pos.y?.toFixed(2)})
          </span>
        )}
        {robot.velocity && <span>v {robot.velocity.linear?.toFixed(2)} m/s</span>}
        {robot.obstacle_distance != null && <span>障害物 {robot.obstacle_distance.toFixed(2)} m</span>}
      </div>
    </li>
  );
}
