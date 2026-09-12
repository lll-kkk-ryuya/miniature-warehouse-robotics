# 06 — ハード差分とベース選定（GNSS・屋外カメラ・非常停止柱・車輪大径化）

作成日: 2026-09-12
Status: **箱（skeleton）**。§2 の導出表と §3 のリスク一覧は既存 docs の確定値から**機械的に導いた参考値**（実測未）。

> 正本ルート: [mode-outdoor/README](README.md)。ハードウェア実体の正本は [shared/02](../shared/02-hardware-design.md)（本 doc は差分のみ）。速度上限の正本は [ADR-0010](../adr/0010-raise-speed-cap-to-platform-max.md)。

## 0. 位置づけ

屋外で追加・変更になるハードと、**現状の M1 車体を屋外で使う場合の制約**を一箇所に集める。ベース選定（A: M1 継続 / B: 屋外ベース）はオペレーター裁定（[00 §4-5](00-mission-and-scope.md)）。

## 1. 差分 BOM（何を書くか・概算は要見積）

| 品目 | 理由 | 候補クラス（`# TODO(選定)`） |
|---|---|---|
| RTK GNSS 受信機 + アンテナ | 単独 GNSS は数 m の誤差で歩道幅を超える（[03 §4](03-localization-gnss-and-ekf.md)） | u-blox ZED-F9P 系 + NTRIP、または QZSS CLAS 対応機 |
| 屋外対応の前方カメラ | 信号認識・歩道領域・負障害物。HP60C は室内用へ格下げ（[04 §1](04-perception-sidewalk-and-signals.md)） | passive stereo（ZED 2i / OAK-D 系） |
| 非常停止装置（柱 + 前後 2 ボタン + リレー） | 法定（[01 §3](01-legal-envelope-japan.md)）。ボタン最下部は地上 60 cm 以上 | 赤ボタン・黄縁・φ20 mm 以上・自己保持 |
| 標識・届出番号プレート | 法定（[01 §4](01-legal-envelope-japan.md)） | 別記様式第 1 の 3 の 3 |
| 荷物箱（固定具つき） | 「人又は物の運送の用に供する」定義への該当（[01 §10](01-legal-envelope-japan.md)） | 転落防止の固定 |
| LTE 経路 | 遠隔操作者卓との常時接続（[02](02-architecture-split-orin-pc-cloud.md)） | スマホテザリング流用可 |
| 車輪 | §2 / §3 | 150 mm 以下の通常タイヤ（A 案）または屋外ベース（B 案） |
| バッテリ | 走行距離・時間が未実測（3S 6000 mAh） | 実測ゲート |

## 2. 車輪大径化と法定 6 km/h の関係（導出・実測未）

前提（[ADR-0010:12-13](../adr/0010-raise-speed-cap-to-platform-max.md:12)）: STM32 ファームは車輪周長 251.327 mm（直径 80 mm 相当）の定数で各輪速度を計算し、**各輪 700 mm/s で切り捨てる**。理論無負荷は 0.86 m/s。車輪を大きくしてもファーム定数は変わらないため、**同じ指令で実速度が直径比で上がる**。ホスト側 L0' クランプ（m/s）も同じ比率で意味がずれ、自前 odom の車輪周長も書き換えが要る。

| 車輪直径 | FW clamp 時の実速度 | 無負荷理論の実速度 | 法定 6 km/h（1.667 m/s）判定 |
|---|---|---|---|
| 80 mm（現状） | 0.70 m/s = 2.5 km/h | 0.86 m/s = 3.1 km/h | 余裕 |
| 100 mm | 0.88 m/s = 3.2 km/h | 1.08 m/s = 3.9 km/h | 可 |
| 120 mm | 1.05 m/s = 3.8 km/h | 1.29 m/s = 4.6 km/h | 可 |
| 150 mm | 1.31 m/s = 4.7 km/h | 1.61 m/s = 5.8 km/h | 可（無負荷で上限に接近） |
| 180 mm | 1.58 m/s = 5.7 km/h | 1.94 m/s = 7.0 km/h | **clamp 実速度が上限に接近・無負荷は超過** |
| 200 mm | 1.75 m/s = 6.3 km/h | 2.15 m/s = 7.7 km/h | **枠外**（構造上 6 km/h を超えうる） |

- clamp が先に効くため「構造上出すことができる最高の速度」は clamp 列で評価する。**直径 190 mm 超で 6 km/h を超える**（1.667 / 0.7 × 80 ≈ 190 mm）。届出には実測値を書く（[01 §4](01-legal-envelope-japan.md)）。
- ファーム clamp を下げる道は [ADR-0010:56](../adr/0010-raise-speed-cap-to-platform-max.md:56) が却下済（custom FW fork）。[ADR-0013](../adr/0013-stm32-command-stream-watchdog.md) の additive fork が成立する場合に同梱するかは `# TODO(裁定)`。
- 車輪トルクは半径に反比例して落ちる。勾配での停止と電流増（[shared/02:318](../shared/02-hardware-design.md:318) の 12 V レール定格 4 A / ピーク 6 A・無保護）が同時に来る。

## 3. 現状車体を屋外で使う場合のリスク（確定事実ベース）

| リスク | 根拠 |
|---|---|
| **fail-active**（ホスト死・USB 断で MCU が最後の速度目標を保持） | [mode-m1/02:25](../mode-m1/02-m1-driver-and-watchdog.md:25)。屋外では W-4 が遠隔で成立しない |
| **モータ電源を切る手段がない**（走行主スイッチは Orin レグのみ） | [mode-m1/02 §3 W-4](../mode-m1/02-m1-driver-and-watchdog.md:65)・[05 §5](05-safety-envelope-and-intervention.md) |
| **メカナムの横滑り**（横断勾配・切下げ斜面で車道側へ流れる、目地・砂利・濡れでスリップ → odom 劣化） | [shared/02:566](../shared/02-hardware-design.md:566)（Yahboom 自身がスリップ対策を説明） |
| **段差**（剛体 80 mm 輪が越えられるのは半径の約 1/3 ≈ 13 mm。歩道切下げの縁石段差は標準 2 cm） | 一般則 + バリアフリー整備の標準値（`# TODO(出典 pin)`） |
| **電源の脆さ**（非安定化 12 V レール・定格 4 A・保護なし・サグで MCU リセット） | [shared/02:312](../shared/02-hardware-design.md:312) / [:318](../shared/02-hardware-design.md:318) |
| **重心**（非常停止柱 60 cm 以上 + アンテナ + カメラ、幅 231 mm） | [shared/02:17](../shared/02-hardware-design.md:17) |
| **25 Hz 報告**（0.7 m/s で 1 周期 28 mm の未観測走行） | [ADR-0010 Context 6](../adr/0010-raise-speed-cap-to-platform-max.md:17) |
| **最低地上高が未実測** | 本 doc `# TODO(実測)` |

## 4. ベース選定 A / B（何を書くか）

- **A. M1 を私有地の開発機として継続**: 150 mm 以下の通常タイヤへ換装（ファームのメカナム IK は `vy = 0` なら skid-steer として成立）、モータレグに非常停止リレー、リンク断 watchdog。GNSS・EKF・知覚・横断ゲートのソフトはここで作る。
- **B. 公道用は屋外ベースを別に選ぶ**: 150 mm 以上の空気入りタイヤ・サスペンション・地上高 50 mm 以上・**コマンドタイムアウトと非常停止入力を持つモータコントローラ**。`# TODO(選定)` 候補・費用・Humble ドライバの有無。
- 推奨（2026-09-12 所見）: A で進め、私有地実走後に B を判断。

## 5. 法定物の物理配置（何を書くか）

- `# TODO(設計)` 非常停止柱（60 cm 以上）と GNSS アンテナ・カメラの同居、標識・届出番号の貼付位置、荷物箱の固定、鋭利な突出部（LiDAR・カメラ・アンテナ）のカバー。高さの上限 120 cm は**センサー・カメラ等を除いて**測る（[01 §2](01-legal-envelope-japan.md)）。

## 6. OPEN QUESTIONS（接頭辞 `OQ-OD6*`）

- `OQ-OD60` 換装後のホイール径実測と odom 定数（`ENCODER_CIRCLE`・車輪周長）の更新手順。
- `OQ-OD61` 非常停止リレーの配置（幹線 / 拡張ボードレグ）と [shared/02:433](../shared/02-hardware-design.md:433) のヒューズ設計との整合。
- `OQ-OD62` バッテリ航続の実測（距離・時間・電圧サグ）。

## References

- [shared/02-hardware-design.md:17](../shared/02-hardware-design.md:17)（寸法）/ [:312](../shared/02-hardware-design.md:312) / [:318](../shared/02-hardware-design.md:318)（12 V レール）/ [:433](../shared/02-hardware-design.md:433)（ヒューズ）/ [:566](../shared/02-hardware-design.md:566)（スリップ）
- [ADR-0010](../adr/0010-raise-speed-cap-to-platform-max.md)（[:12](../adr/0010-raise-speed-cap-to-platform-max.md:12) FW clamp / [:13](../adr/0010-raise-speed-cap-to-platform-max.md:13) 車輪定数 / [:56](../adr/0010-raise-speed-cap-to-platform-max.md:56) clamp 変更却下）/ [ADR-0013](../adr/0013-stm32-command-stream-watchdog.md)
- [mode-m1/02-m1-driver-and-watchdog.md:25](../mode-m1/02-m1-driver-and-watchdog.md:25) / [:65](../mode-m1/02-m1-driver-and-watchdog.md:65)
- [01 法規包絡](01-legal-envelope-japan.md) / [03](03-localization-gnss-and-ekf.md) / [04](04-perception-sidewalk-and-signals.md) / [05](05-safety-envelope-and-intervention.md)
