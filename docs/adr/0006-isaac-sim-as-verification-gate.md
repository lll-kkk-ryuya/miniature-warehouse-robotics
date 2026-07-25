# Isaac Sim を映像用オプションから投入前検証ゲート（Mode X-ER/VLA から必須）へ格上げする

**Status**: accepted（2026-07-15）

Isaac Sim を「間に合わなければカットできる映像用オプション」から、**投入前検証ゲート（Mode X-ER/VLA から必須）＋ fixture 工場**へ格上げし、**映像用途のみ optional として残す**、という決定。理由は、司令官入力が situation JSON である間は sim ≠ テスト入力だが、**司令官入力が pixel になると sim レンダリングがテスト入力そのものになる**ため（中核テーゼ）。正本の展開は [sim/00](../sim/00-simulation-platform-strategy.md)。

## Context / 背景

Mode A/C は司令官（LLM）入力が **situation JSON**（構造化状態）であり、sim の見た目は司令官の意思決定に入らない。この間は Gazebo Harmonic（CPU・決定的・物理/Nav2 の開発ループ）で足りる（[architecture/03-software-architecture.md:263](../architecture/03-software-architecture.md) の tiryoh/Gazebo Harmonic 開発環境）。

一方 Mode X-ER / VLA は司令官入力が **pixel（画像）** になる。ここで sim のレンダリング品質＝**テスト入力の質**になり、「pixel を見て危険な output を出す/出さない」を実機・L0 に触れず検証する基盤が必要になる。Isaac Sim（5.1・photorealistic・RT コア）はこの pixel テスト入力を生成できる唯一の platform だが、RT コア必須で A100/H100 は不可・A10G/L4/RTX 4090 系のみ（[shared/07-research-notes.md:107-112](../shared/07-research-notes.md)）、クラウドは RunPod A10G を用いる。

従来の doc は Isaac Sim を Phase 5 の「確定オプション＝間に合わなければカット」と位置づけていた（[architecture/06-implementation-phases.md:281](../architecture/06-implementation-phases.md)）。本 ADR はこの位置づけを、pixel 入力モードに対しては覆す。正本は [sim/00](../sim/00-simulation-platform-strategy.md)・[sim/01](../sim/01-isaac-sim-verification-gate.md)・[sim/02](../sim/02-synthetic-data-and-domain-randomization.md)。

## Decision / 決定

Isaac Sim の役割を **二層に分割**して格上げする:

- **投入前検証ゲート（Mode X-ER/VLA から必須・カット不可）**: ER/VLA の生 output → L3 → L2 → L1 の鎖を実機・L0 に触れず replay し、危険 output を reject する gate 群の入力供給基盤（[sim/01](../sim/01-isaac-sim-verification-gate.md)）。検証梯子は Mode X-ER-VLA の G0–G7 gate 列（**Ladder S**。sim-only replay = G4）＝[mode-x-er-vla/03-simulation-and-safety-gates.md:15-22](../mode-x-er-vla/03-simulation-and-safety-gates.md)。
- **fixture 工場（fixture factory）**: Isaac Sim を「非決定でよく、新しい failure を発掘し golden fixture に落とす」生成器として使う（[sim/02](../sim/02-synthetic-data-and-domain-randomization.md)）。CI ゲート下限は決定的 replay floor（毎回同じ入力・同じ判定）で、pixel 非決定性を CI に持ち込まない（[sim/01](../sim/01-isaac-sim-verification-gate.md)）。
- **映像・撮影**は従来どおり optional のまま残す（R-16 の逃げ道は「映像制作カット」に限定）。

用途分割（D2）: 映像・撮影 = optional／検証基盤 = カット不可。モード適用（D1）: Mode A/C = Gazebo で足りる／Mode X-ER・VLA から Isaac Sim 必須。

## Considered Options / 却下

- **全モードで Isaac Sim 必須** → 却下。Mode A/C は司令官入力が situation JSON で、pixel テスト入力を必要としない。Gazebo で足りるところに RT コア GPU 前提の Isaac Sim を強制するのは過剰。
- **週数を延長して吸収（15週→20週）** → 却下。R-16（[shared/07-research-notes.md:157](../shared/07-research-notes.md)）のスケジュール risk は、週数延長ではなく **映像制作カット**で逃がすのが D2 の方針（Phase 5 の「Isaac Sim カット」で逃がすと検証基盤ごと消える）。
- **現状維持 = Phase 5 全体を optional のまま** → 却下。検証基盤まで optional にすると、pixel 入力モード（Mode X-ER/VLA）が **実機前 gate 無し**で実機に到達しうる。pixel を見て動く司令官の危険 output を実機前に止める層が消える。

## Consequences / 帰結

- **R-16 のスケジュール risk の逃げ道が「映像制作カット」に限定される**（[shared/07-research-notes.md:157](../shared/07-research-notes.md)）。「Isaac Sim を丸ごとカット」は Mode X-ER/VLA に対しては不可になる。R-18（Isaac Sim 環境構築の実現可能性・[shared/07-research-notes.md:183](../shared/07-research-notes.md)）は検証基盤側では逃がせない risk として残る。
- **RunPod A10G 課金**が検証基盤の恒常コストになる。GPU 制約は RT コア必須（A100/H100 不可・A10G/L4/RTX 4090 系のみ・[shared/07-research-notes.md:107-112](../shared/07-research-notes.md)）。
- **Ladder S（G0–G7・[mode-x-er-vla/03-simulation-and-safety-gates.md:15-22](../mode-x-er-vla/03-simulation-and-safety-gates.md)）が検証梯子として load-bearing** になる。sim-to-real gap（[mode-x-er-vla/03-simulation-and-safety-gates.md:52](../mode-x-er-vla/03-simulation-and-safety-gates.md)）は domain randomization で狭める（[sim/02](../sim/02-synthetic-data-and-domain-randomization.md)）。
- **能力の向上は権限の向上ではない（capability up ≠ authority up）**: VLA を合成データで fine-tune して grounding 精度が上がっても、それを理由に **L3 Validator / L2 Policy Gate / L0 firmware clamp を緩めない**。sim だけで学習した VLA は「自信を持って間違える」ため、検証ゲート（Ladder S）と安全境界は fine-tune 精度と**独立**に維持する。学習射程は局所操作（把持・配置・ドッキング・近接位置合わせ）限定で、移動は Nav2＝L1 が担う（[sim/02 §VLA fine-tune 射程](../sim/02-synthetic-data-and-domain-randomization.md):59-66 と対）。root＝[mode-x-er-vla/03-simulation-and-safety-gates.md:24-30](../mode-x-er-vla/03-simulation-and-safety-gates.md)（実機接続前に禁止すること・firmware clamp を前提に上位安全を省かない [:29](../mode-x-er-vla/03-simulation-and-safety-gates.md)）／L3=[:42](../mode-x-er-vla/03-simulation-and-safety-gates.md)・L2 Policy Gate bypass 禁止=[:65](../mode-x-er-vla/03-simulation-and-safety-gates.md)。
- 安全強制は依然 L2/L1/L0 に残る。sim は「ロボットの CI/CD」＝危険 output を実機前に発掘・reject する場であり、低レイヤ safety mechanism を代替しない。

## なぜ ADR か（3条件すべて成立）

- **hard-to-reverse**: この gate を後から外すと、pixel 入力モードが実機前検証無しで実機へ到達しうる（安全境界の後退）。
- **surprising without context**: 従来 doc（[architecture/06-implementation-phases.md:281](../architecture/06-implementation-phases.md)）は Isaac Sim = 映像用の「確定オプション＝カット可」と読める。「検証基盤としては必須」は文脈無しでは驚く。
- **real trade-off**: 「全モード必須」「週数延長で吸収」という現実的な代替が存在し、それぞれ具体的理由で却下した（上記 Considered Options）。

## References（`origin/main` で検証済み file:line。新規 sim doc は path のみ）

- 正本（新 sim サブツリー）: [sim/00-simulation-platform-strategy.md](../sim/00-simulation-platform-strategy.md)（二層分担・中核テーゼ）／[sim/01-isaac-sim-verification-gate.md](../sim/01-isaac-sim-verification-gate.md)（投入前検証ゲート・決定的 floor）／[sim/02-synthetic-data-and-domain-randomization.md](../sim/02-synthetic-data-and-domain-randomization.md)（fixture 工場・DR）／[sim/README.md](../sim/README.md)
- Gazebo 開発ループ: [architecture/03-software-architecture.md:263](../architecture/03-software-architecture.md)（tiryoh / Gazebo Harmonic）
- GPU 制約・Isaac Sim GA: [shared/07-research-notes.md:107-112](../shared/07-research-notes.md)（RT コア必須・A10G/L4/RTX 4090）・[:127](../shared/07-research-notes.md)（Isaac Sim 5.1 GA）
- スケジュール risk: [shared/07-research-notes.md:157](../shared/07-research-notes.md)（R-16 15週非現実的）・[:183](../shared/07-research-notes.md)（R-18 Isaac Sim 実現可能性）
- Phase 5 の従来位置づけ（映像用 optional）: [architecture/06-implementation-phases.md:281](../architecture/06-implementation-phases.md)（+ 一覧 [:17](../architecture/06-implementation-phases.md)）
- Ladder S（検証梯子）: [mode-x-er-vla/03-simulation-and-safety-gates.md:15-22](../mode-x-er-vla/03-simulation-and-safety-gates.md)（G0–G7・sim-only replay G4=[:19](../mode-x-er-vla/03-simulation-and-safety-gates.md)）・sim-to-real gap [:52](../mode-x-er-vla/03-simulation-and-safety-gates.md)・golden fixture [:55](../mode-x-er-vla/03-simulation-and-safety-gates.md)
- 用語: [GLOSSARY §11 シミュレーション / 検証基盤](../GLOSSARY.md)
- back-link 元: [adr/README](README.md) 一覧・[docs/README](../README.md) sim/ 節・[ADR-FORMAT](../../.claude/skills/domain-modeling/ADR-FORMAT.md) / [docs-first.md](../../.claude/rules/docs-first.md)
