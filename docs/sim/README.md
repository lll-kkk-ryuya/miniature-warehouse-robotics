# sim/ — 二層シミュレーション（Gazebo 開発ループ / Isaac Sim 検証基盤）

作成日: 2026-07-15

> **状態**: サブツリー索引。二層シミュレーション分担の横断正本。具体 scene・DR パラメータ範囲・RunPod 時間予算・fine-tune データ量は**未凍結**（[00 §未凍結](00-simulation-platform-strategy.md)）。

## 一枚要約（中核テーゼ）

司令官の入力が **situation JSON** の間は、sim レンダリング ≠ テスト入力である（JSON は sim 無しでも合成できる）。
入力が **ピクセル**になった瞬間、sim レンダリング **＝ テスト入力そのもの**になる。
ゆえに Isaac Sim は Mode X-ER / X-ER-VLA において「映像ツール」から**検証基盤**へ格上げされる。
Mode X-ER 司令官（Gemini Robotics-ER）が俯瞰画像を入力に取ることは
[../mode-x-er-vla/01-integration-architecture.md:22](../mode-x-er-vla/01-integration-architecture.md)（`overhead image -> Gemini Robotics-ER`）で裏取りできる。

詳細な理由づけ・適用範囲・用途分割は [00-simulation-platform-strategy.md](00-simulation-platform-strategy.md) を正本とする。

## Gazebo と Isaac Sim の分担表

| シミュレータ | 実行環境 | 決定性 | 何を検証 | どのモード | cut 可否 |
|---|---|---|---|---|---|
| **Gazebo Harmonic** | Docker on Mac（CPU・ARM64-native。[../architecture/03-software-architecture.md:263](../architecture/03-software-architecture.md)） | 決定的で速い | 物理・Nav2・交通・2台協調の**開発ループ** | Mode A/C（司令官入力＝situation JSON） | — |
| **Isaac Sim 5.1** | RunPod A10G（RT コア必須・photorealistic。[../shared/07-research-notes.md:107](../shared/07-research-notes.md)-112,:127） | 非決定でよい | pixel 入力の**投入前検証ゲート ＋ fixture 工場** | Mode X-ER・X-ER-VLA | **検証はカット不可・映像用途のみ optional** |

> GPU 制約: Isaac Sim は RT コア必須ゆえ A100/H100 は不可、A10G/L4/RTX 4090 が可（[../shared/07-research-notes.md:107](../shared/07-research-notes.md)-112）。Isaac Sim 5.1 は GA（[:127](../shared/07-research-notes.md)）。Gazebo Harmonic は CPU・ARM64-native で Mac Docker に載る（[../architecture/03-software-architecture.md:263](../architecture/03-software-architecture.md)）。
> 「映像 optional / 検証 カット不可」の根拠（R-16/R-18/推奨#4 の適用範囲）は [00 §D2](00-simulation-platform-strategy.md) と [../adr/0005-isaac-sim-as-verification-gate.md](../adr/0005-isaac-sim-as-verification-gate.md) を参照。

## サブツリー内 doc

| ファイル | 内容 |
|---|---|
| [00-simulation-platform-strategy.md](00-simulation-platform-strategy.md) | 二層分担の正本（なぜ二層か・D1 適用範囲・D2 用途分割・検証する層・未凍結） |
| [01-isaac-sim-verification-gate.md](01-isaac-sim-verification-gate.md) | Isaac Sim を投入前ゲート／fixture 工場として使う詳細 |
| [02-synthetic-data-and-domain-randomization.md](02-synthetic-data-and-domain-randomization.md) | 合成データ生成・domain randomization・VLA fine-tune 用データ射程 |

## 関連正本（forward link）

- 構成図・開発環境: [../architecture/03-software-architecture.md](../architecture/03-software-architecture.md)
- Phase 5（Isaac Sim・確定オプション）: [../architecture/06-implementation-phases.md](../architecture/06-implementation-phases.md)（[:281](../architecture/06-implementation-phases.md)）
- GPU 制約・R-16/R-18・推奨#4: [../shared/07-research-notes.md](../shared/07-research-notes.md)
- Mode X-ER-VLA 位置づけ: [../mode-x-er-vla/README.md](../mode-x-er-vla/README.md)
- ER 入力（俯瞰画像）・data flow: [../mode-x-er-vla/01-integration-architecture.md](../mode-x-er-vla/01-integration-architecture.md)
- G0-G7 gate ladder（**Ladder S**＝ER/VLA sim・offline 検証ゲート）: [../mode-x-er-vla/03-simulation-and-safety-gates.md](../mode-x-er-vla/03-simulation-and-safety-gates.md)
- **別系統** G0-G7（実機 fidelity ゲート・Jetson 到着後に確定。Ladder S とは別ラダー）: [../jetson/01-fidelity-and-validation.md](../jetson/01-fidelity-and-validation.md)
- ADR（Isaac Sim を検証ゲートへ格上げ）: [../adr/0005-isaac-sim-as-verification-gate.md](../adr/0005-isaac-sim-as-verification-gate.md)
- 用語集 §11「シミュレーション / 検証基盤」: [../GLOSSARY.md](../GLOSSARY.md)（§11）

## backlink（別 sub が張る）

このサブツリーは [../README.md](../README.md) の **「## sim/」節** と構成ツリーから backlink される（backlink 自体は別 sub が張る）。
