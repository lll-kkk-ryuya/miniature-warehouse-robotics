# 速度帯は L2 非経由の best-effort 制御面とする（最小安全方針の適用・OQ-R1〜R7 一括裁定）

**Status**: accepted（2026-08-30 オペレーター決定。実装スライス 1+2 済＝`warehouse_perception` publisher node + R-26 unit + bringup 配線（決定 3 の①注入）+ `reset_period` 明示。帯 config 実値は未＝S-SPEED / OQ-T1 / OQ-T2 実測待ち）

ジェスチャ速度セレクタ（3帯）の runtime 経路（[mode-m1/04](../mode-m1/04-runtime-speed-limiter.md) の②）は、帯の引き上げ（安定段→最速段 = loosen）を含めて **L2 Policy Gate を通さない**。帯は「承認済み速度 envelope の内側で動く **best-effort の運用制御面**」であって安全機構ではない——hard な安全床は従来どおり **①起動基準値（launch が MPPI へ注入する解決値）＋凍結契約 `MAX_LINEAR_VELOCITY`＋L0' クランプ**が持ち、本決定はそれを一切変更しない。[mode-m1/04 §6](../mode-m1/04-runtime-speed-limiter.md) の OQ-R1〜R7 を一括裁定する。

## Context / 背景

- **最小安全方針（2026-08-30 オペレーター決定・docs 初出）**: 本フェーズの安全は「自機保護の最小線」のみとし、**一般人が触る際の安全プロセス（多層ガバナンス・緩和の承認フロー）は考慮しない**。操作者本人のみが扱うデモであり、攻撃的に技術限界を探る。[ADR-0010](0010-raise-speed-cap-to-platform-max.md):10 の「安全面は今回考慮しない・速度は出せる限り」（速度限定の記録）の一般化であり、本 ADR がその初の適用例。
- OQ-R1 の発端は [mode-m1/04:173](../mode-m1/04-runtime-speed-limiter.md)——「②はゲート列の外側から走行の物理量に影響する新種」「loosen は [ADR-0004](0004-l2-restrict-only-policy-profile.md) restrict-only と逆向き」の 2 論点。
- **敵対的検証 3 レーン（2026-08-30・Nav2 humble 実ソース `3c3db59` ＋リポジトリ整合）の確定事実**:
  1. **Nav2 / Isaac ROS エコシステムに「速度制限の緩和を検証・承認・拒否する機構」は存在しない**。humble 全 1733 パスで policy / authoriz / arbitr / mux は 0 ヒット。`speedLimitCallback` は受信値を無検査で全 controller に転送するだけ（last-writer-wins・優先度なし・上限なし）で、upstream 自身が無検査の緩和 publisher（`nav2_route` AdjustSpeedLimit・既定 100%）を標準搭載している。「緩和に承認を要求する」という概念自体がエコシステムに無い。
  2. **L2 Policy Gate は velocity path 上に居ない**（[productization/11:214](../productization/11-l2-contract-governance-traffic-box.md) L2-G8「出口は position goal のみ」・[productization/01:88](../productization/01-commercial-box-map.md) 原則 5）。通すには tool 追加（[productization/11:279](../productization/11-l2-contract-governance-traffic-box.md) 禁止 6 項②「motion tool の追加」の趣旨に触れる）と register しない第 2 の validate 入口の新設が要り、周期送出（決定 5）が `RATE_LIMIT_S = 0.5`（`policy_gate.py:134`）と衝突し、`validate_and_register_dispatch` の流用は `active_tasks` 帳簿（`policy_gate.py:400-409`）を上書き破壊して召喚 dispatch を巻き添えにする。**L2 を通す案は安全の上乗せではなく既存 L2 の破壊**。
  3. **Humble MPPI（1.1.20）では帯値は厳密上界にならない**: 帯が黙って消える経路が複数現存（`reset_period` 無活動 1.0s／`fallback()` は同一サイクル内で base 制約の指令を返す／任意の dynamic param set が post-callback `reset()` で帯を全解除。upstream 修正 [PR #5165](https://github.com/ros-navigation/navigation2/pull/5165)・[#5768](https://github.com/ros-navigation/navigation2/pull/5768) は main のみ・[#4545](https://github.com/ros-navigation/navigation2/issues/4545) は wontfix）。さらに Savitzky-Golay 平滑がクリップ**後**に制限前の履歴を混ぜるため、帯遷移直後 ~4 制御サイクルは帯値を約 2 割超過しうる（手計算値）。**帯を安全機構として扱えないことは Nav2 側の実装事実**であり、「帯 = best-effort・安全床は別層」という位置づけはこの事実の追認でもある。

## Decision / 決定

1. **OQ-R1: 帯 publish 経路は L2 Policy Gate を通さない**（引き下げ・引き上げとも）。帯は L4 由来の control-plane 選好であり、緩和瞬間の live state（stale / battery / emergency）検査は行わない（§Trade-offs で受容）。
2. **INV-2（[mode-x-er/09:57](../mode-x-er/09-hand-raise-summon.md)）は改訂しない＝射程外を確定する**。`speed_limit` は `Command` を生まず plan draft を経ない＝「`to_robotics_plan_draft` 以降」という文面射程の外。[09:424](../mode-x-er/09-hand-raise-summon.md) T-8 の「INV-2 生存」判定を確定として維持する。[mode-m1/04:173](../mode-m1/04-runtime-speed-limiter.md) の 2 論点への回答: (a) ゲート列の外側からの物理量影響は、その影響が承認済み envelope の内側に限定されること（決定 3・4）をもって許容する。(b) restrict-only との向きの逆転は [ADR-0004](0004-l2-restrict-only-policy-profile.md) の射程（L2 の data-only profile）の外であり、同 ADR が引く前例（`safety.py:11-12` config may lower never raise／[productization/09:356](../productization/09-run-manifest-and-plugin-composition.md) 起動時 fail-closed）を **L2 外の publisher に独立に適用**する（ADR-0004 の適用拡大ではない）。
3. **publisher クランプは `min(帯値, ①, MAX_LINEAR_VELOCITY)`**。①は launch が MPPI `FollowPath.vx_max` に注入するのと**同一の解決値**（`nav2_bringup.launch.py` の LOWER-only CLI override 込み）を、同一 launch から publisher の param として渡す（真実の源を 2 つにしない）。これにより `max_linear_velocity:=0.1` のような明示縮退構成でも帯が①を超えない（検証 REF-1 の封じ込め。config 運用値だけをクランプ天井にする当初案は launch override 構成で破れるため改訂）。
4. **帯値テーブルは config 注入＋起動時 fail-closed 検証**: 全帯値が有限・`V_FLOOR`（>0・例示 0.05 m/s）以上・①以下・帯間単調（最遅 ≤ 安定 ≤ 最速）。違反は起動失敗。`0.0` は Nav2 の「制限なし」センチネル（`NO_SPEED_LIMIT`）ゆえ**計算経路に流さない**（解除は型で区別し、値としての 0.0 を生成しない）。
5. **送出は 20Hz 周期＋帯遷移時即時**。`Optimizer::setSpeedLimit` は O(1)・状態非破壊（検証済）で送出コストは無視できる。消失 3 経路（Context 3）と SG 過渡の窓を最大 ~50ms（1 制御サイクル）へ短縮する。未検出時は安定段を**能動 publish**（[09 T-5 :392-397](../mode-x-er/09-hand-raise-summon.md) と同じ既定）。`reset_period` は `nav2_params.yaml` で明示設定する。
6. **OQ-R2（条件付き裁定）**: publisher は `gesture_detector` と同パッケージの**別ノード**。パッケージ名は [09 OQ-13 :193](../mode-x-er/09-hand-raise-summon.md) に従属（本 ADR で新 package を発明しない＝[09:376](../mode-x-er/09-hand-raise-summon.md) の宣言を維持）。[productization/01:180-188](../productization/01-commercial-box-map.md) 対応表への行追記は**実装 PR の DoD**（[layer-annotation.md](../../.claude/rules/layer-annotation.md)「実装したら同じ PR で追記」）。
7. **OQ-R3: R-26 unit は 6 本**（`tests/unit/`・独立オラクル＋mutation・[safety.md:7](../../.claude/rules/safety.md)）: ①クランプ必経（`min(帯値, ①, MAX_LINEAR_VELOCITY)`）②`0.0`・非有限・負・`V_FLOOR` 未満を publish しない ③`cmd_vel` 系トピックへ publish しない（velocity producer 化しない）④テーブル起動時検証（単調・有界）が違反を落とす ⑤①超え非送出（launch override 縮退構成の oracle）⑥除算安全（① > 0 の oracle）。
8. **OQ-R5: `percentage = false`（絶対 m/s）**。単位が契約・config・S-SPEED と揃う。[09 OQ-T2 :435](../mode-x-er/09-hand-raise-summon.md)（帯の具体値/倍率・遷移時間窓）は独立に未決のまま。
9. **OQ-R6: recovery 区間の帯無効を許容**。`nav2_core::Behavior` IF に `setSpeedLimit` が無い＝構造的不達（ソース確定済）。recovery 中は①まで出るが、対策（recovery param 連動）は足さない。なお床は **L0' = 凍結契約値**であって帯でも①でもない。
10. **OQ-R7: wz（角速度）同率スケールを許容**。`setSpeedLimit` は vx_min / vy / wz を同 ratio で縮める実装であり「直進のみ減速して旋回を維持」は SpeedLimit 機構では原理的に不可能。デモ挙動として受容し、気になれば帯値側で調整する。
11. **単一 publisher 規律**: `/bot{n}/speed_limit` の publisher は本ノード 1 本に限定する（Nav2 は last-writer-wins で調停しないため）。costmap `filters` に SpeedFilter を入れない・`nav2_route` AdjustSpeedLimit を使わない（実装スライスで起動時アサート）。トピックは相対名 `speed_limit` を維持し、絶対名 `/speed_limit` を注入しない（namespace 迂回＝多台時の全機混線）。

## 得られるもの

- **実装 = 小ノード 1 個（~100 行）＋ config 数キー＋ R-26 unit 6 本**。L2 / L3 / 凍結契約 / Nav2 fork すべて無編集。
- コントローラ差し替え耐性: `setSpeedLimit` は `nav2_core::Controller` の純粋仮想＝全公式 controller に実装義務。MPPI→DWB 退避が起きても②の設計は生存する。
- ジェスチャ→帯反映のレイテンシに L2 往復・承認フローが乗らない。

## トレードオフ / Trade-offs（残余リスク・隠さない）

- **緩和瞬間の live state 検査を失う**。正しく検出された「危険なタイミングでの loosen」（接近中・stale 中の最速段遷移）は L4 の誤検出対策（3帯吸収・4本の安定段帰属・時間窓多数決・standby ゲート）では止まらない。最小安全方針に基づく意図的な受容。
- **帯値は cmd_vel の厳密上界ではない**: (a) SG 平滑により帯遷移直後 ~4 サイクルは旧帯側へ約 2 割の過渡（手計算値・実測回帰は実装 DoD）。(b) `fallback()`（全軌道衝突時＝減速が最も要る場面）は同一サイクルで base 制約の指令を返し、周期送出では原理的に防げない。(c) reset 系の窓は 20Hz 送出で最大 ~50ms。**いずれも ① ≤ 凍結契約値と L0' に包絡され、安全床は破れない**——破れるのは「帯」という運用上の約束だけ。
- **ConstraintCritic は帯を見ない**（initialize 時の param キャッシュ）。帯値を①から下げるほど MPPI の軌道評価と実行速度の乖離が増える（保守側の誤差だが経路追従の質は劣化）。帯値の下限目安は実装時に実測で決める。
- Humble MPPI の `setSpeedLimit` は無ロック（float 4 本・1 サイクルの新旧混在があり得る）を許容する（upstream 自身が同じ race を持つ）。
- 走行中の `FollowPath.*` への `ros2 param set` は帯を全解除する（Humble 固有・[#5790](https://github.com/ros-navigation/navigation2/issues/5790)）。運用で禁止し、20Hz 送出の 50ms 自己修復で受容する。

## Considered Options / 却下

- **L2 Policy Gate 経由（loosen のみ or 全遷移）**: 却下。Context 2 の構造的破綻に加え、得られる live state 検査は最小安全方針の下で費用対効果が立たない。
- **Nav2 fork へ #5165＋#5768 を overlay patch**: 却下（Phase 1）。消失経路を根治するが、upstream fork の保守コストが小ノード 1 個の周期送出より重い。**実測回帰で過渡が許容不能と出たら再訪**（診断材料は §Open の実測回帰）。
- **`ros2 param set vx_max` 直叩き**: 却下。無認可経路であり、かつ任意 param set が `reset()` を誘発して帯を全解除する Humble 固有バグの当事者になる。
- **costmap SpeedFilter**: 却下。空間（mask セル）トリガのみで状態トリガ（ジェスチャ帯）を表現できず、edge-triggered publish が外部 publisher と併存不能（upstream 自身が「同時有効化するな」と明記）。
- **collision_monitor slowdown / limit**: 却下。Humble に `limit` アクションは無く（Iron 以降）、slowdown は空間×点群トリガの相対縮小で帯の意味論と合わない。

## Open / 未決

- OQ-T1（最速段実値＝S-SPEED 実測待ち）／OQ-T2（帯遷移の時間窓・方向依存ヒステリシス）——本 ADR は先取りしない。OQ-13（package 名）は実装スライス 1 で **`warehouse_perception`** に裁定済（2026-08-30・[09:193](../mode-x-er/09-hand-raise-summon.md)）。
- `V_FLOOR` の具体値（例示 0.05 m/s・「実際に動く最遅速度」を実測で確定）。
- **帯遷移過渡の実測回帰**（`/bot{n}/cmd_vel` 記録で帯遷移直後 5 サイクルの超過率）——実装スライスの DoD に含める。
- `reset_period` の明示設定値（既定 1.0s のままか延長するか）。

## References

- [mode-m1/04-runtime-speed-limiter.md](../mode-m1/04-runtime-speed-limiter.md) — 設計解の正本（§6 OQ-R1〜R7 は本 ADR で裁定・同一ラウンドで行内更新済）
- [mode-x-er/09-hand-raise-summon.md](../mode-x-er/09-hand-raise-summon.md) — 帯の意味論（T-1〜T-8・INV-2 :57・T-8 :424・OQ-T2 :435・OQ-T3 :436・OQ-13 :193）
- [ADR-0004](0004-l2-restrict-only-policy-profile.md)（restrict-only の射程＝L2 data-only profile）／[ADR-0010](0010-raise-speed-cap-to-platform-max.md)（速度値の正本・§Decision 5 の三段再導出は contract PR の DoD に紐づく義務であり、承認済み envelope 内の帯遷移には及ばない。[09:436](../mode-x-er/09-hand-raise-summon.md) が求める安全レビュー相当は本 ADR §Trade-offs で消化）
- [mode-m1/02-m1-driver-and-watchdog.md](../mode-m1/02-m1-driver-and-watchdog.md)（L0' 結線済＝#550）／[.claude/rules/layer-annotation.md](../../.claude/rules/layer-annotation.md)
- [docs/GLOSSARY.md §11](../GLOSSARY.md) — **最小安全方針**（本 ADR §Context が docs 初出）と **runtime speed limit** の正準エントリ（双方向）
- **検証一次情報（参照日 2026-08-30・humble ブランチ HEAD `3c3db59`）**: `nav2_controller/src/controller_server.cpp`（`speedLimitCallback` 無検査 fan-out・QoS(10)）／`nav2_mppi_controller/src/optimizer.cpp`（`reset()`・`setSpeedLimit()`・`evalControl` の SG 平滑がクリップ後）／`nav2_mppi_controller/src/parameters_handler.cpp`（無条件 `successful = true`）／`nav2_mppi_controller/src/critics/constraint_critic.cpp`（param キャッシュ＝帯非参照）／`nav2_route/src/plugins/route_operations/adjust_speed_limit.cpp`（既定 100% 無検査 publish）／Issue [#5790](https://github.com/ros-navigation/navigation2/issues/5790)・[#4545](https://github.com/ros-navigation/navigation2/issues/4545)（wontfix）／PR [#5832](https://github.com/ros-navigation/navigation2/pull/5832)・[#5165](https://github.com/ros-navigation/navigation2/pull/5165)・[#5768](https://github.com/ros-navigation/navigation2/pull/5768)（いずれも main のみ・Humble 未 backport）
