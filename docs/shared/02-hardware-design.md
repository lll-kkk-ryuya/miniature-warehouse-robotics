# ハードウェア設計

作成日: 2026-05-21
更新日: 2026-05-21

## A. ロボット — Yahboom MicroROS ESP32 Car

### 仕様

| 項目 | 内容 |
|------|------|
| 台数 | 2台（予備費で+1台追加の可能性あり） |
| 価格 | 約30,000円/台 |
| 駆動 | 310エンコーダモーター × 4（4輪スキッドステアリング、左右2チャンネル制御） |
| LiDAR | ORBBEC MS200 dToF LiDAR（360°全方位, 0.03〜12m, 4500Hz, 角度分解能0.4°） |
| IMU | 6軸IMU（加速度3軸 + ジャイロ3軸、姿勢推定用） |
| バッテリー | 7.4V リポバッテリー |
| 通信 | WiFi UDP（micro-ROS経由） |
| ROS 2対応 | micro-ROS公式サポート → ROS 2 Humble/Jazzy（Jazzy対応確認済み 2026-05-22） |
| サイズ | 約15cm幅（※未検証、公式スペック要確認） |

### センサー詳細

| センサー | 型番 | 用途 | ROS 2トピック |
|---------|------|------|-------------|
| dToF LiDAR | ORBBEC MS200 | AMCL自己位置推定・障害物検知・SLAM | `/bot{n}/scan` |
| 6軸IMU | （基板内蔵） | 姿勢推定・旋回検出 | `/bot{n}/imu`（※要確認 / sim 未橋渡し: #43 は `scan`/`odom`/`cmd_vel` のみ bridge） |
| エンコーダ | 310モーター内蔵 ×4 | オドメトリ（移動量計算） | `/bot{n}/odom` |

**ORBBEC MS200**: dToF（Direct Time of Flight）方式の360°スキャンLiDAR。サイズ54.3×47.0×35.0mmと超小型でminicarに搭載可能。CLASS 1アイセーフティ認証済み。このLiDARにより、minicar単体でAMCLによる自己位置推定が可能。

### 選定理由

- ROS 2 + Nav2 + RViz が箱出しで動作確認可能
- micro-ROS公式サポートにより開発工数を削減
- ROS 2エコシステムに乗っているため長期拡張が容易（SLAM、マルチロボット協調等）
- **360° LiDAR搭載**により、追加センサーなしでAMCL自己位置推定が可能

### 改造計画

- 上面に荷物トレイを3Dプリントで追加（Bambu Lab A1 miniで製作）
- トレイサイズ: 約80×60mm、パレット形状
- 固定方法: M3ネジ or 結束バンド

### 代替案（コスト重視の場合）

自作構成（ESP32-S3 + Yahboom 2WDシャーシ）× 2台 = 約30,000円。
micro-ROS実装に2-3週間の追加工数が必要。予算が厳しい場合のフォールバック。

---

## B. エッジコンピュータ — Jetson Orin Nano Super Dev Kit

### 仕様

| 項目 | 内容 |
|------|------|
| 価格 | 57,200円（税込） |
| AI性能 | 67 TOPS |
| GPU | 1024 CUDA コア、1,050 MHz |
| メモリ | 8GB LPDDR5、102 GB/s |
| 消費電力 | 7〜25W |
| 冷却 | ファン付きヒートシンク同梱 |

### 役割

- ROS 2 Humble のホスト（司令塔。ADR-0005）
- **LLM Bridge Node の実行**（Claude / ChatGPT / Gemini / Grok APIとの通信、Hermes Agent経由）
- Nav2 による経路計画・障害物回避
- SLAM Toolbox による地図生成
- micro-ROS Agent の実行（minicarとのWiFi通信）
- Warehouse Orchestrator との連携API
- 将来: FoundationPose による荷物認識

### ネットワーク要件

Jetsonは以下2つのネットワーク接続を同時に必要とする:

| 接続先 | プロトコル | 用途 |
|--------|----------|------|
| minicar（ESP32）× 2台 | WiFi UDP（ローカル） | micro-ROS通信 |
| LLM API（クラウド） | HTTPS（インターネット） | Claude/ChatGPT/Gemini/Grok |

→ テザリング or WiFiルーター1台でどちらも賄える（ローカル通信+インターネット）。

### 購入時の注意

- スイッチサイエンスで品切れが頻発する
- 菱洋エレクトロは法人向け（要見積、保守付き）
- Isaac ROS最新版（release-3.x）ではJetson Thorがメインターゲットに移行中（※未検証）

### 「Super」化の仕組み — ハードウェアは旧Orin Nanoと完全同一

NVIDIA が 2024年12月に発表した **Jetson Orin Nano Super** は、**旧 Orin Nano (無印) と物理的に同じ基板・モジュール**である。違いは JetPack 6.1+ で解放される **電力枠とクロックの上限**のみ。

| パラメータ | 旧出荷時 (Orin Nano) | Super 化後 (MAXN_SUPER) |
|---|---|---|
| GPU クロック | 635 MHz | **1,020 MHz**（+60%） |
| CPU クロック | 1.5 GHz | **1.7 GHz** |
| メモリ帯域 | 68 GB/s | **102 GB/s**（+50%） |
| 電力枠 | 7W / 15W | 7W / 15W / **25W** |
| AI性能 | 40 INT8 TOPS | **67 INT8 TOPS** |

#### Super 化の手順
```bash
# JetPack 6.1+ を導入後
sudo nvpmodel -q          # 現在の電力モード確認
sudo nvpmodel -m 2        # MAXN_SUPER に切り替え
sudo jetson_clocks        # クロック最大化
```

#### Amazon 等で「無印 Orin Nano Dev Kit」表記の商品でも問題ない理由
- **基板は同一**: NVIDIA は新しい HW を作っておらず、ソフトウェアロック解除でリブランドした
- **JetPack 更新で 67 TOPS 出る**: 旧版が届いても、ファーム適用後は Super と完全同一性能
- **業界慣行**: Intel TDP 設定、Tesla バッテリー解放等と同じ「シリコン共通・ソフト差別化」方式
- **保証範囲内**: NVIDIA 公式の `nvpmodel -m 2` 手順なので問題なし

#### 採用判断（2026-05-28）
- 本案件では **Amazon「IoT本舗」販売 Orin Nano Dev Kit (ASIN: B0BZJTQ5YP, 45,484円)** を採用候補
- スイッチサイエンス Super Dev Kit (57,200円) より約12,000円安い
- 商品 listing 上は表示タイトルに "Super" がないことがあるが、HTMLメタタイトルでは "Super Developer Kit" と明示、比較表でも「本製品 = Super」と記載
- 仮に旧版が届いても Super 化可能なため、性能面でのリスクは無い

#### Super モードで必要な追加投資
- **電源（重要・2026-05-29 訂正）**: Orin Nano Super Dev Kit には **19V DCバレルジャック電源が同梱**されるため、給電用の追加購入は原則不要。
  - ⚠️ **USB-C ポートは output 専用で、Dev Kit の給電には使えない**（NVIDIA公式フォーラム: "the USB-C port on Orin nano devkit is output only, can not be used as power supply of devkit"）。給電は必ず **DCバレルジャック（入力 7–20V、同梱は19V）** を使う。
  - ❌ 旧記載「USB-C PD 45W（Anker Nano II）推奨」は**誤りのため撤回**。Mac用USB-C充電器やUSB-C PDではDev Kitを駆動できない。
  - MAXN SUPER（25W）で Nav2×2 + MPPI を回す場合も同梱19V電源で給電する。Mac純正70W充電器による給電は不可（USB-C のため）。
- **NVMe M.2 2280 SSD**: OS 用（microSD より圧倒的に高速）。**調達済み＝別売購入（2026-05-28・¥31,980）: KIOXIA EXCERIA PLUS G3 1TB `SSD-CK1.0N4PLG3R`**。Gen4x4 品だが Orin の Key-M は **PCIe 3.0 x4** のため Gen3 相当で動作（下位互換・実害なし）
- **microSD 64GB A2**（初回ブート＋QSPI ファーム更新用）: **調達済み（2026-05-28・¥3,980）= SanDisk Extreme 64GB A2/U3/V30**。Mac のみの環境では microSD 経路が唯一のフラッシュ手段

### OS環境構築（JetPack 6.x）

Jetson は「司令塔（現場の脳）」であり、実機デモ時に Nav2/AMCL/SLAM/micro-ROS Agent/LLM Bridge/Warehouse MCP Server/Emergency Guardian 等を**現場でリアルタイムに走らせる中央コンピュータ**。Mac は開発・シミュレーション専用で本番では使わない。

#### 役割（Jetson上で常時動くもの）

`09-navigation-internals.md §5「Jetson上で動くROS 2ノード一覧」`を参照。要約すると micro-ROS Agent / Map Server / AMCL×2 / Nav2(Planner/Controller/Recoveries/Costmap)×2 / Emergency Guardian / State Cache / LLM Bridge / Hermes Gateway / Warehouse MCP Server / WO Bridge / TF2。

#### セットアップ手順（焼き込みは Jetson 到着後に実行）

OS環境構築 = SSD への JetPack 焼き込み。**焼く相手（Jetson本体）が無いと実行できない**が、手順とスクリプトの**準備は到着前にできる**。到着後に「スクリプトを流すだけ」の状態にしておけば半日で完了する。

| 段階 | 作業 | Jetson要否 |
|------|------|-----------|
| 準備（到着前にできる） | NVIDIA SDK Manager / JetPack 6.x イメージのダウンロード | 不要 |
| 準備（到着前にできる） | ROS 2 Humble + 依存パッケージのインストールスクリプト作成 | 不要 |
| 準備（到着前にできる） | 各プロセスの systemd サービス定義のたたき台 | 不要 |
| 実行（到着後） | **別売購入した** KIOXIA NVMe SSD 1TB へ JetPack 6.x を焼き込み（microSDで初回ブート → SSDへ移行） | **必要** |
| 実行（到着後） | Super 化（`sudo nvpmodel -m 2` + `sudo jetson_clocks`、上記「Super化の手順」参照） | **必要** |
| 実行（到着後） | ROS 2 Humble + micro-ROS Agent インストール、メモリ検証（`06-implementation-phases.md` Phase 0.5 段階2） | **必要** |

#### Jetson が無くてもできること / できないこと

| 項目 | Jetsonなしで可 | Jetson必須 |
|------|:---:|:---:|
| Gazeboシミュレーション全般（Phase 0.5） | ✅ | — |
| LLM Bridge / MCP Server プロトタイプ（GCP稼働中のHermes経由） | ✅ | — |
| 8GBメモリ検証（Docker 6GB上限の近似テスト） | ✅ | — |
| 8GBメモリ検証（確定値・ユニファイドメモリ実測） | — | ✅ |
| エッジOS環境の焼き込み・起動 | — | ✅ |
| 実機ロボット制御（Phase 1〜、ただしロボット本体も必要） | — | ✅ |

→ Jetson の遅延はクリティカルパスにほぼ影響しない（最初の2週間 Phase 0.5 は Mac だけで完結）。

### 代替案

Raspberry Pi 5（8GB、14,000〜35,200円）。AI推論不要でNav2+SLAMのみなら十分動作する。

---

## C. 2D LiDAR — RPLiDAR A1

### 仕様

| 項目 | 内容 |
|------|------|
| 価格 | 約15,000円 |
| 用途 | 外部トラッキング補正（オプション）。SLAM地図生成はminicar搭載ORBBEC MS200で行う |
| 接続 | Jetson Orin Nano にUSB接続 |
| 測定範囲 | 12m（ジオラマには十分すぎる） |

### 配置

Jetsonに接続し、ジオラマの端に固定。ロボット走行エリア全体をスキャンする。

### 役割の整理（minicar搭載LiDARとの分担）

| センサー | 設置 | 主な役割 |
|---------|------|---------|
| RPLiDAR A1（本機） | Jetsonに固定設置（俯瞰） | 外部トラッキング補正（オプション） |
| ORBBEC MS200（minicar搭載） | 各minicarに搭載 | AMCL自己位置推定（常時）、障害物検知（常時） |

minicarにORBBEC MS200（360° LiDAR）が搭載されているため、AMCL自己位置推定およびSLAM地図生成はminicar単体で動作可能。RPLiDAR A1はSLAMには使用しない（固定位置からでは棚裏の遮蔽により不完全な地図になるため）。外部トラッキング補正のオプション用途に留める。

### アップグレード候補

RPLiDAR A2（+10,000円）: 精度・回転速度が向上。予備費からの投資対象。

---

## D. 3Dプリンター — Bambu Lab A1 mini

### 仕様

| 項目 | 内容 |
|------|------|
| 価格 | 約30,000円 |
| 造形サイズ | 180×180×180mm |
| 速度 | 500mm/s |
| キャリブレーション | 全自動 |
| マルチカラー | AMS lite対応（最大4色） |

### 印刷するもの

| パーツ | STLデータ元 | スケール | 費用 |
|--------|-----------|---------|------|
| パレットラック（棚） | Printables.com model/567874 | 1:10 | 無料 |
| 倉庫全体セット | Printables.com model/561782 | 1:10 | 無料 |
| パレット | Cults3D | 汎用 | 無料 |
| パレットラックシステム | MakerWorld models/1190695 | 可変 | 無料 |
| ロボット用荷物トレイ | 自作設計 | — | — |
| バース（接車部） | Printables warehouseタグ | 1:10 | 無料 |

### なぜ3Dプリンターが必要か

- 1:10スケールで走行可能な倉庫棚の市販品がない
- ロボットが載せて運べるサイズ・重さのパレットを自作する必要がある
- バース（接車部）は物流特有の構造物で既製品が存在しない
- レイアウト変更のたびにパーツを作り直せる
- 段ボール/スチレンボードの手作りではYouTube映像の品質が下がる

### フィラメント

汎用PLA 1kg × 2巻（グレー+白）= 約4,000円。全パーツ印刷に十分。

---

## E. 撮影機材

| 機材 | 製品 | 価格 |
|------|------|------|
| 俯瞰カメラ | Logicool C922n（1080p） | 10,000円 |
| アームスタンド | サンワサプライ 200-DGCAM028 | 3,500円 |
| LED照明 | テープLED 昼白色5000K + USBバー | 3,000円 |

### 俯瞰撮影の設置

- カメラを床から120〜150cmにアーム固定
- 1,820×910mm（約1.8m×0.9m）のジオラマ全景が映る
- 画角90度前後のレンズが必要（C922nは78度、やや狭い可能性あり → 設置高さで調整）

### 照明

- 昼白色（5,000〜6,500K）で倉庫の白色照明を再現
- テープLEDを天井フレームに沿わせて設置

---

## F. ベースボード

| 素材 | サイズ | 価格 |
|------|--------|------|
| ラワン合板 9mm | 1,820×910mm（約1.8m×0.9m） | 3,500円 |
| 木枠補強（角材30×40mm） | 周囲 | 1,500円 |
| テクスチャーペイント（グレー） | 全面 | 1,500円 |
| ビニールテープ（黄・白） | 通路マーカー | 200円 |

### 走行面の仕上げ

テクスチャーペイント（マット仕上げ）でコンクリート床を再現。微細な凹凸でロボットのタイヤが食いつく。

---

## G. クラウドGPU

### 用途: Isaac Sim 5.1（デジタルツイン映像）

**重要: Isaac SimはRTコア必須。A100/H100では動作しない。**

| プロバイダー | GPU | 時間単価 | 推奨 |
|-------------|-----|---------|------|
| RunPod | A10G | $0.37〜0.54/h | 推奨 |
| Vast.ai | A10G | $0.3〜0.6/h | 代替 |
| RunPod | RTX 4090 | $0.44〜0.69/h | 高品質映像用 |
| Google Cloud | L4 | $0.8〜1.2/h ※未検証 | 高コスト |

使い方: 常時起動ではなく、開発・撮影時のみ使用。月10h × 3ヶ月 = 約15,000円。

---

## ROSMASTER M1 採用検討時の残課題（2026-08-05・未確定）

> Yahboom ROSMASTER M1（メカナム4輪）に**手持ちの Orin Nano Super Dev Kit を載せる**構成を調査した結果。
> 車体寸法は車種選定の制約にしない（[04-diorama-layout.md](04-diorama-layout.md) §「決定（2026-08-05）」）。
> 購入候補: Amazon.co.jp `Superior / without Nano`（ASIN B0G495C65Q・¥67,527・Nuwa-HP60C 深度カメラ同梱）。

### 確認済み（一次情報で裏取り済）

| 項目 | 値 | 出典 |
|---|---|---|
| 車体寸法 | 全幅 231.40 × 全長 284.40 × 全高 181.40mm（LiDAR 上面 147.50 / 車体上面 74.58） | 公式 Product parameters 図 |
| バッテリ | 12.6V 6000mAh + 12.6V/2A 充電器 → **3S 構成と整合**（保管 11.1–11.7V・**9.6V でブザー警報**） | 公式 unboxing / battery precautions |
| 拡張ボード出力 | **DC 12V ×2（XH2.54 2PIN）/ DC 5V ×1（バレル・シルク "5VOUT2"）/ Type-C 5V ×1（"5VOUT1"・Pi5 給電対応）** | ROS robot board V3.0 パラメータ表 |
| 拡張ボード入力 | T プラグ **DC 12V のみ**（公式 Q&A「This board just support 12VDC input」） | 公式製品ページ Q&A |
| Orin 給電経路（純正） | Orin Nano SUPER 版のみ **「DC5.5×2.5 → XH2.54 2PIN 電源ケーブル」**同梱＝**12V を Orin の DC ジャックへ直結**（DC-DC 非使用） | 公式 Shipping List 図 |
| Orin 入力仕様 | **9–20V / センタープラス / バレル外径 5.5mm・ピン 2.5mm / 最大 3.5A**（付属アダプタ 19V） | NVIDIA SP-11324-001 v1.3 §1.2, §3.8 |
| 電力モード | 15W / 25W / MAXN SUPER（**uncapped・W 値は非公開**） | NVIDIA JetPack 6.2 blog / Developer Guide |
| OS | M1 の Orin 版は Ubuntu 22.04 + **ROS 2 Humble**（本プロジェクトは Jazzy） | 公式 Product parameters 図 |
| 拡張ボード実装（2026-08-05 追加） | 型番 **YB-ERF01-V3.0**／MCU STM32F103RCT6／USB-serial **CH340**／IMU **ICM20948 9軸**／モータドライバ **AM2861 ×4**／通信 **115200bps**／待機電流 **約 50mA**／基板 **85×56mm**・取付穴 **4-φ2.5（58×49mm ピッチ）** | 公式 `ROS_control_board_V3.0_parameters.jpg` を実見 |
| 拡張ボード保護回路（2026-08-05 追加） | **「サーボ過電流保護・逆接続保護・短絡保護」のみ**。**12V 出力レールの電流定格・過電流保護・ヒューズの記載は無い** | 同上 |
| **12V 出力の性質（2026-08-05・重要）** | 入力が「T type **DC12V** input」、出力が「**DC 12V** interface ×2」、モータも「**12V** encoder motor」＝**同一呼称の単一レール**。3S（12.6→9.6V）から定電圧 12V を作るには昇降圧回路が要るが**その記載も実装も見当たらない** → **生バッテリ電圧のスルー出力と判断**（強い推定。実測で確定させる） | 同上（パラメータ表からの導出） |

> 注: 本節の「9–20V」が正。上記 `#### Super モードで必要な追加投資` の「入力 7–20V」は NVIDIA フォーラム由来の記述で、**Carrier Board Specification の 9–20V と食い違う**（`# TODO`: 該当行を要訂正）。

### 未確定（購入・実装前に潰す）

1. `# TODO(発注前)` **拡張ボード 12V レールの連続電流定格は、公式パラメータ表を実見しても記載が無い**（2026-08-05 確認）。保護回路の記載も「サーボ過電流保護・逆接続保護・短絡保護」のみで、**12V 出力レールの過電流保護・ヒューズは明示されていない**。Orin は 25W モードで 12V 換算 ≈2.1A、MAXN SUPER は uncapped。同じ 12V 系に AM2861 モータドライバ ×4 がぶら下がるため**モータ加減速時の突入と競合**する。→ Yahboom support へ照会 ＋ 実機実測（下記「給電の実測手順」）。
2. **【ほぼ確定 / 方針決定 2026-08-05】12V 出力は生バッテリ電圧のスルーと判断**（根拠は上表「12V 出力の性質」）。したがって **Orin が見る電圧は満充電 12.6V からブザー警報 9.6V まで下がり、Orin 下限 9V までの余裕は 0.6V しかない**。モータ加速時のサグが重なれば 9V 割れは現実的に起こりうる。
   **→ 既定の構成を「昇圧 DC-DC 経由」とする**（12.6V→19V・連続 **≥45W**・出力 **5.5×2.5 センタープラス**）。**12V 直結は「実測①〜④で問題が無いと確認できた場合のみ選べる縮退案」へ格下げする。** 理由: Orin の電圧断は L1 緊急停止と外部通信ごと落とす安全事象であり、0.6V のマージンに賭ける設計は `.claude/rules/safety.md` の趣旨に反する。純正 Orin 版が 12V 直結ケーブルを同梱している事実（上表）は、Yahboom がこのマージンを許容していることを示すに留まり、**本プロジェクトの安全要件を満たす根拠にはならない**。
3. `# TODO(発注前)` **"without" 版に Orin 用電源ケーブルは付かない見込み**。`without Nano` に付くのは Jetson Nano B01 用の **DC5.5×2.1 ケーブル**で、これは拡張ボードの **5V 出力**バレルから取る線。**電圧もピン径も Orin へ流用不可**。→ **XH2.54 2PIN ⇔ DC5.5×2.5 センタープラス ケーブルを自作 or 単品調達**（要 Yahboom 確認）。
4. `# TODO(Phase 1)` **マウント**: `without` 版の取付板は選択したボード用。Orin Dev Kit（キャリア一体・完成体 103×90.5×34.8mm）は**自作プレートで固定する前提**。上段デッキとの高さ干渉は実物合わせ。
5. **【解決】ソフト方針＝Yahboom スタックを使わず自前 ROS 2 ノードを書く。** 制御プロトコルは判明済（USB シリアル 115200 8N1・`HEAD=0xFF, DEVICE_ID=0xFC, LEN, FUNC, payload…, CHECKSUM`、`CHECKSUM=(sum+257-0xFC)&0xFF`、`FUNC_MOTION=0x12`・`FUNC_MOTOR=0x10`・`FUNC_REPORT_*` を MCU が **40ms 周期で auto-report**）。Yahboom の `Rosmaster_Lib` は `struct/time/serial/threading` のみ依存＝**アーキ非依存で aarch64 可**だが、ライセンスが Proprietary 表記・PyPI 未配布・配布が Google Drive のため、**判明済フレーム仕様から自前実装する方がクリーン**。デバイスは udev symlink `/dev/myserial` に固定。
6. **【解決】メカナム逆運動学は STM32 ファーム側にある** → ホストは `/cmd_vel` の `(vx, vy, wz)` を投げるだけ（`set_car_motion` は body 速度を `int16(v*1000)` で送るのみ・4輪配分なし）。**よって凍結 URDF / Nav2 が diff-drive のままでも `linear.y = 0` で成立し、メカナム採用に契約変更は不要**。omni 化（AMCL Omni / `vy_max` > 0 / `motion_model: "Omni"` / `linear.y` の twist_mux→collision_monitor 通し）は**任意の後続拡張**として扱う。sim 側 diff_drive プラグインの差し替えも omni 化する場合のみ。
7. **【方針決定 2026-08-05・M1 / Superior 構成】速度クランプは「ホスト側シリアルドライバ内の送信直前クランプ（L0'）」に置く。**
   現行方針は ESP32 自前ファーム内で 0.3 m/s をハードクランプする（`.claude/rules/safety.md`・`firmware/include/safety_clamp.h` の R-26 unit）。**M1 の STM32 ファームは Yahboom 製バイナリ**のため、MCU 内に同じ保証を置けない。一方で残課題 5 の通り**ホスト側シリアルドライバは自前実装**であり、`FUNC_MOTION=0x12` フレームを組む直前が**全 `cmd_vel` が必ず通る単一の絞り点**になる。ここでクランプすれば、Nav2 / Policy Gate / Emergency Guardian のいずれが壊れても wire に 0.3 m/s 超は出ない。
   - **採用**: ドライバの送信直前（body 速度を `int16(v*1000)` へ変換する直前）でクランプする。**R-26（独立オラクル unit ＋ mutation で赤くなること）の対象**とし、`warehouse_interfaces.safety.MAX_LINEAR_VELOCITY` を**単一ソースとして import**する（値の再定義を禁止＝`safety.py:19` の「hardcode するな」に従う）。
   - **却下**: STM32 ファームの自前差し替えによる L0 維持 — 残課題 6 の通り**メカナム逆運動学が STM32 側にある**ため、差し替えると 4輪配分の再実装まで背負う。Phase 1 のスコープに対して過大。
   - **明記すべき限界**: L0' は**ホストプロセスが生きている間だけ**有効。ホスト停止・USB 断では MCU 側に最後の指令が残り、暴走しうる。→ `# TODO(Phase 1)` **MCU の通信タイムアウト停止（watchdog）の有無を実機で確認**する。無い場合は Emergency Guardian からの明示 stop フレーム送出＋電源系での縮退で補う。
   - `# TODO(採用時)` **doc 影響**: `.claude/rules/safety.md`「ロボット速度制限をコード内で強制する」の実施箇所と、[12-infrastructure-common.md](../architecture/12-infrastructure-common.md) の Layer マップ（L0 の定義）を M1 採用時に改訂する。
8. `# TODO(発注前)` **Nuwa-HP60C 深度カメラ（Superior 版）の Jazzy 動作は未保証。** ROS 2 ドライバ `ascamera` は ament_cmake ＋ **閉ソースのプリビルド `.so`**（`libAngstrongCameraSdk.so` ほか）に静的リンク。aarch64 バイナリは同梱されるが、その `libs/lib/aarch64-linux-gnu/readme.md` は **「5.4.1 20170404 (Linaro GCC 5.4-2017.05)」＝2017 年 GCC 5.4 ビルド**。動作報告のある distro は Foxy(20.04) / Humble(22.04) のみで、**Jazzy / Ubuntu 24.04 の成功報告は無い**。割れてもソースが無く修正不能。
   **→ 2026-08-05 方針: distro 自体を Humble に寄せることで本項を構造的に解消する（[ADR-0005](../adr/0005-ros2-distro-humble-for-rosmaster-m1.md) proposed）。** retreat plan（Humble コンテナ隔離 / RealSense・Orbbec 等への置換）は ADR-0005 が却下扱いとして保持。
9. `# TODO(Phase 1)` **LiDAR ドライバ**: T-mini Plus は YDLIDAR 製（model 151・baud 230400・12m）。`ydlidar_ros2_driver` は **OSS でソースビルド可＝aarch64 に障害なし**。**master は Jazzy でビルドが割れる**（upstream issue #72 / PR #66 が OPEN）が、`humble` ブランチが本来の対象のため **Humble 採用時は C++17 引き上げパッチ不要**（[ADR-0005](../adr/0005-ros2-distro-humble-for-rosmaster-m1.md)）。Jazzy を維持する場合のみ vendoring + パッチが要る。
10. **【一部解決】小項目**: 公式パラメータ表の実見（2026-08-05）で **USB-serial = CH340**（→ udev は `1a86:7523`）・**IMU = ICM20948**・**通信 115200bps** が確定。残る実機確認は **M1 用 `car_type` 値**と、**MCU auto-report が 40ms=25Hz 固定**である点の Nav2 チューニング（`controller_frequency` / AMCL 更新レート）への影響。

### 給電の実測手順（実機到着後）

| 段階 | 測り方 | 合格ライン |
|---|---|---|
| ① 無負荷 | マルチメータで 12V 出力端子 | 表記どおりの電圧が出ているか |
| ② Orin アイドル | DC インライン電力計 または `sudo tegrastats` の `VDD_IN` | 実消費 W を記録 |
| ③ Orin 高負荷 | MAXN SUPER + Nav2 + LiDAR + カメラ | **9V を下回らない**・再起動しない |
| ④ **モータ同時加速 ＋ ③** | ③の状態で急発進（最悪条件） | 同上 |

④で 9V 割れ・リプルが出た場合のみ、上記 2 の昇圧 DC-DC を採用する。

> 対象 layer: 給電系は **L0 未満（ハードウェア）**。ただし Orin の電圧断は L0 の緊急停止と micro-ROS リンクごと落とすため、安全要件として扱う（`.claude/rules/safety.md`）。

### 決定（2026-08-05）: 車体寸法・既存コードは車種選定の制約にしない

**オペレーター裁定（2026-08-05）**: ジオラマは未着工・実機ソフトは Phase 1 未着手のため、**M1 の実寸に合わせてジオラマを作り直し、開発コードも書き換える**。したがって下表は「M1 を却下する理由」ではなく、**採用した場合に実施する作業項目**として扱う（ジオラマ側の対応は [04-diorama-layout.md](04-diorama-layout.md) §「決定（2026-08-05）」）。

#### 必須（車体が物理的に大きくなることの帰結。駆動方式とは無関係）

| # | 書き換え対象 | 現在値（実ファイル） | M1 採用時 | layer |
|---|---|---|---|---|
| C-1 | `ROBOT_RADIUS` | `ws/src/warehouse_description/warehouse_description/robot_dimensions.py:45` = `0.075`（75mm） | 外接円半径 **≈184mm**（対角 367mm ÷ 2）へ改訂 | L2 |
| C-2 | costmap `robot_radius` ×2 | `ws/src/warehouse_bringup/config/nav2_params.yaml:215` / `:257` = `0.075` | C-1 と同値へ同期（単一ソース維持・R-42） | L2 |
| C-3 | collision_monitor PolygonStop | `ws/src/warehouse_bringup/config/collision_monitor.yaml:68` `radius: 0.09` | 車体外接 + 余裕へ改訂 | L1 |
| C-4 | 速度クランプの**置き場所** | `firmware/include/safety_clamp.h:45`（ESP32 ファーム内） | **ホスト側シリアルドライバの送信直前（L0'）へ移設**（残課題 7） | L0' |

#### 任意（**omni 化を選んだ場合のみ**。既定では不要）

> **重要（2026-08-05 訂正）**: 残課題 6 の通り**メカナム逆運動学は STM32 ファーム側にある**。ホストは `(vx, vy, wz)` を送るだけなので、**`linear.y = 0` に固定すれば凍結 URDF / Nav2 が diff-drive のままで M1 は成立し、契約変更は不要**。下表は「横移動を実際に使う」と決めた場合にのみ発生する。

| # | 書き換え対象 | 現在値（実ファイル） | omni 化する場合 | layer |
|---|---|---|---|---|
| C-5 | 横速度 `linear.y` | `ws/src` / `firmware` に**実装 0 件**（grep 一致なし・テスト除く） | twist_mux → collision_monitor → ドライバを縦断で新規実装 | L0'–L2 |
| C-6 | 駆動モデル | `nav2_params.yaml:52` `DifferentialMotionModel` / `:124` `vy_max: 0.0` / `:134` `motion_model: "DiffDrive"` | AMCL Omni / `vy_max > 0` / `motion_model: "Omni"` | L2 |
| C-7 | sim プラグイン | Gazebo `diff_drive` | メカナム相当へ差し替え | L2 |
| C-8 | **ベクトル速度クランプ** | `ws/src/warehouse_interfaces/warehouse_interfaces/safety.py:26-34` `clamp_velocity()` は**スカラー1軸** | **(vx, vy) の大きさ**でクランプする関数を追加 | L0' / L1 |

> **C-8 は omni 化の前提条件であり、後回しにできない。** `vy ≠ 0` を許した状態で各軸を独立に 0.3 m/s クランプすると、対角合成が √(0.3² + 0.3²) = **0.424 m/s** となり `.claude/rules/safety.md` の 0.3 m/s ハードキャップを **41% 超過**する。C-5 と C-8 は同一 PR で入れること。R-26（独立オラクル・mutation で赤くなること）の対象（[20-dev-quality-and-testing.md](../architecture/20-dev-quality-and-testing.md) §9）。

凍結リンク名 `wheel_{front,rear}_{left,right}`（`robot_dimensions.py:26-29`）はメカナム化でも**無傷**（4輪配置が同じため）。C-1 は `warehouse_description`、C-8 は `warehouse_interfaces` に触れるため **`contract` ラベル PR ＋ 依存トラック予告**が必要（`.claude/rules/parallel-workflow.md` §4）。C-2〜C-7 は config / 各パッケージ内で閉じる。

> `# TODO(採用時)`: C-1〜C-4 を epic Issue のチェックリストへ展開する。C-5〜C-8 は「横移動を使うか」を決めてから別 Issue に切る。

### 決定（2026-08-05）: Superior 版を採用し、HP60C 深度カメラを開発要件に含める

**オペレーター裁定（2026-08-05）**: 購入は **`Superior-without / NANO 4GB SUB`**（公式 sku 3000200910 / $499.90、Amazon.co.jp ASIN B0G495C65Q ¥67,527）とし、同梱の **Nuwa-HP60C 深度カメラを「使わない同梱物」ではなく開発要件として扱う**。

- **帰結（重要）**: 残課題 8（HP60C の ROS 2 ドライバ `ascamera` が閉ソース `.so` 依存・Jazzy 動作報告なし）が**回避可能な項目から、必ず解かねばならない項目へ昇格**する。したがって [ADR-0005](../adr/0005-ros2-distro-humble-for-rosmaster-m1.md)（Jazzy→Humble）の前提が成立する。ADR-0005 は **proposed のまま保持**（同日オペレーター指示）。
- **深度カメラを要件化して得るもの**（何に使うかを docs 側で先に定義しておく）:
  - 2D LiDAR の死角（棚の張り出し・低い荷物・段差）の**3D 障害物検知** → costmap への voxel 反映。
  - **パレット / 荷物の認識**。上記 §B「将来: FoundationPose による荷物認識」の実入力となる。
  - **Mode X-ER（ER 視覚司令官）への実カメラ入力**。現状は静止画・sim 由来のため、実機 RGB-D が入ると live 検証の忠実度が上がる。
- **変わらないもの**: C-1〜C-4（車体寸法由来）は Superior / Standard の別と無関係にそのまま必要。
- `# TODO(発注と独立に先行決定)` **distro をどうするか（ADR-0005 の未決＝Gazebo を Fortress に落とすか Harmonic をソースビルドするか）は Phase 0.5 のブロッカー**。実機到着を待つ必要が無いので、発注可否とは切り離して先に決める。判断材料は ARM64 headless での Gazebo 再スパイク結果（[16-repository-and-conventions.md](../architecture/16-repository-and-conventions.md):213-215 の GO 判定を引き継げるか）。

### 購入確定（2026-08-05）と Orin 立ち上げ経路

**発注済**: Amazon.co.jp 注文 `249-3401070-6986233`（2026-08-05・¥67,527）。商品名「Yahboom Jetson Nano B01搭載 ROS2ロボット …**Superior without Nano**」＝ `Superior-without / NANO 4GB SUB`（公式 sku 3000200910）。**到着予定 2026-08-10〜08-13**。

#### Orin Nano Super Dev Kit の実ポート構成（公式 Hardware Layout 実見）

| 項目 | 実際 | 帰結 |
|---|---|---|
| 映像出力 | **DisplayPort のみ**（「HDMI output and DisplayPort over USB-C are not supported」） | **DP→HDMI 変換が必須**（公式に adapter 対応と明記） |
| USB-C | 映像出力なし・**給電も不可** | モニタ・電源とも USB-C 経由は不可 |
| M.2 Key-E 2230 | **無線モジュール実装済（同梱）** | **WiFi/BT の買い足し不要** |
| M.2 Key-M 2280 | PCIe 3.0 x4 | 別売購入の KIOXIA 1TB NVMe をここへ（Gen4 品だが Gen3 動作） |
| M.2 Key-M 2230 | PCIe 3.0 x2 | 空き |
| USB-A ×4 | 10Gbps・各スタック VBUS 3A 制限 | LiDAR / 拡張ボード(CH340) / HP60C を USB で束ねられる |
| DC ジャック | **5.5×2.5mm** | 昇圧 DC-DC の出力プラグをこれに合わせる |

#### フラッシュ経路（**開発機が Mac のみ → microSD 経路が唯一**）

- QSPI ファームウェアが **`36.0` より古いと JetPack 6.x を起動できない**。確認は起動時 Esc → UEFI setup menu、または `sudo nvbootctrl dump-slots-info`。**`36.x` 以降なら更新不要**。
- 更新方法は2つ: **microSD 経路**（公式「Requires no Ubuntu host PC; needs a computer with Internet access and an SD card reader」）と **SDK Manager**（公式「if you have an Ubuntu **x86_64** host PC」）。
- 本プロジェクトの開発機は **MacBook Pro M4 のみ** → **SDK Manager は使えない**。**microSD 経路を採る**（microSD と SD カードリーダーが前提）。
- JetPack は **6.2 系**（Ubuntu 22.04 rootfs = Humble ネイティブ。Super 化 `nvpmodel -m 2` も 6.2 で入った機能）。

#### マウント

NVIDIA は Carrier Board Specification に**取付穴の位置を公開していない**（外形寸法のみ）。公式フォーラムでモデレータが「穴位置は **Download Center の Carrier Board Reference Design Files（PCB 設計ファイル・A04 / 2023-03-20）** から取れ」と回答している＝自作プレートを起こす場合の一次ソースはそこ。

実用解は**既存の 3D プリントモデルを使い、穴位置を当てる作業そのものを回避する**こと: **MakerWorld `1074925` / Printables `1178594`「JETSON ORIN NANO CARRIER WITH DIN RAIL MOUNT」**（Nathan Litzinger・**CC BY 4.0**・Orin Nano Dev Kit キャリアボード専用と明記）。**Bambu Lab A1 mini のプロファイル同梱でワンクリック印刷可**（0.2mm / 壁2 / infill 15% / 3プレート約3.3h）。必要ネジは **M3 ×4**（DIN レール部の M4 は本用途では不要＝ベースのみ使う）。`# TODO(到着前)` DL 116・評価1件と**検証量が少ないため寸法不一致の可能性がある**。Orin は既に手元にあるので、ロボット到着を待たず**先に試し刷りしてフィットを確認**する。

> 混同注意: 検索で出る「M2.5 スペーサー（4.5mm 六角・6.57mm 長）」は **SoM をキャリアボードに留める**ためのもので、キャリアボードを筐体に留めるネジとは別物。

#### 給電の配線設計（2026-08-06 変更: 拡張ボード経由 → **バッテリー直タップ**）

当初は拡張ボードの DC12V 出力（XH2.54）から昇圧 DC-DC を取る想定だったが、**同レールの連続電流定格が非公開**（残課題 1）である以上、そこに Orin 分（12V 側で最大約 7.5A）を上乗せするのは未知の容量に賭けることになる。→ **バッテリーの T プラグで分岐し、Orin 系統は拡張ボードを一切経由させない**。

```
バッテリー(メス) ──[T型オス]──┬──[T型メス]──→ 拡張ボード(オス) ── モータ×4
                              └── 裸線 ──→ ヒューズ10A ──→ 昇圧DC-DC 12.6→19V ──→ Orin
```

- **コネクタの性別**: 公式基板写真（`ROS_Expansion_Board_Yahboom_06.jpg`「T-shaped DC12V power input interface」）で**基板側はブレード露出＝オス**と判読 → **バッテリー側はメス**。`# TODO(到着後)` 実物で確認する。
- **既製の Y 分岐ハーネスは使えない**: 市販のパラレルハーネスは例外なく **1メス→2オス**（バッテリー2本→機器1台用）で、必要な向き（1オス→2メス）は流通していない。ただし**分岐先の片方（昇圧 DC-DC）はネジ端子で裸線を受ける**ため、**T型オス1個＋メス1個をはんだ付けするだけ**で足りる。
- **ヒューズは 10A（ミニ平型）／電線は 1.25sq 以上**。根拠: ピーク 19V×3.5A=66.5W、昇圧効率 90% で入力 74W → バッテリー 9.9V 時に **7.5A（最悪ケース）**。定格は最悪ケース×1.25 = 9.4A → 10A。**7.5A では電圧低下時の全負荷で誤断**し、**15A では 1.25sq(≈AWG16・許容約12A) の電線を守れない**（ヒューズが守るのは Orin ではなく**配線**。Orin 側の保護は昇圧 DC-DC の責務）。

#### 到着前に用意するもの（2026-08-06 確定）

| 品目 | 用途 | 状態 |
|---|---|---|
| DP→HDMI 変換アダプタ | Orin は映像が DisplayPort のみ。無いと初回ブートで画面が出ない | ✅ **調達済**（UGREEN・アクティブ式・単方向） |
| microSD 64GB A2 | QSPI 更新と初回ブート（Mac 経路の前提） | ✅ **調達済**（SanDisk Extreme） |
| NVMe SSD | JetPack 焼き込み先 | ✅ **調達済**（KIOXIA 1TB） |
| **昇圧 DC-DC 150W**（入力 10–32V / 出力 12–35V 可変・自然空冷 100W） | 12.6V→**19V** | **要購入** |
| **DC プラグ付きケーブル 外径5.5×内径2.5mm・5A 対応** | 昇圧出力 → Orin の DC ジャック | **要購入** |
| **T型（ディーンズ）コネクタ オス＋メス** | バッテリー分岐の自作 | **要購入** |
| **ミニ平型ヒューズホルダー（エーモン 3367・1.25sq）＋ ミニ平型ヒューズ 10A** | バッテリー直タップの配線保護。**ホルダにヒューズは同梱されない**（メーカー公式に「ヒューズは別途お買い求め下さい」と明記） | **要購入** |
| **テスター（マルチメータ）** | **昇圧出力を 19.0V に設定・確認**（下記警告） | **要購入・必須** |
| 14AWG(1.25sq 以上) シリコン電線 赤黒 ／ 熱収縮チューブ ／ M3 ネジ×4 | 分岐の自作・Orin マウント固定 | **要購入** |
| DC インライン電力計（0–60V / 0–100A・分解能 0.01V） | 給電の実測 ①〜④ | 推奨 |
| WiFi モジュール | **不要**（Orin の M.2 Key-E に実装済） | — |

> ⚠️ **手順厳守**: 推奨した昇圧モジュールは**多回転ポテンショメータで 12〜35V に可変**であり、**出荷時の設定値は不明（35V の可能性がある）**。Orin の入力上限は 20V。**① 12V を入れ Orin を繋がずにテスターで出力を測る → ② 19.0V に合わせる → ③ 再確認しポットを固定 → ④ その後で初めて Orin を接続**。この順を飛ばすと Orin を破壊する。テスターが必須なのはこのため。

> `# TODO(到着前)` 昇圧 DC-DC は**汎用モジュールしか見つかっていない**（素性の確かなブランド品は 19V 固定・45W 級で該当なし）。**リプル・効率・実効電流の実測値は不明**なので、現物でのリプル測定を前提に選定する。`# TODO` 価格は各所で未確認（Amazon.co.jp が自動取得に価格を返さず、モノタロウは 403）。

---

## References

- [Yahboom ESP32 MicroROS Robot Car — 公式](https://category.yahboom.net/products/microros-esp32) — 参照日: 2026-05-19
- [Jetson Orin Nano Super Dev Kit — NVIDIA](https://www.nvidia.com/en-us/autonomous-machines/embedded-systems/jetson-orin/nano-super-developer-kit/) — 参照日: 2026-05-19
- [Jetson Orin Nano Super — スイッチサイエンス](https://www.switch-science.com/products/10188) — 参照日: 2026-05-19
- [Bambu Lab A1 mini — Amazon.co.jp](https://www.amazon.co.jp/dp/B0CRYJBKQQ) — 参照日: 2026-05-19
- [Mini Warehouse — Printables.com](https://www.printables.com/model/561782) — 参照日: 2026-05-19
- [Pallet Rack 1:10 — Printables.com](https://www.printables.com/model/567874) — 参照日: 2026-05-19
- [Isaac Sim Requirements — NVIDIA](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/requirements.html) — 参照日: 2026-05-19
- [ORBBEC MS200 dToF LiDAR — Orbbec公式](https://www.orbbec.com/products/lidar/ms200k/) — 参照日: 2026-05-21
- [ORBBEC MS200 ユーザーマニュアル](https://manuals.plus/orbbec/ms200-dtof-lidar-sensor-manual) — 参照日: 2026-05-21
- [Yahboom ROSMASTER M1 — 公式](https://category.yahboom.net/products/rosmaster-m1) — 参照日: 2026-08-05（車体寸法・同梱物は公式 Product parameters 図 / Shipping List 図を実見）
- [Yahboom ROS robot expansion board V3.0 — 公式](https://category.yahboom.net/products/ros-driver-board) — 参照日: 2026-08-05（12V/5V/Type-C 出力・T プラグ 12V 入力・Q&A）
- [Yahboom バッテリ取扱注意](https://www.yahboom.net/public/upload/upload-html/1697613339/Precautions%20for%20battery.html) — 参照日: 2026-08-05（保管 11.1–11.7V / 9.6V 警報）
- [Jetson Orin Nano Devkit Carrier Board Specification SP-11324-001 v1.3 — NVIDIA](https://developer.nvidia.com/downloads/assets/embedded/secure/jetson/orin_nano/docs/jetson_orin_nano_devkit_carrier_board_specification_sp.pdf) — 参照日: 2026-08-05（§1.2 / §3.8 DC ジャック 9–20V・5.5mm/2.5mm・3.5A）
- [Nuwa-HP60C 深度カメラ — 公式](https://category.yahboom.net/products/hp60c) — 参照日: 2026-08-05（単品 $150 / ブラケット付 $170）
