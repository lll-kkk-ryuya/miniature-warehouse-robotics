# robotics/composition — productization composition artifacts (spike S-lanes)

Typed, verifiable forms of the documented composition artifacts (site profile bundle / run
manifest / effective-composition record — docs/productization/04:83-136, 09:42-181).
**bridge-local**: proposal-status docs (doc04:5 / doc09:5-8) — nothing here is promoted to
`warehouse_interfaces`, no new config key / ROS topic. Parallel S-lanes each own their modules;
**append a new section per lane, do not rewrite others'** (`__init__.py` re-exports nothing so
lanes do not collide on it — import submodules directly).

## S3 — site profile identity/hash + calibration governance (`profile.py`, `calibration_gate.py`)

- **担当**: S3 spike lane（site-profile resolver + version/content-hash + calibration governance）
- **編集境界**: `profile.py` / `calibration_gate.py` / この節 / `tests/unit/test_site_profile_hashing.py` / `tests/unit/test_calibration_governance.py`

### 提供 (produce)
- `profile.load_site_profile(base_dir, customer, site) -> SiteProfile` — doc04:87-103 bundle
  loader（`APPROVED.yaml` は hash 対象外・version は任意 `profile.yaml` の `version` キー=提案）。
- `profile.compute_content_hash(profile, merge_order=None) -> ProfileContentHash` — **二方式併記**
  (Q2): `merged_canonical`（deep-merge 後の canonical JSON の SHA-256＝実効合成の意味 pin）+
  `files`（artifact 生バイト単位 SHA-256＝帰属付き byte pin）+ `merge_order` 記録。
- `profile.verify_against_approved(profile, hash, approved) -> ProfileVerification` — fail-closed
  （unapproved / identity_mismatch / mismatch。`SAFETY_CRITICAL_ARTIFACTS`=safety.yaml+
  calibration.json は `safety_critical_mismatch` フラグ）。`assert_verified()` は raise ゲート。
- `profile.approve(...) -> ApprovedProfileRecord` / `load_approved_record(...)` — review pin
  （書き込みは人間 review 行為、runtime は読むだけ。保管先 `APPROVED.yaml`=提案）。
- `profile.composition_record(...) -> dict` — S2 `out/runs/<run_id>/effective_composition.json`
  へ埋め込む `site_profile`（+任意 `calibration_governance`）block の shape 提案（JSON-safe）。
- `calibration_gate.build_calibration_loader(profile, *, policy=None) -> GovernedCalibrationLoader`
  — `validator/seams.py:39` の宣言のみだった `CalibrationLoader` seam を production 経路に配線
  （`adapter_factory.build_er_adapter` と同型: profile in → constructed seam out・純・注入可）。
  **profile gate**: `reprojection_error` が None/非有限/閾値超/閾値未設定は reject（fail-closed）、
  明示 waiver（who/why/when 記録）だけが例外 → resolver.py:172 の self-cert スキップ経路を上流で
  遮断（resolver 本体は不変更）。`report().as_composition_block()` が governance block を出す。

### 消費 (consume)
- `robotics_planning_core.validator.seams.Calibration/CalibrationLoader/InMemoryCalibrationLoader`
  （landed seam・再定義しない）/ `robotics_planning_core.models.base._BridgeModel`（L4→L3 一方向）。
- stdlib + `yaml`（pyproject 既存依存）+ pydantic。config / ROS / network は読まない。

### 前提・未確定 (TODO)
- `profile.yaml`(version) / `APPROVED.yaml`(review pin) / safety.yaml `calibration:` key path /
  calibration.json 内部 shape は **docs 未定義＝この spike の設計提案**（docs lane に要 doc PR）。
- S2 preflight との統合（verify 結果で FAIL/RECORD、run manifest への `profile_ref` additive）は
  interface 提案どまり＝本レーンでは配線しない。
