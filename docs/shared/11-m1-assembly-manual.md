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
9. **Install T-MINI PLUS LiDAR** — ① adapter plate を M3\*6mm（袋③）で取付 → ② LiDAR 本体を M2\*8mm + M2\*10mm+4mm single-pass copper pillar（袋③）で取付。**LiDAR の矢印を車体前方に向ける**。
10. **Install AI large model voice module and speaker** — M2.5\*5mm、M2.5\*16+6mm copper pillar、M2.5\*5+6mm copper pillar（いずれも袋⑤）。
11. Final wiring（§3-§4）→ top cover を M3\*6mm で戻して完成。

## 3. SBC ボード取付（Jetson Orin Nano を軸に・転記）

### 3.1 Jetson Orin Nano board installation step（撮影された範囲）

1. **Install HUB expansion board** — M2.5\*22+6mm single-pass copper pillar（袋⑥）＋ M2.5\*5mm round head screw（袋①）。**本数は説明書に記載なし**（図示は柱4本＝末尾追記 Q-1/Q-2）。
2. 〜5. は撮影範囲外（§7。ボードインターフェース図から SSD 挿入・パッチアンテナ・ボードマウントに相当と推定）。本プロジェクトの Orin Dev Kit は取付板非互換の可能性があり**自作 3D プリントマウント前提**（[02:322](02-hardware-design.md) / [02:414-420](02-hardware-design.md)）。
6. **Jetson Orin Nano board wiring diagram** — 接続ケーブル: OLED cable（OLED-I2C）／ Cooling fan cable ／ **Upper-elbow USB to USB**（Orin ⇔ HUB）／ **XH2.54 cable**（電源。本プロジェクトでは §4 の昇圧経路に置換）／ **Side-elbow Micro USB to USB**（HUB ⇔ 拡張ボード＝シリアル）。

### 3.2 Jetson Orin Nano board interface（転記）

- **SSD insert into the north card slot**（M.2 Key-M。KIOXIA 1TB はここへ = [02:402](02-hardware-design.md)）
- Antenna ／ Cooling fan ／ OLED-I2C wiring
- 下面 USB: 1. Connect USB HUB expansion board、2. Connect handle receiver

### 3.3 参考: Jetson Nano B01 の取付手順（比較用・転記）

1. Install HUB expansion board → 2. Remove core module → 3. Install network card and patch antenna → 4. Install core module（**45° で挿入**）→ 5. Install cooling fan（M3\*14mm・袋⑥）→ 6. Install board（M2.5\*5mm・袋⑥）→ 7. Install patch antenna（acrylic plate 両面に貼付）→ 8. Wiring diagram（Orin と同型）。
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

---

## 【2026-08-23 追記】付属ネジ袋の丸数字は「数量」ではなく「袋番号」＋現物照合

### Q-1. 丸数字 ①②③⑤⑥ は Accessory package の番号（転記ミスの是正）

初版転記は説明書ラベル末尾の丸数字を**本数**と誤読していた（`M2.5*22+6mm ... copper pillar⑥` → 「×6」等）。**丸数字は §1 の「Accessory package ⑤」等と同じ袋番号**であり、**説明書は本数を明記していない**。根拠（同梱紙説明書・撮影原本 `docs/assets/m1-manual/`・参照日 2026-08-23）:

1. **Shipping List（p10）**: 「Jetson Orin Nano board (Optional)」行および「Jetson Nano B01 board (Optional)」行の *Accessory package* のイラストが、**ネジ袋に「6」と印字された図**になっている＝袋そのものに番号が振られている。
2. **p04「2. Install AI large model voice module and speaker」**: `M2.5*5mm round head screw⑤` / `M2.5*16+6mm single-pass copper pillar⑤` / `M2.5*5+6mm single-pass copper pillar⑤` と、**異なる3部品すべてに同じ⑤**。数量ならすべて偶然5本になる必要があり不自然。音声モジュールは §1.2 のとおり **Accessory package ⑤** 同梱。
3. **p05「1. Install T-MINI PLUS LiDAR」**: `M3*6mm③` / `M2*8mm③` / `M2*10mm+4mm single-pass copper pillar③` と全て③。LiDAR は §1.3 のとおり **Accessory package ③** 同梱。
4. **p06**: B01 の手順 5/6/7（cooling fan・board・patch antenna）が**すべて⑥**で、B01 は §1.3 のとおり **Accessory package ⑥** 同梱。Orin 手順1 も柱が⑥、ネジのみ①（①＝§1.1 の標準車体付属袋）。

→ 本 doc §2-§3 の該当行（`:60` `:61` `:68` `:80`）を袋番号表記へ是正済み。**「6本必要」という要求は元から存在しない**ため、手元の本数と説明書の突合で過不足を判定することはできない。**取付穴の実数が正**（現物実測）。

### Q-2. M2.5\*22+6mm 単通銅柱は「Orin 本体の固定具」ではなく「SBC を HUB ボードの上に渡す支柱」

p05 右上「6. Jetson Orin Nano board wiring diagram」下段の組立図で構造が読める:

| 段 | 中身 |
|---|---|
| 上段 | **SBC 本体**（USB-A・LAN・DP が見える面）。4本の銅柱の上に載る |
| 中段 | **USB HUB expansion board**（青い USB3.0 ×4。LiDAR / 深度カメラ / 音声モジュールのケーブルはここへ） |
| 下段 | シャーシ上面デッキ。HUB ボードは低いスペーサでデッキ直付け |

**柱は図示 4 本**（左2・右2＝上段ボードの四隅）。22mm という高さは **HUB ボードのコネクタ高さを跨ぐ**ためのもの。手順名が "Install HUB expansion board" なので「HUB ボードを留めるネジ」と読めるが、実際は**その上に SBC を渡すための支柱を立てる工程**。

⚠️ **本プロジェクトではこの穴位置がそのまま使えるとは限らない**。搭載するのは Yahboom の「Jetson Orin Nano board」オプションではなく **NVIDIA Dev Kit（キャリア一体・103×90.5×34.8mm）** であり、`# TODO(Phase 1)` 取付板非互換の可能性と自作プレート前提が [02:322](02-hardware-design.md) / [02:414-420](02-hardware-design.md) に記録済み。キャリアボードの取付穴位置は NVIDIA が非公開（[02:416](02-hardware-design.md)）。
`# TODO(要検討)` 3D プリントマウント（[02:418](02-hardware-design.md)・M3×4）を**この4本の銅柱の上に載せる**構成は、説明書が意図した高さ・HUB ボードとのクリアランスをそのまま引き継げるため有力だが、プリント品は汎用キャリアで柱ピッチ穴を持たない。柱ピッチの実測とプレート側の穴追加が必要かどうかは**現物合わせで決める**（本 doc は転記 doc のため、決定は [02](02-hardware-design.md) 側に書く）。

### Q-3. 手元の実物（2026-08-23 オペレーター現物確認）

Superior-without の同梱袋から、**単通銅柱（六角部のみで約22mm・オス側 +6mm ＝ `M2.5*22+6mm` 相当）** と **M2.5\*5mm なべネジ**、および**六角部約16mm の単通銅柱**を実物確認。
- ネジの呼び `M2.5*5mm` の "5mm" は**軸（ねじ部）長で頭を含まない**——なべ頭の頭厚（M2.5 で概ね 1.7mm 前後）を足した全長ではない。測るときは軸だけ。
- `# TODO(現物確認)` **本数が確定していない**（22mm 系を 4 本とする記録と 5 本とする記録が混在）。Q-1 のとおり説明書側に本数の記載が無いため、**必要数は Dev Kit 側の取付穴数を数えて決める**。
- 🔴 `# TODO(現物確認)` **§1.3 開梱記録の「袋⑥は丸ごと非同梱」と矛盾する可能性**。`M2.5*22+6mm` は説明書上 **袋⑥**（Orin/B01 オプション）の部品だが、上記は without 構成の同梱物として出てきた。**袋に印字された番号を確認**すれば決着する（p10 のとおり袋には番号が印字されている）——「6」なら §1.3 の非同梱記述と [01:149](01-budget-and-procurement.md) を要修正、「1」なら袋①（標準車体）にも 22mm 柱が入っていることになり、これも記録価値がある。**どちらに転んでも「追加購入は不要」の結論は変わらない**（[01:149](01-budget-and-procurement.md)）。

### Q-4. 取り違え防止（M2.5 系の単通銅柱が3種類ある）

| 呼称 | 六角部 | 用途 | 袋 |
|---|---|---|---|
| M2.5\*22+6mm 単通 | 22mm | **Orin / B01: SBC を HUB ボード上に渡す支柱** | ⑥ |
| M2.5\*16+6mm 単通 | 16mm | B01 パッチアンテナ ／ AI 音声モジュール | ⑥ / ⑤ |
| M2.5\*5+6mm 単通 | 5mm | AI 音声モジュール | ⑤ |
| M2\*10+4mm 単通 | 10mm | T-MINI PLUS LiDAR（**M2**。M2.5 と混ぜない） | ③ |

「単通（single-pass）」＝**片端オスねじ・反対端メスねじ**。呼びの `+6mm` はオス側突起長なので、**六角部だけを測って 22mm なら `22+6mm` 品**で正しい。袋ごとに分けて保管する（混ざると Q-3 の照合が二度とできなくなる）。
