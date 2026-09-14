# 07 — 駆動系と車輪径（法定 6 km/h・目標 ≥4 km/h・4WD の要否・懸念）

作成日: 2026-09-12
Status: **記載済（設計値・一次情報つき・実測未）**。2026-09-12 のエージェントチーム 4 レーン（法規／駆動系物理／駆動方式・車体／M1 実機制約）の報告を統合し、数値は本 doc 執筆時に**再計算**、引用は**再 Read** した（[D]=一次情報を執筆者が実 Read・[L]=調査レーン報告で URL あり（執筆者未再読）・[I]=推論・計算）。実機の実測（§10）で確定するまで**設計値**として扱う。**2026-09-12 統合**: 径の推奨は [06 §2](06-hardware-delta-and-base-selection.md) が固定した法定判定の保守基準（満充電 12.6 V の無負荷実速度 < 6.0 km/h ＝ ≤147 mm）に合わせ **144 mm 級**とし、150 mm は「FW clamp を構造と認める場合のみ」に格下げした。 **2026-09-13 ユーザー裁定: 150 mm（案 A'・FW clamp を「構造」の根拠として認める）を採用**＝末尾【2026-09-13 追補③】。144 mm は事前相談で clamp が構造と認められなかった場合の退避先（k を 1.8 に変えるだけ）。

> 正本ルート: [mode-outdoor/README](README.md)。本 doc は「車輪径・速度・駆動方式」の**裁定材料**に閉じる。差分 BOM・ベース選定は [06](06-hardware-delta-and-base-selection.md)、法規は [01](01-legal-envelope-japan.md)、速度上限の契約は [ADR-0010](../adr/0010-raise-speed-cap-to-platform-max.md)、ハードウェア実体は [shared/02](../shared/02-hardware-design.md)、車体ファームの挙動（driver / watchdog）は [mode-m1/02](../mode-m1/02-m1-driver-and-watchdog.md)（本 doc §8 の事実は同 doc 末尾追補へ forward link 済）。
> レイヤ注記: 本 doc の対象は **L0'（ホスト送信直前クランプ・`m1_driver`）と MCU ファーム（L0 相当・vendor 所有）、および自律走行（Hard-RT・安全層外＝[23 §1](../architecture/23-perception-and-localization.md:21)）の速度予算**。L2/L1 の契約は変えない（変更は [00 §4 行 7](00-mission-and-scope.md) の contract PR）。

## 0. 要旨（結論）

**Q1. 法律ギリギリの車輪直径は？** — 答えは 2 層になる。

- **法定 6 km/h の判定方法**は警察庁の型式認定基準が定める: 水平路面 20 m・助走 10 m ＋ 測定 10 m を**往復**・`V = 36 / T`・**電池 75 % 以上**・**速度を調整できるものは最大値にセット**（[01 追補②](01-legal-envelope-japan.md)）。つまり「運用速度」ではなく**最大設定値**で 6.0 km/h 以下でなければならない。
- stock ファームの各輪 clamp `CAR_M1_MAX_SPEED = 700`（FW 換算 mm/s）は**車輪 167 rpm** に相当し、実速度は `π·D·167/60`。これが 6.0 km/h に達する径は **190 mm**（§3）。しかし ① 前後輪の軸間距離（推定 185 mm・§7）で入らない、② 9.6 V ではモータ無負荷が 164 rpm しか回らず 167 rpm の clamp に届かない、③ 牽引力は `1/D` で落ちる。→ **FW を触らない前提の実用域は 140〜147 mm**（法定判定の保守基準＝満充電 12.6 V の無負荷実速度が 6.0 km/h 未満・上限 **147.9 mm**。[06 §2](06-hardware-delta-and-base-selection.md) の裁定と同じ）。市販の代表径 **144 mm**（Pololu スクータ輪 144×29 mm）で最大設定 **4.5 km/h**・9.6 V 負荷時 **4.1 km/h**・満充電無負荷 **5.8 km/h**。**150 mm（満充電無負荷 6.1 km/h）は FW clamp（最大設定 4.7 km/h）を「構造」の根拠として認める場合にのみ可**＝[01 §10](01-legal-envelope-japan.md) のグレーに乗る。**目標 ≥4 km/h は満たすが、6 km/h ギリギリにはならない。**
- **6 km/h ギリギリで走る**には **FW 定数の修正（実円周＋clamp 1667 mm/s）＋ 1:40 L 型モータ**が要る（§9 案 C）。stock FW のままモータだけ 1:40 に換えると clamp 換算が **6.6 km/h** になり**法定超え**（ホスト clamp 頼み＝[01 §10](01-legal-envelope-japan.md) のグレー）。

**Q2. 4WD は必要か？** — 法的要件ではない。**技術的には「4 輪とも駆動・固定・接地荷重あり」が必要**（§4）: 歩道級の商用機にキャスタ採用例がない・2 cm 段差はキャスタでは越えられない・制動力が約 1.9 倍・勾配トルク余裕が 2 倍・M1 は既に 4 モータで追加費用ゼロ。**メカナムは屋外で放棄**する（濡れ・砂利・横断勾配でのスリップと横流れ）。

**Q3. 周辺の懸念は？** — 重大度順: ① 速度が clamp と電池サグで 4.5 → 4.1 km/h（144 mm。6 km/h は FW 改変が前提）② 2 cm 段差のトルク（144 mm で 0.73 N·m ＝ 定格超・停動未満）③ 6 mm D 軸の片持ちとギヤボックス出力軸受 ④ 低電圧ラッチ（9.6 V × 2 s で**電源再投入まで復帰不能**停止）⑤ 2D LiDAR 走査面下の死角 ⑥ 重心と停止距離 ⑦ odom スケール校正（§6・§7・§8）。

**推奨**: **Phase 1 = 144 mm 級（≤147 mm）通常輪・1:56 据え置き・stock FW・M1 car_type ＋ ホスト側車輪スケール k**（4.1〜4.5 km/h・満充電無負荷 5.8 km/h で「構造上 6 km/h 未満」を物理で満たし、FW clamp も MCU 側に残る）。**Phase 2（任意・ADR 要）= FW 定数修正 ＋ 1:40 L 型で 6 km/h**（[ADR-0013](../adr/0013-stm32-command-stream-watchdog.md) の additive fork と同じツールチェーン課題を共有＝§8-5）。

## 1. 前提（確定事実と出所）

| 項目 | 値 | 出所 | 確度 |
|---|---|---|---|
| モータ | MD520Z56: 12 V・無負荷 **205 ± 10 rpm**・定格 6.5 kg·cm / 停動 8.3 kg·cm・定格 0.3 A / 停動 4 A・≤4 W・**6 mm D 型軸**・11 線エンコーダ。同系: 1:30 = 333 rpm / 3.3 / 4.8 kg·cm、1:19 = 550 rpm / 2.2 / 3.1、**L 型 1:40 = 300 rpm / 4.4 / 10 kg·cm / ≤6 W** | Yahboom 520 motor 公式表（[shared/02:585](../shared/02-hardware-design.md:585) の URL・参照日 2026-09-12）[D] | 高 |
| 車輪（現状） | メカナム **80 mm**・周長 251.327 mm・6 mm 六角カップリング | [ADR-0010:13](../adr/0010-raise-speed-cap-to-platform-max.md:13) / [shared/02:749](../shared/02-hardware-design.md:749) [D] | 高（実測は [ADR-0010:63](../adr/0010-raise-speed-cap-to-platform-max.md:63)） |
| FW 定数（M1 = car_type 0x0A） | `MECANUM_M1_CIRCLE_MM = 251.327` / `MECANUM_M1_APB = 189.5` / `CAR_M1_MAX_SPEED = 700`（`app_mecanum.h:35,37,39`）・`ENCODER_CIRCLE_205 = 2464` counts/車輪 1 回転（`app_motion.h:8`）・速度換算 `speed_mm = Δcounts × 100 × circle_mm / circle_pulse`（`app_motion.c:245`）・各輪 clamp ±700（`app_mecanum.c:56-65`） | vendor FW V3.6.5 実ソース（[shared/02:797](../shared/02-hardware-design.md:797) の zip）[D] | 高（搭載版の一致は U-5 待ち） |
| FW 定数（X1 = car_type 0x04・差動） | `FOURWHEEL_CIRCLE_MM = 215.2` / `FOURWHEEL_APB = 164.555`（`app_fourwheel.h:13,9`）・`ENCODER_CIRCLE_330 = 1320`（`app_motion.c:46`）・`V_y = 0` 強制（`app_fourwheel.c:34`）・各輪 clamp ±1000（`app_fourwheel.c:47-54`） | 同上 [D] | 高 |
| 車体寸法 | 全幅 231.40 × 全長 284.40 × 全高 181.40 mm・**車体上面 74.58 mm**・LiDAR 上面 147.50 mm | [shared/02:302](../shared/02-hardware-design.md:302) [D] | 高 |
| 軸間・輪距 | `MECANUM_M1_APB = 189.5` = (輪距 + 軸間) / 2 → **和 379 mm**。輪距 ≈ 231.4 − 37（メカナム厚）≈ **194 mm**、軸間 ≈ **185 mm** | FW 定数の定義式（`app_mecanum.h:11` コメント・X3 の (169.0 + 160.11)/2 = 164.555 で式を検証）[D] ＋ 減算 [I] | **推定**（STEP 未取得・§10） |
| 電源 | 3S 12.6 → 9.6 V 警報・6000 mAh。12 V レールは非安定化・定格 4 A / ピーク 6 A・保護なし | [shared/02:318](../shared/02-hardware-design.md:318) / [:433](../shared/02-hardware-design.md:433) [D] | 高 |
| 法定 | 6 km/h・最大設定で測定・電池 75 % 以上・往復 10 m | 警察庁 丙交企発第 118 号 別添「型式認定基準」（[01 追補②](01-legal-envelope-japan.md)）[D] | 高 |
| 質量 | M1 車体 + Orin + 電池 + RTK + カメラ + 柱 ≈ **5〜6 kg**（本 doc は 6 kg で計算） | [I] | 推定 |

## 2. 速度の式と表

式（すべて [I]・定数は §1）:

- 車輪 1 回転あたりの真の距離 = `π·D`。FW は距離を `circle_mm / circle_pulse` × counts で数えるため、**実速度 = FW 換算速度 × (π·D / circle_mm) × (circle_pulse / 真の counts/rev)**。M1 type + 1:56 なら **実速度 = 指令 × (D / 80 mm)**（純スケール・方向は歪まない）。
- FW clamp の実速度 = `0.700 × (2464 / counts_rev) × (D / 80)` [m/s]。
- 無負荷 = `π·D·n0·f / 60`（`f` = 12.6 V で 1.05・12 V で 1.00・9.6 V で 0.80＝回転数の電圧比例 [I]）。平地負荷は無負荷比 −5〜−10 %（表は −7.5 % [I]）。

| モータ | D [mm] | **FW clamp 実速度** | 無負荷 12.6 V | 無負荷 12 V | 無負荷 9.6 V | **9.6 V 負荷（−7.5 %）** | ≥4 km/h（9.6 V） | 6 km/h 維持（9.6 V） |
|---|---|---|---|---|---|---|---|---|
| 1:56 | 80（現状） | 0.700 m/s = **2.5 km/h** | 3.3 | 3.1 | 2.5 | **2.3** | ✗ | ✗ |
| 1:56 | 127 | 1.111 = **4.0** | 5.2 | 4.9 | 3.9 | **3.6** | ✗（満充電のみ） | ✗ |
| 1:56 | 140 | 1.225 = **4.4** | 5.7 | 5.4 | 4.3 | **4.0** | △（余裕ゼロ） | ✗ |
| **1:56** | **144（市販代表・≤147）** | **1.260 = 4.5** | **5.8** | 5.6 | 4.5 | **4.1** | **○** | ✗ |
| 1:56 | 150 | 1.313 = 4.7 | **6.1（保守基準超）** | 5.8 | 4.6 | **4.3** | ○（FW clamp を構造と認める場合のみ） | ✗ |
| 1:56 | 160 | 1.400 = 5.0 | 6.5 | 6.2 | 5.0 | **4.6** | ○ | ✗ |
| 1:56 | 180 | 1.575 = 5.7 | 7.3 | 7.0 | 5.6 | **5.2** | ○（軸間で不可） | ✗ |
| 1:56 | 190.5 | 1.667 = **6.0** | 7.7 | 7.4 | 5.9 | **5.5** | ○（軸間で不可） | ✗ |
| 1:40 L | 150 | 1.837 = **6.6（法定超）** | 8.9 | 8.5 | 6.8 | **6.3** | ○ | ○※ |
| 1:40 L + **FW 定数修正** | 150 | **1.667 = 6.0**（clamp を実単位 1667 に） | 8.9 | 8.5 | 6.8 | **6.3** | ○ | **○** |
| 1:30 | 150 | 2.450 = **8.8（法定超）** | 9.9 | 9.4 | 7.5 | **7.0** | ○ | ○※ |

※ = モータ能力としては維持できるが、stock FW のままでは clamp が **6 km/h を超える**ため法定枠外（ホスト clamp のみが cap になる＝[01 §10](01-legal-envelope-japan.md) の「ソフト上限の構造上該当性」グレーに落ちる）。1:40 / 1:30 の行の clamp 列は、FW が 2464 counts/rev（`ENCODER_CIRCLE_205`）を仮定したまま実エンコーダが 1760 / 1320 counts/rev になることによる係数（2464/1760 = 1.40・2464/1320 = 1.87）を含む [I]。**FW に 1:40 用の counts 定数は存在しない**（`app_motion.h:8-17` は 205/330/450/550 の 4 種のみ）。

**読み方**: 1:56 のまま径を上げる限り、**clamp 列が 6.0 を超えるのは 190 mm 超だけ**＝「構造上 6 km/h を超えられない」根拠は MCU 側に残る。144 mm では最大設定が 4.5 km/h、電池が警報電圧まで落ちても 4.1 km/h（150 mm なら 4.7 / 4.3 km/h）。

## 3. 「法律ギリギリ径」を決める 4 つの上限

| 上限の種類 | 式 | 1:56 | 1:40 L | 1:30 | 意味 |
|---|---|---|---|---|---|
| (a) FW clamp が 6.0 km/h になる径 | `D = 1.667 × 60 / (π × 車輪 rpm@clamp)`（1:56: 167.1 rpm） | **190.5 mm** | 136.1 mm | 102.0 mm | これより大きいと最大設定が法定超 |
| (b) 満充電無負荷が 6.0 km/h になる径（物理的に超えられない） | `D = 1.667 × 60 / (π × n0 × 1.05)` | **147.9 mm** | 101.1 mm | 91.0 mm | これ以下なら FW に頼らず物理で 6 km/h 未満 |
| (c) 9.6 V 負荷で 6.0 km/h を維持できる径 | `D = 1.667 × 60 / (π × n0 × 0.80 × 0.925)` | 209.8 mm | 143.4 mm | 129.2 mm | 「6 km/h で走り続ける」ための径 |
| (d) 機械上限（前後輪が当たる） | `D < 軸間 − 隙間`（軸間 ≈ 185 mm 推定） | **≈ 160〜170 mm** | 同左 | 同左 | 144 mm で隙間 ≈ 41 mm・150 mm で ≈ 35 mm・160 mm で ≈ 25 mm [I] |

**結論**: 1:56 のまま (a)(c) を満たす径（190〜210 mm）は (d) で入らない。**(b)(d) を同時に満たす最大 = 147 mm（市販代表 144 mm）** が「法律ギリギリ径」の実用解であり、その最大設定は 4.5 km/h。(a) の FW clamp を「構造」の根拠に認めるなら 150〜160 mm まで広がる（最大設定 4.7〜5.0 km/h）が、[06 §2](06-hardware-delta-and-base-selection.md) は保守側 (b) に基準を固定している（[01 §10](01-legal-envelope-japan.md) のグレーが事前相談で解けるまで）。6 km/h を**維持**したいなら径ではなくモータ回転数（1:40 L 型）と FW 定数の側で解く（§9 案 C）。

## 4. 4WD の要否

### 4-1. 駆動方式の比較（284 × 231 mm・6 kg・4〜6 km/h・歩道）

| 方式 | 濡れタイル / 草 / 砂利 | 2 cm 段差 | 5 cm 段差 | 旋回（舗装） | odom | 制動 | 複雑度 | 判定 |
|---|---|---|---|---|---|---|---|---|
| **4WD skid-steer（4 輪固定・全輪駆動）** | ◎ 全輪に荷重と駆動 | ◎ 後輪が押し前輪が乗る | ○ 径次第 | △ 4 輪が擦る（トルク大・タイヤ摩耗） | △ 旋回でスリップ（IMU / RTK で補う） | ◎ 全輪制動 | 中 | **採用** |
| 2WD 差動 + キャスタ | △ 駆動輪に荷重を寄せる必要 | **✗** キャスタ 50 mm で h/R = 0.8 | ✗ | ◎ | ◎ | △ 駆動輪のみ（約 55 %） | 低 | 不採用 |
| 2WD + 固定従動輪 2 | △ | ✗ 従動輪が押されて登れない | ✗ | ✗ 従動輪が横滑り＝実質 skid | ○ | △ | 低 | 不採用 |
| 6 輪ボギー（Starship 型） | ◎ | ◎ | ◎ | △ | △ | ◎ | 高 | 将来ベース候補 |
| メカナム（現状） | **✗** 45° ローラで粗面トラクション低下・砂利不適・水でローラ錆 | ✗ ローラが段差に嵌る | ✗ | ◎ | ✗ 不規則ドリフト | △ 横滑り | 中 | **屋外で放棄** |

商用の歩道級ロボットは全数が「全輪に荷重が載る駆動輪」＝4WD 相当か 6 輪で、**キャスタ採用機・メカナム採用機は見当たらない** [L]: Starship = 6 輪ボギー（縁石昇降が設計目的）・Cartken Model C = 6 輪（段差 40 mm 自動 / 150 mm 遠隔）・Hakobot = 4WD + 4WS・ノーパンクタイヤ・KCCS 中速機 = 四輪駆動でグリップタイヤ。同一シャシで通常タイヤとメカナムを比べた例（AgileX Scout Mini）では**メカナムで最高速が約半分**（2.7 → 1.3 m/s）[L]（出典は §References）。

### 4-2. 段差の幾何と 4WD の効き

剛体輪が高さ h の段に当たるとき、角の接触点まわりの釣り合いから（[I]・W = 1 輪の接地荷重）:

- **駆動輪に要るトルク** `T ≥ W·√(h(2R − h))`（幾何上限 h = R）。
- **従動輪を水平に押す力** `F/W = √(h(2R − h)) / (R − h)`（h → R で発散）。実務則: 駆動輪 ≈ 0.5 R が無理なく、4WD ＋ 助走で 0.8 R まで。**従動輪 / キャスタは 0.2 R が実用上限**。

| D | R | 2 cm の h/R | 5 cm の h/R | 2 cm に要る T（W = 14.7 N・6 kg / 4） | 判定（1:56 定格 0.637 / 停動 0.814 N·m） |
|---|---|---|---|---|---|
| 80 mm（現状） | 40 | 0.50 | 1.25（不可） | 0.51 N·m | トルクは足りるが幾何が限界 |
| 127 mm | 63.5 | 0.31 | 0.79（限界） | 0.68 N·m | 定格超・停動未満 |
| **144 mm** | 72 | **0.28** | 0.69 | **0.73 N·m** | **定格超・停動未満 → 助走 ＋ 4WD（後輪の押し）前提・実測ゲート** |
| 150 mm | 75 | 0.27 | 0.67 | 0.75 N·m | 同上 |

歩車道境界の段差は**標準 2 cm**（[06 §3](06-hardware-delta-and-base-selection.md) の国交省基準）。150 mm 化で幾何は楽になるが、**同じ絶対段差に要るトルクは R とともに増える**（大径化はトルク側では不利）。4WD なら後輪の牽引力（μ·W）が前輪の乗り上げを助けるため、上の「単輪」計算より要求は下がる [I]。**2WD ではこの助けが無い。**

### 4-3. 制動とトルク余裕（4WD vs 2WD）

- 制動は摩擦限界 `a = μ·g·(制動輪荷重比)`。濡れタイル μ = 0.5 では **4WD 4.9 m/s²・2WD（荷重 55 %）2.7 m/s²** → 1.67 m/s からの純制動距離 **0.28 m vs 0.52 m**（約 1.9 倍）[I]。
- 勾配 10 %・Crr 0.05・6 kg の必要推力 8.8 N に対し、1:56 / 144 mm の定格推力は **4WD 35.4 N（4.0 倍）・2WD 17.7 N（2.0 倍）**（§5）。
- 4 モータは既に搭載済（[shared/02:310](../shared/02-hardware-design.md:310) AM2861 × 4）。**4WD 維持の追加費用はゼロ**。

**結論**: 4WD は法的には不要だが、段差・制動・勾配・追加費用のすべてで **4 輪固定・全輪駆動（skid-steer）を維持**する。M1 type の混合式は `vy = 0` なら左右差動に一致するため、**契約（`linear.y = 0` の diff-drive）は不変**（[shared/02:324](../shared/02-hardware-design.md:324)）。

## 5. トルク・電力・航続（m = 6 kg・D = 144 mm）

**利用可能推力**（`F = T / r`・r = 0.072 m。150 mm なら 4 % 小さい）:

| モータ | T 定格 / 停動 [N·m] | F/輪 定格 / 停動 [N] | 4WD 定格 / 停動 [N] | 2WD 定格 [N] |
|---|---|---|---|---|
| **1:56** | 0.637 / 0.814 | 8.9 / 11.3 | **35.4 / 45.2** | 17.7 |
| 1:40 L | 0.431 / 0.981 | 6.0 / 13.6 | 24.0 / 54.5 | 12.0 |
| 1:30 | 0.324 / 0.471 | 4.5 / 6.5 | 18.0 / 26.1 | 9.0 |

**必要推力** `F = m·g·(sinθ + Crr·cosθ)`（6 kg・Crr 0.05）: 平地 2.9 N・5 % 5.9 N・10 % 8.8 N・15 % 11.6 N。**余裕率（4WD 定格）**: 1:56 = 12.0 / 6.0 / **4.0** / 3.0 倍、1:40 L = 8.2 / 4.1 / 2.7 / 2.1 倍、1:30 = 6.1 / 3.1 / 2.0 / 1.5 倍。摩擦天井（μ 0.6・N = 58.8 N）は 35 N なので、1:56 の 4WD 停動 45 N は**グリップ律速**＝トルク不足ではない [I]。

**電力・航続**（電池 72 Wh・使用可 80 % = 57.6 Wh。3S 公称 11.1 V なら 66.6 Wh ＝ 約 −8 %）[I]:

| 条件 | 走行 P（機械） | 走行 P（電気・η 0.55） | + ホスト 25 W | + ホスト 35 W |
|---|---|---|---|---|
| 5 km/h・平地・Crr 0.03 | 2.5 W | 4.5 W | 29.5 W → **1.96 h / 9.8 km** | 39.5 W → 1.46 h / 7.3 km |
| 5 km/h・5 %・Crr 0.05 | 8.2 W | 14.8 W | 39.8 W → 1.45 h / 7.2 km | 49.8 W → 1.16 h / 5.8 km |
| 6 km/h・平地・Crr 0.05 | 4.9 W | 8.9 W | 33.9 W → 1.70 h / 10.2 km | 43.9 W → 1.31 h / 7.9 km |
| 6 km/h・5 %・Crr 0.05 | 9.8 W | 17.8 W | 42.8 W → 1.35 h / 8.1 km | 52.8 W → 1.09 h / 6.5 km |

支配項は**ホスト（Orin + センサ 25〜35 W）**で、走行は 5〜20 W。速度を 4.7 → 6 km/h に上げても航続時間は −8 % 程度（**距離はむしろ伸びる**）。エネルギーは径の制約にならない。ただし**停動時 4 A × 4 = 16 A** は非安定化 12 V レール（定格 4 A・ヒューズなし・[shared/02:318](../shared/02-hardware-design.md:318)）の瞬時制約で、§8-1 の低電圧ラッチと結合する。

## 6. 動特性（停止距離・25 Hz 報告・odom 分解能）

| 速度 | 反応 0.2 s・減速 1.5 m/s² | 反応 0.4 s・減速 1.5 m/s² | 反応 0.2 s・減速 2.5 m/s² | 40 ms あたりの進行 |
|---|---|---|---|---|
| 4.5 km/h（1.260 m/s・144 mm clamp） | **0.78 m** | 1.03 m | 0.57 m | **50.4 mm** |
| 4.7 km/h（1.313 m/s・150 mm clamp） | 0.84 m | 1.10 m | 0.61 m | 52.5 mm |
| 6.0 km/h（1.667 m/s） | **1.26 m** | 1.59 m | 0.89 m | **66.7 mm** |

- 6 km/h 化は停止距離を約 +50 % 悪化させる。L1 の停止ポリゴン（C-3・[ADR-0010:28](../adr/0010-raise-speed-cap-to-platform-max.md:28) の `margin = v_max × t_react`）と L2 鮮度窓は新 `v_max` で再導出（[00 §4 行 7](00-mission-and-scope.md)）。
- MCU 報告は 25 Hz 固定（[ADR-0010:17](../adr/0010-raise-speed-cap-to-platform-max.md:17)）。4.7 km/h なら 1 周期 52.5 mm（0.7 m/s 時 28 mm の約 1.9 倍）。Nav2 MPPI は 25 Hz で成立するが、6 km/h では **30 Hz 以上**が望ましい [I]。
- odom 分解能（1:56・2464 counts/rev・150 mm）: **5,229 counts/m・0.19 mm/count**・40 ms あたり 275〜349 counts → 速度量子化 0.3〜0.4 %。分解能はボトルネックにならない [I]。
- 重心: 輪距 194 mm・重心高 0.25 m（非常停止柱・アンテナ込みの推定）で横加速度限界 **3.8 m/s²（0.39 g）** → 1.67 m/s の最小安全旋回半径 **0.73 m**、1.31 m/s で 0.45 m。2 % 横断勾配自体は無害、危険なのは**全速旋回 ＋ 縁石横当たり** [I]。
- 運動エネルギー ½·6·1.667² = **8.3 J**。バンパ変位 20 / 50 / 60 mm で平均接触力 417 / 167 / 139 N。ISO/TS 15066 の胸部準静的限界 140 N と比べると、**60 mm 以上ストロークするコンプライアントバンパ**で正面接触が協働ロボの痛み閾値域に収まる [L][I]。法令に運動エネルギー・制動距離の基準は無い（[01 追補②](01-legal-envelope-japan.md)）。

## 7. 車体・機械の制約（M1 実機）

| 項目 | 値・所見 | 出所 | 確度 |
|---|---|---|---|
| ホイールアーチ | **存在しない**。車輪頂点 80 mm ＞ 車体上面 74.58 mm（[shared/02:302](../shared/02-hardware-design.md:302)）＝車輪は側板の外側にあり車体より高い。前回所見（2026-09-12 午前）の「アーチ干渉」は**撤回** | [D] 寸法 ＋ 説明書表紙写真 | 高 |
| 実際の干渉部位 | **前後ロアバンパ / スカート**（前後方向）。127 mm でタイヤ最前点が中心から 63.5 mm → バンパ張出し（≈ 全長 284.4 − 軸間 185 → 片側 ≈ 50 mm）を **≈ 14 mm** 超える、160 mm で ≈ 30 mm | [I]（軸間推定に依存） | 中・要現物 |
| 軸間 / 輪距 | ≈ 185 / ≈ 194 mm（§1）。**前後輪が当たる径 ≈ 185 mm・実用 ≤ 160〜170 mm** | [I] | 中（STEP 取得か 5 分実測で確定） |
| 地上高 | 現状 **推定 20〜30 mm**（最低点 = バッテリ底カバー or モータ胴）。144 mm 化で **+32 mm**（150 mm なら +35 mm）。デッキ 74.58 → 106.6 mm・LiDAR 上面 147.5 → 179.5 mm（URDF / Nav2 の Z・重心がすべてずれる） | [I] | 低 |
| 車輪取付 | 6 mm D 軸 → **6 mm 六角メタルカップリング**（イモネジ）→ 車輪。モータは側板内側に L 字ブラケット・軸が側板を貫通。幅広輪は**長いカップリング / スペーサ**で逃がす | Yahboom wheel-set / 520 motor 公式 [D]・説明書 p.3 [L] | 中 |
| 純正の大径輪 | **無い**。Yahboom wheel-set の最大は通常輪 **85 mm**・メカナム **97 mm**（6 mm カップリング品）。127〜160 mm はサードパーティ ＋ 6 mm D 軸ハブが必須 | Yahboom wheel-set 公式（参照日 2026-09-12）[D] | 高 |
| 市場クラス（6 mm D 軸・要見積） | (1) PU ソリッド 125〜144 mm スクータ輪（608 ベアリング）＋ **Pololu 6 mm 軸用スクータホイールアダプタ**（最短経路・幅 24〜29 mm）(2) Nexus 152 mm メカナム（600 g/輪・幅 55 mm＝屋外不採用）(3) AndyMark 6 in 空気入り（1/2 in 六角前提＝要変換・実質不適） | Pololu 2674 / 3281・Nexus 14101L / 18007・AndyMark [L] | 中 |
| **6 mm D 軸の片持ち** | 37 mm 級ギヤモータの出力軸は**ブッシュ 1 個支持**で、ロボット重量の径方向荷重を受けるべきでない（Pololu 公式フォーラム）。径 1.9 倍で同じ牽引力に対する曲げモーメントも約 1.9 倍・段差衝撃はピーク。**緩和（優先順）**: ① 車輪ハブ内に 608 ベアリング 2 個 ＋ ピロー / フランジ軸受で径方向荷重を筐体で受け、モータ軸はトルク伝達のみ ② 片持ち長を最小化（幅 24〜29 mm 級・ハブ内側面をギヤボックス端面へ寄せる）③ ベルト / チェーンで軸分離 | Pololu forum 8629 / 5242 [L]・[I] | 中 |
| 車輪質量 | 0.1 kg（純正 80 mm メカナム）→ 0.3〜0.7 kg/輪。回転慣性込みの等価質量 **+1〜2 kg**（加減速・停止距離・トルク余裕に効く） | [I] | 推定 |
| タイヤ | 空気入り = 衝撃吸収に優れるがパンク・軸径変換 / **PU ソリッド・ノーパンク** = 保守不要（Hakobot・Scout Mini と同型）/ フォーム充填 = 重い。濡れ点字ブロックの μ は実測 | [L][I] | — |
| 乗り上げ | 半径の 1/3 ≈ 24 mm が目安（144 mm）。**歩道の 2 cm 切り下げは幾何上は可**・トルクは §4-2 | [I] | — |

## 8. ファーム挙動（屋外速度に効く 5 点・vendor V3.6.5 実ソース）

すべて `ROS-Driver-Board-FW-master.zip`（[shared/02:797](../shared/02-hardware-design.md:797)）の `Source/` を執筆者が実 Read [D]。車体側の正本 [mode-m1/02](../mode-m1/02-m1-driver-and-watchdog.md) 末尾追補（2026-09-12）へ同内容を forward link 済（本 doc は屋外への含意だけを持つ）。

1. **低電圧はラッチ式ハード停止**: `app_bat.c` は 9.6 V 以下（`:83 return 96`）または 13.0 V 以上（`:94`）を `BAT_CHECK_COUNT 20` × 100 ms ＝ **2 秒**連続で検出すると `g_system_enable = 0`（`:108`）にし、コメントどおり**リセットでしか復帰しない**（`:13`「只能通过复位恢复」）。停止は `Motion_Stop(STOP_BRAKE)` の周期実行。6.5〜8.5 V の読みは「電池非装着」として無視（`:58`）。→ 大径化で電流が増え、非安定化レールが停動時に 9.6 V を 2 秒割ると**歩道上で電源再投入まで復帰不能**になる。屋外では **operational stop（運用停止）の一種として遠隔卓へ通知**し、電流・電圧ログ（§10）で閾値との距離を測る。
2. **ゼロ指令はアクティブブレーキ**: `Mecanum_Ctrl` は `(0,0,0)` を受けると即 `Motion_Stop(STOP_BRAKE)`（`app_mecanum.c:34-38`）＝H ブリッジ短絡ブレーキ（惰行ではない）。150 mm ゴム輪・1.3 m/s では運動エネルギーと車輪慣性が大きく、**ホスト側（L0' / Nav2）でランプダウン**しないとピッチング / スキッドを起こす [I]。非常停止・W-1/W-2 の**即時停止経路は逆にこの挙動を使う**（変えない）。
3. **yaw-adjust ビットは 0 を厳守**: `config.h:26 ENABLE_YAW_ADJUST 1` は常時有効だが、発動は `FUNC_MOTION` の car_type バイト bit 0x80 をホストが立てたときだけ（`protocol.c:525,534`）。有効時は IMU ヨー PID が左右輪に `∓g_offset_yaw` を足し、**指令 wz は目標に反映されない**ため旋回を打ち消す。skid-steer の通常輪では `0x80 = 0`（`m1_driver` の R-26 unit に pin する候補）。
4. **通信途絶停止は無い（既知）・SBUS 経路に雛形あり**: `ENABLE_IWDG 0`（`config.h:17`）かつ `IWDG_Init()` は宣言のみで呼び出し 0 件（`bsp_wdg.h:5`）。USB / シリアル経路にタイムアウトは無い（[mode-m1/02:25](../mode-m1/02-m1-driver-and-watchdog.md:25) と一致）。`app_sbus.c:122` の `stop_count = 100` は**フレームが届いている間の中立デバウンス**であり、リンク断 watchdog ではない。ただし同じ `Motion_Stop(brake)` とカウンタ手法で [ADR-0013](../adr/0013-stm32-command-stream-watchdog.md) の追記の雛形になる。
5. **FW 再ビルドの実体（ADR-0013 前提ゲートへの申し送り・未裁定）**: 配布 zip のプロジェクトは **Keil MDK（`rosmaster.uvprojx`・`<ToolsetName>ARM-ADS`・`<uAC6>0` ＝ ARM Compiler 5）のみ**で、Makefile / CMake / STM32CubeIDE / IAR プロジェクトは**無い**。`output/rosmaster_V3.6.5.hex` は Intel HEX テキスト 256,343 B（バイナリ換算 **≈ 86〜89 KiB [I]**）＝ **MDK-Lite の 32 KB 制限を超える**。→ 再ビルドには**有償 MDK-ARM ＋ レガシー AC5**、または **arm-none-eabi-gcc への移植**（StdPeriph + FreeRTOS + `startup_stm32f10x_hd.s` の armasm 構文書き直し）が要る。書込は UART ISP（USART1 = PA9/PA10・`bsp_usart.c:72,78`・BOOT0 ジャンパ）で可＝[ADR-0013](../adr/0013-stm32-command-stream-watchdog.md) の Context と整合。ADR-0013 が引く公式 wiki の「STM32CubeIDE」は開発環境の一般案内であり、**配布ソースの実体は Keil**である点を前提ゲート (c) に加える提案（[mode-m1/02](../mode-m1/02-m1-driver-and-watchdog.md) 末尾追補）。**大径化（§9 案 A）に FW 再ビルドは不要**。

## 9. 選択肢と推奨

| 案 | 内容 | 実現速度（最大設定 / 9.6 V 負荷） | 「構造上 6 km/h 未満」の根拠 | 2 cm 段差 | 必要作業 | 判定 |
|---|---|---|---|---|---|---|
| **A. 1:56 + 144 mm 級（≤147 mm）通常輪・stock FW・M1 type**（**Phase 1・推奨**） | ホスト L0' に車輪スケール k = 144/80 = 1.8（送信 `v / k`・odom は `0x0D` 生カウント × 真の周長）。契約 `MAX_LINEAR_VELOCITY` を実単位で再 pin | **4.5 / 4.1 km/h** | **物理（満充電無負荷 5.8 km/h ＜ 6.0）＋ FW clamp（MCU 側）** | 定格超・停動未満（助走 + 4WD 前提） | 車輪・ハブ・外部軸受・バンパ処理・k の実測校正・R-26 unit・contract PR | ◎ |
| A'. 1:56 + 150 mm | 同上・k = 1.875 | 4.7 / 4.3 | FW clamp のみ（満充電無負荷 6.1 = 保守基準超） | 同上（T 0.75 N·m） | 同上 + [01 §10](01-legal-envelope-japan.md) のグレー（clamp を構造と認めるか）を事前相談で確定 | ○（グレー解消時） |
| A''. 1:56 + 160 mm | 同上・k = 2.0 | 5.0 / 4.6 | FW clamp のみ（満充電無負荷 6.5） | 同上（T 0.78 N·m） | 同上 + 前後隙間 25 mm・バンパ 30 mm 超え | △（グレー + 現物次第） |
| B. stock FW のまま 1:40 L / 1:30 換装 | 径 150 mm | 6.6 / 6.3（1:40）・8.8 / 7.0（1:30） | **失われる**（ホスト clamp のみ・グレー） | 1:40 停動 13 N/輪は最良 | 4 モータ交換 + PID 再調整 | ✗（法定枠外） |
| **C. FW 定数修正（実円周 471.24 mm・counts 1760・clamp 1667）+ 1:40 L + 150 mm**（Phase 2・ADR 要） | FW の速度単位を実単位に正し、clamp = 6.0 km/h。1:56 は 9.6 V で 4.3 km/h までしか出ないため 1:40 L がセット | **6.0 / 6.0 km/h**（能力 6.3） | **FW clamp（実単位）＋ ホスト clamp**。型式基準の「最大設定」を 6.0 で実測 | 1:40 停動 0.98 N·m で余裕 | **Keil 有償 or GCC 移植**（§8-5）・ADR-0013 の fork と同一運用・停止距離 +50 %・MPPI ≥ 30 Hz・C-3 再導出・ADR-0010 の契約意味の再定義 | △（6 km/h が要件なら唯一の解） |
| D. 1:56 + 210 mm（解釈 (c) の厳密解） | 6 km/h を 9.6 V まで維持 | 6.0+ | 失われる（clamp 6.6） | — | 軸間 185 mm に入らない・車体再設計 | ✗ |
| E. ハブモータ + VESC / ODrive ベース | 6.5〜8 in ハブモータ。**VESC: CAN 無通信 0.5 s 既定で停止・timeout brake current・ERPM 上限 / ODrive: `watchdog_timeout` で IDLE** ＝ [mode-m1/02](../mode-m1/02-m1-driver-and-watchdog.md) W-3 と速度上限を**コントローラ FW が提供** | ◎ 余裕（上限固定が要る） | コントローラ FW（ERPM 上限） | ◎ | L0' driver 新設（ADR-0011 ros2_control と合流）・機械設計・5〜15 万円 | survey-first の B 案（[06 §4](06-hardware-delta-and-base-selection.md)） |
| F. COTS 屋外ローバー | Waveshare UGV Rover（1.3 m/s = 4.7 km/h・$540〜）/ AgileX Scout Mini（2.7 m/s・段差 70 mm・$8,280〜・上限固定は自前）/ Clearpath Jackal（2.0 m/s・€36k 級） | 機種次第 | 自前 | ◎ | 価格・Humble ドライバ | 参考 [L] |

**推奨**: **A（144 mm 級・≤147 mm）を Phase 1** とする（4 km/h 目標を 9.6 V まで満たし、法定「構造上 6 km/h 未満」を**物理と FW clamp の両方**で満たす唯一の低コスト解。[06 §2](06-hardware-delta-and-base-selection.md) の保守基準と整合）。150 mm 以上（A'/A''）は「ソフト / FW の clamp を構造と認めるか」の事前相談結果が出てから。**「法律ギリギリ（6 km/h）で走る」が要件になった時点で C を ADR で裁定**する（ADR-0013 の toolchain 課題と束ねる）。B は採らない。

## 10. 実測ゲート（設計値を確定するための試験・すべて未実施）

| # | 試験 | 確定するもの | 手順の正本 |
|---|---|---|---|
| G-W1 | **軸間・輪距・地上高・バンパ張出しの実測 5 分**（または `ROSMASTER M1-V1.0.STEP` 取得） | §1 / §7 の「推定」4 項目・径の機械上限 | [shared/02:761](../shared/02-hardware-design.md:761)（STEP 未取得） |
| G-W2 | **S-SPEED を型式認定基準の手順で**（水平 20 m・助走 10 m・測定 10 m・往復・電池 ≥ 75 %・最大設定・`V = 36/T`） | 届出に書く「構造上出すことができる最高の速度」・k の妥当性 | [01 追補②](01-legal-envelope-japan.md) / [ADR-0010 §Open 2](../adr/0010-raise-speed-cap-to-platform-max.md) |
| G-W3 | 2 cm 段差乗り越え（前進・助走あり / なし・積載あり） | §4-2 のトルク余裕 | 本 doc |
| G-W4 | **UMBmark**（直線 10 m 往復 + 方形路・前後両方向） | odom の車輪スケール k と有効転がり半径（3 % 誤差 = 200 m で 6 m） | 本 doc |
| G-W5 | 傾斜台の転倒角（柱・アンテナ・荷物箱を載せた状態） | §6 の横加速度限界 | 本 doc |
| G-W6 | 停動・急加速時の電池電圧ログ（`0x0A` 自動レポート）と 9.6 V ラッチの有無 | §8-1 の運用余裕・レール保護の要否 | [shared/02:448](../shared/02-hardware-design.md:448) |
| G-W7 | 濡れタイル / 点字ブロックの制動距離 | §6 の μ | 本 doc |
| G-W8 | ゼロ指令のブレーキ挙動（ランプダウン有無での挙動差） | §8-2 | 本 doc |

## 11. OPEN QUESTIONS（接頭辞 `OQ-OD7*`）

- `OQ-OD70` 6 km/h（案 C）を要件にするか、Phase 1（案 A・4.7 km/h）で公道まで進むか（ユーザー裁定・ADR-0014 の一部）。
- `OQ-OD71` 車輪スケール k を L0' のどこに持つか（`m1_driver` param か凍結契約の定数か）。契約 `MAX_LINEAR_VELOCITY` の再 pin 値（案 A: 実単位 1.26 m/s 候補〔144 mm〕・150 mm なら 1.31 m/s）と ADR-0010「platform max」の意味の再定義（[00 §4 行 7](00-mission-and-scope.md)）。
- `OQ-OD72` car_type を M1（0x0A・`vy = 0`）のまま使うか X1（0x04・差動・clamp 1000）へ切り替えるか。X1 は APB 164.555 で wz スケールが約 1.7 倍ずれる（[I]）ため、切り替える場合も odom は生カウントから自前。
- `OQ-OD73` 外部軸受（ピロー / フランジ）の実装形と側板の加工範囲。
- `OQ-OD74` 低電圧ラッチ（§8-1）を「operational stop」として遠隔卓へどう通知するか（[05](05-safety-envelope-and-intervention.md) の producer に含めるか）。
- `OQ-OD75` ADR-0013 前提ゲートへの toolchain 追記（Keil 有償 / GCC 移植）の裁定（[mode-m1/02](../mode-m1/02-m1-driver-and-watchdog.md) 末尾追補）。

## References

docs 内（file:line は執筆時に実 Read）:

- [ADR-0010](../adr/0010-raise-speed-cap-to-platform-max.md)（[:12](../adr/0010-raise-speed-cap-to-platform-max.md:12) clamp / [:13](../adr/0010-raise-speed-cap-to-platform-max.md:13) 車輪定数 / [:17](../adr/0010-raise-speed-cap-to-platform-max.md:17) 25 Hz / [:28](../adr/0010-raise-speed-cap-to-platform-max.md:28) C-3 margin / [:56](../adr/0010-raise-speed-cap-to-platform-max.md:56) clamp 変更却下 / [:63](../adr/0010-raise-speed-cap-to-platform-max.md:63) 実機確認）/ [ADR-0013](../adr/0013-stm32-command-stream-watchdog.md)（additive fork・UART ISP）
- [shared/02-hardware-design.md](../shared/02-hardware-design.md)（[:302](../shared/02-hardware-design.md:302) 寸法 / [:310](../shared/02-hardware-design.md:310) 拡張ボード / [:318](../shared/02-hardware-design.md:318) 12 V レール / [:324](../shared/02-hardware-design.md:324) `linear.y = 0` / [:433](../shared/02-hardware-design.md:433) ヒューズ / [:448](../shared/02-hardware-design.md:448) 電圧監視 / [:543](../shared/02-hardware-design.md:543)〜[:551](../shared/02-hardware-design.md:551) V-2 モータ表 / [:566](../shared/02-hardware-design.md:566) スリップ / [:585](../shared/02-hardware-design.md:585) 520 motor URL / [:749](../shared/02-hardware-design.md:749) FW 定数 / [:761](../shared/02-hardware-design.md:761) STEP 未取得 / [:797](../shared/02-hardware-design.md:797) FW ソース / [:812](../shared/02-hardware-design.md:812) ライセンス）
- [mode-m1/02-m1-driver-and-watchdog.md](../mode-m1/02-m1-driver-and-watchdog.md)（[:25](../mode-m1/02-m1-driver-and-watchdog.md:25) fail-active / [:31](../mode-m1/02-m1-driver-and-watchdog.md:31) 幾何ハードコード / [:64](../mode-m1/02-m1-driver-and-watchdog.md:64) W-3 / [:65](../mode-m1/02-m1-driver-and-watchdog.md:65) W-4 / 末尾追補 2026-09-12 = §8 の車体側正本）
- [01 法規包絡](01-legal-envelope-japan.md)（§2 枠 / §3 非常停止 / §10 グレー / 追補② 型式認定基準）/ [00 §4](00-mission-and-scope.md) / [05](05-safety-envelope-and-intervention.md) / [06](06-hardware-delta-and-base-selection.md)
- vendor FW V3.6.5 実ソース（zip = [shared/02:797](../shared/02-hardware-design.md:797)・repo 外）: `Source/APP/app_mecanum.h:11,31,35,37,39` / `app_mecanum.c:28-38,56-65` / `app_motion.h:8-17` / `app_motion.c:34-64,236-247,350-360` / `app_fourwheel.h:9,13` / `app_fourwheel.c:34,47-54` / `app_bat.c:11,13,58,83,94,108` / `app_sbus.c:122,151-170` / `config.h:17,26` / `protocol.c:525,534` / `Source/BSP/bsp_motor.h:87-89` / `bsp_wdg.h:5` / `bsp_usart.c:72,78` / `rosmaster.uvprojx` / `output/rosmaster_V3.6.5.hex`

一次情報（参照日 2026-09-12）:

- 警察庁 通達 丙交企発第 118 号「遠隔操作型小型車の型式認定制度の概要及び運用上の留意事項について」別添「遠隔操作型小型車の型式認定基準」<https://www.npa.go.jp/laws/notification/koutuu/kouki/04enkakusousagatakogatasya.pdf>（最高速度試験・非常停止装置・突出 8 mm・高さ測定）[D]
- Yahboom「520 motor introduction and usage」<https://www.yahboom.net/public/upload/upload-html/1742005967/0.520%20motor%20introduction%20and%20usage.html>（減速比別 rpm / トルク / 電流・6 mm D 軸・11 線）[D]
- Yahboom wheel-set <https://category.yahboom.net/products/wheel-set>（純正最大 = 通常輪 85 mm・メカナム 97 mm・6 mm カップリング）[D]
- Pololu: 6 mm 軸用スクータホイールアダプタ <https://www.pololu.com/product/2674> / スクータホイール 144×29 mm <https://www.pololu.com/product/3281> / フォーラム「Radial load and number of bearings of 37D gearmotor」<https://forum.pololu.com/t/radial-load-and-number-of-bearings-of-50-1-metal-gearmotor-3/8629>・<https://forum.pololu.com/t/max-load-of-the-29-1-metal-gearmotor-37dx52l-1443/5242>（片持ち軸の弱点）[L]
- Nexus Robot 152 mm メカナム <https://www.nexusrobot.com/product/6-inch-152mm-mecanum-wheel-left-bearing-rollers14101l.html> / 6 mm ハブ <https://www.nexusrobot.com/product/6mm-universal-aluminum-mounting-hubs-for-shaft-18007.html>（600 g/輪・幅 55 mm）[L]
- 商用機の駆動方式: Starship <https://www.starship.xyz/our-robots/> / Cartken Model C（RobotATTA）<https://www.robotatta.com/products/2077> / ZMP DeliRo <https://www.robotatta.com/ja/products/2> / Hakobot <https://note.com/hakobot/n/n2d80bf88cbc6> / KCCS <https://www.kccs.co.jp/contents/mobility/> / AgileX Scout Mini 仕様 <https://docs.trossenrobotics.com/agilex_scout_mini_docs/specifications.html>（通常輪 2.7 m/s vs メカナム 1.3 m/s・段差 70 mm）[L]
- メカナムの屋外適性 <https://www.bhthv.com/blog/can-mecanum-wheels-be-used-in-outdoor-environments-175973.html> / Hackaday「All About Mecanum」<https://hackaday.com/2022/01/20/all-about-mecanum/> [L]
- 段差乗り越えとサスペンションの μ 条件: MDPI Machines 14(3):334 <https://doi.org/10.3390/machines14030334> [L]
- モータコントローラの安全機能: VESC CAN <https://github.com/vedderb/bldc/blob/master/documentation/comm_can.md> / ODrive CAN protocol（watchdog）<https://docs.odriverobotics.com/v/0.6.7/manual/can-protocol.html> [L]
- ISO/TS 15066 の身体部位別限界（A3 解説）<https://www.automate.org/robotics/tech-papers/iso-ts-15066-explained> [L]
- 歩道の構造（2 cm 段差・2 % 横断勾配）: [06 §3](06-hardware-delta-and-base-selection.md) の国土交通省基準 [D]

---

## 【2026-09-13 追補③】ユーザー裁定 = 150 mm（案 A'）・実装スライス 1・購入リスト

### ③-1. 裁定と根拠

- **裁定（2026-09-13・ユーザー）**: 車輪径は **150 mm** とし、法定「6 km/h を超える速度を出すことができない」の根拠として **stock FW の各輪 clamp（MCU 内・車輪 167 rpm）を「構造」に含める**（§2 表の 150 mm 行 = 最大設定 4.7 km/h・9.6 V 負荷 4.3 km/h・満充電無負荷 6.1 km/h）。[01 追補②](01-legal-envelope-japan.md) の型式認定基準が「速度を調整できるものは最大値にセットして実測」と定めるため、実測される最大値は clamp が決める 4.7 km/h になる。
- **退避先**: 届出前の事前相談（[01 §9](01-legal-envelope-japan.md)）で「ファーム clamp は構造ではない」と判断された場合は **144 mm（案 A・満充電無負荷 5.8 km/h）へ戻す**。実装は車輪径を param 化してあるので、**k を 1.875 → 1.8、`wheel_diameter_m` を 0.150 → 0.144 に変えるだけ**（§③-2）。[06 §2](06-hardware-delta-and-base-selection.md) の保守基準（≤147 mm）はこの退避条件として残す。
- 4WD 維持・メカナム放棄・その場旋回を使わない（円弧旋回）は §4 のとおり。

### ③-2. 実装スライス 1（branch `feat/m1-wheel-scale-odom`・PR pending・L0'）

`warehouse_m1_driver`（L0'・package-local・既定挙動は bit 等価）に以下を実装済（R-26 unit 61 本・mutation 11/11 KILLED・ruff/pytest/`check_consistency` 緑）:

| 要素 | 内容 | 既定値 | 150 mm での値 |
|---|---|---|---|
| `wheel_scale`（車輪スケール k） | clamp（実単位・凍結契約 `MAX_LINEAR_VELOCITY` 不変）の**後**に wire = 実速度 ÷ k。**範囲 [1.0, 2.5] 外・非有限は fail-closed**（全 command・全 tick が brake。1.0 への fallback は 150 mm 装着時に 1.875 倍の fail-open になるため採らない） | 1.0 | **1.875** |
| `yaw_scale` | wz にのみ追加で掛かる補正（FW の混合定数 APB 189.5 と skid-steer 実効輪距の差）。範囲 [0.2, 5.0] | 1.0 | 実測（G-W4/G-W9） |
| `lateral_enabled` | False で vy を clamp の**前**に 0（通常輪は横移動不可） | True | **False** |
| `odom_enabled` | True で `/bot1/odom`（`nav_msgs/Odometry`・[doc03:77](../architecture/03-software-architecture.md:77)）を publish。**TF は出さない**（[doc23:163](../architecture/23-perception-and-localization.md:163)） | False | **True** |
| `wheel_diameter_m` / `counts_per_rev` | `0x0D` 生カウント × 真の周長で積分（FW の 80 mm / 2464 counts を使わない） | 0.080 / 2464 | **0.150** / 2464 |
| `track_m` / `wheel_signs` | 差動積分の実効輪距・各輪の符号。**PROVISIONAL**（0.194 は導出値・符号は未確認） | 0.194 / [1,1,1,1] | G-W1 / `m1_probe` で確定 |
| `odom_period_s` / covariance | FW 25 Hz 報告に合わせ 0.04 s。共分散は暫定（twist 0.02・pose 1e3） | — | 実測 |
| backend seam | `read_encoders()`（vendor `get_motor_encoder()`・失敗は None＝publish しない） | — | — |

車体側の正本 [mode-m1/02](../mode-m1/02-m1-driver-and-watchdog.md) 末尾追補②に同内容を forward link 済。**契約 `MAX_LINEAR_VELOCITY`（0.3 m/s）の実単位再 pin は本スライス範囲外**（`OQ-OD71`・contract PR）。運用値（k・径・odom）は bringup/launch の param 注入で入れる（bringup 所有＝別 PR）。

### ③-3. 購入リスト

[06 末尾【2026-09-13 追補】](06-hardware-delta-and-base-selection.md) に最小構成とオプションを置いた。**2026-09-13 の再調査（2 レーン: 国内一般流通／海外ロボット流通）で訂正: 真の 150 mm 品は国内在庫にある**（流通はキックボードではなく**車いす前輪キャスタと台車車輪**。代表 = シシク PU-150: 150×35 mm・ボス幅 40 mm・軸径 8 mm・ボールベアリング・許容 75 kgf・0.28 kg・MonotaRO 当日出荷〔Bildy 掲載仕様で確認〕／Amazon の 6 インチ 5 穴ソリッド PU・608ZZ 品 ≈ ¥1,000〔仕様は検索スニペット確認・要現物〕）。経路は 3 つ: **(A') 国内 150 mm 品 ＋ Pololu #2674（6 mm D 軸アダプタ）＋ M3×35 長ねじ**（150 mm・k = 1.875・即納・**推奨**）／(A) Pololu 144×29 ＋ #2674（k = 1.8・退避先）／(B) 12 mm 穴ウレタン台車車輪（TRUSCO TYSUW-150 等）＋ φ12 スタブ軸 ＋ フランジ軸受（片持ち解消・軸設計要）。裁定 = `OQ-OD76`。

### ③-4. 追加の実測ゲート

- **G-W9 その場旋回**: ゴム輪の舗装路 μ ≈ 0.7 では旋回に要るトルク（0.71 N·m/輪）が停動（0.81）の 9 割。固定経路では円弧旋回を基本にし、停止時の向き直しだけ実測で可否を決める。
- G-W1（軸間・輪距・地上高・バンパ）→ `track_m` 確定、G-W4（UMBmark）→ k と `yaw_scale` 校正、`m1_probe` → `wheel_signs` 確定。

### ③-5. OPEN QUESTIONS（追加）

- `OQ-OD76` 車輪実体 (A') 国内 150 mm・608/8 mm 軸受品 ＋ #2674（推奨）/ (A) Pololu 144×29 / (B) 12 mm 穴台車車輪 ＋ スタブ軸 の選択。A' の残確認 = ハブ幅 40 mm に対する M3 長ねじ（#2674 付属は 14 mm）と、#2674 が 608 を置き換える構造ゆえ片持ちが残る点（外部軸受の要否は G-W1 の軸露出長で決める）。
- `OQ-OD77` 事前相談で「FW clamp = 構造」が否認された場合の手順（k / 径 param の切替と届出値の再実測）。

【2026-09-14 追記】`OQ-OD76` **裁定 = A'（シシク PU-150 ＋ Pololu #2674）**。実測（軸径 ≈ 6・側板外面→軸端 ≈ 19・D 面 15）で #2674 の取付は確定（[06 ④-9](06-hardware-delta-and-base-selection.md)）。車輪側の 4 点確認と確定購入リストは [06 ④-10](06-hardware-delta-and-base-selection.md)。退避 = Amazon 6 インチ 車いす前輪（608ZZ）→ #3281。
