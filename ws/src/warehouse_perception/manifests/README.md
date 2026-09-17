# `manifests/` — 09_Runtime_and_Models の model manifest 置き場

**layer**: 09_Runtime_and_Models は**基盤**（単一 layer に帰属させない・版 pin は 00）。
ここに置くのは**採用単位の記録（artifact）**であって、実行時に読まれる config ではない
（[04:335](../../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:335) レイヤ annotation・
[04:204](../../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:204) 「04 が artifact として所有」）。

## 1. 置き場と読み方

- **場所**: `ws/src/warehouse_perception/manifests/<model-slug>.yaml`（package-local）。
- **型の正本**: [04 追補 ④ §1-5](../../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:452)。
  実体は凍結契約 `warehouse_interfaces.perception.ModelManifest`。**docs の例示より凍結契約が優先**
  （[.claude/rules/docs-first.md](../../../../.claude/rules/docs-first.md)）。
- **読み方**: `warehouse_perception.model_manifest.load_manifest(path)`（PyYAML `safe_load` →
  `ModelManifest.model_validate`）。検証だけなら CLI:

  ```bash
  python3 -m warehouse_perception.model_manifest validate manifests/example.rf-detr-nano.yaml
  python3 -m warehouse_perception.model_manifest validate <yaml> --weights <weights-file>
  ```

  console_script 名は `perception_manifest`（`setup.py`）。exit 0 = 妥当 / exit 1 = 不備。
- **`weights_sha256`** は `sha256sum <weights>` が出す 64 hex。アルゴリズムを sha256 に固定するかは
  **暫定**（正本は「重み hash」としか言っていない）＝
  [`OQ-OD4Y-k`](../../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:496)。
- **`data_files` に載せない**: これは配布物ではなく repo に残す記録。`setup.py` の `data_files` へ
  追加すると `share/` へインストールされ、「実行時に読む設定」に見えてしまう。

## 2. engine はボード上で焼く

TensorRT engine は **GPU arch と TRT 版に固定され可搬でない**
（[`OQ-OD4U`](../../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:358)）。
本 package の tool は **engine を焼かない・読まない**。manifest はその事実を
`engine_built_on_board` / `tensorrt_version` / `gpu_arch` に**記録するだけ**。

- engine 未作成（ONNX 止まり）の採用単位は正当 → `tensorrt_version` / `gpu_arch` は**空でよい**
  （[04:467](../../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:467) /
  [:468](../../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:468)）。
- 実機推論に使うなら、ボード上で焼いた実値を入れて `engine_built_on_board: true` にする。
- **公開ベンチ値（640・前後処理除外・T4）を予算に使わない**
  （[04:244](../../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:244)）。
  ここに書く数値は自画角・自解像度・前後処理込みの実測に置き換わるまで placeholder。

## 3. ライセンス境界（配布物に載るもの／載らないもの）

`license` が空だと `ValidationError`＝「不明」を黙って通さない
（[04:469](../../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:469)）。

- **本命 = RF-DETR-Nano（Apache-2.0）** / 保険 = D-FINE-S・RT-DETRv2-S（Apache-2.0）
  （[04:231](../../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:231) /
  [04:235](../../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:235)）。
- **YOLO 系は AGPL-3.0 ＝「速度上限の測定器」に限定し配布物に載せない**
  （[04:245](../../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:245) /
  [`OQ-OD4N`](../../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:351)）。
  測定器として manifest を書くこと自体は可。**配布判断は manifest と一緒に動く**ので `license` を必ず書く。
- **DEIMv2 は非商用ライセンス＝除外**
  （[04:242](../../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:242)）。
- 評価データ側の境界は別物: JRDB（CC BY-NC-SA 3.0）は **評価にも使わない**
  （[04:278](../../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:278) /
  [`OQ-OD4T`](../../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:357)）、SANPO は CC-BY-4.0
  （[04:277](../../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:277)）。

## 4. 例ファイル

[`example.rf-detr-nano.yaml`](example.rf-detr-nano.yaml) は **形の例**。値はすべて placeholder で、
重み・engine・bag は存在しない。`evaluation` は省略不可
（[04:471](../../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:471)）なので
`dataset_id: "example-bag-0000"` という**存在しない bag** を名乗り、指標も自己整合な合成値を置いてある。
`"UNEVALUATED"` のようなダミー id は**書かない**——「評価した」と読める嘘になるため。
例ファイルが `load_manifest` を通ることは unit（`tests/unit/test_model_manifest.py`）で pin してある。

## 5. 実装記録

手順・指標の定義・残 OQ は
[04 追補 ⑦](../../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:524)。
produce / consume は [`../CLAUDE.md`](../CLAUDE.md)。
