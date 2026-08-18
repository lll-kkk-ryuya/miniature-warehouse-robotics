# ジェスチャ司令（召喚・指差し）— 搭載カメラ + ローカル骨格 NN で「呼ぶ・指す」を実現する

作成日: 2026-08-07 ／ **全面改訂: 2026-08-09**（[ADR-0007](../adr/0007-no-overhead-camera-gesture-via-onboard-nn.md)）
Status: **設計提案（未凍結）**。`warehouse_interfaces` は Phase 1 で不触（例外は `camera_link` の contract PR ＝ [23 §4](../architecture/23-perception-and-localization.md) と共通）。
スコープ: 単騎構成（[ADR-0006](../adr/0006-single-bot-first.md)・ROSMASTER M1 + Orin Nano Super 8GB / Humble）・X-lite profile のみ。

> **改訂の経緯**: 初版（2026-08-07）は「固定俯瞰カメラ + 召喚マーカー + ER 画像推論」方式だった。2026-08-09 のユーザー決定（①俯瞰カメラ不使用 ②実人間のジェスチャ2種を認識 ③検出は搭載 HP60C + NN 骨格認識）により全面改訂。旧方式は §13 に**俯瞰カメラ復帰時の代替案として降格保存**する。初版が「(B) 俯瞰に映る実人間の手」を却下した理由（手は盤面平面より上＝homography 平面射影が不正）は**俯瞰 homography に固有の制約**であり、搭載 RGB-D + 3D 骨格では発生しない——この却下理由の消滅こそが今回の方式転換の技術的正当化である。

## 0. 前提（前提であることを明示する）

| # | 前提 | 状態 |
|---|---|---|
| P1 | 搭載 HP60C（RGB+深度）から上半身骨格（肩/肘/手首）が 2D+深度で ~15-30fps 取れる | **未検証の作業前提**。NN 第1候補 = **MediaPipe Pose Landmarker**（Apache-2.0・CPU 推論・公式 aarch64 wheel あり・world landmarks 3D）。fallback F1=RTMPose（Apache-2.0/TensorRT）、F2=YOLO-pose（**AGPL-3.0＝オペレーター判断必須**）。SAM2 は骨格を出さないため不採用、Isaac ROS 3.x に人体骨格パッケージは無い（lane-gesture-tech 調査・2026-08-09） |
| P2 | `bot1/camera_link`（＋光学 frame）TF | **camera_link は contract PR で landed**（`robot_dimensions.py:41` の `FROZEN_LINK_NAMES`。名前のみ・URDF は実測待ち）。**光学 frame 名は未凍結**＝OQ-4。[23 §4](../architecture/23-perception-and-localization.md) |
| P3 | HP60C の FOV / 解像度 / fps / min range / depth-color alignment | **未裏取り**（公称 73.8°/0.2-4m のみ。[23 §7 S2](../architecture/23-perception-and-localization.md)） |
| P4 | 単騎構成 bot1 のみ | 確定（ADR-0006） |
| P5 | `config/warehouse.base.yaml:47-56` の 9 location 座標 | **暫定値**・ジオラマ再設計待ち（[04](../shared/04-diorama-layout.md)） |

**発明しないもの**: `/goal_pose` 直注入・standoff distance 独立パラメータ・新 location キー（Phase 1）・safety 閾値の緩和・角速度上限の新設。

## 1. なぜ /goal_pose 直注入をしないか（初版から無改訂で継承・むしろ強化）

1. [README:16](README.md)「ER は Nav2 / ROS / `/cmd_vel` を直接叩かない」 2. [01:112](01-architecture-and-flow.md) L4 非所有 3. [08-llm-bridge-common:464](../architecture/08-llm-bridge-common.md) 4. **機械強制**: `handoff.py:66-90` の `_FORBIDDEN_KEY_RULES`（`goal`/`coordinate`/`waypoint`/`topic`/`cmd_vel` 系キーを draft 構築前に fail-closed reject）＋ `schemas.py:159` の KNOWN_LOCATIONS validator。

> **INV-1（本書の中核不変条件）**: 幾何（3D キーポイント・レイ・交点座標）は **plan draft の外**を通る。draft に載るのは `detections[].id`（free str）と `TaskNode.target = <known location 名>` だけ。座標は監査用 `evidence`（§10）に置き、draft には一度も入れない。これで `handoff.py` は無改訂のまま Phase 2（coordinate goal 解凍）でも触らずに済む。

## 2. 2 つのジェスチャの定義

| | ①召喚（summon） | ②指差し（pointing） |
|---|---|---|
| 人の動作 | 片腕を**肩より上**に挙げて保持 | 腕を**前方に伸ばして対象を指し、保持**（伸ばす動作＝トリガ edge） |
| 意味 | 「私のところへ来て」 | 「あそこ／あの人のところへ行って」 |
| 幾何要件 | 手首 z > 肩 z ＋ 人物の map 位置 | 肩→手首 3D レイ × 盤面平面の交点 or 他人物との角度近接 |
| 深度依存 | **低**（2D の y 比較でも成立＝RGB のみで先行実装可） | **高**（3D ベクトル必須） |
| Phase 1 到達点 | 人物位置に最も近い known location | 交点に最も近い known location（margin test 付き） |

同一骨格ストリームから**排他的に**判定する（同時成立は clarification。§7）。「なぞる」は**入力の作法（トリガ edge + 保持）であって解釈対象ではない**——軌跡を目標に写像する意味論は発明しない。

## 3. 幾何の成立性

- **画角**: M1 車体上面 74.58mm にカメラ ≈ 板面上 0.09-0.13m。人（盤外・板面上の肩高 ≈0.6-0.75m）は**距離 1.2-1.5m なら肩・肘・手首が入るが 0.6m では切れる**（概算・S2 実測で確定）。近すぎ＝無検出（fail-closed）で安全側。立ち位置は床マーキングの運用規律で拘束する。
- **G-1**: 「肩より上」判定に**頭は要らない**（参考実装の「頭より上」と違い、頭が画角外でも成立）。ユーザー指定の肩基準は画角制約と整合しており、明示的に維持する。
- **チルト競合**: nvblox は下向き寄り・ジェスチャは水平〜上向き寄りを望む。**Phase 1 は水平固定**（nvblox は S1/S2 未通過の TARGET であり今は最適化の根拠が無い。パン/チルト雲台は `camera_link` が動的 TF になり [23 §5-2](../architecture/23-perception-and-localization.md) の static 契約を破るため不採用。2台目カメラは S1 結果次第で defer）。
- **sentry pose（監視姿勢）**: 搭載カメラは egocentric なので、idle 時は既定 known location に停まり**操作者側長辺を向いて待つ**（Phase 1）。巡回中の常時検出は Phase 2。その場スキャン回転は不採用（動体ブラー・絵が悪い・角速度上限の発明が要る）。sentry の条件: 9 キー内・操作者側への視線・**召喚到達先になりにくいこと**（同一だと `duplicate_destination` reject で「呼んだのに動かない」）。暫定座標では `shelf_2`(0.7,0.57・棚前 docking 点＝doc04 §走行目標点) が第一候補（ジオラマ再設計後に再選定）。
- **Phase 1 の絵**: 召喚の近端到達先は暫定座標で `shipping_station`(0.45, 0.12) / `charging_station`(1.5, 0.12) の 2 点（2026-08-17 改訂＝doc04 §走行目標点）＝「**左に立てば左へ、右に立てば右へ**」が契約変更ゼロで成立。指差しは 9 点全部が対象。

## 4. 層配置 — ER をバイパスする決定論ローカル経路

| 案 | 経路 | 判定 |
|---|---|---|
| (i) ErTaskRequest→ER→L3 | ER median 4.68s + 課金 + 非決定（旧版の poll 課金問題 Q8 をそのまま継承） | 不採用 |
| (ii) ER に gesture event+画像を渡し解釈 | 同上 | Phase 1 不採用・§4-3 の seam として残す |
| **(iii) bridge-local 決定論変換で ER バイパス** | gesture→**同一の L3 Handoff**→L3→L2→Nav2 | **採用**（サブ秒・課金ゼロ・決定論） |

**(iii) は Mode X-ER の枠組みから外れない**: `robotics_planning_core` は provider 非依存で、L3 は「誰が RawModelOutput を作ったか」を問わない。決定論ローカル producer は**もう一つの provider**にすぎない。

> **INV-2**: gesture 経路は ER 呼び出しだけを省略し、**`to_robotics_plan_draft` 以降は 1 ステップも迂回しない**（handoff 禁止キー gate → L3 Validator → Visual Resolver（snap 段のみ）→ Task Graph Executor → Command Compiler → action_map（gen_id/idempotency_key）→ MCP → **L2 Policy Gate** → Nav2 Bridge REST → Nav2 → L1 collision_monitor → L0' 0.3m/s クランプを全通過）。

### 4-1. ノード分割とデータフロー

新規ノード **`gesture_detector`**（L4 知覚・publish-only・**0 actuation**）を 1 本立て、`x_er_bridge` とはトピックで疎結合する（GPU/NN 初期化失敗を司令ノードの起動列に混入させない＝原則 P1 の精神の延長）。

```
HP60C (ascamera)
  /bot1/camera/color/image_raw（既定 OFF→ON 化要）+ depth + camera_info
        │
        ▼
gesture_detector（★NEW・骨格NN + 幾何 + 時間窓多数決）
  ① keypoints(肩/肘/手首) 2D+depth ~15-30fps
  ② TF: camera_optical→camera_link→base_link→odom→map（camera_link=contract PR）
  ③ ジェスチャ判定（§5）+ hold window 多数決（§6）
  ④ 幾何解決: 人物map位置 / レイ×盤面交点（§5-4）
        │ /perception/gesture_events（std_msgs/String JSON・additive 契約）
        ▼
x_er_bridge 内 gesture_source（★NEW・bridge-local）
  ⑤ event → RoboticsPlanDraft 相当 dict を決定論生成
     {plan_id:"<uuid>",
      detections=[{id:"summon:p0"|"pointing:p0", confidence}],
      task_graph=[{id:"g1", robot:"bot1", action:"navigate",
                   target:"<location名>"}]}   ※ plan_id/TaskNode.id は凍結モデル必須 field
     ※ 座標は入れない（INV-1）。target=location 名の compile は §10 の
        known-location passthrough（必須実装項目）が前提
        ▼
to_robotics_plan_draft（ER 経路と同一ゲート）→ L3 → gen 発番 → MCP
        ▼
L2 Policy Gate（location/freshness/battery/emergency/rate 0.5s/duplicate）
        ▼
Nav2 Bridge REST → Nav2 → L1 → twist_mux → L0' ≤0.3m/s → STM32
        ▼
/nav2_bridge/goal_result → mark_succeeded（既存 step7 パターン）
```

### 4-2. ER との役割分担（併用・排他でない）

**NN（ローカル・10-30Hz・決定論）＝「いつ・どの方向」／ ER（4-6s・課金・意味理解）＝「その先にあるものは何か」**。gesture event に `needs_semantic_resolution: bool` を持たせ、指差し交点がどの location にも snap できず盤面内の場合のみ ER に画像+交点近傍を渡す seam を残す（**Phase 1 は false 固定・配線しない**＝課金は具体要求が出るまでゼロ）。音声との分担は §12。

## 5. ジェスチャ判定（擬似コード・要点）

定数はすべて `mode_x_er.gesture.*` config 注入（コード定数禁止＝[02:98](02-l3-planning-core.md)）。keypoint confidence 不足・深度欠損は**判定しない**（fail-closed・外挿しない）。

```python
# ①召喚: 手首が肩より上（map z 比較。頭は使わない）
is_raise = P_map(WRIST).z - P_map(SHOULDER).z > cfg.summon.wrist_above_shoulder_m  # 例 0.10

# ②指差し: (a)腕が伸びている (b)肩→手首レイと肘→手首レイが一致 (c)盤面へ下向き成分
straightness = |W-S| / (|E-S|+|W-E|)                      # 例 ≥0.85
d = unit(W - S)          # 主レイ=肩→手首（基線0.55m。肘→手首0.25mの2倍強→角度誤差半減）
is_point = straightness ≥ th and angle(d, unit(W-E)) ≤ 12° and -d.z ≥ sin(15°) and not is_raise
```

**幾何解決**（§5-4 相当）: 召喚＝人物胴中心を板面へ正射影→最寄り location（人は盤外ゆえ snap 半径は課さず sanity bound 2.0m のみ）。指差し＝まず他人物との角度近接（cone 10°・複数該当は clarify）→無ければレイ×板面 z=0 交点→valid polygon→最寄り location（`snap_radius_m 0.25` 再利用）＋ **margin test（`d2-d1 < 0.10m` は clarify＝近い方を勝手に選ばない**。初版「複数マーカー＝clarification」の直系）。

**誤差の定量（G-3・本書で最も重要な数字）**: キーポイント 3D 誤差 ±2-5cm（未検証仮定）× 基線 0.55m → レイ角度誤差 ≈5.2° → 交点誤差 ≈**15-25cm** ＜ location 間隔 **500mm**。→ **snap 0.25m がちょうど吸収する＝指差しにも coordinate goal は要らない**（§9 の定量的根拠）。ただし境界すれすれ＝**ジオラマ再設計に「location 間隔 ≥ 2×交点誤差（≈0.5m）」の制約を申し送る**（OQ-11）。

## 6. 信頼性設計

- **①時間的多数決の再定義**: 初版の `confirm_frames: 2` は ER 1 cycle≈5s が単位。NN は 15-30fps なので「2 フレーム＝66-133ms」では無意味。**時間窓多数決 + ヒステリシス + 不応期**へ再定義: `hold_duration_s 1.2`（意図的保持の最短）・`min_frames_in_window 12`（fps 低下時は確定させない=fail-closed）・`enter/exit_ratio 0.80/0.50`・`refractory_s 5.0`（連射防止）。すべて例示値・実測で確定。
- **②confidence 閾値**: `gesture_guard` plugin（narrow-only・[ADR-0003](../adr/0003-bridge-local-manifest-composition.md)）。1 cycle の navigate は robot あたり 1 件。
- **③語彙 gate**: KNOWN_LOCATIONS 外は L3 Validator + `schemas.py:159` が二重 reject。
- **freshness**: ER 4.68s vs `unavailable_after_s 2.0` の初版の衝突は**構造的に消滅**（ローカル処理は数 ms）。ただし `STALE_AFTER_S 0.5` は残る＝**State Cache 10Hz writer 並行稼働 + dispatch 直前の最新 state** の規律は継承必須。tighten-only（ADR-0004・`policy_gate.py:92-96`）で緩めない裁定も不変。
- **レイテンシ**: 手挙げ→発進 ≈ **1.3s**（NN 検出 ~0.07s + hold 1.2s + L3/L2 sub-ms）・到着 ≈5-9s。初版の 5-13s から発進 1/5。

## 7. 状態機械とエッジケース

`IDLE/SENTRY → DETECTING（hold 窓蓄積）→ RESOLVING（幾何解決）→ GATING（plugin→L3→L2）→ IN_FLIGHT（in-flight ≤1）→ ARRIVED（mark_succeeded・notice）→ REFRACTORY → IDLE`。UNRESOLVED/CLARIFY/reject は全て **0 dispatch** で IDLE へ。

| 状況 | 裁定 |
|---|---|
| 腕を下ろした（走行中） | **キャンセルしない**（到着まで。明示 cancel は既存 `stop`）＝初版裁定継承 |
| 走行中の新規召喚 | **log して drop**（キューしない）。到着 + 不応期後に再評価（初版 Q2 を確定） |
| 複数人同時挙手 / 指差し曖昧（margin 不足）| CLARIFY → 0 dispatch（勝手に選ばない） |
| レイが盤面と交わらない | UNRESOLVED（数値発散を明示拒否） |
| fps 低下・深度欠損・フレーム取得失敗 | 確定させない / publish しない（fail-closed） |
| sentry と到達先が同一 | `duplicate_destination` reject＝既に居るので動かない（sentry 選定で回避） |
| battery low / emergency | 既存 L3/L2 が reject |

## 8. 安全

**変わらないもの**: L0' 0.3m/s クランプ・L1 collision_monitor（scan+virtual_scan・**GPU 非依存**＝骨格 NN が落ちても L1 は生きる）・twist_mux prio100 FROZEN・L2 全チェック。

- **人へ向かって走る**: 到達点は必ず盤面上の 9 location（`schemas.py:159`）＝**盤外の人へは構造的に到達できない**。standoff パラメータは発明しない（初版 §standoff 継承）。
- **人がジオラマに手を入れた場合（正直な限界）**: 板面上 ≳150mm（LiDAR 面）は `/bot1/scan` が見る→停止。**それ以下の低い手は CURRENT では検知できない**（3D 検知は nvblox の TARGET 機能・S1/S2 未通過）。Phase 1 の答えは運用規律（「盤面への手入れは robot 停止時のみ」）＋0.3m/s。「3D で手を検知して止まる」とは主張しない。**骨格 NN の人物位置を反射安全の入力に使うことは明示的に不採用**（GPU を反射経路に持ち込む＝[23 §1 P1](../architecture/23-perception-and-localization.md) 違反。この誘惑は必ず出るので書いておく）。
- **8GB 予算**: 骨格 NN は S1 の第 3 の競合者。**S1 測定順に `+gesture NN` を 1 段追加**する（[23 §7](../architecture/23-perception-and-localization.md) へ申し送り済み）。MediaPipe（CPU 推論）が第1候補である理由もここ（GPU/VRAM を食わない）。

## 9. 「人物の元へ行く」× KNOWN_LOCATIONS 9 キー制約 — 選択肢と推奨

| 案 | 契約コスト | 到達解像度 | §5 誤差との適合 | 判定 |
|---|---|---|---|---|
| **(a) 最寄り known location へ snap** | **ゼロ** | 9 点 | **適合**（誤差 15-25cm < 間隔 500mm） | **Phase 1 採用** |
| (a′) 呼び出し口 location 追加 | 高（凍結ハブ locations.py 9→11・4 箇所同期・全トラック予告） | 11 点 | 適合 | 不採用（**ジオラマ再設計時の location 配置として解けば追加コスト≒0**） |
| (b) coordinate goal 解凍 | 高（[06:127](06-unfrozen-contract-resolutions.md) 案①/②: MCP 引数・Policy Gate 座標検証・duplicate 座標意味論＝未定義数値多数） | 連続 | **過剰**（誤差はレイ側律速＝連続にしても精度は上がらない） | Phase 2 defer（gate=06:131「coordinate goal を要する具体デモ要件」。**指差し実測 σ が判定材料**） |
| **(c) ハイブリッド (a)→(b)** | 段階的 | — | — | **推奨** |

- (b) を採る場合も **INV-1 を守る限り `handoff.py` は不触**（座標は L3 の導出結果でありモデルの主張でないため）。「モデルに座標を主張させる」方向に拡張する時だけ handoff 改訂が要る（06 への条件付き注記を追補済み）。
- (b) の「障害物外検証を L2 に足す」は**不要が結論**: L2 は state.json を読む単純決定論層で costmap を持たない。L2 は valid polygon 内のみ検証し、障害物回避は Nav2 planner + L1 に委ねる（既存分業 [23 §1](../architecture/23-perception-and-localization.md) と整合）。棚内部を指すケースは valid polygon を棚抜き形状にして吸収（calibration artifact 側の責務）。

## 10. 契約 / config（Phase 1・すべて additive / safe-OFF）

- **config**: `mode_x_er.gesture.*`（`enabled: false` / `source_topic: ""`=fail-closed / §5-§6 の各閾値 / `sentry.location: ""`）。`snap_radius_m` は**新設せず**既存 `mode_x_er.visual.snap_radius_m` を再利用。base.yaml 実追加は bringup/skeleton 所有 Issue へ予告→末尾追記（[08 §3](08-x-er-bridge-node-spec.md) 手順）。
- **新トピック**: `/perception/gesture_events`（`std_msgs/String` JSON・`goal_result` と同形式・`.msg` 化は Phase 4=doc16 方針）。封筒に `resolved{status,location,d1_m,d2_m}` と監査用 `evidence{ray_origin/dir/board_hit}`（**draft には入れない**=INV-1）、`needs_semantic_resolution`。doc03 カタログへ land 時に追記。
- **contract PR 依存**: `camera_link` は [23 §4](../architecture/23-perception-and-localization.md) の contract PR で **landed（名前のみ）**。残るのは ROS 光学規約 z-forward の**光学 frame 名の凍結**（OQ-4）と URDF 取付実測。
- **必須実装項目（consistency-audit 2026-08-09 E1 で判明した設計の空白）**: 現行 Command Compiler は `task.target` を **`ResolutionResult`（`detection.id` キー）経由でしか compile しない**（`command_compiler/compiler.py` の `by_target` 参照）ため、本設計の `target=<known location 名>` は現行実装のままだと **skip＝0 dispatch** になる（L3 Validator は location 名を許すが compiler が落とす）。**bridge-local の known-location passthrough（[02:240](02-l3-planning-core.md) の compiler plugin seam / [ADR-0003](../adr/0003-bridge-local-manifest-composition.md) 準拠・narrow 追加のみ）を Phase 1 の必須実装項目とする**。よって「L3 実装は無編集」は Visual Resolver（`homography: []` fail-closed）には成立するが **Command Compiler には成立しない**——正直に明記する。
- **初版の「契約変更ゼロ」は本改訂では成立しない**（camera_link PR + additive topic/config + compiler passthrough が要る）——正直に明記する。

## 11. 到着後の振る舞い

`goal_result` → `mark_succeeded` で停止。`/operator/notice`（#446 RESOLVED・依存トラック合意待ち＝[06:33](06-unfrozen-contract-resolutions.md)）への到着発話は、[05 §5.3 発話スコープ](05-operator-feedback-and-voice-response.md):196 の「operator の音声命令に紐づく」定義を「**operator 起点の明示的意図表明（音声 or ジェスチャ）**」へ広げる additive 提案（gen_id 相関の機構は無改造）。可否は #446/doc05 所有者判断（OQ-8）。決定論テンプレート・LLM 作文なし。

## 12. 音声入力（将来拡張・メモ）

将来形は「**ジェスチャで呼び、音声で指示**」の 2 段: ①ジェスチャ召喚（本書・決定論・サブ秒・課金ゼロ）→ ②到着後に音声指示（[04](04-er-input-modalities-and-stt.md) の ER audio 直入力・`ErTaskRequest.instruction_audio_ref` は既存第一級フィールド＝**契約追加ゼロ**・transport は `resolve_audio_transport` 既定 DIRECT）。ジェスチャ＝低情報量高頻度→ローカル NN、音声＝高情報量低頻度→ER、という技術特性への忠実な分業であり、ER 呼び出しが明示イベント駆動 1 回になるため初版 Q8（ポーリング課金 vs standing 無承認 spend 禁止）が**構造的に解消**する。本書ではこれ以上設計しない。

## 13. 旧方式（俯瞰カメラ + 召喚マーカー・2026-08-07 初版）— 降格保存

俯瞰カメラ復帰時の代替案として保存する（削除しない）。要点: 固定俯瞰 C922n + homography 前提で実人間の手が幾何的に解決できないため、**挙手姿勢の物理マーカーを盤面に置く**方式を採り、ER が俯瞰 1 枚から「マーカーの最寄り location」を直接名指し（R1 経路）していた。confirm_frames=2（ER cycle 単位）・レイテンシ 5-13s・poll 課金が課題だった。**本改訂で消滅した初版の未決**: Q3（2枚目画像）・Q7（マーカー物理仕様）・Q8（poll 課金）・Q10（R1 live 成立性）。**継承された未決**: 走行中割り込み（→§7 で確定）・task timeout・battery 優先度・notice 拡張・ジオラマ再設計後の座標。

## 14. OPEN QUESTIONS

| # | 問い | 優先度 |
|---|---|---|
| OQ-1 | 骨格 NN の Orin 8GB 実効 fps/メモリ（P1 前提そのもの。S4 スパイク: `pip install mediapipe` が JetPack6.x で通るか→CPU 10Hz→FP/FN） | **最高** |
| OQ-2 | HP60C 実 FOV/解像度/fps/min range/**depth-color alignment（`ascamera` が aligned depth を出すか＝②の工数分岐**） | **最高** |
| OQ-3 | 取付角（nvblox との競合の最終値）と sentry pose の具体キー | 高 |
| OQ-4 | カメラ光学 frame の凍結（`camera_link` contract PR の切り方・[23 OQ-7](../architecture/23-perception-and-localization.md) と統合） | 高 |
| OQ-5 | `/bot1/camera/color/image_raw` ON 化コスト（DDS 帯域・CPU・composable 可否） | 高 |
| OQ-6 | x_er_bridge が dispatch 直前に fresh StateSnapshot を取るか（`STALE_AFTER_S 0.5` に対して。初版 Q1 継承） | 高 |
| OQ-7 | **キーポイント 3D 誤差 σ の実測**（§5 G-3・§9 の全結論がこの 1 数値に従属。既知位置の的を指す 20 試行＝最安で最多を決める検証） | **最高（費用対効果）** |
| OQ-8 | 到着 notice の `/operator/notice` additive 可否（#446/doc05 所有者） | 中 |
| OQ-9 | `goal_result` が来ない場合の task timeout（[08:112](08-x-er-bridge-node-spec.md) 残件と同根） | 中 |
| OQ-10 | 充電中・低バッテリー時に召喚を受けるか（sentry=charging_station 案と衝突） | 中 |
| OQ-11 | **ジオラマ再設計の location 配置制約「間隔 ≥ 2×交点誤差 ≈0.5m」**（doc04 へ申し送り） | 高 |
| OQ-12 | [23](../architecture/23-perception-and-localization.md) の「ジオラマに人はいない」の言い換え（「走行面上に動的障害物が無い」）＝dynamic 層不採用の結論は不変・追補済み | 中 |
| OQ-13 | `gesture_detector` の所有トラック / package 配置（bridge に GPU 依存を入れない→新 package `warehouse_perception`（仮）か） | 高 |
| OQ-14 | ①②の同時成立 dead zone（「斜め上を指す」の分布を OQ-7 実測で確認） | 中 |
| OQ-15 | (b) coordinate goal を解凍する具体デモ要件が本当に出るか（「出ない＝恒久 defer」も正当な着地） | 中 |

## References

- [01-architecture-and-flow.md](01-architecture-and-flow.md)（:112 L4 非所有）/ [02-l3-planning-core.md](02-l3-planning-core.md)（:78 target=location 名・:98 hardcode 禁止・:149 calibration 5 field・snap）
- [06-unfrozen-contract-resolutions.md](06-unfrozen-contract-resolutions.md)（:127 coordinate goal DEFER 案・:131 解凍 gate）/ [08-x-er-bridge-node-spec.md](08-x-er-bridge-node-spec.md)（§3 config 手順・§5 cycle・:112 残件）
- [04-er-input-modalities-and-stt.md](04-er-input-modalities-and-stt.md)（音声将来形）/ [05-operator-feedback-and-voice-response.md](05-operator-feedback-and-voice-response.md)（:196 発話スコープ）
- [architecture/23-perception-and-localization.md](../architecture/23-perception-and-localization.md)（camera_link contract PR・S1/S2 ゲート・原則 P1/P2）
- [ADR-0003](../adr/0003-bridge-local-manifest-composition.md) / [ADR-0004](../adr/0004-l2-restrict-only-policy-profile.md) / [ADR-0006](../adr/0006-single-bot-first.md) / [ADR-0007](../adr/0007-no-overhead-camera-gesture-via-onboard-nn.md)
- `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/handoff.py`（:67-90 forbidden keys）/ `ws/src/warehouse_interfaces/warehouse_interfaces/schemas.py`（:159）+ `locations.py`（9 キー）/ `ws/src/warehouse_mcp_server/warehouse_mcp_server/policy_gate.py`（:50-51 freshness・:92-96 tighten-only・:222 duplicate）
- [docs/GLOSSARY.md §11](../GLOSSARY.md)（ジェスチャ司令 / 手挙げ召喚 / 指差し の正準用語）
- HTML 全体地図: [architecture/robot-architecture-tree.html](../architecture/robot-architecture-tree.html)（本 doc は 08_Human_Interaction ノードの正本）
- 参考実装: [ZED2i Body Tracking 召喚事例（Qiita motoms）](https://qiita.com/motoms/items/b87d08448cdaddb24c35) — 参照日: 2026-08-09（本改訂で「ローカル骨格 NN・決定論」という同じ側に寄った。残る差は /goal_pose 直 publish の有無と到達点の 9 キー制約）

---

## 【2026-08-18 追補】部屋スケール運用でのジェスチャ司令（ADR-0009）

> **決定正本**: [../adr/0009-m1-room-scale-operation.md](../adr/0009-m1-room-scale-operation.md)（オペレーター決定 2026-08-18）。M1 はミニチュアジオラマではなく**実際の部屋（room scale）**を走る。**本書のジェスチャ司令は「従」から「主役」へ格上げ**される（倉庫設定は薄め、召喚・指差しを中心に据える）。
> 既存本文は**編集しない**（下流参照の行ズレ回避＝[#165 教訓](../dev/03-retrospectives.md)）。本節と既存記述が食い違う場合、M1 フェーズについては本節が正。

### R-1. 変わらないもの（構造は不変）

**設計の骨格は 1 行も変わらない**（[ADR-0009](../adr/0009-m1-room-scale-operation.md) Decision 6）:

- **ER バイパスの決定論ローカル経路**（:53 の (iii)）・サブ秒・課金ゼロ。
- **INV-1**（幾何は plan draft の外・座標は `evidence` のみ・:25）と **INV-2**（`to_robotics_plan_draft` 以降を 1 ステップも迂回しない・:57）。
- **KNOWN_LOCATIONS への snap**（:149 の (a) 採用）と語彙 gate（L3 Validator + `schemas.py:159` の二重 reject）。**9 キーは凍結のまま・改名しない**（値だけが部屋の実測 waypoint へ差し替わる）。
- 信頼性設計（§6 の時間窓多数決・ヒステリシス・不応期）、状態機械（§7）、契約 / config の additive・safe-OFF 方針（§10）。

**変わるのは §3 の幾何前提と §8 の安全論証の形**である。以下 R-2 / R-3。

### R-2. 幾何前提は部屋で再検証が要る（Phase 1 実測・最重要）

**根本原因**: ジオラマでは盤面が机高にあり、走行面がそのまま操作者の腰〜胸の高さに来ていた。部屋では**走行面＝床**になるため、カメラ（走行面上 0.09-0.13m＝:41）から操作者の肩までの**高低差がおよそ倍**になる。

**① カメラ仰角（本追補で導出・入力はすべて既存 doc）**

| 量 | ジオラマ | 部屋 |
|---|---|---|
| 走行面から操作者の肩まで | ≈0.6-0.75m（:41） | ≈1.30-1.45m（成人立位・**一般値**） |
| カメラ→肩の高低差 Δz | ≈0.47-0.66m | ≈1.17-1.36m |
| 距離 1.2-1.5m での仰角 | ≈**22°** | ≈**43°** |

`# TODO(Phase 1 実測)`: 成人立位の肩高 1.30-1.45m は**一般値であり本 repo の docs 由来ではない**。実際の操作者で実測して確定する。

**帰結**: HP60C の公称画角は 73.8°（**対角/水平いずれかも未裏取り**＝P3・:15）だが、**対角読みなら垂直半画角 ≈20°・水平読みでも ≈30° 程度**と見積もられる。いずれの読みでも **43° は垂直画角の外**であり、**水平固定のままでは操作者の肩が画面に入らない**公算が高い。:41 の「距離 1.2-1.5m なら肩・肘・手首が入る」は**ジオラマ前提の見積もりであって部屋には持ち越せない**。

**選択肢（Phase 1 で決める・いずれも本追補では決めない）**:

- **(a) 操作者が離れて立つ**: 仰角 22° 相当に戻すには **≈3.1m** 必要（本追補で導出）。HP60C の公称レンジ 0.2-4m の後端に寄り、**キーポイント 3D 誤差 σ（OQ-7）が距離とともに悪化**するため、§5 G-3 の交点誤差 15-25cm 見積もりごと再計算が要る。
- **(b) カメラを上向き固定で取り付ける**: **:43 が却下したのは「パン/チルト雲台」＝可動機構**であり（`camera_link` が動的 TF になり [23 §5-2](../architecture/23-perception-and-localization.md) の static 契約を破るため）、**固定の上向き取付角は static TF のままなので契約を破らない**。ただし [23 :280](../architecture/23-perception-and-localization.md) の**チルト競合（nvblox は下向き寄り・ジェスチャは上向き寄り）が部屋ではより鋭くなる**。取付角は S2 実測と同一セッションで決める（:43 の方針は維持）。
- **(c) 操作者がしゃがむ / 座る**: 運用規律で解く案。絵として自然かは撮影構図（ADR-0009 Open）と併せて判断。

**② sentry pose（監視姿勢）の再定義** — :44 の「操作者側**長辺**を向いて待つ」は **1800×900mm 盤面の長辺**を指すジオラマ固有の表現であり、部屋では意味を失う。sentry の**条件そのもの**（9 キー内・操作者側への視線・**召喚到達先になりにくいこと**＝同一だと `duplicate_destination` reject で「呼んだのに動かない」）は不変で、部屋の waypoint 確定後に再選定する。暫定候補として挙げた `shelf_2`(0.7,0.57) も同様に再選定対象。

**③ 到達先の具体座標** — :45 の「左に立てば左へ、右に立てば右へ」を成立させていた `shipping_station`(0.45,0.12) / `charging_station`(1.5,0.12) の 2 点はジオラマ座標。**「左右に立ち分けると行き先が変わる」という演出の意味論は部屋でも成立する**が、成立させる waypoint 配置は部屋で設計し直す。

**④ 盤面 z=0 平面 → 床平面** — §5 の幾何解決（レイ×板面 z=0 交点・valid polygon・snap 0.25m）は**式としてはそのまま**で、平面が床に、valid polygon が部屋の走行可能領域に置き換わる（calibration artifact 側の責務＝:155 の分業は不変）。**OQ-11「location 間隔 ≥ 2×交点誤差（≈0.5m）」の申し送りは生きており、宛先が [04](../shared/04-diorama-layout.md) のジオラマ再設計から部屋 waypoint 設計へ差し替わる**（部屋は広いぶん間隔を取りやすく、制約としては緩む方向）。

### R-3. 安全論証の組み替え（新規レビュー項目）

**:141 の論証は部屋では成立しない。** 現行は「到達点は必ず盤面上の 9 location（`schemas.py:159`）＝**盤外の人へは構造的に到達できない**」を安全の根拠にしている。ジオラマでは人が盤外＝走行面の外に立つのでこれは真だったが、**部屋では人とロボットが同一平面に立つ**ため、この**構造的保証は消える**。

**組み替え後の論証（多層防御）**——「到達できない」ではなく「**到達集合が限定され、かつ物理反射が生きている**」の形にする:

1. **到達集合の限定**: 到達点は依然 `KNOWN_LOCATIONS` の 9 キーのみ（`schemas.py:159` + L3 Validator の二重 reject・INV-1 により座標は draft に載らない）。**人の現在位置へ直接向かうことは構造的にできない**——変わったのは「人の立つ場所が走行面上にありうる」点であって、**人を追尾する経路が生まれるわけではない**。
2. **waypoint 配置の規律（新規・部屋設計時）**: 操作者が常在する位置（デモ中の立ち位置・撮影ポジション）を waypoint に**しない**。召喚の到達先は操作者の手前で止まる配置にする（`standoff` パラメータは:141 のとおり**発明しない**——配置で解く）。
3. **L1 collision_monitor**（`/scan` 由来・**GPU 非依存**＝骨格 NN が落ちても生きる・§8）と **L0' 0.3m/s クランプ**は不変。
4. **低い手・低い障害物の限界は部屋でも同じ**（:142）: 走行面上 ≳150mm（LiDAR 面）以下は CURRENT では検知できない。**「3D で人を検知して止まる」とは引き続き主張しない。**骨格 NN の人物位置を反射安全の入力に使わない方針（GPU を反射経路に入れない＝[23 §1 P1](../architecture/23-perception-and-localization.md)）も不変。
5. **運用規律の書き換え**: :142 の「盤面への手入れは robot 停止時のみ」は、部屋では「**走行中の robot の進路に立ち入らない**」へ言い換える。

> **⚠️ これは実質的な安全性の低下ではなく論証形式の変更**だが、**流用せず安全レビューを通し直す**こと（[ADR-0009](../adr/0009-m1-room-scale-operation.md) 帰結 ⑦ / [.claude/rules/safety.md](../../.claude/rules/safety.md)）。人が走行面上に立ちうる構成は本プロジェクトで初めてである。

### R-4. 到達姿勢（yaw）の重要度が上がる

召喚の到達点で**ロボットが操作者の方を向いて止まる**かは、部屋デモでは絵の質に直結する。しかし現行の座標ゴール経路は **yaw を落としている**（`nav2_bridge.py` が `orientation.w = 1.0` を固定＝`warehouse_nav2_bridge/CLAUDE.md` の「#223 残 ②」）。ジオラマでは [04](../shared/04-diorama-layout.md) F-L3「通路端の goal は回転不要な向きで置く」で回避していた制約が、**部屋では「向きを設計したいのに設計できない」形で表面化する**。yaw 対応（`_pose` の quaternion 化）は本書の射程外＝所有トラック判断（[ADR-0009](../adr/0009-m1-room-scale-operation.md) 帰結 ③）。

### R-5. 新規 OQ（本追補由来・§14 の表は行安定のため編集せず本節を正とする）

- **OQ-16: 部屋でのカメラ仰角と取付角の確定**（R-2①。(a) 距離 / (b) 固定上向き / (c) しゃがむ の選択 ＋ nvblox とのチルト競合の再裁定）。優先度 **最高**（P1 前提そのものが部屋で崩れうる）。
- **OQ-17: 部屋 waypoint 9 点の配置設計**（R-2②③④。sentry / 召喚到達先 / 間隔 ≥ ~0.5m / 操作者常在位置を避ける規律）。優先度 **高**。
- **OQ-18: 人が走行面上に立つ構成での安全論証の再レビュー**（R-3）。優先度 **高**（安全レビュー必須）。
- **OQ-19: 距離 ~3m でのキーポイント 3D 誤差 σ**（R-2(a) を採る場合。OQ-7 の測定を部屋の実距離で行う）。優先度 **高**。

### R-6. 関連リンク（双方向）

- **決定正本**: [../adr/0009-m1-room-scale-operation.md](../adr/0009-m1-room-scale-operation.md)（帰結 ⑦ が本節に対応）
- 知覚・Nav2 側: [../architecture/23-perception-and-localization.md](../architecture/23-perception-and-localization.md) 末尾【2026-08-18 追補】**G 系列**
- レイアウト側: [../shared/04-diorama-layout.md](../shared/04-diorama-layout.md) 末尾【2026-08-18 追補】（ジオラマ凍結・W3 中止）
- [docs/GLOSSARY.md §11](../GLOSSARY.md) — **部屋スケール運用（room-scale operation）** の正準定義
