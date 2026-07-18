# 二層シミュレーション分担の正本（Gazebo / Isaac Sim）

作成日: 2026-07-15

> **状態**: 二層シミュレーション分担の正本。具体 scene・DR パラメータ範囲・RunPod 時間予算・fine-tune データ量は**未凍結**（§未凍結）。本 doc は分担の原理と適用範囲のみを固定し、数値は下流 doc（[01](01-isaac-sim-verification-gate.md) / [02](02-synthetic-data-and-domain-randomization.md)）と実測に委ねる。

## なぜ二層か

司令官（commander）の**入力の種類**が、シミュレーションの役割を決める。

- **situation JSON 入力（Mode A/C）**: 司令官は構造化された状況 JSON を受け取り、コマンドを返す。この JSON は sim レンダリング**無しでも合成できる**（fake state・fixture で足りる）。よって sim の描画は司令官のテスト入力**ではない**。sim は物理・Nav2・交通を回す開発ループの道具にとどまる。
- **pixel 入力（Mode X-ER / X-ER-VLA）**: 司令官（Gemini Robotics-ER）は**俯瞰画像そのもの**を入力に取る（[../mode-x-er-vla/01-integration-architecture.md:22](../mode-x-er-vla/01-integration-architecture.md)：`audio / transcript / overhead image / state -> Gemini Robotics-ER`）。この瞬間、sim が描くピクセル **＝ 司令官のテスト入力そのもの**になる。赤箱/青箱/robot pose/aisle/shelf を識別できるか等、検証すべき観点は [../mode-x-er-vla/03-simulation-and-safety-gates.md:47](../mode-x-er-vla/03-simulation-and-safety-gates.md)-55「Isaac Sim で見る観点」が列挙している。

**結論（中核テーゼ）**: 入力が JSON の間、sim レンダリング ≠ テスト入力。入力が pixel になった瞬間、sim レンダリング＝テスト入力。ゆえに Isaac Sim は「映像ツール」から**検証基盤**へ格上げされる。

## 二層分担

| 層 | シミュレータ | 役割 | 実行環境 |
|---|---|---|---|
| 開発ループ | **Gazebo Harmonic** | 物理・Nav2・交通・2台協調を決定的かつ速く回す | Docker on Mac（CPU・ARM64-native。[../architecture/03-software-architecture.md:263](../architecture/03-software-architecture.md)） |
| 投入前ゲート ＋ fixture 工場 | **Isaac Sim 5.1** | pixel 入力の photorealistic 検証・golden fixture 生成 | RunPod A10G（RT コア必須。[../shared/07-research-notes.md:107](../shared/07-research-notes.md)-112,:127） |

- Gazebo は CPU で決定的・速いため、開発の内側ループに向く。物理・Nav2 の妥当性は situation JSON さえ合成できれば sim 描画に依存しない。
- Isaac Sim は RT コア必須（A100/H100 不可、A10G/L4/RTX 4090 可。[../shared/07-research-notes.md:107](../shared/07-research-notes.md)-112）で photorealistic。pixel をテスト入力にできる唯一の層であり、投入前ゲートと fixture 工場を担う。Isaac Sim 5.1 は GA（[:127](../shared/07-research-notes.md)）。
- Gazebo↔Isaac 間の資産共有方式は**未凍結**（§未凍結）。

## D1 適用範囲

- **Mode A/C は Gazebo で足りる**: 司令官入力が situation JSON ゆえ、pixel 検証は不要。合成 JSON + Gazebo の開発ループで完結する。
- **Mode X-ER / X-ER-VLA から Isaac Sim 必須**: 司令官入力が pixel（俯瞰画像）になり、sim 描画がテスト入力そのものになるため、photorealistic な Isaac Sim でなければ検証にならない。

## D2 用途分割（映像 optional / 検証 カット不可）

Isaac Sim には**別々の用途**があり、それぞれ扱いが異なる。

- **映像・撮影用途（Before/After・デジタルツイン映像）＝ optional のまま**: R-18「Isaac Sim なしでも動画成立」（[../shared/07-research-notes.md:183](../shared/07-research-notes.md)）と推奨アクション #4「Phase 5 を完全オプション化」（[:214](../shared/07-research-notes.md)）は、**映像制作に関してのみ真**。Phase 5 を「確定オプション」とする [../architecture/06-implementation-phases.md:281](../architecture/06-implementation-phases.md)（および [:349](../architecture/06-implementation-phases.md)）の逃げ道も、この映像用途に閉じる。
- **検証基盤用途＝ カット不可**: pixel 入力モード（X-ER/X-ER-VLA）では sim 描画がテスト入力そのものであり、検証をカットすれば「投入前に pixel を一度も検証していない」状態になる。R-16（[../shared/07-research-notes.md:157](../shared/07-research-notes.md)）が示すスケジュール緩和策としての「Isaac Sim カット」も、**映像制作カット限定**であり検証には非適用。

この用途分割の hard-to-reverse な決定は [../adr/0005-isaac-sim-as-verification-gate.md](../adr/0005-isaac-sim-as-verification-gate.md) に記録する。

## Isaac Sim が検証する層（layer 明記）

Isaac Sim の sim-only replay（[../mode-x-er-vla/03-simulation-and-safety-gates.md:19](../mode-x-er-vla/03-simulation-and-safety-gates.md) の **G4 sim-only replay**。この G0-G7 ladder を本サブツリーでは **Ladder S** と呼ぶ）は、pixel/state を入口にソフトウェアの鎖 **L4 → L3 → L2 → L1** を動かし、実機なしで failure を再現・reject できるかを見る。

- L4（入力・知覚／オーケストレーション）→ L3（司令・検証 Planning Core）→ L2（実行許可・交通管理）→ L1（自律走行・安全）。各層の定義は [../GLOSSARY.md](../GLOSSARY.md)（§3 レイヤ L0–L4）に一致させる。
- **L0（ESP32 firmware の速度クランプ・物理安全）と物理そのものには触れない**。L0 は MCU 常駐で実機にしか存在せず、実機 fidelity ゲート（**別系統** G0-G7＝[../jetson/01-fidelity-and-validation.md:99](../jetson/01-fidelity-and-validation.md)-106。Ladder S とは別ラダー、特に G0 安全）で確定する。

詳細（ゲート運用・fixture 生成手順）は [01-isaac-sim-verification-gate.md](01-isaac-sim-verification-gate.md) へ委譲する。

## 未凍結

数値・具体仕様は docs に無いため発明しない。以下は未凍結として明示列挙する。

- Isaac scene の具体（レイアウト・オブジェクト・カメラ配置）
- domain randomization パラメータの範囲
- RunPod の時間予算（A10G 稼働時間・コスト上限）
- Gazebo ↔ Isaac Sim の資産共有方式（URDF/USD 変換・座標系整合）
- VLA fine-tune 用の合成データ量（詳細射程は [02-synthetic-data-and-domain-randomization.md](02-synthetic-data-and-domain-randomization.md) が本体）

## References

- サブツリー索引: [README.md](README.md)
- Isaac Sim 検証ゲート詳細: [01-isaac-sim-verification-gate.md](01-isaac-sim-verification-gate.md)
- 合成データ・DR: [02-synthetic-data-and-domain-randomization.md](02-synthetic-data-and-domain-randomization.md)
- 構成図・開発環境（Gazebo Harmonic）: [../architecture/03-software-architecture.md:263](../architecture/03-software-architecture.md)
- Phase 5（Isaac Sim・確定オプション）: [../architecture/06-implementation-phases.md:281](../architecture/06-implementation-phases.md)
- GPU 制約・R-16/R-18・推奨#4: [../shared/07-research-notes.md](../shared/07-research-notes.md)（[:107](../shared/07-research-notes.md)-112,:127,:157,:183,:214）
- Mode X-ER-VLA 位置づけ: [../mode-x-er-vla/README.md](../mode-x-er-vla/README.md)
- ER 入力（俯瞰画像）・data flow: [../mode-x-er-vla/01-integration-architecture.md:22](../mode-x-er-vla/01-integration-architecture.md)
- Ladder S（G0-G7・sim/offline ゲート）: [../mode-x-er-vla/03-simulation-and-safety-gates.md](../mode-x-er-vla/03-simulation-and-safety-gates.md)（[:19](../mode-x-er-vla/03-simulation-and-safety-gates.md),:47-55）
- 別系統 G0-G7（実機 fidelity ゲート）: [../jetson/01-fidelity-and-validation.md:99](../jetson/01-fidelity-and-validation.md)-106
- ADR（Isaac Sim を検証ゲートへ格上げ）: [../adr/0005-isaac-sim-as-verification-gate.md](../adr/0005-isaac-sim-as-verification-gate.md)
- 用語集 §11「シミュレーション / 検証基盤」・§3 レイヤ L0–L4: [../GLOSSARY.md](../GLOSSARY.md)
