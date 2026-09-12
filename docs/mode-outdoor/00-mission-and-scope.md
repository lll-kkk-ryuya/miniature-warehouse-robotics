# 00 — ミッションとスコープ（歩道 A→B・GNSS 主体・完全自律）

作成日: 2026-09-12
Status: **箱（skeleton）**。節見出しと「何を書くか」だけを置く。裁定待ち項目（§4）は実体。

> 正本ルート: [mode-outdoor/README](README.md)。法規は [01](01-legal-envelope-japan.md)、分担は [02](02-architecture-split-orin-pc-cloud.md)。
> 旧ミッション（ミニチュア倉庫・2 台・LLM 司令官比較）は [shared/00](../shared/00-project-overview.md) が保持する。本 doc はそれを**置換せず、第一優先の対象を屋外へ移す**（オペレーター指示 2026-09-12）。

## 0. 位置づけ

- 何を: 屋外の**歩道**を通って、**特定の地点 A から特定の地点 B まで**、GNSS などを使い**完全自律**で走る（オペレーター指示の原文要旨）。
- なぜ: `# TODO(オペレーター)` 動画・営業資産・技術検証のどれを主目的にするか（[shared/05](../shared/05-video-storyboard.md) の再設計と連動）。
- 前提: 単騎 M1（[ADR-0006](../adr/0006-single-bot-first.md)）・Humble（[ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)）・最小安全方針（[GLOSSARY §11「最小安全方針」](../GLOSSARY.md)）。**ただし公道は自機保護の最小線を超える法定要件が床になる**（[05 §0](05-safety-envelope-and-intervention.md)）。

## 1. ミッション定義（何を書くか）

- `# TODO(設計)` A / B の定義（緯度経度・地物・目印）と「到達」の判定条件。
- `# TODO(設計)` 経路の性質（歩道のみ／横断歩道を含む／信号付き横断を含む）。横断の有無で [04](04-perception-sidewalk-and-signals.md) / [05 §4](05-safety-envelope-and-intervention.md) の要否が変わる。
- `# TODO(設計)` 成功指標（到達率・人手介入回数・平均速度・停止回数）。観測面（[architecture/22](../architecture/22-web-observability.md)）で取れる形にする。

## 2. スコープ IN / OUT（何を書くか）

| IN（案） | OUT（案） |
|---|---|
| 歩道・路側帯の通行、横断歩道の横断（信号あり／なし） | 車道走行、自転車道、階段 |
| 遠隔操作者（PC 卓）による監視・介入・手動 takeover | 遠隔操作者なしの完全無人運用（[01 §5](01-legal-envelope-japan.md) の自動操縦除外） |
| 物の運送（小型荷物箱・固定） | 人の運送 |
| 昼間・乾燥路面から開始 | 夜間・雨天（`# TODO(裁定)` 段階的に） |

## 3. 法的・運用の 2 段階（正本は [01 §5](01-legal-envelope-japan.md)）

1. **随伴フェーズ**: すぐに停止させられる距離にいる人がデッドマン付きゲームパッドで操作・停止できる形（法 2 条 3 項 1 号の歩行者扱い・届出不要・標識と非常停止装置は必要）。介入は物理的で最強。
2. **遠隔操作フェーズ**: PC 卓から監視・介入（法 15 条の 3 の届出・通行開始 1 週間前・標識と届出番号の表示）。
3. どちらも**私有地（一般交通の用に供されない閉鎖区画）での先行検証**を前段に置く（[01 §10](01-legal-envelope-japan.md)「道路」の定義に注意）。

## 4. オペレーター裁定待ち（決め方で設計が変わる 6 点）

| # | 裁定事項 | 推奨（2026-09-12 所見） | 影響先 |
|---|---|---|---|
| 1 | **走行環境の段階**: 私有地 → 随伴（公道・届出不要） → 遠隔操作（届出） の順にするか | この順で進める。届出はコースを番地まで特定する必要があるため、コース確定が先 | [01](01-legal-envelope-japan.md) / [05](05-safety-envelope-and-intervention.md) |
| 2 | **RTK を入れるか** | 入れる。単独 GNSS は数 m の誤差で歩道幅を超え、歩道内に留まる責務が知覚側へ全面移る | [03](03-localization-gnss-and-ekf.md) / [06](06-hardware-delta-and-base-selection.md) |
| 3 | **屋外知覚センサ** | passive stereo を推奨。HP60C（構造化光）は室内用へ格下げ | [04](04-perception-sidewalk-and-signals.md) / [06](06-hardware-delta-and-base-selection.md) |
| 4 | **横断の権威**: 遠隔者承認を必須にするか、完全自動化を目標に置くか | 承認必須で設計し、承認の自動化は後段（法規と整合） | [05 §4](05-safety-envelope-and-intervention.md) |
| 5 | **メカナムの扱い**: 平滑舗装限定で継続か、通常輪へ換装か、屋外ベースを別途選ぶか | A 案（M1 継続・私有地開発機）で進め、私有地実走後に B 案（屋外ベース）を判断 | [06 §4](06-hardware-delta-and-base-selection.md) |
| 6 | **契約と命名**: `KNOWN_LOCATIONS` の倉庫語彙（9 キー凍結）に屋外地点語彙を additive に足す contract PR が要るか。モード正準名 | 正準名は **Mode Outdoor**（[GLOSSARY §12](../GLOSSARY.md)）。地点語彙は additive 提案（contract ラベル） | [ADR-0009 Decision 5](../adr/0009-m1-room-scale-operation.md) / `warehouse_interfaces` |

## 5. 流用する既存資産（仕分け・草案）

| 対象（layer） | 屋外での扱い |
|---|---|
| `m1_driver` clamp / watchdog W-1・W-2（L0'） | 不変・流用（[mode-m1/02](../mode-m1/02-m1-driver-and-watchdog.md)） |
| Nav2 MPPI / collision_monitor / twist_mux / Emergency Guardian（L1） | 流用。costmap 入力センサだけ屋外向けに差替（[04](04-perception-sidewalk-and-signals.md)） |
| ゲームパッド deadman・非常停止 latch・stop_request 経路 B（L1 producer） | 流用。遠隔卓からの停止・リンク断を同じ契約に載せる（[05](05-safety-envelope-and-intervention.md)） |
| Policy Gate / audit / idempotency（L2） | 流用。横断許可ゲートの置き場所 |
| Planning Core（L3） | 流用。task graph = waypoint 列 + 横断ノード（地点語彙は §4-6） |
| LLM Bridge / Hermes / Mode X-ER の Gemini ER（L4） | 流用。役割を例外処理・状況説明・遠隔者向け要約に限定（信号判定の唯一の権威にしない） |
| web_bridge / console / Langfuse / eval_sdk（観測面） | 流用・拡張 = 遠隔操作者卓の土台（[02](02-architecture-split-orin-pc-cloud.md)） |
| SLAM Toolbox / AMCL（自律走行層） | 屋外主経路から外す。EKF 単一所有・MOLA-LO 第 2 入力の構造は生かし global EKF を足す（[03](03-localization-gnss-and-ekf.md)） |
| ジェスチャ召喚 / standby HRI / 速度帯セレクタ | **第一優先から外し凍結保存**（削除しない。ADR-0014 で裁定） |
| 2 台 traffic / RMF / ジオラマ | 凍結のまま（[ADR-0006](../adr/0006-single-bot-first.md) / [ADR-0009](../adr/0009-m1-room-scale-operation.md)） |

## 6. OPEN QUESTIONS（接頭辞 `OQ-OD*`）

- `OQ-OD1` 主目的（動画 / 営業 / 技術検証）の優先順位。
- `OQ-OD2` 最初のコース（私有地候補・公道候補）と、届出に書く「通行場所（番地まで）」。
- `OQ-OD3` 成功指標と観測の取り方。

## References

- [mode-outdoor/README](README.md) / [01 法規包絡](01-legal-envelope-japan.md) / [02 分担](02-architecture-split-orin-pc-cloud.md)
- [shared/00-project-overview.md](../shared/00-project-overview.md)（旧ミッション・置換しない）/ [shared/05-video-storyboard.md](../shared/05-video-storyboard.md)（撮影構成の再設計）
- [ADR-0009](../adr/0009-m1-room-scale-operation.md) / [ADR-0006](../adr/0006-single-bot-first.md) / [ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)
- [GLOSSARY §12](../GLOSSARY.md)
