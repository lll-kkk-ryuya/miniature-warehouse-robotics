# robotics/composition — productization composition seam (spike S-lanes)

Typed, verifiable forms of the documented composition artifacts (run manifest / site profile
bundle / effective-composition record / plugin composition — docs/productization/04:83-136,
09:42-298). **bridge-local**: proposal-status docs (doc04:5 / doc09:5-8) — nothing here is
promoted to `warehouse_interfaces`, no new config key / ROS topic. Parallel S-lanes each own
their modules; **append a new section per lane, do not rewrite others'**. `__init__.py`
re-exports the S2 (manifest/loader/preflight/record) + S4 (plugin_results/plugins) symbols as a
union; S3's `profile` / `calibration_gate` are imported directly (not re-exported).

## S2 — run manifest + fail-closed preflight + effective record (`manifest.py` / `loader.py` / `preflight.py` / `record.py` / `fixtures.py`)

### 提供 (produce)
- `manifest.RunManifest` / `load_run_manifest` — bridge-local `run_manifest.v1` pydantic
  (unknown schema_version incl `proposal` = fail-closed reject・extra=forbid・run_id は
  path-traversal-safe token)。
- `preflight.preflight_composition(manifest, registry)` — **declared == registered hookimpls**
  でなければ `CompositionError`（registered-superset は `allow_unlisted` opt-in のみ）。plugin
  不在が「全 approve」と観測的に同一になる fail-open を閉じる。
- `record.build_effective_composition` / `write_run_artifacts` — `out/runs/<run_id>/` に構築済み
  object（`type()`）＋ merge 後 policy から `effective_composition.v1` witness を起票（recorded==ran・
  mismatch は `CompositionError`・gitignored）。**任意 S3 governance ブロック（doc09:145-151）**:
  `EffectiveComposition` は `site_profile` / `calibration_governance`（JSON-safe mapping・shape は
  S3 所有 = `profile.composition_record(...)["site_profile"]` / `report().as_composition_block()`）
  を **`effective_composition.v1` 配下の nested block** として運ぶ optional slot を予約（`extra='forbid'`
  維持・S3 の別 `effective_composition.site_profile.s3-proposal` top-level marker は競合 schema_version
  として持ち込まない）。既定 `None` は書出時に elide＝S3 未配線 run は従来と byte 一致（#409 residual Lane C）。
### 消費 (consume)
- stdlib + pydantic + `yaml`。frozen 契約無編集（doc09:8）。config/ROS/network を読まない。

## S3 — site profile identity/hash + calibration governance (`profile.py`, `calibration_gate.py`)

- **担当**: S3 spike lane（site-profile resolver + version/content-hash + calibration governance）
- **編集境界**: `profile.py` / `calibration_gate.py` / この節 / `tests/unit/test_site_profile_hashing.py` / `tests/unit/test_calibration_governance.py`

### 提供 (produce)
- `profile.load_site_profile(base_dir, customer, site) -> SiteProfile` — doc04:87-103 bundle
  loader（`APPROVED.yaml` は hash 対象外・version は任意 `profile.yaml` の `version` キー=提案）。
- `profile.compute_content_hash(profile, merge_order=None) -> ProfileContentHash` — **二方式併記**
  (Q2): `merged_canonical`（deep-merge 後の canonical JSON の SHA-256）+ `files`（artifact 生バイト
  単位 SHA-256）+ `merge_order` 記録。
- `profile.verify_against_approved(...) -> ProfileVerification` — fail-closed（`SAFETY_CRITICAL_ARTIFACTS`=
  safety.yaml+calibration.json は `safety_critical_mismatch` フラグ）。`assert_verified()` は raise ゲート。
- `calibration_gate.build_calibration_loader(profile, *, policy=None) -> GovernedCalibrationLoader`
  — `validator/seams.py:39` の宣言のみだった `CalibrationLoader` seam を（**XER6-pending**で）
  production 経路へ配線する提案。`reprojection_error` None/非有限/閾値超/閾値未設定は reject、
  明示 waiver だけが例外 → resolver.py:172 self-cert スキップを上流で遮断（resolver 不変更）。
### 消費 (consume)
- `robotics_planning_core.validator.seams.Calibration/CalibrationLoader`（landed seam・再定義しない）/
  `models.base._BridgeModel`（L4→L3 一方向）。stdlib + `yaml` + pydantic。
### 前提・未確定 (TODO)
- `profile.yaml`(version) / `APPROVED.yaml` / safety.yaml `calibration:` key / calibration.json
  内部 shape は **docs 未定義＝設計提案**（docs PR #403 で pin 済）。production 配線は XER6-pending。

## S4 — L3 plugin composition (pluggy + typed validate_plan) (`plugins.py`, `plugin_results.py`)

- **担当**: productization spike S4（pluggy PluginManager / typed hookspec / namespaced plugin
  codes / trust & fail-closed granularity）。**未凍結・bridge-local**。凍結 Validator 語彙
  (`robotics_planning_core/validator/report.py:69-88`) は一切編集しない（兄弟実装で成立）。
- **編集境界**: この `composition/` サブツリーと `tests/unit/test_plugin_composition.py` のみ。

### 提供 (produce)
- `PluginComposition`（pluggy wrap）: `register` / `run_validate_plan` / `registered_plugin_ids()` /
  `missing_declared()` / `preflight()`。typed hookspec `validate_plan(plan, context)`（引数名 literal・canary）。
- `StructuredPluginRuleResult`（**変種B 推奨**: `plugin_id`+`reason_code` 分離）/ `NamespacedPluginRuleResult`（変種A）。
- `PluginDispatchPolicy` + `clamp_finding`（effect は下方向 clamp・`emergency_stop` は allowlist のみ・
  `clamped_from` 記録・fail-closed/crash は clamp 対象外）。`PluginCodeRegistry.from_manifest_dicts`
  （plugin manifest `emits.reason_codes` 由来＝S2 との統合点）。
- `ComposedValidationReport` + `compose_report`（凍結 `from_rules` 無編集の兄弟集約・most-severe-wins）。
### 消費 (consume)
- L3 `robotics_planning_core.validator`（`report.py` 凍結語彙・`_STATUS_PRIORITY` を read-only import）。
- pip: `pluggy>=1,<2`（**setup.py install_requires に宣言済み**・hard import）。
### 設計判断（docs 接地）
- plugin code は凍結 9 code と構造的に非交差（小文字+`:` vs 大文字コロン無し・doc09:204 / report.py:79-87）。
- trust model: in-proc enforce は幻想（test 実証）→ advisory（manifest 自己申告 + human review +
  registry preflight + 例外 fail-closed）。emergency_stop_allowlist の base-policy 化・preflight `==`
  整合は residual slice（docs PR #403 pin）。

## テスト
- `tests/unit/test_{run_manifest,composition_preflight,composition_record,mode_a_composition_fixture,
  site_profile_hashing,calibration_governance,plugin_composition}.py`（host-runnable・py3.12・
  ROS/network 不要・安全不変条件は `@pytest.mark.safety`）。
