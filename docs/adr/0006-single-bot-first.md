# ロボット1台先行構成（single-bot-first）で実装する

**Status**: accepted（2026-08-07 ユーザー決定。初回公開ゲートの再定義のみ Open）

本プロジェクトは当初から「ミニチュア倉庫に**2台**の自律ロボット + LLM司令官（交通管理）」を前提に設計してきた（[00 §目的](../shared/00-project-overview.md):8 / [06 Phase 3](../architecture/06-implementation-phases.md):182）。今回の実装フェーズは **ロボット1台のみ（単騎構成 / single-bot-first）** で進め、2台の交通管理・キャラLLM交渉・min-separation 系の実機実証は**後続フェーズ（2台復帰フェーズ）へ繰延**する。実機を M1 1台しか購入していないという事実と、立ち上げリスクを直列化しない判断による。**既存の2台系設計 doc・実装資産は削除せず凍結保存する。**

## Context / 背景

- **ハードウェアが物理的に1台しかない。** 実機は Yahboom ROSMASTER M1（メカナム4輪）**1台**（Amazon `Superior / without Nano`・[02 §ROSMASTER M1 採用検討時の残課題](../shared/02-hardware-design.md)）＋ 手持ちの Jetson Orin Nano Super 8GB。一方 docs の正本は依然 2台前提（[02 §A 仕様表](../shared/02-hardware-design.md):12「台数 | 2台」、[01 予算](../shared/01-budget-and-procurement.md):38「2台+LLMで動いてから追加投資を判断」）。**2台目を今買うか**は未決であり、決めるまで実装を止める理由が無い。
- **立ち上げリスクを直列化しない。** M1 採用は distro 切替（[ADR-0008](0008-ros2-distro-humble-for-rosmaster-m1.md) = Jazzy→Humble）、Gazebo 公式ペア喪失、Yahboom 閉ソース driver、電源自作（バッテリー直タップ＋昇圧 19V＋10A ヒューズ・[02 §給電の配線設計](../shared/02-hardware-design.md)）、L0' ホスト側速度クランプ（[02 残課題7](../shared/02-hardware-design.md)）と、**未検証の一次リスクが同時に立っている**。ここに「2台同時通信・namespace 分離・交通管理」を重ねると、失敗時の切り分けができない。
- **2台前提の一次リスクの一部は、M1 採用で構造ごと消えている。** R-37「micro-ROS Agent 2台同時接続の XRCE `client_key` 衝突」（[07](../shared/07-research-notes.md):242）と R-43「LaserScan の micro-ROS UDP MTU」（[07](../shared/07-research-notes.md):253）は、いずれも **ESP32 + micro-ROS WiFi UDP × 2台**という旧構成に固有。M1 はホスト（Orin）直結の自前シリアルドライバ経路（[02 残課題5-7](../shared/02-hardware-design.md)）であり、micro-ROS Agent の多重化問題そのものが存在しない。**2台に戻すときはリスク地図の書き換えが要る。**
- **1台でも「主役の絵」は成立する。** LLM司令官が状況を読んで実機に指示を出す様子（Before/After・障害物投入→迂回・思考ログ・LLM 4社比較）は robot 1台で撮れる。Mode X-ER 系の「人が意図を与え、ロボットが解釈して動く」デモ＝**手挙げ召喚**も1台で成立する（正本: [mode-x-er/09-hand-raise-summon.md](../mode-x-er/09-hand-raise-summon.md)）。
- **凍結契約は台数非依存に作られている。** `warehouse_interfaces` の `StateSnapshot.robots` / `RobotState` は `dict[str, ...]` で台数を固定しない（[schemas.py:104,130](../../ws/src/warehouse_interfaces/warehouse_interfaces/schemas.py)）。**1台化のために凍結契約を変更する必要は無い。**

## Decision / 決定

**今回のフェーズは robot 1台（`bot1`）のみで実装・検証・撮影する。**

1. **正本の「2台」記述は将来形として残し、削除・書き換えをしない。** 入口 doc（[00](../shared/00-project-overview.md) / [06](../architecture/06-implementation-phases.md) / [05](../shared/05-video-storyboard.md) / [02](../shared/02-hardware-design.md) / [01](../shared/01-budget-and-procurement.md)）に**末尾追記**で「現行は1台先行」と明記し、本 ADR を指す。中段挿入はしない（#165 行ドリフト教訓 = [dev/03-retrospectives.md](../dev/03-retrospectives.md)）。
2. **2台系の設計 doc は無編集で凍結保存する。** [mode-a/11a](../mode-a/11a-traffic-mode-a.md)（SimpleTrafficManager / VirtualScan 相互注入 / §9 ≥0.15m yield デモ）、[mode-c/11c](../mode-c/11c-traffic-mode-c.md)（RMF Traffic Schedule / Fleet Adapter）、[14-character-llm-negotiation](../architecture/14-character-llm-negotiation.md)（bot1/bot2 交渉）、[12 §Emergency Guardian](../architecture/12-infrastructure-common.md)、[09 §TFツリー（2台構成）](../shared/09-navigation-internals.md):259-273 は**そのまま**。
3. **2台系の実装資産も削除しない（凍結保存）。** `warehouse_traffic`（`aisle_locks` / `virtual_scan`）・`warehouse_nav2_bridge/head_on_injector.py`・negotiation engine・`warehouse_safety` の2台間距離監視は**コードを残し、1台構成では非発火のまま**とする。「動かさない」と「消す」を混同しない。**なお `collision_monitor` は VirtualScan 相互注入（2台系）だけが非発火であり、`/scan` 由来の L1 物理反射は1台でも現役で稼働する**（Decision 5 の「安全レイヤは1枚も減らさない」と同義）。
4. **1台運用は `config/warehouse.base.yaml` の `robots:` を単一ソースとして表現する**（[config:8-11](../../config/warehouse.base.yaml)）。ただし現状 `ws/src/warehouse_bringup/launch/nav2_bringup.launch.py:49` は `ROBOTS = ("bot1", "bot2")` の **literal ハードコード**であり、`:74` の `other = "bot2" if robot == "bot1" else "bot1"` は**ちょうど2台**を仮定している。**config→launch の単一ソース化は 1台先行スライスの実装項目**とする（凍結契約変更ではなく launch の実装修正）。
5. **Emergency Guardian は落とさない。** 2台間距離監視は1台では発火しないが、**バッテリー監視・blocked 検出・pose freshness guard**（[12 §freshness guard](../architecture/12-infrastructure-common.md):506-513）は1台でも必要。L0'（ホスト側シリアルドライバ送信直前の 0.3 m/s クランプ）も不変。**1台になっても安全レイヤは1枚も減らさない。**

## 得られるもの

- **未検証リスクを直列に潰せる。** M1 の distro（ADR-0008）・電源・シリアル driver・L0' クランプ・Nav2 パラメータ実測を、交通管理の変数を混ぜずに1つずつ確定できる。
- **実機ゲートが軽くなる。** [jetson/01](../jetson/01-fidelity-and-validation.md) の G2「micro-ROS 2台」（:101）と G6「WiFi 同時通信」（:105）は M1 1台では非適用（ゲート定義は消さず「本フェーズ N/A」として残す）。G4「Nav2×2」（:103）も Nav2×1 に軽くなり、Orin Nano 8GB のユニファイドメモリ制約に余裕が生まれる（Isaac ROS 知覚スタック＝[architecture/23](../architecture/23-perception-and-localization.md) を載せる余地。ADR-0008 の狙いと整合）。
- **凍結契約を1行も変えずに済む**（`dict[str, RobotState]` が台数非依存）。
- **sim は2台のまま維持できる。** sim 先行リリース版（[05 §先行リリース構成](../shared/05-video-storyboard.md)）は Gazebo なので台数制約が無い＝実機1台と sim 2台を同時に持てる。
- **ジオラマ再設計の自由度が上がる。** ジオラマは M1 実寸に合わせて作り直す方針（[04 §M1 影響](../shared/04-diorama-layout.md)）であり、1台なら 200mm 真隘路の「すれ違い不可」制約が当面クリティカルパスから外れる。

## トレードオフ / Trade-offs

- **「AIが2台を指揮できた」（技術的核心マイルストーン・[06](../architecture/06-implementation-phases.md):333）が今回フェーズでは達成されない。** LLM司令官の価値の一部（複数体の調停）は実機で示せない。
- **Mode A/B の交通管理・キャラLLM交渉（doc14）・min-separation ≥0.15m 系の実機実証が全面 defer。** いずれも2台以上でのみ発火する機能であり、実装済み資産（sim live 実証済み含む）は凍結資産として保持する。
- **動画構成が変わる。** [05](../shared/05-video-storyboard.md) の 2台前提カット（Bot1 待機・衝突回避成功率など）は実機素材としては撮れない。1台版の代替は「単騎巡回 + 障害物投入→迂回 + 思考ログ + LLM 4社比較 + 手挙げ召喚」。sim カットは2台のまま使えるが、**実機/sim の素材が別台数であることを視聴者に誤認させない編集規律**が新たに要る。
- **Phase 定義の意味がずれる。** [06](../architecture/06-implementation-phases.md) の Phase 2 後半（2台目 HW・:166-171）と Phase 3（:182-238）は**2台復帰フェーズの定義に格下げ**。今回のゴールは「Phase 1（実機1台）+ Phase 2 前半（SLAM+Nav2）+ LLM Bridge 実機接続」の合成になる。
- **2台に戻すコストは消えず、後ろにずれるだけ。** namespace 分離・Multi-Robot Costmap・2台目キャリブレーション・通信同時性検証はそのまま残る。1台前提で書いた launch/config が負債化しないよう Decision 4（config 単一ソース化）で予防する。

## Considered Options / 却下

- **2台のまま進め docs 無改訂**: 却下。実機が1台しか無い状態で正本が「2台」を主張し続けると docs と現実のドリフトが下流判断を汚染する。実機ゲート G2/G6 が「未達」のまま残り、ブロッカーと非適用を区別できなくなる。
- **2台目を即追加購入して当初計画を維持**: 却下（今回は）。M1 の一次リスク（distro / 閉ソース driver / 電源 / L0'）が**1台でも未検証**であり、未検証構成を2倍に増やすのは順序が逆。**1台目の bring-up 完了が2台目の購入判断の前提。**
- **sim だけ2台で進め、実機は1台（docs は2台のまま）**: 部分採用。sim の2台維持は**採用**。却下したのは「docs 無改訂」の部分＝実機の正本は1台と明記する。
- **2台系の設計 doc / コードを削除して1台構成に整理**: **明確に却下**。SimpleTrafficManager・negotiation engine・HeadOnInjector・collision_monitor 配線・§9 yield トポロジは live 実証まで到達した資産。消せば後続フェーズで作り直しになる。「起動しない」で足りる。
- **`bot2` を config から消す（`robots:` を1要素に）**: **保留（実装スライスで判断）**。`config/warehouse.base.yaml` は単一所有の共有ファイル（parallel-workflow §7.1・所有=bringup/skeleton）で所有トラック調整が必要であり、`tests/unit/test_collision_monitor_launch.py:93` の `== 2` ハード assert と `nav2_bringup.launch.py:49` の literal の非対称が現存する。Decision 4 の単一ソース化と合わせて実装時に決着させる。

## Open / 未決

- `# TODO(ユーザー判断)` **初回公開の必須ゲートの再定義。** [06](../architecture/06-implementation-phases.md):225 の「モードA/Bが動作することを初回公開の必須ゲートとする」は1台実機では満たせない。(a) ゲートを「1台 + LLM司令官 + 障害物回避」に置換 / (b) 初回公開を2台復帰後に延期し sim 先行リリース版のみ公開 — のいずれか。
- `# TODO(ユーザー判断)` **2台目の購入タイミングと判断基準**（1台 bring-up のどのゲートを通ったら発注するか）。
- `# TODO(実装スライス)` `nav2_bringup.launch.py:49,74` の config 単一ソース化と、台数依存 assert（`test_collision_monitor_launch.py:93`）の扱い。
- `# TODO(実装確認)` 1台運用時の LLM プロンプト（[08a](../mode-a/08a-llm-bridge-mode-a.md):234「倉庫ロボット**2台**の司令官AI」）が存在しない bot2 を幻視しないか。プロンプト側の1台対応は実装課題として切り出す。

## References

- [ADR-0008 ROS 2 distro を Jazzy から Humble へ](0008-ros2-distro-humble-for-rosmaster-m1.md) — M1 + Orin + Isaac ROS 3.x 経路
- [02-hardware-design §ROSMASTER M1 採用検討時の残課題 / §給電の配線設計](../shared/02-hardware-design.md)
- [06-implementation-phases §Phase 2/3 / §マイルストーン:333](../architecture/06-implementation-phases.md)
- [05-video-storyboard §先行リリース構成](../shared/05-video-storyboard.md)
- [jetson/01-fidelity-and-validation §G2/G4/G6](../jetson/01-fidelity-and-validation.md):101-105
- [warehouse_interfaces/schemas.py:104,130](../../ws/src/warehouse_interfaces/warehouse_interfaces/schemas.py)（`robots: dict[str, ...]` ＝台数非依存）
- [config/warehouse.base.yaml:8-11](../../config/warehouse.base.yaml)（`robots: bot1, bot2`）
- [architecture/23-perception-and-localization.md](../architecture/23-perception-and-localization.md)（単騎構成の知覚・自己位置 TARGET 設計）
- [mode-x-er/09-hand-raise-summon.md](../mode-x-er/09-hand-raise-summon.md)（1台で成立する手挙げ召喚デモ）
- [dev/03-retrospectives.md](../dev/03-retrospectives.md)（#165 末尾追記規律）
- [docs/GLOSSARY.md §11](../GLOSSARY.md)（単騎構成 の正準用語）
