# ER/知覚入力に俯瞰カメラを使わない（ジェスチャは搭載 HP60C + ローカル骨格 NN で認識する）

**Status**: accepted（2026-08-09 ユーザー決定。撮影用俯瞰カメラの扱いのみ Open）

本フェーズでは **ER/知覚の画像入力に固定俯瞰カメラを使用しない**。視覚入力はロボット搭載 Nuwa HP60C（RGB+深度）に一本化し、実人間のジェスチャ2種（①腕を肩より上＝召喚 ②腕を前方に伸ばす＝指差し）を**ローカル骨格 NN（第1候補 MediaPipe Pose・Apache-2.0・CPU 推論）で決定論的に認識**して、既存の L3 Handoff → L2 Policy Gate 経路へ流す。ER は常時ポーリングに使わず、意味解釈が要る明示イベント時のみ呼ぶ。

## Context / 背景

- **前日（2026-08-07）の [mode-x-er/09](../mode-x-er/09-hand-raise-summon.md) 初版は俯瞰カメラ前提だった。** 固定俯瞰 + homography では実人間の手が幾何的に解決できない（手は盤面平面より上＝平面射影が不正・人の頭が画角に入らない）ため「召喚マーカー」という代理物を発明していた。ユーザーは実人間のジェスチャそのものを認識したい。
- **搭載 RGB-D + 3D 骨格なら初版の却下理由が消える。** 深度があるので平面射影に依存せず、前方視なら人の上半身が画角に入る。surprising な点: 俯瞰カメラを**外す**ことで実人間ジェスチャが**可能になる**（直感と逆）。
- **Visual Resolver の homography 契約は壊れない。** `homography: []` のままなら `NO_CALIBRATION` → 0 dispatch＝**実装コード無編集で fail-closed**（`visual_resolver/resolver.py` の既存挙動）。壊れるのは homography 適用の 1 関数だけで、valid polygon・snap・0-dispatch 不変条件は map 空間演算のため生存する。
- **ER 常時ポーリングの課金問題が構造的に解消する。** 初版の poll 5s＝12 calls/min は「standing 無承認 spend 禁止」（dev/07 課金 gate）と正面衝突していた。骨格 NN はローカル・課金ゼロ・15-30fps・決定論。「腕が肩より上→最寄り location」は世界知識不要の変換であり、ER（4.68s・非決定）を挟む理由が無い。
- **ライセンス調査済（2026-08-09 lane-gesture-tech）**: MediaPipe=Apache-2.0・公式 aarch64 wheel（2026-07 以降）・CPU 推論で 8GB GPU 予算（S1）を食わない。YOLO-pose は AGPL-3.0（収益化/productization と衝突）で fallback F2 に降格。SAM2 は骨格を出さず不採用。Isaac ROS 3.x に人体骨格パッケージは無い。

## Decision / 決定

1. **本フェーズ、ER/知覚の画像入力源は搭載 HP60C のみ**（`/bot1/camera/color/image_raw` 系）。固定俯瞰カメラ（C922n）を ER 入力・ジェスチャ検出に使わない。
2. **ジェスチャ認識は `gesture_detector`（新規ノード・L4 知覚・publish-only・0 actuation）+ ローカル骨格 NN** で行い、bridge-local 決定論変換で `to_robotics_plan_draft` 以降の**既存ゲートを 1 ステップも迂回しない**（設計正本: [mode-x-er/09](../mode-x-er/09-hand-raise-summon.md) INV-1/INV-2）。
3. **homography 系（俯瞰前提）の設計・実装・fixture は削除しない。** `homography: []` の fail-closed を本フェーズ既定とし、俯瞰復帰時の代替案として docs 内に降格保存する（09 §13）。`overhead_image_ref` フィールドは**改名しない**（実装 3 箇所波及。意味のみ「搭載カメラフレーム参照」へ再定義）。
4. **ER は意味解釈が要る明示イベントのみ**（指差し先の物体解釈 seam・到着後の音声指示）。常時ポーリング用途は禁止のまま。

## 得られるもの

- 実人間ジェスチャ（ユーザー要求）が成立し、召喚マーカーという代理物が不要になる。
- **手挙げ→発進 ≈1.3s**（初版 5-13s の約 1/5）・**課金ゼロ**・決定論（初版の Q8 poll 課金・Q10 R1 live 未実証リスクが構造的に消滅）。
- **Visual Resolver 実装は無編集**（`homography: []` fail-closed）。calibration artifact の配置・`calibration_id ≡ camera_id` 契約も無傷。**ただし Command Compiler には bridge-local の known-location passthrough が必須**（現行は `detection.id` キーの ResolutionResult 経由のみ compile＝location 名 target は skip。[09 §10 必須実装項目](../mode-x-er/09-hand-raise-summon.md)）＝「L3 全体が無編集」ではない。
- ロボット視点の骨格 overlay という強い絵（動画デモ）。

## トレードオフ / Trade-offs

- **全景 1 枚の視野を失う。** 俯瞰は常時全景・pose 非依存だった。搭載カメラは egocentric＝**sentry pose（idle 時に操作者側を向いて待つ）の運用**が必要になり、巡回中の召喚は取りこぼす（Phase 2 課題）。
- **投影が AMCL pose 品質と TF に従属する**（俯瞰 homography は pose 非依存だった）。pose_stale 時の挙動設計が要る。
- **カメラ取付角が nvblox（下向き）とジェスチャ（水平〜上向き）で競合する。** Phase 1 は水平固定で妥協（09 §3）。
- **`camera_link`（+光学 frame）の contract PR が前提条件になる**（[23 §4](../architecture/23-perception-and-localization.md) と共通）。「契約変更ゼロ」は初版から後退。
- **8GB 予算に骨格 NN が第 3 の競合者として加わる**（S1 測定順に +gesture NN を追加）。
- ER の視覚理解（object 認識）を使う検証項目（mode-x-er README の「俯瞰カメラ画像からの object target 認識」）は搭載カメラ画像に読み替えとなり、全景前提の task graph 例は成立条件が変わる。

## Considered Options / 却下

- **俯瞰カメラを維持し初版（召喚マーカー）のまま進める**: 却下。ユーザー要求（実人間のジェスチャ・俯瞰不使用）に反する。マーカーの物理仕様リスク（俯瞰 1px≈0.9mm で ER が判別できるか）も未解決だった。
- **俯瞰カメラで実人間の骨格を取る**: 却下。人の上半身が俯瞰画角に入らず、homography は盤面平面上の点にしか適用できない（初版 (B) 却下と同根）。
- **ER に搭載カメラ画像を渡してジェスチャを判定させる**: 却下（常時用途）。4.68s・課金・非決定的で、決定論で足りる判定に使う理由が無い。意味解釈 seam としてのみ残す。
- **ZED2i 等 Body Tracking 内蔵カメラの購入**: 却下（本フェーズ）。HP60C が既に確保済みで、MediaPipe 系で同等機能が Apache-2.0・追加ハード無しで見込める。NN 側が全滅した場合の retreat plan として残す。
- **YOLO-pose を第1候補にする**: 却下。AGPL-3.0 が公開リポジトリ+収益化+将来 productization と衝突。fallback F2（採用時はオペレーターの明示ライセンス判断が必須）。

## Open / 未決

- `# TODO(ユーザー判断)` **撮影用（動画）俯瞰カメラ C922n の扱い**。storyboard の主素材 70% が俯瞰映像前提であり、本 ADR の射程は「ER/知覚入力」のみ＝**撮影用は残す想定が自然**だが明示裁定が要る。残す場合「撮影用カメラを ER 入力に流用しない」の明文化も要る。
- `# TODO(実測)` 骨格 NN の Orin 8GB 実効性能（S4）・HP60C の depth-color alignment（S5）・キーポイント 3D 誤差 σ（09 OQ-7）。
- `# TODO(設計)` calibration artifact 5 field の `homography` の扱い（空維持か additive に intrinsics/camera_frame を足すか）＝所有トラック判断。

## References

- [mode-x-er/09-hand-raise-summon.md](../mode-x-er/09-hand-raise-summon.md) — ジェスチャ司令の設計正本（INV-1/INV-2・状態機械・誤差定量 G-3）
- [mode-x-er/02-l3-planning-core.md](../mode-x-er/02-l3-planning-core.md)（:149 calibration 5 field・homography/snap）/ [mode-x-er/06](../mode-x-er/06-unfrozen-contract-resolutions.md)（:127 coordinate goal DEFER）
- [architecture/23-perception-and-localization.md](../architecture/23-perception-and-localization.md)（camera_link contract PR・S1/S2・原則 P1/P2）
- [ADR-0008](0008-ros2-distro-humble-for-rosmaster-m1.md) / [ADR-0006](0006-single-bot-first.md)
- `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/visual_resolver/resolver.py`（空 homography → `NO_CALIBRATION` → 0 dispatch の fail-closed）
- lane-gesture-tech 調査（2026-08-09・MediaPipe aarch64 wheel / YOLO AGPL / SAM2 評価 / Isaac ROS 骨格不在。出典 URL は 09 References と同調査ログ）

---

**【2026-08-28 追補】ジェスチャは 2 種 → 3 種**: 本 ADR 後の 2026-08-19 ラウンド（[ADR-0010](0010-raise-speed-cap-to-platform-max.md) と同時）で**第3のジェスチャ＝③右手指カウント速度セレクタ**が [mode-x-er/09 追補④](../mode-x-er/09-hand-raise-summon.md)（③の正本。3 帯への改訂は 2026-08-21）に追加された（定義の正準: [mode-x-er/09 【2026-08-21 追補④】T-1 :352](../mode-x-er/09-hand-raise-summon.md)・知覚: [同 T-3 :372](../mode-x-er/09-hand-raise-summon.md)）。本文の「ジェスチャ2種」は決定当時（2026-08-09）の記述として保存する。③も本 ADR の決定どおり**搭載 HP60C + ローカル NN（MediaPipe Hand Landmarker 候補・①②と同一の L4 知覚・publish-only・0 actuation）**で認識し、**認識面は**本 ADR の決定 1〜4 と矛盾しない（Hand NN 同居の 8GB 影響は 09 T-3 の `# TODO(実測)` 扱い）。帯を走行系へ写す `speed_limit` 経路と決定 2 後段「既存ゲートを 1 ステップも迂回しない」の関係は **[ADR-0012](0012-speed-band-no-l2-best-effort.md) で裁定済（2026-08-30）**: `speed_limit` は plan draft を経ない control-plane 信号で INV-2 の文面射程外＝ゲート列は不変のまま、帯経路自体は **L2 非経由**の best-effort 制御面とする。
