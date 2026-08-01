# Isaac Sim 投入前検証ゲート（sim = ロボットの CI/CD）

作成日: 2026-07-15

> **状態**: 設計スケルトン（本 round の並列 sibling）。中核テーゼの本体は [docs/sim/00](00-simulation-platform-strategy.md)（round-sibling）に置き、本 doc は **Ladder S（ER/VLA sim→robot gate・[mode-x-er-vla/03](../mode-x-er-vla/03-simulation-and-safety-gates.md)）への 1:1 接続**と **layer 明記**に集中する。sim-only evaluator の具体・fixture フォーマット・CI 配線先・改名の採否は未凍結（[§未凍結](#未凍結)）。docs に無い契約・topic・schema・threshold は発明しない（[docs-first.md](../../.claude/rules/docs-first.md)）。

## 一枚要約

- 司令官入力が **situation JSON** の間は、sim は環境を回すだけで **sim ≠ テスト入力**（司令官はテキストを読んでいる）。
- 司令官入力が **pixel（画像）** になると、**sim レンダリング＝テスト入力そのもの**になり、Isaac Sim は「見た目」ではなく **検証基盤（ロボットの CI/CD）** へ格上げされる。テーゼ本体・論証は [docs/sim/00](00-simulation-platform-strategy.md) が正本（本 doc はコピーせずリンクで受ける）。
- ゆえに本 doc は、ER/VLA 系の実機投入前ゲート表 **Ladder S**（[mode-x-er-vla/03:13-22](../mode-x-er-vla/03-simulation-and-safety-gates.md)）を **CI/検証の観点で受け直し**、特に **G4 sim-only replay** を詳述する。

## Ladder S との 1:1 接続

Ladder S（[mode-x-er-vla/03:13-22](../mode-x-er-vla/03-simulation-and-safety-gates.md) の表）を、「各 gate が **何を検証し／何を検証しないか**」の観点で受ける。gate の内容・完了条件は 03 が正本（下表は派生ビュー・逐語コピーではない）。

| 記号 | Ladder S gate | 検証する | 検証しない | 正本 |
|---|---|---|---|---|
| G0 | fixture | ER/VLA に recorded image+text+fake state を入れ response を保存できるか | 実機・物理・latency 実測 | [03:15](../mode-x-er-vla/03-simulation-and-safety-gates.md) |
| G1 | output classification | VLA response の shape（grounding/action/trajectory/unknown）分類 | shape が安全かどうか | [03:16](../mode-x-er-vla/03-simulation-and-safety-gates.md) |
| G2 | fusion decision | ER と VLA output の統合方式（cross-check / L3 candidate / Safety Compiler） | 統合結果の実走妥当性 | [03:17](../mode-x-er-vla/03-simulation-and-safety-gates.md) |
| G3 | offline validator | invalid robot/action/target/stale/emergency を **0 dispatch** にできるか（`ValidationReport` 相当で reject 理由を残す） | 物理停止（それは L1/L0） | [03:18](../mode-x-er-vla/03-simulation-and-safety-gates.md) |
| **G4** | **sim-only replay** | **Isaac Sim または offline evaluator で ER/VLA candidate を replay し、実機なしで危険な failure を再現・reject できるか**（[§G4 の層](#g4-が動かす層と動かさない層layer-明記)） | L0 firmware clamp・物理ロボット | [03:19](../mode-x-er-vla/03-simulation-and-safety-gates.md) |
| G5 | compiler decision | `Command Compiler` で足りるか `SafetyCompiler` が要るか | compiler の実機挙動 | [03:20](../mode-x-er-vla/03-simulation-and-safety-gates.md) |
| G6 | MCP path | command candidate が MCP / Policy Gate を **bypass せず**通るか | Policy 判定後の実駆動 | [03:21](../mode-x-er-vla/03-simulation-and-safety-gates.md) |
| G7 | robot-gated | 実機接続を人間 gate にする（Jetson/ESP32/safety 確認後のみ）＝**L1/L0 物理境界の人間 gate** | （sim/offline では通せない・人間判断） | [03:22](../mode-x-er-vla/03-simulation-and-safety-gates.md) |

**G4（sim-only replay）詳述**: G4 は Ladder S の中で「pixel をテスト入力に変える」rung。ER/VLA candidate を Isaac Sim レンダリング（または offline evaluator）に流し、**実機を一切駆動せずに** 危険な output を再現し、下流の deterministic validator/compiler が reject できることを確かめる。ここで発掘した failure を **golden fixture 化**して決定的 CI へ落とす（[§CI](#ci-としての現実解-決定的-floor-と-fixture-工場)。Isaac Sim が供給する合成データ・domain randomization は [docs/sim/02](02-synthetic-data-and-domain-randomization.md)）。G4 は「危険を再現する」ことが目的で、「実機で安全を保証する」ことは **しない**（それは G7）。

## G4 が動かす層と、動かさない層（layer 明記）

layer 番号は [GLOSSARY §3:29-37](../GLOSSARY.md)（L0–L4）と一致させる。file-level 対応は [productization/01:180-187](../productization/01-commercial-box-map.md)（レイヤ annotation 対応表）。

**G4 sim-only replay が動かす鎖**（実機なしで回る上位安全の全経路）:

- **L4** ER/VLA 生 output（RawModelOutput）— sim/fixture 画像から生成。[GLOSSARY §3:33](../GLOSSARY.md) / [productization/01:182](../productization/01-commercial-box-map.md)
- **↓ L3** Planning Core: Handoff → Validator → Visual Resolver → Task Graph Executor → Command Compiler。[GLOSSARY §4:42](../GLOSSARY.md)（data flow 正本）/ [productization/01:183](../productization/01-commercial-box-map.md)（file 実体）。Ladder S の G3 offline validator（[03:18](../mode-x-er-vla/03-simulation-and-safety-gates.md)）・G5 compiler decision（[03:20](../mode-x-er-vla/03-simulation-and-safety-gates.md)）がここ。
- **↓ L2** Policy Gate / MCP Governance（accepted motion だけ通す）。[GLOSSARY §3:35](../GLOSSARY.md) / [productization/01:184](../productization/01-commercial-box-map.md)。Ladder S の G6 MCP path（[03:21](../mode-x-er-vla/03-simulation-and-safety-gates.md)）がここ。
- **↓ L1** Nav2 / collision_monitor（実走・物理停止）。[GLOSSARY §3:36](../GLOSSARY.md) / [productization/01:185](../productization/01-commercial-box-map.md)。

**G4 が動かさない層（触れてはならない）**:

- **L0** ESP32 firmware の速度 clamp ≤0.3 m/s・近接停止（MCU/即時）。[GLOSSARY §3:37](../GLOSSARY.md) / [productization/01:186](../productization/01-commercial-box-map.md)。
- **物理ロボット**。G4 は sim/offline のみ。ER/VLA output を `/cmd_vel` や Nav2 action に直接流さず、**L0 firmware clamp を前提に上位安全を省略しない**（[03:26-27,29](../mode-x-er-vla/03-simulation-and-safety-gates.md) 実機接続前に禁止すること）。

**L1/L0 物理境界の人間 gate = G7 robot-gated**。実機接続は Jetson/ESP32/safety 確認後のみ・人間判断（[03:22](../mode-x-er-vla/03-simulation-and-safety-gates.md)）。実機へ進む条件（MCP/Policy Gate を bypass しない・Layer0 の優先順位を変えない）は [03:65-66](../mode-x-er-vla/03-simulation-and-safety-gates.md)。sim（G4）は上位 L4→L1 を検証するが、L0 と物理は G7 の人間 gate を越えてからしか触れない。

## CI としての現実解: 決定的 floor と fixture 工場

- **floor（毎回の CI ゲート）＝決定的 fixture replay**。同じ入力→同じ判定を保証する（G0 fixture〔[03:15](../mode-x-er-vla/03-simulation-and-safety-gates.md)〕→ G3 offline validator〔[03:18](../mode-x-er-vla/03-simulation-and-safety-gates.md)〕の deterministic 経路）。CI が緑/赤を再現可能に判定できるのはこの層。
- **Isaac Sim ＝非決定でよい「fixture 工場」**。pixel レンダリングは run ごとにピクセルが揺れて非決定でよい。役割は **新しい failure を発掘し、それを golden fixture に落とす**こと（G4 sim-only replay〔[03:19](../mode-x-er-vla/03-simulation-and-safety-gates.md)〕・「failure case を golden fixture にできるか」[03:55](../mode-x-er-vla/03-simulation-and-safety-gates.md)）。
- **設計理由**: **pixel 非決定性を CI ゲートに持ち込まない**。非決定な Isaac Sim を毎 PR の合否判定に置くと赤/緑が揺れて gate が壊れる。だから CI floor は決定的 fixture、Isaac Sim は floor の外で failure を発掘し fixture を供給する非同期の工場、と役割を分離する。golden fixture 化の概念は [mode-x-er-vla/03:55](../mode-x-er-vla/03-simulation-and-safety-gates.md)（「failure case を golden fixture にできるか」）、工場が供給する合成データ・domain randomization の射程は [docs/sim/02](02-synthetic-data-and-domain-randomization.md) が正本（本 doc はリンクで委譲）。

## G0-G7 名前衝突の注意喚起

同じ **G0–G7** 記号を、**別物の 2 つの梯子**が使っている（両方 docs に実在）。読み間違えると「sim で通した」と「実機 bench で通した」を取り違える。

| 記号 | **Ladder S**（本 doc・ER/VLA sim→robot・[mode-x-er-vla/03:15-22](../mode-x-er-vla/03-simulation-and-safety-gates.md)） | **Ladder H**（Jetson 実機 bring-up・[jetson/01:99-106](../jetson/01-fidelity-and-validation.md) ＋抜粋 [architecture/19:152-157](../architecture/19-environments-and-config.md)） |
|---|---|---|
| G0 | fixture | 安全（Layer0 clamp ≤0.3 m/s・近接 e-stop / Guardian unit） |
| G1 | output classification | メモリ（`free -h` 残RAM ≥500MB・Open-RMF Go/No-Go） |
| G2 | fusion decision | micro-ROS 2台（distinct `client_key`・WiFi UDP 双方向） |
| G3 | offline validator | 実時間性 jitter（50ms Guardian / 100ms State Cache p99/max） |
| **G4** | **sim-only replay（Isaac Sim/offline evaluator）** | **nav2/SLAM 性能＋熱スロットリング（`tegrastats` 持続負荷）** |
| G5 | compiler decision | 実センサ精度（MS200 測距 / encoder / battery scale） |
| G6 | MCP path | WiFi 同時通信（micro-ROS×2 + LLM API + scan） |
| **G7** | **robot-gated（実機接続の人間 gate）** | **Hermes(GCP) 到達 + 司令官サイクル E2E** |

> **特に紛らわしい**: **G4** は「sim replay（上位安全の検証）」対「nav2/SLAM+熱（実機ハード性能）」で完全に別物。**G7** は「実機接続の人間 gate」対「Hermes E2E」でこれも別物。sim/検証の文脈ではどちらも自然に聞こえるため取り違えやすい。Ladder H は G0/G1/G2/G3/G4/G7 のみが [architecture/19:152-157](../architecture/19-environments-and-config.md) に抜粋され、全 G0–G7 は [jetson/01:97-106](../jetson/01-fidelity-and-validation.md) が正本。

**改名案（未凍結の提案・本 doc では実施しない）**: 記号衝突を恒久解消するなら、Ladder S を **VG0..VG7**（verification gate）、Ladder H を **HG0..HG7**（hardware bring-up）へ改名する案がある。これは提案の記録に留め、[mode-x-er-vla/03](../mode-x-er-vla/03-simulation-and-safety-gates.md) / [jetson/01](../jetson/01-fidelity-and-validation.md) / [architecture/19](../architecture/19-environments-and-config.md) を**本 doc から書き換えない**（採否は別 PR）。競合する「番号体系」を読み替える先例＝[productization/11:30](../productization/11-l2-contract-governance-traffic-box.md)（§レイヤ番号の対応・軸が違う番号は裸で書かず対応表を正とする）。

## 未凍結

- **sim-only evaluator の具体**: Isaac Sim か offline evaluator か（Ladder S G4 は「Isaac Sim **または** offline evaluator」＝[03:19](../mode-x-er-vla/03-simulation-and-safety-gates.md)。どちらを floor 供給に使うか未確定）。
- **fixture フォーマット**: golden fixture のスキーマ・保存形式（`ValidationReport` 相当の shape は [03:18](../mode-x-er-vla/03-simulation-and-safety-gates.md) 参照だが凍結スキーマは未定）。工場側の詳細は [docs/sim/02](02-synthetic-data-and-domain-randomization.md)。
- **CI 配線先**: 決定的 fixture replay を回す CI job / gate の配線先（未凍結・数値 threshold も未定＝発明しない）。
- **改名の採否**: 上記 VG/HG 改名の採否（本 doc は提案のみ）。

## References

- [docs/sim/README](README.md) — sim サブツリー索引（本 doc を索引・二層分担表）
- [docs/sim/00](00-simulation-platform-strategy.md) — 中核テーゼ「sim = ロボットの CI/CD」本体（round-sibling・着地済）
- [docs/sim/02](02-synthetic-data-and-domain-randomization.md) — 合成データ生成・domain randomization・VLA fine-tune 用データ射程（Isaac Sim が floor に供給するデータ・round-sibling）
- [mode-x-er-vla/03-simulation-and-safety-gates.md](../mode-x-er-vla/03-simulation-and-safety-gates.md) — **Ladder S** の正本（本 doc が 1:1 接続。03 側に本 doc への forward link あり）
- [mode-x-er-vla/README.md](../mode-x-er-vla/README.md) — Mode X-ER-VLA 位置づけ・安全境界（[:17-23](../mode-x-er-vla/README.md)）
- [jetson/01-fidelity-and-validation.md](../jetson/01-fidelity-and-validation.md) — **Ladder H** の正本（[:97-106](../jetson/01-fidelity-and-validation.md) 全 G0–G7）
- [architecture/19-environments-and-config.md](../architecture/19-environments-and-config.md) — Ladder H 抜粋（[:148-160](../architecture/19-environments-and-config.md) §7.2・G0/G1/G2/G3/G4/G7）
- [productization/01-commercial-box-map.md](../productization/01-commercial-box-map.md) — レイヤ annotation 対応表（[:174-187](../productization/01-commercial-box-map.md)）
- [productization/11-l2-contract-governance-traffic-box.md](../productization/11-l2-contract-governance-traffic-box.md) — §レイヤ番号の対応（[:30](../productization/11-l2-contract-governance-traffic-box.md)・競合番号体系の読み替え先例）
- [GLOSSARY.md](../GLOSSARY.md) — §3 レイヤ（[:29-37](../GLOSSARY.md) L0–L4・裏取り済）／§4 L3 seam（[:42](../GLOSSARY.md)）／§11 シミュレーション・検証基盤（本 round で追加中・§ form）
- [adr/0006](../adr/0006-isaac-sim-as-verification-gate.md) — Isaac Sim を検証基盤へ格上げする決定記録（round-sibling・着地済）
