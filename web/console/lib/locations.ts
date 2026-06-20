// The 9 KNOWN_LOCATIONS for the 2D map (doc22:331 → config/warehouse.base.yaml:36-44). Embedded
// as a build-time layout constant (warehouse geometry, not an env endpoint). Coords are 暫定
// (base.yaml: "Phase 2 地図確定後に実測で確定") — a follow-up can serve these from /config so the
// SPA stops duplicating the yaml.
export interface WarehouseLocation {
  key: string;
  x: number;
  y: number;
  kind: "shelf" | "berth" | "shipping" | "charging" | "retreat";
}

export const KNOWN_LOCATIONS: WarehouseLocation[] = [
  { key: "shelf_1", x: 0.2, y: 0.3, kind: "shelf" },
  { key: "shelf_2", x: 0.7, y: 0.3, kind: "shelf" },
  { key: "shelf_3", x: 1.2, y: 0.3, kind: "shelf" },
  { key: "berth_A", x: 0.2, y: 0.8, kind: "berth" },
  { key: "berth_B", x: 0.7, y: 0.8, kind: "berth" },
  { key: "shipping_station", x: 0.2, y: 0.1, kind: "shipping" },
  { key: "charging_station", x: 1.2, y: 0.1, kind: "charging" },
  { key: "retreat_A", x: 0.45, y: 0.85, kind: "retreat" },
  { key: "retreat_B", x: 0.95, y: 0.85, kind: "retreat" },
];

// Warehouse extent (1.8m × 0.9m diorama, shared/02). Used to scale map coords into the SVG.
export const WAREHOUSE_BOUNDS = { minX: 0, maxX: 1.4, minY: 0, maxY: 0.95 };
