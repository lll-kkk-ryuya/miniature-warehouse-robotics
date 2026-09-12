# 04 — 知覚: 歩道走行可能領域・障害物・歩行者用信号

作成日: 2026-09-12
Status: **箱（skeleton）**。

> 正本ルート: [mode-outdoor/README](README.md)。室内知覚の原則 P1（L1 に GPU 依存を持ち込まない＝[23:37](../architecture/23-perception-and-localization.md:37)）・P2（cmd_vel 経路は 1 バイトも変えない＝[23:41](../architecture/23-perception-and-localization.md:41)）は**屋外でも不変**。知覚は costmap のセル値と L4 producer の出力にのみ影響し、`cmd_vel` を publish しない。

## 0. 位置づけ

屋外で新規に要る知覚は 4 つ: ①歩道の走行可能領域（歩道から出ない）②障害物と**負障害物**（縁石落ち）③**歩行者用信号**の状態 ④歩行者（進路を譲る義務＝[01 §6](01-legal-envelope-japan.md)）。信号の**判定結果は L4 producer**であり、**横断の許可は L2 ゲート**（[05 §4](05-safety-envelope-and-intervention.md)）が持つ。

## 1. センサ前提（確定事実 + 何を書くか）

| センサ | 事実（一次情報・参照日 2026-09-12） | 屋外での扱い |
|---|---|---|
| Nuwa-HP60C 深度カメラ | 公式ページは **structured light（構造化光）**・有効深度 4 m・FOV 73.8°。屋外・直射日光の記述なし | **屋外の主センサにしない**（IR 構造化光は直射日光で不安定）。室内（[ADR-0007](../adr/0007-no-overhead-camera-gesture-via-onboard-nn.md)）の射程は不変 |
| YDLIDAR T-mini Plus（2D・12 m） | 公式ページに **60 kLux** の耐環境光を明記 | 反射安全（collision_monitor）の入力として**継続候補**。縁石より低い物・張り出しは見えない。**要実測**（直射日光下） |
| 屋外向け前方カメラ（新規） | `# TODO(選定)` passive stereo（ZED 2i / OAK-D 系。OAK-D はカメラ側で深度計算＝Orin GPU を温存） | 信号認識・歩道領域・負障害物の主センサ候補（[06 §1](06-hardware-delta-and-base-selection.md)） |

## 2. 歩道走行可能領域（何を書くか）

- `# TODO(設計)` セグメンテーション（TensorRT）→ costmap の専用層（走行可能領域外を lethal/高コスト）。GNSS の歩道ポリゴン（ジオフェンス＝[05 §3](05-safety-envelope-and-intervention.md)）との二重化。
- `# TODO(設計)` 点字ブロック・排水溝・芝生の扱い（通行可だが減速か、不可か）。

## 3. 障害物・負障害物（何を書くか）

- `# TODO(設計)` 3D 知覚 → voxel / obstacle 層（室内の nvblox 層の屋外版。原則 P1 により collision_monitor には入れない）。
- `# TODO(設計)` **負障害物**（縁石落ち・段差）: 下向きカメラの深度から「地面が無い」領域を lethal にする。歩道切下げ部の段差（標準 2 cm）と車輪径の関係は [06 §3](06-hardware-delta-and-base-selection.md)。

## 4. 歩行者用信号の検出・状態分類（何を書くか）

- 法定の意味（[01 §6](01-legal-envelope-japan.md) 信号表）: **青 = 進行可／青点滅 = 横断を始めてはならない（横断中は速やかに終えるか引き返す）／赤 = 横断不可**。分類器の出力クラスはこの 3 状態 + 不明（fail-closed）にする。
- `# TODO(設計)` 検出器（TensorRT・日本の歩行者用灯器で学習）・時間窓多数決・距離/角度の妥当性・「対面の灯器」を選ぶロジック（交差点には複数の灯器がある）。
- `# TODO(設計)` 出力契約（L4 producer → L2 横断ゲート）。**LLM / ER の判断を唯一の権威にしない**（クラウド断で fail-closed）。

## 5. 歩行者（何を書くか）

- `# TODO(設計)` 歩行者検出 → 進路を譲る（停止・退避）挙動。法 14 条の 2（[01 §1](01-legal-envelope-japan.md)）。

## 6. 受け入れ条件（何を書くか）

- `# TODO(設計)` 信号分類の混同行列（特に「青点滅→青」「赤→青」の誤りはゼロ要求）・負障害物の検出距離・歩道領域の IoU。

## 7. OPEN QUESTIONS（接頭辞 `OQ-OD4*`）

- `OQ-OD40` T-mini Plus の直射日光下の実性能（60 kLux 公称の実測）。
- `OQ-OD41` 信号認識の学習データ（自前収集の可否・公開データセット）。
- `OQ-OD42` 車両（右左折車）の検出を横断ゲートの条件に含めるか。

## References

- [architecture/23-perception-and-localization.md:37](../architecture/23-perception-and-localization.md:37)（P1）/ [:41](../architecture/23-perception-and-localization.md:41)（P2）
- [ADR-0007](../adr/0007-no-overhead-camera-gesture-via-onboard-nn.md)（HP60C 一本化の射程は室内）
- Nuwa HP60C 公式 <https://category.yahboom.net/products/hp60c>（参照日 2026-09-12・structured light）/ YDLIDAR T-mini Plus 公式 <https://www.ydlidar.com/product/ydlidar-t-mini-plus>（参照日 2026-09-12・60 kLux）
- [01 法規包絡 §6](01-legal-envelope-japan.md)（信号の意味・進路を譲る義務）/ [05 §4](05-safety-envelope-and-intervention.md)（横断ゲート）/ [06](06-hardware-delta-and-base-selection.md)
