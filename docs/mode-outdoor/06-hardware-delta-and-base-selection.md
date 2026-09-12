# 06 — ハード差分とベース選定（GNSS・屋外カメラ・非常停止柱・車輪大径化）

作成日: 2026-09-12
Status: **箱（skeleton）**。§2 の導出表と §3 のリスク一覧は既存 docs の確定値から**機械的に導いた参考値**（実測未）。**2026-09-12 後半: 車輪径・速度・駆動方式の裁定材料は [07](07-drivetrain-and-wheel-sizing.md) に集約**（§2 の保守基準 ≤147 mm は 07 でも採用・「ホイールアーチ干渉」の旧記述は撤回）。

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
| 車輪 | **決定（2026-09-12・ユーザー）: 換装前提・最低 4 km/h（歩行速度）** → §2 | **140〜147 mm（市販代表 144 mm）の通常タイヤ（PU ソリッド・減速比 1:56 のまま・stock FW で最大設定 4.5 km/h）**・6 mm D 軸ハブ + 外部軸受・前後ロアバンパの干渉は実測（導出と裁定材料は [07](07-drivetrain-and-wheel-sizing.md)） |
| バッテリ | 走行距離・時間が未実測（3S 6000 mAh） | 実測ゲート |

## 2. 車輪大径化と法定 6 km/h の関係（導出・実測未）

前提（[ADR-0010:12-13](../adr/0010-raise-speed-cap-to-platform-max.md:12)）: STM32 ファームは車輪周長 251.327 mm（直径 80 mm 相当）の定数で各輪速度を計算し、**各輪 700 mm/s で切り捨てる**。理論無負荷は 0.86 m/s。車輪を大きくしてもファーム定数は変わらないため、**同じ指令で実速度が直径比で上がる**。ホスト側 L0' クランプ（m/s）も同じ比率で意味がずれ、自前 odom の車輪周長も書き換えが要る。無負荷列は 12 V 定格（205 rpm）換算で、満充電 12.6 V では約 1.05 倍、警報 9.6 V では約 0.8 倍になる。

| 車輪直径 | FW clamp 時の実速度（名目 700 mm/s） | 無負荷 12 V 定格 | 無負荷 満充電 12.6 V（**法定判定の保守基準**） | 法定 6 km/h（1.667 m/s）判定 |
|---|---|---|---|---|
| 80 mm（現状） | 0.70 m/s = 2.5 km/h | 0.86 m/s = 3.1 km/h | 0.90 m/s = 3.3 km/h | 余裕 |
| 100 mm | 0.88 m/s = 3.2 km/h | 1.08 m/s = 3.9 km/h | 1.13 m/s = 4.1 km/h | 可 |
| 120 mm | 1.05 m/s = 3.8 km/h | 1.29 m/s = 4.6 km/h | 1.36 m/s = 4.9 km/h | 可 |
| 140 mm（**換装候補**） | 1.23 m/s = 4.4 km/h | 1.51 m/s = 5.4 km/h | 1.58 m/s = 5.7 km/h | **可（目標 ≥4 km/h を満たし 6 km/h 未満）** |
| 150 mm | 1.31 m/s = 4.7 km/h | 1.61 m/s = 5.8 km/h | **1.69 m/s = 6.1 km/h** | **保守基準で超過＝候補から外す** |
| 180 mm | 1.58 m/s = 5.7 km/h | 1.94 m/s = 7.0 km/h | 2.03 m/s = 7.3 km/h | 枠外 |
| 200 mm | 1.75 m/s = 6.3 km/h | 2.15 m/s = 7.7 km/h | 2.26 m/s = 8.1 km/h | 枠外 |

- **法定判定の基準は 1 つに固定する: 保守側＝満充電 12.6 V の無負荷実速度**（ソフト/ファームの clamp が「構造」と認められるかは [01 §10](01-legal-envelope-japan.md) のグレー点であり、届出の「性能最高速度」は実測で書く＝[01 §4](01-legal-envelope-japan.md)）。この基準では**直径 ≈147 mm が 6 km/h の交点**（1.667 ÷ (0.86 × 1.05) × 80）。FW clamp（名目 700 mm/s）は二次的な証拠で、それだけなら交点は ≈190 mm（1.667 ÷ 0.7 × 80）。
- ファーム clamp を下げる道は [ADR-0010:56](../adr/0010-raise-speed-cap-to-platform-max.md:56) が却下済（custom FW fork）。[ADR-0013](../adr/0013-stm32-command-stream-watchdog.md) の additive fork が成立する場合に同梱するかは `# TODO(裁定)`。
- 車輪トルクは半径に反比例して落ちる。勾配での停止と電流増（[shared/02:318](../shared/02-hardware-design.md:318) の 12 V レール定格 4 A / ピーク 6 A・無保護）が同時に来る。

**ユーザー決定（2026-09-12）と vendor FW V3.6.5 実 Read の追加事実**（ソースは取得済 `ROS-Driver-Board-FW-master.zip`＝仕様の裏取り資料扱い・[shared/02 P-7e](../shared/02-hardware-design.md)。数値は満充電 12.6 V / 警報 9.6 V の無負荷換算・実測未）:

- **目標: タイヤ換装で最低 4 km/h（歩行速度）**。モータは MD520Z56（205 rpm / 12 V・6.5 kg·cm・6 mm D 軸）。**減速比 1:56 のまま 140 mm 級（保守基準で ≤147 mm）の通常輪で成立**: 140 mm では満充電無負荷 5.7 km/h（保守基準で 6 km/h 未満）・FW clamp 相当 4.4 km/h・9.6 V 時 4.3 km/h。**150 mm は満充電無負荷 6.1 km/h で保守基準を超える**ため候補から外す。127 mm は満充電時のみ 4 km/h。**1:30 への減速比変更は採らない**: 1:30 品は無負荷 ≈332 rpm / 12 V（[shared/02:551](../shared/02-hardware-design.md:551) の X3 1:30 / 65 mm → 1.13 m/s からの検算値）で、80 mm 輪のままでは 9.6 V で約 4.0 km/h まで落ちて目標割れ＋トルク半減、140 mm 輪と組むと 12 V で約 8.8 km/h と 6 km/h 枠外＝どちらの組合せも成立しない。市販の代表径は **144 mm**（Pololu スクータ輪 144×29 mm・6 mm 軸アダプタ）: 最大設定 4.5 km/h・9.6 V 負荷時 4.1 km/h・満充電無負荷 5.8 km/h（[07 §2-3](07-drivetrain-and-wheel-sizing.md)）。
- 車体側: **ホイールアーチは存在しない**（車輪頂点 80 mm ＞ 車体上面 74.58 mm＝[shared/02:302](../shared/02-hardware-design.md:302)。2026-09-12 午前の「アーチ干渉」は撤回）。干渉するのは**前後ロアバンパ**（127 mm で ≈ 14 mm・160 mm で ≈ 30 mm 超え・軸間推定 185 mm に依存）と**前後輪どうし**（≈ 185 mm で接触 → 実用 ≤ 160〜170 mm）。実物採寸 or STEP 取得は `# TODO(実測)`（[07 §7 / §10 G-W1](07-drivetrain-and-wheel-sizing.md)）。
- ファーム側の代替: car_type **X1（0x04）は差動ミキシング・各輪 clamp 1000・`FOURWHEEL_CIRCLE_MM = 215.2` / 1320 counts** という別定数を持つ。M1 type（0x0A・メカナム IK・clamp 700・251.327 mm / 2464 counts）のまま `vy = 0` で使うか X1 type へ切り替えるかは `# TODO(裁定)`（どちらも定数は 80 mm / 68.5 mm 相当のまま＝実単位ではない）。
- ホスト側の補正案: L0' で **`v / k`（k = D / 80 mm）を送信**して FW の名目速度を実速度に合わせ、odom は **`0x0D` 生カウント + 真の周長**で計算する（[mode-m1/02 §1-3](../mode-m1/02-m1-driver-and-watchdog.md:31) の方針と同じ）。
- **契約への帰結**: FW clamp の**指令値と実速度の対応が崩れる**（名目 700 mm/s ≠ 実速度）ため、**実単位の cap はホスト側（① launch 注入値 / config 運用値 / 凍結契約 `MAX_LINEAR_VELOCITY` / L0' クランプ）だけになる**（[ADR-0012 決定 3](../adr/0012-speed-band-no-l2-best-effort.md:20) の `min(帯値, ①, MAX_LINEAR_VELOCITY)`）。[ADR-0010](../adr/0010-raise-speed-cap-to-platform-max.md)「platform max」の意味と [ADR-0012](../adr/0012-speed-band-no-l2-best-effort.md) の帯上限は再導出が要る（contract PR・[00 §4 行 7](00-mission-and-scope.md)）。ただし FW clamp は実単位でも `0.7 × (D / 80)` m/s の上限として**残る**（144 mm で 1.26 m/s = 4.5 km/h・D ≤ 190 mm なら 6 km/h 未満）＝[07 §2](07-drivetrain-and-wheel-sizing.md)。

## 3. 現状車体を屋外で使う場合のリスク（確定事実ベース）

| リスク | 根拠 |
|---|---|
| **fail-active**（ホスト死・USB 断で MCU が最後の速度目標を保持） | [mode-m1/02:25](../mode-m1/02-m1-driver-and-watchdog.md:25)。屋外では W-4 が遠隔で成立しない |
| **法定要件を満たす非常停止装置がない**（モータ電源を切れるのは拡張ボードのメインスイッチと T プラグ抜きだけ＝手が届く距離の操作。走行主スイッチ 4962 は Orin レグのみ） | [shared/02:897](../shared/02-hardware-design.md:897) / [:913](../shared/02-hardware-design.md:913) / [shared/01:187](../shared/01-budget-and-procurement.md:187) / [mode-m1/02 §3 W-4](../mode-m1/02-m1-driver-and-watchdog.md:65)・[05 §5](05-safety-envelope-and-intervention.md) |
| **メカナムの横滑り**（横断勾配・切下げ斜面で車道側へ流れる、目地・砂利・濡れでスリップ → odom 劣化） | [shared/02:566](../shared/02-hardware-design.md:566)（Yahboom 自身がスリップ対策を説明） |
| **段差**（剛体 80 mm 輪が越えられるのは半径の約 1/3 ≈ 13 mm〔一般則〕。歩車道境界の段差は**標準 2 cm**） | 国土交通省「歩道の一般的構造に関する基準」（平成 17 年 2 月 3 日 国都街第 60 号・国道企第 102 号）<https://www.mlit.go.jp/road/sign/kijyun/pdf/20050203hodou.pdf> ／ 道路の移動等円滑化整備ガイドライン概要 <https://www.mlit.go.jp/kisha/kisha02/06/061218/061218_3.pdf>（参照日 2026-09-12。要旨は MLIT 掲載資料の検索結果で確認・原文 PDF の実 Read は `# TODO`） |
| **電源の脆さ**（非安定化 12 V レール・定格 4 A・保護なし・サグで MCU リセット） | [shared/02:312](../shared/02-hardware-design.md:312) / [:318](../shared/02-hardware-design.md:318) |
| **重心**（非常停止柱 60 cm 以上 + アンテナ + カメラ、幅 231 mm） | [shared/02:17](../shared/02-hardware-design.md:17) |
| **25 Hz 報告**（0.7 m/s で 1 周期 28 mm の未観測走行） | [ADR-0010 Context 6](../adr/0010-raise-speed-cap-to-platform-max.md:17) |
| **最低地上高が未実測** | 本 doc `# TODO(実測)` |
| **低電圧ラッチ停止**（9.6 V 以下を 2 秒連続で検出すると MCU が `g_system_enable = 0` にして停止・**電源再投入まで復帰不能**。停動 4 A × 4 輪 = 16 A が非安定化レールを叩くと歩道上で起きうる） | vendor FW `app_bat.c:11,13,83,108`（[07 §8-1](07-drivetrain-and-wheel-sizing.md) / [mode-m1/02 末尾追補](../mode-m1/02-m1-driver-and-watchdog.md)） |
| **ゼロ指令 = 短絡ブレーキ**（惰行ではない。大径ゴム輪・1.3 m/s ではホスト側ランプダウンが要る） | vendor FW `app_mecanum.c:34-38`（[07 §8-2](07-drivetrain-and-wheel-sizing.md)） |
| **6 mm D 軸の片持ち**（37 mm 級ギヤモータの出力軸はブッシュ 1 個支持。大径化で曲げモーメント約 1.8〜1.9 倍） | [07 §7](07-drivetrain-and-wheel-sizing.md)（Pololu forum） |
| **前後輪の軸間 ≈ 185 mm（推定）**が径の機械上限（実用 ≤ 160〜170 mm） | [07 §1 / §3 (d)](07-drivetrain-and-wheel-sizing.md) |

## 4. ベース選定 A / B（何を書くか）

- **A. M1 を私有地の開発機として継続**: 140 mm 級（≤147 mm）の通常タイヤへ換装（ファームのメカナム IK は `vy = 0` なら skid-steer として成立）、モータレグに非常停止リレー、リンク断 watchdog。GNSS・EKF・知覚・横断ゲートのソフトはここで作る。
- **B. 公道用は屋外ベースを別に選ぶ**: 150 mm 以上の空気入りタイヤ・サスペンション・地上高 50 mm 以上・**コマンドタイムアウトと非常停止入力を持つモータコントローラ**。`# TODO(選定)` 候補・費用・Humble ドライバの有無。
- **決定（2026-09-12・ユーザー）: タイヤ換装で進める（A 案系）**。B 案（屋外ベース）は survey-first の調査対象として残し、採用は調査後に決める。
- 案の比較（A〜F・6 km/h は FW 定数修正 + 1:40 L 型の案 C = Phase 2・ADR 要）と **4WD 維持**の根拠は [07 §4 / §9](07-drivetrain-and-wheel-sizing.md)。ハブモータ + VESC / ODrive（案 E）は W-3 と速度上限をコントローラ FW が持つ点で B 案の本命候補。

## 5. 法定物の物理配置（何を書くか）

- `# TODO(設計)` 非常停止柱（60 cm 以上）と GNSS アンテナ・カメラの同居、標識・届出番号の貼付位置、荷物箱の固定、鋭利な突出部（LiDAR・カメラ・アンテナ）のカバー。高さの上限 120 cm は**センサー・カメラ等を除いて**測る（[01 §2](01-legal-envelope-japan.md)）。

## 6. OPEN QUESTIONS（接頭辞 `OQ-OD6*`）

- `OQ-OD60` 換装後のホイール径実測と odom 定数（`ENCODER_CIRCLE`・車輪周長）の更新手順。
- `OQ-OD61` 非常停止リレーの配置（幹線 / 拡張ボードレグ）と [shared/02:433](../shared/02-hardware-design.md:433) のヒューズ設計との整合。
- `OQ-OD62` バッテリ航続の実測（距離・時間・電圧サグ）。
- `OQ-OD63` 車輪換装の実測ゲート G-W1〜G-W8（[07 §10](07-drivetrain-and-wheel-sizing.md)）の実施順と、換装前に閉じる M0-M2（[mode-m1/03](../mode-m1/03-joystick-teleop-bringup.md)）との依存。
- `OQ-OD64` 6 km/h を要件にするか（[07 §9 案 C](07-drivetrain-and-wheel-sizing.md)）＝ `OQ-OD70`（本 doc からは参照のみ）。

## References

- [shared/02-hardware-design.md:17](../shared/02-hardware-design.md:17)（寸法）/ [:312](../shared/02-hardware-design.md:312) / [:318](../shared/02-hardware-design.md:318)（12 V レール）/ [:433](../shared/02-hardware-design.md:433)（ヒューズ）/ [:566](../shared/02-hardware-design.md:566)（スリップ）
- [ADR-0010](../adr/0010-raise-speed-cap-to-platform-max.md)（[:12](../adr/0010-raise-speed-cap-to-platform-max.md:12) FW clamp / [:13](../adr/0010-raise-speed-cap-to-platform-max.md:13) 車輪定数 / [:56](../adr/0010-raise-speed-cap-to-platform-max.md:56) clamp 変更却下）/ [ADR-0013](../adr/0013-stm32-command-stream-watchdog.md)
- [mode-m1/02-m1-driver-and-watchdog.md:25](../mode-m1/02-m1-driver-and-watchdog.md:25) / [:65](../mode-m1/02-m1-driver-and-watchdog.md:65)
- [01 法規包絡](01-legal-envelope-japan.md) / [03](03-localization-gnss-and-ekf.md) / [04](04-perception-sidewalk-and-signals.md) / [05](05-safety-envelope-and-intervention.md) / **[07 駆動系と車輪径](07-drivetrain-and-wheel-sizing.md)**（速度表・法律ギリギリ径・4WD・懸念・FW 挙動・実測ゲート）
