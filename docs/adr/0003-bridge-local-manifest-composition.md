# bridge-local run manifest + fail-closed plugin composition を A案（manifest resolution 層）で標準化する

**Status**: accepted（**決定**）／実装 = offline spike 済・稼働配線は **XER6 pending**（稼働 Bridge cycle への配線はまだ）。

Mode X-ER L3 の商用再利用（box / plugin を案件ごとに組み替えても Eval / Observability が
join / funnel / score を保つ）を、**bridge-local な `run_manifest.v1` ＋ startup fail-closed
composition preflight ＋ 実効構成レコード ＋ typed `validate_plan` hookspec（namespaced plugin
code ＋ downward-only policy clamp）** で実現し、これを **spike ではなく今すぐ標準**として建てる、
という決定。**`warehouse_interfaces` frozen contract は追加しない**（[09](../productization/09-run-manifest-and-plugin-composition.md):8）。

## Context / 背景

[09](../productization/09-run-manifest-and-plugin-composition.md) が run / plugin / site の
manifest 構成を提案していたが、状態は `proposal` に留まり、次の急所が未閉だった:

- pluggy の hook を **0 impl で呼ぶと空 list**＝「全 plugin approve」と観測上区別できず **fail-open**（急所1）。
- plugin 未 load が「全承認」と同一に見える＝motion dispatch 前に閉じるべき安全 gap。
- site profile（`safety.yaml` / `calibration.json`）の **self-cert hole**（内容の証明無しで通る・急所2）。
- plugin が frozen 9-code `ValidationCode` enum（[`report.py`](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/validator/report.py):69-88）を汚す危険、trust の強制可否、composition を「今 spike/後で標準」にするか等。

これらを Grill Q1〜Q7 ＋ 7 conditions ＋ 2 lead arbitration で設計確定した（先行 = [ADR-0002](0002-er-in-hermes-standard.md) 隣接、正本 = [09](../productization/09-run-manifest-and-plugin-composition.md) 全体）。

## Decision / 決定（要点）

1. **A案採用** = manifest resolution 層が `compile_raw_output` に構成を供給する（[`pipeline.py`](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/pipeline.py):89 は既に `resolver_policy` / `context` / `compiler` を param injection で受ける）。**B案（box-body DI container）は却下**。
2. manifest は **bridge-local pydantic**（`RunManifest`）で、`warehouse_interfaces` へ昇格しない（run artifact schema）。**unknown な `schema_version`（`run_manifest.proposal` を含む）= fail-closed reject**。
3. **startup fail-closed preflight**：`manifest.plugins == registered hookimpls`（集合等価・registered-superset は `allow_unlisted` opt-in 時のみ）でなければ起動拒否（`CompositionError`）。silent pass 経路を持たない。
4. **実効構成レコード**（recorded == ran witness）：構築済みオブジェクト（`type(obj)`）＋ merge 後 policy を `out/runs/<run_id>/`（gitignored）へ preflight 通過後に一度書く。宣言と実体が食い違えば起票拒否。
5. **plugin code は namespaced（9-enum 非改変）**・**Variant B**（`plugin_id` ＋ `reason_code` を別フィールド。`id:code` 連結文字列ではない）。
6. **downward-only clamp**：plugin は `dispatch_effect` を requested するだけ、policy が下方向のみ clamp（ceiling 既定 = `block`・`emergency_stop` は BASE `PlanPolicy` の allowlist のみ・fail-closed 変換と crash は clamp 免除）。site profile / run manifest は allowlist を **narrow（除去）のみ・追加不可**。
7. **trust = ADVISORY**：in-proc hookimpl は `object.__setattr__` で `frozen=True` を破れる＝**enforce 不可**。防御は明文 trust model ＋ review gate ＋ fail-closed。**真の強制は L2 / L1 / L0** に残す。
8. **granularity = ISOLATE_PLUGIN 既定**（crash plugin を blocking reject にし他は継続）。**ABORT_ALL** も選択可。両者とも 0-dispatch＝safety-equivalent。
9. **hooks = `validate_plan` のみ**。compiler / store / resolver の 1-of-N 差替は **param / registry injection**（hook にしない）。
10. **safety-critical profile（`safety.yaml` / `calibration.json`）は review + version + content-hash gate**（SHA-256・safety-critical の hash mismatch = FAIL・その他 = RECORD）。calibration self-cert hole を上流で閉じる。
11. **今すぐ標準として建てる**。`entry_points` 自動 discovery は **explicit-registry-first の後回し最適化**であり、composition 層を建てない理由にはしない。

## Why / なぜ

fail-open composition（plugin 未 load が全承認と観測上同一）は、motion dispatch の前に閉じねば
ならない安全 gap である。frozen 契約は安全側に**遅く**保ちつつ（safety-driven
frozen-contract-late・Core が産業非依存である原則は不変＝[03](../productization/03-l3-planning-core-box.md):235）、
商用の差替点だけを bridge-local に建てることで、9-enum 凍結を守りながら案件差を吸収できる。

## トレードオフ / Trade-offs

- **A案 vs B案**：A案は compile 経路に resolution 層を差すぶん entry point が増えるが、B案の
  box-body DI は box interface を侵し疎結合を壊す。
- **advisory trust**：enforce できないが、in-proc hookimpl では `object.__setattr__` により
  原理的に不可避。強制は下位層（L2/L1/L0）に置くのが誠実。
- **namespaced code**：string を二軸（`plugin_id` / `reason_code`）に分けるコストはあるが、
  frozen 9-enum の凍結を守れる。

## Considered Options / 却下・保留

- **B案（box-body DI container）**：box interface を侵襲するため却下。
- **plugin code を 9-enum に追加**：frozen 契約破壊のため却下（[`report.py`](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/validator/report.py):69-88 の `from_rules`〔:184〕は非改変）。
- **trust を enforce（frozen 強制）**：`object.__setattr__` で原理的に破れるため不可。
- **`entry_points` 自動 discovery を先行**：explicit-registry-first が先。

## Consequences / 帰結

- 稼働 Bridge cycle（`compile_raw_output` を呼ぶ経路）への配線は **XER6 pending**。
- root `.gitignore` の `out/runs/` entry は follow-up（現 `origin/main` に未在＝open flag）。
- `emergency_stop_allowlist` は project BASE `PlanPolicy`（Core ceiling）所有。site / manifest は narrow のみ。
- 本 ADR は [09](../productization/09-run-manifest-and-plugin-composition.md)（§run_manifest.v1 schema・§startup fail-closed composition preflight・§Trust model と fail-closed granularity・§実装順序）・[adr/README](README.md)・[docs/README](../README.md) §adr の索引行から back-link される。

## References（`origin/main` で検証済み file:line）

- 正本 doc: [09](../productization/09-run-manifest-and-plugin-composition.md) 全体（:8 frozen contract 非追加）/ [03](../productization/03-l3-planning-core-box.md):235（Core 産業非依存）・:264（製造 coordinate-goal を先に凍結＝別トラック）/ [04](../productization/04-box-storage-and-reuse-guidelines.md):83（Site Profile・:94 `calibration.json`）/ [05](../productization/05-decision-observability-and-tooling.md):55-58・:412（`box`/`stage`/`reason_code`＝:55-58、`plugin_id` additive＝:412）/ [10](../productization/10-llm-assisted-rule-authoring.md):394-396（reject vs `needs_clarification` fail-closed・`plugin_id` 区別）/ [mode-x-er/02](../mode-x-er/02-l3-planning-core.md):304（most-severe-wins lattice）
- frozen validator seam（非改変・検証側）: [`report.py`](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/validator/report.py):69-88（9-code `ValidationCode`）・:184（`from_rules`）・:100-105（`_STATUS_PRIORITY` lattice）/ 供給 seam: [`pipeline.py`](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/pipeline.py):89（`compile_raw_output` の resolver / compiler / context 注入点）
- 実装ステータス: composition 層（`manifest` / `loader` / `preflight` / `record` / `profile` / `calibration_gate` / `plugins` / `plugin_results`）は bridge-local offline spike で、**`origin/main` には未着地**（配線 = XER6 pending）。上記 frozen seam のみが現 `origin/main` の検証側実体。想定 vs 現状の追跡は [mode-x-er/07 §L3 composition 層](../mode-x-er/07-implementation-status.md)（matrix row）。
- 先行 ADR / ルール: [ADR-0002](0002-er-in-hermes-standard.md) / [ADR-FORMAT](../../.claude/skills/domain-modeling/ADR-FORMAT.md) / [docs-first.md](../../.claude/rules/docs-first.md)
