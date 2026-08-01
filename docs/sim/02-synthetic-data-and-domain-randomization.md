# 合成データ生成と domain randomization（fixture 工場の中身）

作成日: 2026-07-15

> **状態**: [sim/01](01-isaac-sim-verification-gate.md) が定義する「fixture 工場」の中身の設計。DR パラメータ範囲・fine-tune データ量/学習設定・OpenVLA 採用・ラベル生成ツール・sim 資産の具体・real データ収集方法は**すべて未凍結**（軸と原則のみ定義し、数値は決めない）。

## 位置づけ

[sim/01](01-isaac-sim-verification-gate.md) は Isaac Sim を「非決定でよく、新しい failure を発掘し golden fixture に落とす**生成器（fixture 工場）**」として位置づけ、その出力を消費する**決定的 floor**（CI で毎回同じ入力・同じ判定の測定器）を定義する。本 doc はその**工場の中身**——何をどう randomize し、どのラベルを自動生成し、発掘した failure をどう決定的 fixture へ落とし、学習/評価をどう分けるか——を具体化する。決定的 floor 側の gate 定義は [sim/01](01-isaac-sim-verification-gate.md) を、VLA 接続前の gate は [mode-x-er-vla/03](../mode-x-er-vla/03-simulation-and-safety-gates.md) を正本とし、ここでは繰り返さない（リンクで受ける）。

## domain randomization の軸

randomize する軸を列挙する。**各軸の具体的な数値範囲は未凍結**（軸の列挙のみ・範囲は floor の決定一致率を見ながら後で凍結）。入力は俯瞰（overhead）カメラ画像を主に想定するため（overhead / robot-mounted の最終選択は [mode-x-er-vla/03:49](../mode-x-er-vla/03-simulation-and-safety-gates.md) で未決）、揺らす軸は**視覚とカメラに寄せる**:

- **照明・マテリアル・背景クラッタ**: 照明の色温度/強度/方向、箱・棚のテクスチャと色、背景の乱雑さ。
- **カメラ内外パラメータ**: extrinsics（設置高さ・傾き・位置）と intrinsics（画角・焦点距離・レンズ歪み）。
- **箱/棚の配置と色**: 赤箱 / 青箱 / robot pose / aisle / shelf の配置（[mode-x-er-vla/03:51](../mode-x-er-vla/03-simulation-and-safety-gates.md) が識別対象として列挙）。
- **センサ画質**: 露出・ホワイトバランス・ノイズ・**圧縮アーティファクト**。
- **オクルージョン**: 箱同士・robot による遮蔽。

> **物理 DR（摩擦・質量など）は制御ポリシー学習向け**であり、俯瞰画像からの **L4 視覚 grounding にはほぼ効かない**。本 doc の DR は視覚+カメラを主軸にする。

## ラベル自動生成（sim は ground truth を持つ）

sim が物体・pose の**真値**を持つのは実機に対する最大の利点。Isaac Sim の **Replicator 等の合成ラベル生成機能**で、bbox / segmentation / depth / object・camera pose の ground truth を自動付与できる。これは:

- ER / VLA の **visual grounding 検証**（L4：出力が真の物体位置と一致するか）、
- VLA **fine-tune の教師ラベル**（後述 §VLA fine-tune 射程・training set ③）、
- **G3 offline validator**（L3：[mode-x-er-vla/03:18](../mode-x-er-vla/03-simulation-and-safety-gates.md)）が赤箱/青箱を**機械判定**する根拠（真値ラベルがあるから accept/reject を自動採点できる）——に使える。

**ラベル生成ツールの具体は未凍結**。

## golden fixture 化パイプライン

sim / 実機で発掘した **failure** を、決定的 fixture へ落として floor へ接続する:

1. **discovery**（後述 §3セット分離 ②）で DR を効かせ、ER/VLA が誤る**新しい failure** を発掘する（[mode-x-er-vla/03:55](../mode-x-er-vla/03-simulation-and-safety-gates.md)「failure case を golden fixture にできるか」）。
2. その1ケースを **G0 fixture 形**（`recorded image + text instruction + fake state`、[mode-x-er-vla/03:15](../mode-x-er-vla/03-simulation-and-safety-gates.md)）に、**expected verdict**（accept/reject と reason_code、`ValidationReport` 相当 [03:18](../mode-x-er-vla/03-simulation-and-safety-gates.md)）を付けて凍結する。
3. 凍結 fixture を **golden eval fixture**（§3セット分離 ①）へ**昇格（promotion）**し、[sim/01](01-isaac-sim-verification-gate.md) の**決定的 floor**（CI で毎回同じ入力・同じ判定）に組み込む。

これで「一度見つけた failure は二度と回帰しない」——非決定な工場（②）が測定器（①）を継続的に太らせる。

## eval・discovery・training の3セット分離（不変条件）

DR で作った randomize データを **eval（gate）に混ぜると gate が測定器でなくなる**（[sim/01](01-isaac-sim-verification-gate.md) の決定的 floor 前提を壊す）。sim 出力を**3セットに分け、混ぜない**:

| セット | 量 | DR | 用途 |
|---|---|---|---|
| ① golden eval fixture | 少数・凍結・決定的 | **禁止** | 決定的 floor の測定器（gate） |
| ② discovery / stress set | 大量 | **必須** | failure 発掘 → ①へ昇格（promotion） |
| ③ training set | 大量 | **必須** | VLA fine-tune 入力 |

**不変条件**（このどれを破っても gate が測定器でなくなる）:

- **DR は ②③ のみ**。①には入れない（①は決定的 floor の測定器＝揺らしてはならない）。
- **① と ③ は disjoint**。学習に使った画像で評価しない。
- **① には実機の実写 fixture を必ず混ぜる**（第二の不変条件）。sim 画像だけで①を作ると sim-to-real gap がゼロに見える**自己欺瞞**になる。

## VLA fine-tune 射程

合成データ（③）は VLA の fine-tune 入力に使いうる。ただし **OpenVLA の採用可否・dataset・runtime・GPU・license はすべて未凍結**——[mode-x-er-vla/02:29-40](../mode-x-er-vla/02-openvla-research-plan.md) の調査項目表（model availability / license / input・output shape / runtime / **simulation**）を先に確定してからでないと進めない。sim を評価/runtime に使えるかは [mode-x-er-vla/02:38](../mode-x-er-vla/02-openvla-research-plan.md)（`simulation` 行）が gate。本 doc は 02 と**接続するだけで採用を決めない**。

**制約**（[mode-x-er-vla/03](../mode-x-er-vla/03-simulation-and-safety-gates.md)「実機接続前に禁止すること」「VLA 起動前の L3 条件」と接続）:

- 学習データは**局所操作**（把持・配置・ドッキング・近接位置合わせ、[mode-x-er-vla/02:13](../mode-x-er-vla/02-openvla-research-plan.md)）に限定。**移動のみ task は VLA 非起動**（Nav2＝L1 の仕事、[mode-x-er-vla/03:44](../mode-x-er-vla/03-simulation-and-safety-gates.md)）。
- **fine-tune は能力を上げるが権限は上げない**。精度向上を理由に **L3 Validator（[03:42](../mode-x-er-vla/03-simulation-and-safety-gates.md)）/ L2 Policy Gate（[03:65](../mode-x-er-vla/03-simulation-and-safety-gates.md) bypass 禁止）を緩めない**。
- sim だけで学習した VLA は「**自信を持って間違える**」。対策＝DR（③）＋後述の決定一致率 gap＋eval（①）への実写混入。

## sim-to-real gap の測り方（ピクセルでなく「決定の一致率」）

sim の object label / 見え方と real camera の差分（[mode-x-er-vla/03:52](../mode-x-er-vla/03-simulation-and-safety-gates.md)）は**ピクセル差分では測らない**。同一シナリオを **sim と実機の両方**で ER/VLA に入力し、**決定の一致率**を測る:

- **L4 grounding**: ER/VLA が同じ物体を同じ pose に grounding するか。
- **L3 task graph**: task graph と **Validator の accept/reject + reason_code**（`ValidationReport` [03:18](../mode-x-er-vla/03-simulation-and-safety-gates.md)）が一致するか。
- **L2 Policy Gate**: Policy Gate の通過可否が一致するか（[03:65](../mode-x-er-vla/03-simulation-and-safety-gates.md) bypass 禁止）。

**忠実度は決定に相対的**——sim 忠実度は決定が一致するまで上げ、一致したら止める。これは Isaac Sim GPU が**従量課金**（RunPod A10G、[shared/01:67](../shared/01-budget-and-procurement.md)）で **RT コア必須**（[shared/07:107](../shared/07-research-notes.md)）・GPU 追加時間が有限予算（[shared/01:35](../shared/01-budget-and-procurement.md)）ゆえの、撮影時間を止める合理的基準。DR（②③）で gap を狭める。**gap 測定手順の具体値（一致率しきい値・シナリオ数）は未凍結**。

## 未凍結

- **DR パラメータ範囲**: 各軸（照明/テクスチャ/カメラ内外/ノイズ/オクルージョン）の具体的な randomize 範囲。
- **fine-tune データ量・学習設定**: サンプル数・学習率・epoch・sim 学習時間。
- **OpenVLA 採用可否**: dataset / runtime / GPU / license（[mode-x-er-vla/02](../mode-x-er-vla/02-openvla-research-plan.md) が調査の正本）。
- **ラベル生成ツールの具体**: Replicator 等の設定・出力形式。
- **sim 資産の具体**: scene / robot embodiment / camera 配置。
- **real データ収集方法**: ①へ混ぜる実写 fixture の撮影・アノテーション手順。
- **決定一致率 gap のしきい値**とシナリオ数（sim-to-real 停止基準）。

## References

- [sim/README](README.md) — sim サブツリー索引
- [sim/00-simulation-platform-strategy](00-simulation-platform-strategy.md) — 二層シミュレーション分担（Gazebo 開発ループ / Isaac Sim 検証ゲート）
- [sim/01-isaac-sim-verification-gate](01-isaac-sim-verification-gate.md) — 投入前検証ゲート・決定的 floor と fixture 工場（本 doc の親・正本）
- [mode-x-er-vla/02-openvla-research-plan.md](../mode-x-er-vla/02-openvla-research-plan.md) — OpenVLA 調査計画（fine-tune 射程の正本・§調査項目 :29-40 / :38 simulation）
- [mode-x-er-vla/03-simulation-and-safety-gates.md](../mode-x-er-vla/03-simulation-and-safety-gates.md) — ER+VLA gate（G0 fixture :15 / G3 validator :18 / golden fixture :55 / sim-to-real :52）
- [GLOSSARY §11](../GLOSSARY.md) — sim / 合成データ 正準用語（DR・golden fixture・3セット分離）
- [adr/0006-isaac-sim-as-verification-gate](../adr/0006-isaac-sim-as-verification-gate.md) — 非決定 sim 工場 → 決定的 gate の決定記録
- [shared/01-budget-and-procurement.md](../shared/01-budget-and-procurement.md)（RunPod A10G 従量課金 :67 / GPU 追加時間 :35）/ [shared/07-research-notes.md](../shared/07-research-notes.md)（Isaac Sim RT コア必須 :109）
