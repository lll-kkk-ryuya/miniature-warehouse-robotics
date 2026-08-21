# ROSMASTER M1 紙説明書の転記（同梱部品・組立手順・配線・音声フロー）

> **位置づけ**: Yahboom ROSMASTER M1 に同梱される**紙説明書（12 ページ・2026-08-19 撮影）の転記**であり、一次資料の記録。**本 doc は設計判断を持たない**。電源・調達・マウント・知覚スタックの設計正本は
> [02-hardware-design.md](02-hardware-design.md)（§ROSMASTER M1 採用検討時の残課題 / §給電の配線設計）・
> [architecture/23-perception-and-localization.md](../architecture/23-perception-and-localization.md)・
> [ADR-0009](../adr/0009-m1-room-scale-operation.md)。説明書と設計正本が食い違う箇所（電源）は **§4 に明示**し、設計正本が優先する。
> 対象構成: 本プロジェクトは **Superior-without（SBC 無し版）＋手持ち Jetson Orin Nano**（[02:296](02-hardware-design.md) / [02:393](02-hardware-design.md)）。説明書は Jetson Nano B01 / Jetson Orin Nano / Raspberry Pi 5 / RDK X5 の 4 ボード共通版。
> 表記: 部品名末尾の丸数字（①〜⑦）は、ネジ類では**本数**、Accessory package では**袋番号**（説明書の表記慣習）。説明書中の `T-MINI PLUS` は正準表記 **T-mini Plus**（[02:333](02-hardware-design.md)）と同一物（本 doc の転記部は説明書シルクのまま残す）。

## 1. Shipping List（同梱部品一覧・転記）

### 1.1 Car body（Standard・全構成共通）

| 部品 | 補足 |
|---|---|
| Basic car body | **OLED とロボット制御基板（拡張ボード V3.0）はプリインストール済** |
| USB HUB expansion board | 上段に載せる USB ハブ基板 |
| USB wireless handle + AAA battery | ゲームパッド＋受信機＋単4電池 |
| 12.6V charger (2A, DC4017) | 充電器 |
| Battery pack (12.6V, 6000mAh) | 3S リチウム |
| Velcro strap | バッテリ固定用 |
| Black cable ties ×3 (100mm) | 充電ケーブル固定用 |
| Upper elbow USB to USB cable (30cm) | SBC⇔HUB 接続用 L字ケーブル |
| Crystal screwdriver / Orange screwdriver | ドライバー2本 |
| Accessory package ① | ネジ袋 |

### 1.2 AI large model voice module（Standard・全構成共通）

| 部品 | 補足 |
|---|---|
| AI large model voice module | 音声対話モジュール基板 |
| Speaker | スピーカー |
| Side elbow Type-C cable (25cm) | 接続ケーブル |
| Accessory package ⑤ | ネジ袋 |

### 1.3 オプション（該当品を購入した場合のみ同梱）

| パッケージ | 内容 |
|---|---|
| **Nuwa-HP60C depth camera** | カメラ本体、Camera bracket（**プリインストール**）、Side elbow Type-C cable (30cm)、Accessory package ② |
| **2DOF PTZ** | 2DOF PTZ 本体、PTZ package（M2\*4mm ネジ・M2\*5mm double-pass copper pillar 等） |
| **T-MINI PLUS LiDAR** | LiDAR 本体、Accessory package ③ |
| **Raspberry Pi board** | Pi 5 本体、Cool Cooler Pi5 radiator、TF card 128GB、Accessory package ⑦ + Double-elbow Type-C to Type-C data cable (30cm) |
| **RDK X5 board** | RDK X5 本体、RGB cooling HAT、TF card 128GB、Accessory package ⑦ + Double-elbow Type-C to Type-C data cable (30cm) |
| **Jetson Nano B01 board** | B01 本体、WiFi/Bluetooth network card、4010 fan、Patch antenna acrylic plate + PCB patch antenna ×2、U disk、Accessory package ⑥ + Double-elbow DC5.5\*2.1 power cable |
| **Jetson Orin Nano board** | Orin Nano 本体、**SSD**、Patch antenna acrylic plate、PCB patch antenna ×2、**DC5.5\*2.5 to XH2.54 power cable 2PIN**、Accessory package ⑥ |

**開梱記録（2026-08-19・実物確認）**: 本プロジェクト購入の Superior-without には **T-mini Plus LiDAR と Nuwa-HP60C 深度カメラが同梱されていた**（[02:393](02-hardware-design.md) と 1:1 同期）。Jetson Orin Nano board オプション列（SSD・パッチアンテナ・DC5.5×2.5⇔XH2.54 ケーブル・袋⑥）は**丸ごと非同梱**——SSD は別売購入の KIOXIA 1TB を充当し、給電は §4 の通り説明書の直結ケーブルを使わない（[02:320-321](02-hardware-design.md)）。**Orin 固定用のネジ・銅柱（袋⑥相当）が手元に無い可能性**に注意（マウント自体は 3D プリント・M3×4 = [02:414-420](02-hardware-design.md)）。

## 2. 組立手順（Installation Steps (Generally)・転記）

1. **Remove top cover** — M3\*6mm round head screw を外して上部カバーを外す。
2. **Remove battery bottom cover** — M3\*6mm を外して底面のバッテリカバーを外す。
3. **Main control board power cable wiring** — 説明書上はボード別に: Raspberry Pi・RDK X5 → Double-elbow Type-C ／ Jetson Nano B01 → Double-elbow DC ／ **Jetson Orin Nano → XH2.54 to DC cable**。車体の出口は「Type-C outlet」「DC / XH2.54 outlet」の2箇所。**⚠️ 本プロジェクトはこの手順を採らない（§4）**。
4. **Fixed power charging interface cable** — バッテリカバー横の4穴に結束バンド2本を仮止め（**締め切らない**）→ 充電ケーブルを通し、車体側面の開口に合わせてから固定。
5. **Install the battery pack** — Velcro で固定し、カバーを M3\*6mm で戻す。
6. **Install depth camera bracket** — M3\*6mm ×2（HP60C 購入時のみ。ブラケットはカメラ側にプリインストール）。
7. **Install depth camera** — M3\*6mm ×2 でブラケットへ。
8. **Install 2DOF PTZ**（購入時のみ）— ① Remove front trim cover（M3\*6mm）→ ② Disassembly 2DOF PTZ top → ③ Install 2DOF PTZ bottom servo（M2\*4mm screw + M2\*5mm double-pass copper pillar・PTZ package）。④以降は撮影範囲外（§7）。
9. **Install T-MINI PLUS LiDAR** — ① adapter plate を M3\*6mm ×3 で取付 → ② LiDAR 本体を M2\*8mm ×3 + M2\*10mm+4mm single-pass copper pillar ×3 で取付。**LiDAR の矢印を車体前方に向ける**。
10. **Install AI large model voice module and speaker** — M2.5\*5mm ×5、M2.5\*16+6mm copper pillar ×5、M2.5\*5+6mm copper pillar ×5（袋⑤）。
11. Final wiring（§3-§4）→ top cover を M3\*6mm で戻して完成。

## 3. SBC ボード取付（Jetson Orin Nano を軸に・転記）

### 3.1 Jetson Orin Nano board installation step（撮影された範囲）

1. **Install HUB expansion board** — M2.5\*22+6mm single-pass copper pillar ×6 ＋ M2.5\*5mm round head screw ×1。
2. 〜5. は撮影範囲外（§7。ボードインターフェース図から SSD 挿入・パッチアンテナ・ボードマウントに相当と推定）。本プロジェクトの Orin Dev Kit は取付板非互換の可能性があり**自作 3D プリントマウント前提**（[02:322](02-hardware-design.md) / [02:414-420](02-hardware-design.md)）。
6. **Jetson Orin Nano board wiring diagram** — 接続ケーブル: OLED cable（OLED-I2C）／ Cooling fan cable ／ **Upper-elbow USB to USB**（Orin ⇔ HUB）／ **XH2.54 cable**（電源。本プロジェクトでは §4 の昇圧経路に置換）／ **Side-elbow Micro USB to USB**（HUB ⇔ 拡張ボード＝シリアル）。

### 3.2 Jetson Orin Nano board interface（転記）

- **SSD insert into the north card slot**（M.2 Key-M。KIOXIA 1TB はここへ = [02:402](02-hardware-design.md)）
- Antenna ／ Cooling fan ／ OLED-I2C wiring
- 下面 USB: 1. Connect USB HUB expansion board、2. Connect handle receiver

### 3.3 参考: Jetson Nano B01 の取付手順（比較用・転記）

1. Install HUB expansion board → 2. Remove core module → 3. Install network card and patch antenna → 4. Install core module（**45° で挿入**）→ 5. Install cooling fan（M3\*14mm ×4）→ 6. Install board（M2.5\*5mm ×6）→ 7. Install patch antenna（acrylic plate 両面に貼付）→ 8. Wiring diagram（Orin と同型）。
Orin はコアモジュール脱着・network card 増設が無い分 B01 より単純。

## 4. 配線全体像と電源の差し替え（説明書 ≠ 設計正本の唯一の箇所）

配線図（Jetson Orin Nano Wiring Diagram）の転記:

| 系統 | 部品 | 接続 |
|---|---|---|
| 電源 | Battery (12.6V) → 拡張ボード V3.0 | 車体内蔵。**説明書は 12V 出力(XH2.54)→Orin 直結を図示** |
| 駆動 | M1〜M4 Motor（メカナム4輪） | 拡張ボード（IK は STM32 側 = [02:324](02-hardware-design.md)） |
| 制御基板 | 拡張ボード V3.0 | Side-elbow Micro USB → USB HUB board（シリアル・CH340） |
| ハブ | USB HUB board | Upper-elbow USB → Orin |
| センサ | T-MINI PLUS LiDAR | LiDAR cable → HUB 下段（driver は `ydlidar_ros2_driver` = [02:333](02-hardware-design.md)） |
| センサ | Nuwa-HP60C depth camera | Side elbow Type-C → HUB |
| UI | OLED display | OLED-I2C 配線（接続先ヘッダは推定・説明書のボード図参照） |
| UI | AI voice module + Speaker | Side elbow Type-C → HUB |
| その他 | 2DOF PTZ ／ Handle receiver ／ Cooling fan | PTZ/カメラ/音声は HUB 下段、receiver は Orin 直挿し |

> 注: 説明書は LiDAR・カメラ・音声を USB HUB に集約して Orin の 1 ポートへ入れるが、[02:404](02-hardware-design.md) は Orin の USB-A 直結（各スタック VBUS 3A 制限）前提で書かれている。矛盾ではなく、集約/直結は実装時判断。
>
> ⚠️ **電源は説明書どおりに配線しない**。拡張ボードの 12V 出力は生バッテリ電圧スルー（非安定化）・**定格 4A／ピーク 6A・保護なし**で、Orin 系統の最悪ケース約 7.5A に対して不足する（[02:318-319](02-hardware-design.md)）。既定構成は**バッテリー直タップ → ヒューズ 10A → 昇圧 DC-DC 12.6→19V → Orin DC 5.5×2.5**（[02:320](02-hardware-design.md) / [02:422-428](02-hardware-design.md)）。説明書の XH2.54 直結は「給電実測①〜④（[02:336-345](02-hardware-design.md)）合格時のみの縮退案」（[02:321](02-hardware-design.md)）。**昇圧モジュールは Orin 未接続で 19.0V に調整してから接続**（[02:451](02-hardware-design.md)。飛ばすと Orin 破壊）。部材の調達状況は [02:435-449](02-hardware-design.md) の台帳が正本（本 doc は複製しない）。

## 5. AI voice module のデュアルモデル推論フロー（Quick Start Tutorials・転記）

説明書「Dual model reasoning principle flow chart」:

1. **Start** → **Voice wake-up**（ウェイクワード: **"Hi, Yahboom"**）
2. **VAD voice activity detection** → **Local dynamic recording**
3. **Upload audio to the cloud** → **Speech recognition in the cloud** → 認識結果をクラウドへ
4. **Decision layer: Text large model** が指示分解・計画（disassembly instructions, planning steps）
5. **Cloud returns instruction content** → **Execution layer: Multi-modal large model** が制御指示と応答を生成
6. ローカルで **Analyze the returned instructions**: 実行アクションと返答テキストに分割
   - アクション側: **Call action function → Action execution** → 実行結果をクラウドへ返す（5 へフィードバック）
   - 返答側: **Upload text to the cloud → Cloud speech synthesis → Play audio**
7. **End** →（"One more loop" で 1 へ）

**位置づけ**: Yahboom 純正の**クラウド二段推論デモ**（Decision layer = テキスト LLM、Execution layer = マルチモーダル LLM）。本プロジェクトの層で言えば **L4 司令官相当を Yahboom クラウドが担う構成**であり、本プロジェクトの L4 は自作 LLM Bridge / X-ER Bridge（[architecture/08](../architecture/08-llm-bridge-common.md)）が担う——つまり**このクラウドフローは本プロジェクトの構成に存在しない**（事実帰結）。**voice module ハード（mic/speaker）自体の処遇は未決**であり、裁定の正本候補は音声入力側 [mode-x-er/04](../mode-x-er/04-er-input-modalities-and-stt.md)（入力 modality / STT）と音声応答側 [mode-x-er/05](../mode-x-er/05-operator-feedback-and-voice-response.md)（speaker = TTS sink・optional 縮退設計）。`# TODO(Phase 1)` 両 doc 側でハード採否を裁定。

## 6. 本プロジェクトでの利用マップ（転記 → 設計正本への接続）

| 説明書の内容 | 本プロジェクトでの扱い | 正本 |
|---|---|---|
| 組立手順（§2-§3） | そのまま使う（電源のみ §4 で置換） | 本 doc |
| 電源配線（XH2.54 直結） | **使わない**（昇圧 DC-DC 経由が既定） | [02 §給電の配線設計](02-hardware-design.md) |
| Orin の OS 導入 | microSD 経路で JetPack 6.2 → SSD 移行 | [02:407-412](02-hardware-design.md) / [02:149](02-hardware-design.md) / [setup/jetson-deploy.md](../setup/jetson-deploy.md) |
| 実機 bringup・安全ゲート | G0-G7・L0' 結線・部屋 SLAM の順（本 doc の範囲外） | [jetson/01](../jetson/01-fidelity-and-validation.md) / [mode-x-er/10](../mode-x-er/10-room-scale-safety-review.md) / [ADR-0009](../adr/0009-m1-room-scale-operation.md) |
| AI voice module クラウドフロー（§5） | 本プロジェクト構成に存在しない（L4 = 自作 Bridge）。**ハードの処遇は未決**（§5 の TODO） | [architecture/08](../architecture/08-llm-bridge-common.md) / [mode-x-er/04](../mode-x-er/04-er-input-modalities-and-stt.md) / [mode-x-er/05](../mode-x-er/05-operator-feedback-and-voice-response.md) |
| USB wireless handle | 転記事実のみ: receiver は Orin 直挿し。**手動走行の採否・その経路が L0'（送信直前クランプ）を通るかは未決**。`# TODO(Phase 1)` 実機確認。G0 ゲート通過前に motion を有効化しない | [02:325-329](02-hardware-design.md) / [jetson/01](../jetson/01-fidelity-and-validation.md) |

## 7. 転記の限界（未撮影ページ）

以下は撮影範囲外のため本 doc に含まれない。**組立時は紙の該当ページを直接参照**すること:

- Jetson Orin Nano board installation step の **2〜5**（SSD・アンテナ・ボードマウント相当）
- 2DOF PTZ 取付の **④以降**
- Quick Start Tutorials の Example mode 以外のページ

## References

- [02-hardware-design.md](02-hardware-design.md) — M1 ハード設計正本（§ROSMASTER M1 採用検討時の残課題・§給電の配線設計・§到着前に用意するもの・フラッシュ経路・マウント）
- [architecture/23-perception-and-localization.md](../architecture/23-perception-and-localization.md) — 知覚スタック正本（HP60C / T-mini Plus の採用形）
- [adr/0009-m1-room-scale-operation.md](../adr/0009-m1-room-scale-operation.md) — 部屋スケール運用（Phase 1 = 部屋 SLAM）
- [setup/jetson-deploy.md](../setup/jetson-deploy.md) / [jetson/01-fidelity-and-validation.md](../jetson/01-fidelity-and-validation.md) — 実機 deploy・ゲート
- 原本: Yahboom ROSMASTER M1 同梱紙説明書（Quick Start Tutorials / Shipping List / Installation Steps。参照日: 2026-08-19）
