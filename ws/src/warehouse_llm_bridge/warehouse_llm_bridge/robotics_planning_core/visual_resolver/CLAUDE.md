# visual_resolver — Mode X-ER L3 Visual Resolver (XER3, GitHub #339)

Pixel -> map -> known-location snap. Standalone, **bridge-local offline core** consumed LATER
by XER5. Turns each `RoboticsPlanDraft` detection's image pixel into a map point via the
calibration homography, gates it (valid polygon / reprojection error / snap radius), and snaps
it to the nearest frozen `KNOWN_LOCATIONS` key — or marks it `unresolved` (the 0-dispatch path).

- **担当トラック / ブランチ**: Mode X-ER / `feat/mode-x-er-xer3`
- **Phase**: Mode X-ER L3 Planning Core (stage 2 of 4, doc02:14-16).
- **編集境界**: this subpackage dir + `tests/unit/test_visual_resolver.py` ONLY. **Additive**:
  no existing file was edited (not `validator/*`, not `models/*`, not `pipeline.py`, not
  `conftest.py`, not docs/config). The R-26 0-dispatch gate (`validator/report.py`
  `command_candidates`) is untouched.

## frozen vs bridge-local

doc02 line 5 declares EVERYTHING in doc02 internal/illustrative — **NOT** a frozen
`warehouse_interfaces` contract. Every class/threshold/enum here is **bridge-local (発明)**,
marked as such in each module. It stays bridge-local until XER1-XER2 stabilize the shape
(models/base.py:8-12, doc02:278). It REUSES (does not redefine) the landed
`Calibration`/`CalibrationLoader` and the frozen `KNOWN_LOCATIONS` vocabulary.

## 提供 (produce) — consumed later by XER5

- `VisualTaskResolver.resolve(plan: RoboticsPlanDraft, calibration: Calibration) -> ResolutionResult`
  — the doc02:251-252 signature. Stateless; inject a `VisualPolicy` at construction.
- `ResolutionResult{ targets: list[ResolvedTarget] }` (doc02:252).
- `ResolvedTarget{ target_id:str, resolution:Resolution, destination:str|None, confidence:float,
  reason:str }` (doc02:126-131).
- `Resolution` StrEnum `{known_location, unresolved}` (doc02:128,151).
- `UnresolvedReason` StrEnum `{off_map, outside_valid_polygon, beyond_snap_radius,
  reprojection_error_too_large, no_calibration}` (doc02:151 + the snap/calibration gates).
- `VisualPolicy` (frozen dataclass) — INJECTED thresholds + confidence formula + location coords.

## 消費 (consume)

- `warehouse_llm_bridge.robotics_planning_core.models.robotics_plan_draft.RoboticsPlanDraft` /
  `Detection` — `Detection.pixel: list[int]` (u, v) IS the per-target pixel (draft already
  carries it, so NO bridge-local Detection input type was added; the draft was NOT modified).
- `warehouse_llm_bridge.robotics_planning_core.validator.seams.Calibration` — the LANDED
  5-field artifact (`camera_id, map_frame, homography, reprojection_error, valid_polygon`),
  NOT redefined. (`CalibrationLoader` / `InMemoryCalibrationLoader` used in tests.)
- `warehouse_interfaces.locations.KNOWN_LOCATIONS` (the frozen location vocabulary; names only).

## adjudicated bridge-local decisions (recorded per docs-first)

1. **resolved-target kind KEY = `"resolution"`** (doc02:128 +
   docs/mode-x/08x-robotics-bridge-mode-x.md:280 = **2 independent sources**), NOT `"kind"`.
   - **docs-reconcile follow-up**: doc02:211 (and docs/mode-x/08x:370,538) spell the key
     `"kind"` inside the *Command Compiler*'s nested `resolved_target` example (a different,
     downstream object). A later docs PR should reconcile those `"kind"` spellings to use
     `resolution` (or document the two as the same field). This module uses `resolution`;
     XER5/Command Compiler must read `resolution`.
2. **Types**: `ResolvedTarget{target_id, resolution, destination, confidence, reason}`
   (doc02:126-131); `ResolutionResult` wraps `list[ResolvedTarget]` (the `resolve()` return,
   doc02:252).
3. **ALL thresholds + the confidence formula are INJECTED** via `VisualPolicy`
   (constructor param), never hardcoded in `resolve()` logic (doc02:98): `snap_radius_m`
   (doc02:150), `max_reprojection_error` (doc02:151), `compose_confidence` (doc02:159).
   Defaults are explicitly **illustrative** (発明), not frozen — a site overrides them per
   `VisualPolicy` instance, mirroring the Validator's `PlanPolicy` injected-threshold pattern
   (validator/policy.py:44-57).
4. **`unresolved` = the 0-dispatch path**: `{resolution:"unresolved", destination:None,
   reason in (off_map|outside_valid_polygon|beyond_snap_radius|reprojection_error_too_large|
   no_calibration)}`. An unresolved target **NEVER** yields a destination (doc02:151,68). The
   resolver only ever sets `destination` on the `known_location` branch; `_unresolved()` always
   sets `destination=None`. Test `test_every_unresolved_target_has_no_destination` pins it.

## consume gap — injected location coordinates (発明)

The frozen `KNOWN_LOCATIONS` carries only **names**, no coordinates (locations.py:11-23), but
snap-to-nearest needs map (x, y). The coordinates live in `config/warehouse.base.yaml`
`locations` — but **this lane must not read config** (scope) and inventing coordinates would
violate docs-first. **Resolution**: `VisualPolicy.location_coords` is an INJECTED
`name -> (x, y)` map supplied by the caller; the resolver validates every supplied name against
`KNOWN_LOCATIONS` so **no new location is invented** (doc06 §1:52), and silently ignores any
non-frozen name. XER5 (the wiring lane) will supply the real coordinate map from the config /
state source. Until then tests inject placeholder magnitudes.

## 前提・未確定 (TODO)

- `# TODO(XER5)`: wire `resolve()` into the L3 pipeline AFTER the Validator and supply the real
  `location_coords` + per-site `Calibration` + tuned `VisualPolicy` thresholds. This subpackage
  does NOT touch `pipeline.py`, does NOT compile a `Command`, and does NOT promote anything to
  `warehouse_interfaces`.
- `# TODO(docs)`: reconcile doc02:211 (and docs/mode-x/08x:370,538) `"kind"` vs the
  authoritative `"resolution"` key (decision 1 above) in a `docs/*` PR.
- `# TODO(object-class)`: doc02:150 snaps by "距離 *and* object class"; this MVP slice snaps by
  Euclidean distance ONLY and **defers** the object-class clause (the draft carries a proxy in
  `Detection.color`). XER5 / a follow-up should fold object-class into the snap gate.
- Geometry is stdlib-only (no OpenCV/NumPy): a hand-rolled 3x3 homography apply + ray-casting
  point-in-polygon. doc02:196 suggests NetworkX/OpenCV for production; kept dependency-free here
  so the offline unit core needs no native libs.

## テスト

`tests/unit/test_visual_resolver.py` — 14 offline pydantic tests (no ROS/Hermes). Run from the
worktree root: `python -m pytest tests/unit/test_visual_resolver.py -q`. Covers: red_box->shelf_1
& blue_box->shelf_2 via real homography + `InMemoryCalibrationLoader` + real `KNOWN_LOCATIONS`;
outside-valid-polygon; beyond-snap-radius; empty/degenerate homography -> no_calibration;
reprojection-error ceiling (both sides); SAME input + two snap radii flips known<->unresolved
(threshold-injection proof); confidence composition (injected combiner + default bound);
0-dispatch invariant (every unresolved target has `destination is None`).

## 設計ドキュメント

- docs/mode-x-er/02-l3-planning-core.md §2 Visual Resolver (:109-159, :126-132 output, :143
  ResolvedTarget, :149 calibration 5 fields, :150-151 snap & unresolved, :159 confidence
  composition, :251-252 resolve signature). **doc02:5 = all internal/illustrative.**
- docs/mode-x/08x-robotics-bridge-mode-x.md:280 (independent `resolution` key source;
  `"known_location|coordinate_goal|unresolved"`). doc02:150 also names an *object class* snap
  clause that this MVP slice defers (distance-only) — see resolver.py TODO.
- docs/mode-x-er/06-unfrozen-contract-resolutions.md §1:52 (reuse KNOWN_LOCATIONS, invent none).
- Landed seam: validator/seams.py (Calibration / CalibrationLoader / InMemoryCalibrationLoader).
