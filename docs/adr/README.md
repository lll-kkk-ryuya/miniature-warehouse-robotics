# Architectural Decision Records (ADR)

hard-to-reverse な設計判断と**その理由**を記録する場所。フォーマットと「いつ ADR を起こすか」の判定は
[`.claude/skills/domain-modeling/ADR-FORMAT.md`](../../.claude/skills/domain-modeling/ADR-FORMAT.md)。
決定を対話で詰めながら ADR を書き起こす入口は `/grill-with-docs`。

- **命名**: `NNNN-slug.md`（連番。最大番号 +1）。設計 doc の `NN-xx` 番号体系とは別クラス。
- **いつ起こすか（3条件すべて）**: ①hard to reverse ②surprising without context ③real trade-off。1つでも欠けたら起こさない。
- **retrospectives との違い**: ADR = **前向きの決定＋トレードオフ**、[docs/dev/03-retrospectives.md](../dev/03-retrospectives.md) = **事後の教訓・インシデント**。重複させず相互リンクする。
- **索引**: 各 ADR は本 README と（load-bearing なら）[docs/README.md](../README.md) に 1 行 back-link を張る（双方向リンク＝[docs-authoring](../../.claude/skills/docs-authoring/SKILL.md)）。
- **分野別ビュー**: M1（ROSMASTER 単騎フェーズ）関連 ADR の索引は [docs/mode-m1/README.md](../mode-m1/README.md) §関連 ADR。

## 一覧（新しい順）

| ADR | 決定 | 状態 |
|---|---|---|
| [0010](0010-raise-speed-cap-to-platform-max.md) | 凍結契約 `MAX_LINEAR_VELOCITY = 0.3 m/s` を**ミニチュア安全値からプラットフォーム上限へ再定義**（2026-08-19 オペレーター決定「速度は出せる限り」）。公式 FW V3.6.5 の M1 候補は **car_type 0x0A / 0.7m/s**で、搭載 FW の版・応答確認後に contract PR で pin。運用値は config（≤契約上限・既存 fail-closed 機構流用）とし、デモ最終値は **S-SPEED（段階増速実測）**で確定。L0' クランプは廃止せず**方向保存・暴走バックストップ**として再定義し serial driver slice で結線（引き上げの前提条件）。ESP32 firmware は 0.3 凍結のまま。stock FW に通信途絶停止が無いため G-g MCU watchdog も必須。代償 = 人への保護余裕縮小（margin ∝ v_max・C-3 と同期改訂）・Orin ブラウンアウトリスク増・docs 約90箇所 sweep | accepted（値 pin は実機確認 + S-SPEED 待ち・safety.md 改訂は governance PR） |
| [0009](0009-m1-room-scale-operation.md) | M1 単騎フェーズは**実際の部屋（room scale）で運用**し、ミニチュアジオラマ（1.8×0.9m）は**走行に使わず凍結保存**する（sim の回帰環境としては現状維持・2台復帰フェーズの資産）。部屋デモは**倉庫設定を薄めジェスチャ召喚を主役**にする。PR #530（OQ-3 = 非円形 footprint の F 系列）は revert せず **re-scope**——footprint polygon 採用は部屋でも生存し、280mm 通路 / 420mm 角ポケット / F-L 系列はジオラマ限定の歴史記録へ。`KNOWN_LOCATIONS` の 9 キーは凍結のまま**値のみ**部屋の実測 waypoint へ差し替え、W3（ジオラマ 9 点再設計）は中止。代償はプロジェクトの看板（ジオラマ）が画面から消えること・環境統制の喪失・sim↔実機の乖離・**「人へは構造的に到達できない」安全論証の組み替え**（人と robot が同一平面に立つ＝構造的保証から多層防御への後退であり同等性は主張しない） | accepted（部屋の範囲・撮影構図・room sim world の要否・**sim/実機の config 二重化・安全論証の再レビュー等**が Open＝ADR §Open + 末尾追補②） |
| [0007](0007-no-overhead-camera-gesture-via-onboard-nn.md) | 本フェーズは **ER/知覚入力に俯瞰カメラを使わない**。視覚入力は搭載 HP60C に一本化し、実人間ジェスチャ2種（肩上げ召喚・指差し）を**ローカル骨格 NN（第1候補 MediaPipe・Apache-2.0・CPU）で決定論認識**して既存 L3/L2 ゲートへ流す（ER は意味解釈イベント時のみ・常時ポーリング禁止のまま）。homography 系は削除せず `homography: []` の fail-closed で降格保存・`overhead_image_ref` は改名しない。代償は全景視野の喪失（sentry pose 運用）・camera_link contract PR・撮影用俯瞰の扱いが Open | accepted（撮影用俯瞰の扱いのみ Open） |
| [0006](0006-single-bot-first.md) | 今回のフェーズは**ロボット1台（単騎構成 / single-bot-first）**で実装・検証・撮影する。決定要因は ①実機 M1 は1台のみ購入 ②M1 の distro/電源/閉ソース driver/L0' クランプという一次リスクを交通管理と直列化しない ③1台でも LLM 司令官デモ・手挙げ召喚（mode-x-er/09）は成立。**2台系の設計 doc・実装資産（SimpleTrafficManager / HeadOnInjector / negotiation engine / collision_monitor）は削除せず凍結保存**（**collision_monitor は VirtualScan 相互注入〔2台系〕だけが非発火で、`/scan` 由来の L1 物理反射は1台でも現役**＝Decision 3）。代償は Mode A/B 交通管理・キャラ LLM 交渉・min-separation ≥0.15m の実機実証が2台復帰フェーズへ繰延 | accepted（初回公開ゲートの再定義のみ Open） |
| [0008](0008-ros2-distro-humble-for-rosmaster-m1.md) | ROS 2 distro を **Jazzy → Humble / Ubuntu 22.04** へ切替。決定要因は ①**Orin Nano で Isaac ROS を使う道は 3.x=Humble のみ**（4.x は Jazzy だが Thor 専用）②ROSMASTER M1 の Yahboom driver が Humble 固定（HP60C は 2017 GCC5.4 の閉ソース .so）。MPPI は Humble にも有り。代償は **Gazebo の公式ペア喪失**（Humble↔Fortress・Harmonic は非公式） | proposed（Gazebo の扱い等 Open。[02:383](../shared/02-hardware-design.md) のオペレーター指示で proposed 保持） |
| [0005](0005-l0-battery-brownout-floor.md) | L0 の battery brownout floor は percent 3段 policy と**別名・別機構の voltage-based** MCU floor として**将来 phase**に持つ方針（現行 L0 は cutoff 無し＝`/battery` publish + 物理切断のみ・percent policy は L1 所有・cutoff 電圧は Phase-1 実測）。凍結 percent `battery_is_critical(pct)` とは非対称 | accepted |

| [0004](0004-l2-restrict-only-policy-profile.md) | L2 Governance は自由 plugin 化せず **data-only restrict-only policy profile** に閉じる（凍結値=floor・緩い値は起動拒否 fail-closed・reject 巻き戻し禁止・v1 で code plugin 不採用）。L3 の自由 plugin 化（ADR-0003）と非対称 | accepted |
| [0003](0003-bridge-local-manifest-composition.md) | bridge-local run manifest + fail-closed plugin composition を A案で標準化（manifest resolution 層／namespaced plugin code〔9-enum 非改変〕／advisory trust／ISOLATE_PLUGIN／safety-critical profile hash gate）。実装 = offline spike 済・配線 XER6 pending | accepted |
| [0002](0002-er-in-hermes-standard.md) | ER-in-Hermes を標準 transport に採用（fork gateway 8644 一本で全 modality／`direct`=緊急 fallback／Langfuse Pattern A 現行・Pattern B は HLF gate 後）。実装は TARGET（#389 の live-send seam は main 着地済・残は wiring〔XER6〕と 8644 fork 配備） | accepted |
| [0001](0001-adopt-grill-with-docs-and-canonical-glossary.md) | docs authoring 規律として grill-with-docs skill 群＋単一正準 `docs/GLOSSARY.md`＋ADR 実践を採用 | accepted |
