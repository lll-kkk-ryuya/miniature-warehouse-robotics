# ハードウェア設計

作成日: 2026-05-21
更新日: 2026-08-21

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

- ROS 2 Humble のホスト（司令塔。ADR-0008）
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
> 購入候補: Amazon.co.jp `Superior / without Nano`（ASIN B0G495C65Q・¥67,527・Nuwa-HP60C 深度カメラ同梱）。**出品者確認済（2026-08-06・Amazonメッセージ）**: 本 SKU の制御ボードは拡張ボード V3.0（YB-ERF01-V3.0）であり、`without Nano` は **Jetson Nano B01 計算ボードのみ非同梱**の意。

### 確認済み（一次情報で裏取り済）

| 項目 | 値 | 出典 |
|---|---|---|
| 車体寸法 | 全幅 231.40 × 全長 284.40 × 全高 181.40mm（LiDAR 上面 147.50 / 車体上面 74.58） | 公式 Product parameters 図 |
| バッテリ | 12.6V 6000mAh + 12.6V/2A 充電器 → **3S 構成と整合**（保管 11.1–11.7V・**9.6V でブザー警報**） | 公式 unboxing / battery precautions |
| 拡張ボード出力 | **DC 12V ×2（XH2.54 2PIN）/ DC 5V ×1（バレル・シルク "5VOUT2"）/ Type-C 5V ×1（"5VOUT1"・Pi5 給電対応）** | ROS robot board V3.0 パラメータ表 |
| 拡張ボード入力 | T プラグ **DC 12V のみ**（公式 Q&A「This board just support 12VDC input」） | 公式製品ページ Q&A |
| Orin 給電経路（純正） | Orin Nano SUPER 版のみ **「DC5.5×2.5 → XH2.54 2PIN 電源ケーブル」**同梱＝**12V を Orin の DC ジャックへ直結**（DC-DC 非使用） | 公式 Shipping List 図 |
| Orin 入力仕様 | **9–20V / センタープラス / バレル外径 5.5mm・ピン 2.5mm / 最大 3.5A**（付属アダプタ実銘板 = LITEON `PA-1450-26` / **19V 2.37A = 45.0W** / 入力 100-240V。現物確認 2026-08-23 → 末尾追記 P-1） | NVIDIA SP-11324-001 v1.3 §1.2, §3.8 ＋ 現物銘板 |
| 電力モード | 15W / 25W / MAXN SUPER（**uncapped・W 値は非公開**） | NVIDIA JetPack 6.2 blog / Developer Guide |
| OS | M1 の Orin 版は Ubuntu 22.04 + **ROS 2 Humble**（本プロジェクトは Jazzy） | 公式 Product parameters 図 |
| 拡張ボード実装（2026-08-05 追加） | 型番 **YB-ERF01-V3.0**／MCU STM32F103RCT6／USB-serial **CH340**／IMU **ICM20948 9軸**／モータドライバ **AM2861 ×4**／通信 **115200bps**／待機電流 **約 50mA**／基板 **85×56mm**・取付穴 **4-φ2.5（58×49mm ピッチ）** | 公式 `ROS_control_board_V3.0_parameters.jpg` を実見 |
| 拡張ボード保護回路（2026-08-05 追加） | **「サーボ過電流保護・逆接続保護・短絡保護」のみ**。**12V 出力レールには過電流保護・ヒューズが無いことを出品者が明言**（2026-08-06 回答「No overcurrent protection circuit or fuse is installed」） | 同上 ＋ 出品者回答（下記 References） |
| **12V 出力の性質（2026-08-05・重要）** | 入力が「T type **DC12V** input」、出力が「**DC 12V** interface ×2」、モータも「**12V** encoder motor」＝**同一呼称の単一レール**。3S（12.6→9.6V）から定電圧 12V を作るには昇降圧回路が要るが**その記載も実装も見当たらない** → **生バッテリ電圧のスルー出力で確定**（2026-08-06 出品者回答「The output voltage is the same as the input voltage from the T-plug (it is not regulated)」。定格 **4A／ピーク 6A**） | 同上 ＋ 出品者回答（下記 References） |

> 注: 本節の「9–20V」が正。上記 `#### Super モードで必要な追加投資` の「入力 7–20V」は NVIDIA フォーラム由来の記述で、**Carrier Board Specification の 9–20V と食い違う**（`# TODO`: 該当行を要訂正）。

### 未確定（購入・実装前に潰す）

1. **【解決 2026-08-06・出品者回答】拡張ボード 12V レールは定格 4A／ピーク 6A・過電流保護もヒューズも無し**（Amazonメッセージでの Yahboom 回答。2026-08-05 時点では公式パラメータ表に記載が無かった項目）。Orin 系統の最悪ケースは 12V 側で約 7.5A（下記「給電の配線設計」）＝**定格 4A のほぼ 2 倍**であり、同レールに AM2861 モータドライバ ×4 も同居する。→ **バッテリー直タップ（拡張ボード非経由）の決定が定量的にも裏付けられた**。実機実測（下記「給電の実測手順」）はレール確認から Orin 系統の健全性確認へ目的を変えて維持。
2. **【確定 2026-08-06 / 方針決定 2026-08-05】12V 出力は生バッテリ電圧のスルー（非安定化）**（出品者回答で確定。根拠は上表「12V 出力の性質」）。したがって **Orin が見る電圧は満充電 12.6V からブザー警報 9.6V まで下がり、Orin 下限 9V までの余裕は 0.6V しかない**。モータ加速時のサグが重なれば 9V 割れは現実的に起こりうる。出品者自身も「9V 未満で電圧降下が顕著・**9.5V 以上での運用を推奨**」と回答しており、マージンの薄さは vendor 公認。
   **→ 既定の構成を「昇圧 DC-DC 経由」とする**（12.6V→19V・連続 **≥45W**・出力 **5.5×2.5 センタープラス**）。**12V 直結は「実測①〜④で問題が無いと確認できた場合のみ選べる縮退案」へ格下げする。** 理由: Orin の電圧断は L1 緊急停止と外部通信ごと落とす安全事象であり、0.6V のマージンに賭ける設計は `.claude/rules/safety.md` の趣旨に反する。純正 Orin 版が 12V 直結ケーブルを同梱している事実（上表）は、Yahboom がこのマージンを許容していることを示すに留まり、**本プロジェクトの安全要件を満たす根拠にはならない**。
3. **【解決 2026-08-06・出品者回答】XH2.54 2PIN ⇔ DC5.5×2.5 ケーブル（Orin 版同梱と同一品）は単品購入可**。ただし現行の既定構成はバッテリー直タップ＋昇圧 DC-DC（出力側は汎用 DC5.5×2.5 ケーブル）のため、このケーブルが必要なのは**縮退案（12V 直結）を選ぶ場合のみ**。縮退案の保険として購入するかは発注時に判断。
4. `# TODO(Phase 1)` **マウント**: `without` 版の取付板は選択したボード用。Orin Dev Kit（キャリア一体・完成体 103×90.5×34.8mm）は**自作プレートで固定する前提**（3D プリント案は下記「Orin マウント」）。出品者回答（2026-08-06）: Orin Dev Kit 用取付板の単品販売は**手持ちボードの確認待ち**（「Orin Nano Developer Kit か Nano B01 か」への返信が必要）＋**現行キット付属品の一部は Orin Dev Kit に非互換の可能性**と注意あり。上段デッキとの高さ干渉は実物合わせ。（→ **末尾追記 P-5 で更新**: 公式 Orin 用組立動画の確認により**付属部品での直ネジ止めが第一候補**・自作プレートは fallback）
5. **【解決】ソフト方針＝Yahboom スタックを使わず自前 ROS 2 ノードを書く。** 制御プロトコルは判明済（USB シリアル 115200 8N1・`HEAD=0xFF, DEVICE_ID=0xFC, LEN, FUNC, payload…, CHECKSUM`、`CHECKSUM=(sum+257-0xFC)&0xFF`、`FUNC_MOTION=0x12`・`FUNC_MOTOR=0x10`・`FUNC_REPORT_*` を MCU が **40ms 周期で auto-report**）。Yahboom の `Rosmaster_Lib` は `struct/time/serial/threading` のみ依存＝**アーキ非依存で aarch64 可**だが、ライセンスが Proprietary 表記・PyPI 未配布・配布が Google Drive のため、**判明済フレーム仕様から自前実装する方がクリーン**。デバイスは udev symlink `/dev/myserial` に固定。
6. **【解決】メカナム逆運動学は STM32 ファーム側にある** → ホストは `/cmd_vel` の `(vx, vy, wz)` を投げるだけ（`set_car_motion` は body 速度を `int16(v*1000)` で送るのみ・4輪配分なし）。**よって凍結 URDF / Nav2 が diff-drive のままでも `linear.y = 0` で成立し、メカナム採用に契約変更は不要**。omni 化（AMCL Omni / `vy_max` > 0 / `motion_model: "Omni"` / `linear.y` の twist_mux→collision_monitor 通し）は**任意の後続拡張**として扱う。sim 側 diff_drive プラグインの差し替えも omni 化する場合のみ。
7. **【方針決定 2026-08-05・M1 / Superior 構成】速度クランプは「ホスト側シリアルドライバ内の送信直前クランプ（L0'）」に置く。**
   現行方針は ESP32 自前ファーム内で 0.3 m/s をハードクランプする（`.claude/rules/safety.md`・`firmware/include/safety_clamp.h` の R-26 unit）。**M1 の STM32 ファームは Yahboom 製バイナリ**のため、MCU 内に同じ保証を置けない。一方で残課題 5 の通り**ホスト側シリアルドライバは自前実装**であり、`FUNC_MOTION=0x12` フレームを組む直前が**全 `cmd_vel` が必ず通る単一の絞り点**になる。ここでクランプすれば、Nav2 / Policy Gate / Emergency Guardian のいずれが壊れても wire に 0.3 m/s 超は出ない。
   - **採用**: ドライバの送信直前（body 速度を `int16(v*1000)` へ変換する直前）でクランプする。**R-26（独立オラクル unit ＋ mutation で赤くなること）の対象**とし、`warehouse_interfaces.safety.MAX_LINEAR_VELOCITY` を**単一ソースとして import**する（値の再定義を禁止＝`safety.py:19` の「hardcode するな」に従う）。
   - **却下**: STM32 ファームの自前差し替えによる L0 維持 — 残課題 6 の通り**メカナム逆運動学が STM32 側にある**ため、差し替えると 4輪配分の再実装まで背負う。Phase 1 のスコープに対して過大。
   - **明記すべき限界**: L0' は**ホストプロセスが生きている間だけ**有効。ホスト停止・USB 断では MCU 側に最後の指令が残り、暴走しうる。→ `# TODO(Phase 1)` **MCU の通信タイムアウト停止（watchdog）の有無を実機で確認**する。無い場合は Emergency Guardian からの明示 stop フレーム送出＋電源系での縮退で補う。
   - `# TODO(採用時)` **doc 影響**: `.claude/rules/safety.md`「ロボット速度制限をコード内で強制する」の実施箇所と、[12-infrastructure-common.md](../architecture/12-infrastructure-common.md) の Layer マップ（L0 の定義）を M1 採用時に改訂する。
8. `# TODO(発注前)` **Nuwa-HP60C 深度カメラ（Superior 版）の Jazzy 動作は未保証。** ROS 2 ドライバ `ascamera` は ament_cmake ＋ **閉ソースのプリビルド `.so`**（`libAngstrongCameraSdk.so` ほか）に静的リンク。aarch64 バイナリは同梱されるが、その `libs/lib/aarch64-linux-gnu/readme.md` は **「5.4.1 20170404 (Linaro GCC 5.4-2017.05)」＝2017 年 GCC 5.4 ビルド**。動作報告のある distro は Foxy(20.04) / Humble(22.04) のみで、**Jazzy / Ubuntu 24.04 の成功報告は無い**。割れてもソースが無く修正不能。
   **→ 2026-08-05 方針: distro 自体を Humble に寄せることで本項を構造的に解消する（[ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md) proposed）。** retreat plan（Humble コンテナ隔離 / RealSense・Orbbec 等への置換）は ADR-0008 が却下扱いとして保持。
9. `# TODO(Phase 1)` **LiDAR ドライバ**: T-mini Plus は YDLIDAR 製（model 151・baud 230400・12m）。`ydlidar_ros2_driver` は **OSS でソースビルド可＝aarch64 に障害なし**。**master は Jazzy でビルドが割れる**（upstream issue #72 / PR #66 が OPEN）が、`humble` ブランチが本来の対象のため **Humble 採用時は C++17 引き上げパッチ不要**（[ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)）。Jazzy を維持する場合のみ vendoring + パッチが要る。
10. **【一部解決】小項目**: 公式パラメータ表の実見（2026-08-05）で **USB-serial = CH340**（→ udev は `1a86:7523`）・**IMU = ICM20948**・**通信 115200bps** が確定。残る実機確認は **M1 用 `car_type` 値**と、**MCU auto-report が 40ms=25Hz 固定**である点の Nav2 チューニング（`controller_frequency` / AMCL 更新レート）への影響。

### 給電の実測手順（実機到着後）

| 段階 | 測り方 | 合格ライン |
|---|---|---|
| ① 無負荷 | マルチメータで 12V 出力端子 | 表記どおりの電圧が出ているか |
| ② Orin アイドル | DC インライン電力計 または `sudo tegrastats` の `VDD_IN` | 実消費 W を記録 |
| ③ Orin 高負荷 | MAXN SUPER + Nav2 + LiDAR + カメラ | **9V を下回らない**・再起動しない |
| ④ **モータ同時加速 ＋ ③** | ③の状態で急発進（最悪条件） | 同上 |

既定は昇圧 DC-DC 経由（残課題 2）。①〜④すべてで 9V 割れ・リプルが無いと確認できた場合のみ、縮退案（12V 直結）を選択できる。

> 対象 layer: 給電系は **L0 未満（ハードウェア）**。ただし Orin の電圧断は L0 の緊急停止と micro-ROS リンクごと落とすため、安全要件として扱う（`.claude/rules/safety.md`）。

### 決定（2026-08-05）: 車体寸法・既存コードは車種選定の制約にしない

**オペレーター裁定（2026-08-05）**: ジオラマは未着工・実機ソフトは Phase 1 未着手のため、**M1 の実寸に合わせてジオラマを作り直し、開発コードも書き換える**。したがって下表は「M1 を却下する理由」ではなく、**採用した場合に実施する作業項目**として扱う（ジオラマ側の対応は [04-diorama-layout.md](04-diorama-layout.md) §「決定（2026-08-05）」）。

#### 必須（車体が物理的に大きくなることの帰結。駆動方式とは無関係）

| # | 書き換え対象 | 現在値（実ファイル） | M1 採用時 | layer |
|---|---|---|---|---|
| C-1 | `ROBOT_RADIUS` | `ws/src/warehouse_description/warehouse_description/robot_dimensions.py:66` = `0.075`（75mm） | **【2026-08-17 改訂】非円形へ移行（additive）**: `FOOTPRINT_POLYGON`（矩形 **231.4 × 284.4mm**）と `CIRCUMSCRIBED_RADIUS`（外接円 **≈184mm**＝対角 367mm ÷ 2）を**新定数として追加**。`ROBOT_RADIUS`(=0.075) は**値も意味も変えず**旧車体値のまま据え置く（内接前提の live consumer があるため意味の読み替え禁止＝23 末尾 F-6）。保守的用途（C-3 等）は `CIRCUMSCRIBED_RADIUS` を消費。**円形 184mm への単純改訂は不可**（直径 368mm > 通路 280mm で通路が全面 lethal）＝OQ-3 決定（→ 本 doc 末尾【2026-08-17 追補】/ [23 末尾 F 系列](../architecture/23-perception-and-localization.md)） | L2 |
| C-2 | costmap `robot_radius` ×2 | `ws/src/warehouse_bringup/config/nav2_params.yaml:215` / `:257` = `0.075` | **【2026-08-17 改訂】**: `robot_radius` に C-1 同値を入れるのではなく **`footprint:` polygon へ移行**（C-1 の矩形・単一ソース維持 R-42）。MPPI `consider_footprint: true`（`nav2_params.yaml:179`）と**同一 PR で同時 flip**（片方だけだと controller_server が configure 失敗＝#67 E2E ゲート教訓 `:171-178`）（→ 本 doc 末尾【2026-08-17 追補】/ [23 末尾 F 系列](../architecture/23-perception-and-localization.md)） | L2 |
| C-3 | collision_monitor PolygonStop | `ws/src/warehouse_bringup/config/collision_monitor.yaml:68` `radius: 0.09` | 車体外接 + 余裕へ改訂 | L1 |
| C-4 | 速度クランプの**置き場所** | `firmware/include/safety_clamp.h:45`（ESP32 ファーム内） | **ホスト側シリアルドライバの送信直前（L0'）へ移設**（残課題 7） | L0' |

#### 任意（**omni 化を選んだ場合のみ**。既定では不要）

> **重要（2026-08-05 訂正）**: 残課題 6 の通り**メカナム逆運動学は STM32 ファーム側にある**。ホストは `(vx, vy, wz)` を送るだけなので、**`linear.y = 0` に固定すれば凍結 URDF / Nav2 が diff-drive のままで M1 は成立し、契約変更は不要**。下表は「横移動を実際に使う」と決めた場合にのみ発生する。

| # | 書き換え対象 | 現在値（実ファイル） | omni 化する場合 | layer |
|---|---|---|---|---|
| C-5 | 横速度 `linear.y` | `ws/src` / `firmware` に**実装 0 件**（grep 一致なし・テスト除く） | twist_mux → collision_monitor → ドライバを縦断で新規実装 | L0'–L2 |
| C-6 | 駆動モデル | `nav2_params.yaml:52` `DifferentialMotionModel` / `:124` `vy_max: 0.0` / `:134` `motion_model: "DiffDrive"` | AMCL Omni / `vy_max > 0` / `motion_model: "Omni"` | L2 |
| C-7 | sim プラグイン | Gazebo `diff_drive` | メカナム相当へ差し替え | L2 |
| C-8 | **ベクトル速度クランプ** | `ws/src/warehouse_interfaces/warehouse_interfaces/safety.py:26-34` `clamp_velocity()` は**スカラー1軸** | **(vx, vy) の大きさ**でクランプする関数を追加（**【2026-08-17】L0' driver 側 `clamp.py:127` の hypot 実装で landed＝interfaces 無編集**） | L0' / L1 |

> **C-8 は omni 化の前提条件であり、後回しにできない。** `vy ≠ 0` を許した状態で各軸を独立に 0.3 m/s クランプすると、対角合成が √(0.3² + 0.3²) = **0.424 m/s** となり `.claude/rules/safety.md` の 0.3 m/s ハードキャップを **41% 超過**する。C-5 と C-8 は同一 PR で入れること。R-26（独立オラクル・mutation で赤くなること）の対象（[20-dev-quality-and-testing.md](../architecture/20-dev-quality-and-testing.md) §9）。

凍結リンク名 `wheel_{front,rear}_{left,right}`（`robot_dimensions.py:32-35`）はメカナム化でも**無傷**（4輪配置が同じため）。C-1 は `warehouse_description`、C-8 は `warehouse_interfaces` に触れるため **`contract` ラベル PR ＋ 依存トラック予告**が必要（`.claude/rules/parallel-workflow.md` §4）。C-2〜C-7 は config / 各パッケージ内で閉じる。（**注 2026-08-17**: C-8 は L0' driver 側実装＝interfaces 無編集の別解で landed 済み・contract PR 不要になった。C-1 は従来通り）

> `# TODO(採用時)`: C-1〜C-4 を epic Issue のチェックリストへ展開する。C-5〜C-8 は「横移動を使うか」を決めてから別 Issue に切る。

### 決定（2026-08-05）: Superior 版を採用し、HP60C 深度カメラを開発要件に含める

**オペレーター裁定（2026-08-05）**: 購入は **`Superior-without / NANO 4GB SUB`**（公式 sku 3000200910 / $499.90、Amazon.co.jp ASIN B0G495C65Q ¥67,527）とし、同梱の **Nuwa-HP60C 深度カメラを「使わない同梱物」ではなく開発要件として扱う**。

- **帰結（重要）**: 残課題 8（HP60C の ROS 2 ドライバ `ascamera` が閉ソース `.so` 依存・Jazzy 動作報告なし）が**回避可能な項目から、必ず解かねばならない項目へ昇格**する。したがって [ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)（Jazzy→Humble）の前提が成立する。ADR-0008 は **proposed のまま保持**（同日オペレーター指示）。
- **深度カメラを要件化して得るもの**（何に使うかを docs 側で先に定義しておく）:
  - 2D LiDAR の死角（棚の張り出し・低い荷物・段差）の**3D 障害物検知** → costmap への voxel 反映。
  - **パレット / 荷物の認識**。上記 §B「将来: FoundationPose による荷物認識」の実入力となる。
  - **Mode X-ER（ER 視覚司令官）への実カメラ入力**。現状は静止画・sim 由来のため、実機 RGB-D が入ると live 検証の忠実度が上がる。
- **変わらないもの**: C-1〜C-4（車体寸法由来）は Superior / Standard の別と無関係にそのまま必要。
- `# TODO(発注と独立に先行決定)` **distro をどうするか（ADR-0008 の未決＝Gazebo を Fortress に落とすか Harmonic をソースビルドするか）は Phase 0.5 のブロッカー**。実機到着を待つ必要が無いので、発注可否とは切り離して先に決める。判断材料は ARM64 headless での Gazebo 再スパイク結果（[16-repository-and-conventions.md](../architecture/16-repository-and-conventions.md):213-215 の GO 判定を引き継げるか）。

### 購入確定（2026-08-05）と Orin 立ち上げ経路

**発注済**: Amazon.co.jp 注文 `249-3401070-6986233`（2026-08-05・¥67,527）。商品名「Yahboom Jetson Nano B01搭載 ROS2ロボット …**Superior without Nano**」＝ `Superior-without / NANO 4GB SUB`（公式 sku 3000200910）。**着荷済（2026-08-19 開梱確認: T-mini Plus LiDAR・Nuwa-HP60C 同梱を実物確認）**。紙説明書の Shipping List（[11-m1-assembly-manual.md](11-m1-assembly-manual.md) §1）上は他に**音声モジュール・ゲームパッド等も同梱**だが、実物での一次確認は上記 2 点のみ＝`# TODO(実物確認)`。

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
- 本プロジェクトの開発機は **MacBook Pro M4 のみ** → **SDK Manager は使えない**。**microSD 経路を採る**（microSD と SD カードリーダーが前提。焼いた microSD は SSD 移行後も**復旧用ブートメディアとして保管**する）。
- JetPack は **6.2 系**（Ubuntu 22.04 rootfs = Humble ネイティブ。Super 化 `nvpmodel -m 2` も 6.2 で入った機能）。

#### マウント

NVIDIA は Carrier Board Specification に**取付穴の位置を公開していない**（外形寸法のみ）。公式フォーラムでモデレータが「穴位置は **Download Center の Carrier Board Reference Design Files（PCB 設計ファイル・A04 / 2023-03-20）** から取れ」と回答している＝自作プレートを起こす場合の一次ソースはそこ。（→ **末尾追記 P-5**: 穴位置を含む **3D CAD STEP モデルが Download Center に公式提供**と判明・本文の「Spec に穴位置なし」自体は正）

実用解は**既存の 3D プリントモデルを使い、穴位置を当てる作業そのものを回避する**こと: **MakerWorld `1074925` / Printables `1178594`「JETSON ORIN NANO CARRIER WITH DIN RAIL MOUNT」**（Nathan Litzinger・**CC BY 4.0**・Orin Nano Dev Kit キャリアボード専用と明記）。**Bambu Lab A1 mini のプロファイル同梱でワンクリック印刷可**（0.2mm / 壁2 / infill 15% / 3プレート約3.3h）。必要ネジは **M3 ×4**（DIN レール部の M4 は本用途では不要＝ベースのみ使う）。`# TODO(到着前)` DL 116・評価1件と**検証量が少ないため寸法不一致の可能性がある**。Orin は既に手元にあるので、ロボット到着を待たず**先に試し刷りしてフィットを確認**する。（→ **末尾追記 P-5 で fallback に格下げ**: 公式動画が付属部品での直ネジ止めを示した。試し刷りは必須でなく fallback 準備）

> 混同注意: 検索で出る「M2.5 スペーサー（4.5mm 六角・6.57mm 長）」は **SoM をキャリアボードに留める**ためのもので、キャリアボードを筐体に留めるネジとは別物。

#### 給電の配線設計（2026-08-06 変更: 拡張ボード経由 → **バッテリー直タップ**）

当初は拡張ボードの DC12V 出力（XH2.54）から昇圧 DC-DC を取る想定だったが、決定時点では**同レールの連続電流定格が非公開**（残課題 1）で、未知の容量に賭けることになるため回避した。**2026-08-06 の出品者回答で定格 4A／ピーク 6A・保護なしと判明**し、Orin 分（12V 側で最大約 7.5A）は**定格のほぼ 2 倍**＝この回避は事後的にも必須だったと確定。→ **バッテリーの T プラグで分岐し、Orin 系統は拡張ボードを一切経由させない**。

```
バッテリー(メス) ──[T型オス 30cm]──(WFR-3 ＋/−各1)──┬──[T型メス 30cm]──→ 拡張ボード(オス) ── モータ×4
                                                    └──→ ヒューズ10A ──→ 昇圧DC-DC 12.6→19V ──→ Orin
```

- **コネクタの性別**: 公式基板写真（`ROS_Expansion_Board_Yahboom_06.jpg`「T-shaped DC12V power input interface」）で**基板側はブレード露出＝オス**と判読 → **バッテリー側はメス**。`# TODO(到着後)` 実物で確認する。
- **既製の Y 分岐ハーネスは使えない**: 市販のパラレルハーネスは例外なく **1メス→2オス**（バッテリー2本→機器1台用）で、必要な向き（1オス→2メス）は流通していない。ただし**分岐先の片方（昇圧 DC-DC）はネジ端子で裸線を受ける**ため、**ワイヤー付き T型オス／メス（14AWG・裸線端）＋ WAGO WFR-3 レバーコネクタ**で組める＝**はんだ付けは不要**（2026-08-21 着荷・実物確認済。ゆえに電線・熱収縮チューブ・はんだ工具は購入していない）。**買うときの罠**: 「T プラグ変換アダプター」名の商品は両端がコネクタの**規格変換品**が多く分岐材料にならない——**片端が裸線**である明記を確認する。WAGO も 2 穴の 221-412 では 3 本結線が組めない（3 本用は WFR-3 / 221-413）。
- **ヒューズは 10A（ミニ平型）／電線は 1.25sq 以上**。根拠: ピーク 19V×3.5A=66.5W、昇圧効率 90% で入力 74W → バッテリー 9.9V 時に **7.5A（最悪ケース）**。定格は最悪ケース×1.25 = 9.4A → 10A。**7.5A では電圧低下時の全負荷で誤断**し、**15A では 1.25sq(≈AWG16・許容約12A) の電線を守れない**（ヒューズが守るのは Orin ではなく**配線**。Orin 側の保護は昇圧 DC-DC の責務）。`# TODO(要検討)` 本ヒューズが守るのは **Orin レグのみ**——幹線（バッテリー→分岐点）と拡張ボードレグはヒューズ上流で無保護のため、幹線側メインヒューズの要否は別途検討。

#### 到着前に用意するもの（2026-08-06 確定）

| 品目 | 用途 | 状態 |
|---|---|---|
| DP→HDMI 変換アダプタ | Orin は映像が DisplayPort のみ。無いと初回ブートで画面が出ない | ✅ **調達済**（UGREEN・アクティブ式・単方向） |
| microSD 64GB A2 | QSPI 更新と初回ブート（Mac 経路の前提） | ✅ **調達済**（SanDisk Extreme） |
| NVMe SSD | JetPack 焼き込み先 | ✅ **調達済**（KIOXIA 1TB） |
| **昇圧 DC-DC 150W**（入力 10–32V / 出力 12–35V 可変・自然空冷 100W） | 12.6V→**19V** | ✅ **着荷済**（2026-08-19 発注・8/20 発送・**現物確認 2026-08-23**）。実仕様・保護回路の欠如・調整ボリュームの罠は末尾追記 P-4。**本表の「発注済で未着」はこれでゼロ**＝残るのは下2行の「未購入」のみ |
| **DC プラグ付きケーブル 外径5.5×内径2.5mm・5A 対応** | 昇圧出力 → Orin の DC ジャック（Yahboom の XH2.54⇔DC5.5×2.5 単品は**縮退案=12V 直結用**。残課題 3） | ✅ **調達済**（5.5×2.5/2.1 両対応・2本入。2026-08-21 着荷）。`# TODO(到着後)` Orin 側はピン **2.5mm 固定**（`:307`/`:405`）——2.1 兼用プラグが緩まないか実挿しで確認 |
| **T型（ディーンズ）コネクタ オス＋メス ＋ WAGO WFR-3BP レバーコネクタ**（3本用・8個入） | バッテリー分岐の自作。**ワイヤー付き**T コネクタで裸線端が出るため、WFR-3（=WAGO **221-413** 相当のレバー式。適合: 単線 φ0.5–2.0mm・IV7本より線 0.2–3.5mm²・**可とうより線 0.14–4.0mm²**＝14AWG 可とうより線が適合・最大被覆外径 φ4.0mm）で結線＝**はんだ不要**。＋側／−側で各1個使う。**定格 20A/300V（PSE）・32A/450V（JIS）**（WAGO 公式カタログ ctlg_wfr.pdf・参照日 2026-08-21）——分岐ノードは `:427-428` の図のとおり**ヒューズより上流＝無保護で総電流**（拡張ボード側ピーク 6A（`:318`）＋ Orin 系統最悪 7.5A（`:433`）≈ 13.5A）を通すが**定格内** | ✅ **調達済**（ワイヤー付き 14AWG 約30cm オス／メス 各1・予備に裸コネクタ10個・WFR-3BP 8個入。2026-08-21 着荷。結線は**単線・より線 共通**: むき長さ **10–12mm** → **レバーを上げる → 突き当たりまで挿入 → レバーを下げる → 軽く引いて抜けないことを確認**〔WAGO 公式カタログ準拠〕） |
| **ミニ平型ヒューズホルダー（エーモン 3367・1.25sq）＋ ミニ平型ヒューズ 10A** | バッテリー直タップの配線保護。**ホルダにヒューズは同梱されない**（メーカー公式に「ヒューズは別途お買い求め下さい」と明記） | ✅ **調達済**（3367 ＋ エーモン 3677 10A 5個入を別注文。2026-08-21 着荷） |
| **テスター（マルチメータ）** | 3役: **昇圧出力を 19.0V に設定・確認**（下記警告）＋**初通電（下記①）前のハーネス導通・＋/−短絡チェック**＋**Orin 接続（下記④）前の DC プラグ極性（センタープラス `:307`）確認** | ✅ **調達済**（HIOKI 3244-60 カードハイテスタ・日本製。2026-08-20 着荷） |
| 14AWG(1.25sq 以上) シリコン電線 赤黒 ／ 熱収縮チューブ ／ M3 ネジ×4 | 分岐の自作・Orin マウント固定 | 単品の電線・熱収縮チューブは**不要**（無はんだ構成に確定＝上記 Y 分岐の項）。**`:433` の「1.25sq 以上」要件は撤回していない**——ワイヤー付き T コネクタの 14AWG(≈2.0sq) とエーモン 3367 のリード(1.25sq) で満たす。**M3 ネジ×4 は未購入**（マウント試し刷り後に長さ確定・キット余りを先に確認） |
| DC インライン電力計（0–60V / 0–100A・分解能 0.01V） | 給電の実測 ①〜④ | **未購入**（2026-08-19 判断）。実測② は `:341` が元から併記する `tegrastats` の `VDD_IN` で代替できるが、**③④ はバッテリー側電圧の連続監視が要り、手段が未定＝`# TODO(要決着)`**（`:323` の `FUNC_REPORT_*` に電圧が含まれるかは docs に根拠なし・未確認。テスターは走行中の常時ログに不向き） |
| WiFi モジュール | **不要**（Orin の M.2 Key-E に実装済） | — |

> ⚠️ **手順厳守**: 採用した昇圧モジュールは**多回転ポテンショメータで 12〜35V に可変**であり、**出荷時の設定値は不明（35V の可能性がある）**。Orin の入力上限は 20V。**① 12V を入れ Orin を繋がずにテスターで出力を測る → ② 19.0V に合わせる → ③ 再確認しポットを固定 → ④ その後で初めて Orin を接続**。加えて、**① の前（＝最初の通電前）に、組み上げたハーネスの導通と＋/−の分離（同一 WFR-3 への極性違い挿し＝バッテリー直短絡が無いこと）**を、**④ の前に DC プラグ極性（センタープラス）**を、それぞれテスターで確認する——無はんだ構成では極性が結線作業に依存し、**逆接も Orin を破壊**、分岐ノードの短絡は**ヒューズ上流＝無保護**。この順を飛ばすと Orin を破壊する。テスターが必須なのはこのため。

> `# TODO(到着後)` 昇圧 DC-DC は**汎用モジュール**（「DC-DC昇圧コンバーター 150W 入力10-32V 出力12-35V」）で確定・発注済（価格は [01-budget-and-procurement.md](01-budget-and-procurement.md) 末尾の実購入台帳）。素性の確かなブランド品が無い状況は変わらないため、**リプル・効率・実効電流は依然不明**——上の手順①〜④に加え、**現物でリプルを測る**ことを前提とする。

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
- Yahboom 出品者回答（Amazon メッセージ・2026-08-06 16:41）— 一次情報。①本 SKU の制御ボード=拡張ボード V3.0・`without Nano`=Jetson Nano B01 のみ非同梱 ②12V 出力（XH2.54）= T プラグ入力の非安定化スルー・**定格 4A／ピーク 6A・過電流保護/ヒューズ無し**・バッテリー 9.5V 以上推奨 ③XH2.54⇔DC5.5×2.5 ケーブル（Orin 版同梱と同一）単品購入可 ④Orin Dev Kit 用取付板はボード種別の確認待ち＋キット付属品の一部非互換の注意

---

## 【2026-08-07 追記】台数と知覚スタックの現行方針

- **台数**: §A 仕様表の「2台」は ESP32 Car 旧前提。現行実機は **ROSMASTER M1 1台 + Orin 直結シリアル（micro-ROS 経路不使用）**＝[ADR-0006 単騎構成](../adr/0006-single-bot-first.md)。§「ROSMASTER M1 採用検討時の残課題」以降が実機の正本。
- **知覚・自己位置**: HP60C 深度・T-mini Plus・IMU/エンコーダを使う TARGET スタック（nvblox / MOLA-LO〔旧 cuVSLAM は blocked〕/ robot_localization EKF）は [architecture/23](../architecture/23-perception-and-localization.md) が設計正本（スパイクゲート S1=8GB メモリ・S2=HP60C 互換が前提）。固定 RPLiDAR A1 の「外部トラッキング補正」→ **ground truth 取得装置**への役割変更は同 doc §5-5 の**提案**（doc09 所有トラック承認待ち）。

---

## 【2026-08-09 追記】俯瞰カメラの用途切り分け（ADR-0007）

§E 撮影機材の俯瞰カメラ（Logicool C922n）は**動画撮影専用**であり、**ER/知覚の画像入力には使わない**（[ADR-0007](../adr/0007-no-overhead-camera-gesture-via-onboard-nn.md)。ER/ジェスチャ入力は搭載 HP60C に一本化）。撮影用 C922n 自体の要否・「撮影用カメラを ER 入力に流用しない」運用の明文化は ADR-0007 Open。ジェスチャ認識の設計正本は [mode-x-er/09](../mode-x-er/09-hand-raise-summon.md)、HP60C の S2 スパイクゲートは [architecture/23 §7](../architecture/23-perception-and-localization.md)。

---

## 【2026-08-17 追補】OQ-3 決定に伴う C-1 / C-2 の改訂（非円形 footprint への移行）

Status: **決定の docs 反映のみ**（実装なし。CURRENT の `robot_dimensions.py` / `nav2_params.yaml` / `collision_monitor.yaml` は本追補では変更しない＝実装は Slice 1 の別 PR）。決定正本は [23-perception-and-localization.md](../architecture/23-perception-and-localization.md) 末尾【2026-08-17 追補】**F 系列**、起票は [Issue #519](https://github.com/lll-kkk-ryuya/miniature-warehouse-robotics/issues/519)。本節は §「決定（2026-08-05）: 車体寸法・既存コードは車種選定の制約にしない」の **C-1 / C-2 行**（同 §「必須」表）を改訂した理由と旧文言の履歴を残すためのもの。

### 1. 旧処方が自己矛盾だった理由

- 旧 C-1 / C-2 は「`ROBOT_RADIUS` を外接円 **≈184mm** へ改訂 → costmap `robot_radius`（`nav2_params.yaml:215` / `:257`）を同値へ同期」だった。
- しかしこれは **円形 footprint = 直径 368mm**（184 × 2）を意味し、[`04-diorama-layout.md:128`](04-diorama-layout.md)（通路幅表「すれ違い不可（渋滞誘発用）」= **≈280mm**）を **88mm 上回る**。inflation を語る以前に通路が全面 lethal になり、Nav2 は経路を生成できない（同 [`:130`](04-diorama-layout.md) の交差点 **≈367mm 角**も、円形前提では余裕がゼロになる）。
- 実車体は **231.40 × 284.40mm の矩形**（本 doc `:302` 車体寸法・公式 Product parameters 図）で、**幅 231.4mm は通路 280mm に収まる**。矛盾の出所は寸法そのものではなく、**非円形の車体を円で近似したこと**にある。→ OQ-3（[23 §8 項目 3](../architecture/23-perception-and-localization.md)）。

### 2. C-1 / C-2 の新旧対比（旧文言をここに履歴として保存）

| 行 | 旧文言（2026-08-05） | 新（2026-08-17・本改訂） |
|---|---|---|
| C-1 | 「外接円半径 **≈184mm**（対角 367mm ÷ 2）へ改訂」 | `FOOTPRINT_POLYGON`（矩形 231.4 × 284.4mm）と `CIRCUMSCRIBED_RADIUS`（外接円 ≈184mm）を**新定数として additive 追加**。`ROBOT_RADIUS`（`robot_dimensions.py:66` = `0.075`）は**値も意味も変えず**旧車体値のまま据え置き（保守的用途 C-3 等は `CIRCUMSCRIBED_RADIUS` を消費） |
| C-2 | 「C-1 と同値へ同期（単一ソース維持・R-42）」 | `robot_radius` へ同値を入れるのではなく **`footprint:` polygon へ移行**し、MPPI `consider_footprint: true` と**同一 PR で同時 flip** |

- **同時 flip が必須な理由**: [`nav2_params.yaml:171-178`](../../ws/src/warehouse_bringup/config/nav2_params.yaml) のコメントが #67 E2E ゲートの実測教訓を記録している — `consider_footprint: true` は costmap が footprint polygon を publish していることを要求し、`robot_radius` のままだと `controller_server` の configure が失敗して lifecycle bringup ごと落ちる。逆に polygon だけ入れて [`:179`](../../ws/src/warehouse_bringup/config/nav2_params.yaml) `consider_footprint: false` を残すと、MPPI CostCritic は外接円相当の判定を続けるため矩形にした意味が出ない。**片側だけの変更はどちらの向きでも壊れる。**
- **`ROBOT_RADIUS` を消さず・意味も変えない理由**: `ROBOT_RADIUS = 0.075` は旧 ~150mm 車体の**内接半径**であり、live consumer（`warehouse_traffic/virtual_scan_logic.py`・`traffic_manager.py` の `0.15m = 2*ROBOT_RADIUS` margin・`warehouse_sim/scenarios.py`・unit tests の `== 0.075` pin）が**内接前提**で読んでいる。名称を据え置いたまま「外接円 184mm」へ意味だけ倒すと、1 行も編集せずこれら全てを壊す（silent semantic break）。また 0.075 は M1 の内接 0.1157 すら下回り、そもそも外接円になり得ない。よって外接円は別名 `CIRCUMSCRIBED_RADIUS` の**新定数**とし、既存 consumer の M1 値への移行は 2 台復帰フェーズで**消費箇所ごとに明示的に**行う。単一ソース原則（R-42）は「同じ値を全箇所へ配る」ではなく「**用途ごとの定数を 1 箇所で定義する**」として維持する（polygon = `FOOTPRINT_POLYGON`、外接円 = `CIRCUMSCRIBED_RADIUS`、旧内接 = `ROBOT_RADIUS`）。

### 3. C-3（collision_monitor）は外接円ベースのままで妥当

- [`collision_monitor.yaml:64`](../../ws/src/warehouse_bringup/config/collision_monitor.yaml) `type: "circle"` / [`:68`](../../ws/src/warehouse_bringup/config/collision_monitor.yaml) `radius: 0.09`（現行は旧車体前提の暫定値）。ここは **L1 = 最後の物理的安全網**であり、「実車体を必ず包含する保守側の円」であることが正しい振る舞い（判断に迷う状況では止まる側へ倒れる）。
- L2 の costmap（C-1 / C-2）は「**通れる経路を作る**」ために正確な形状を要するのに対し、L1 は「**当たりそうなら止める**」ための過剰包含でよい。両者が同じ数字を共有する必然はない（layer で要求が違う＝`.claude/rules/layer-annotation.md`）。
- したがって C-3 は本改訂の対象外で、C-3 行の記述どおり「車体外接 + 余裕へ改訂」（実値は実機で再調整）のまま残る。

### 4. 実装は Slice 1（別 PR・CURRENT 未変更）

- 本追補と C-1 / C-2 行の書き換えは **docs のみ**。`FOOTPRINT_POLYGON` の追加は `warehouse_description` に触れるため、C 表直後の注記どおり **`contract` ラベル PR ＋ 依存トラック予告**が要る（`.claude/rules/parallel-workflow.md` §4）。
- Slice 1 の実装範囲・順序・検証（footprint polygon 反映 → `consider_footprint` flip → 通路 280mm を通過できることの sim 確認）は [Issue #519](https://github.com/lll-kkk-ryuya/miniature-warehouse-robotics/issues/519) と [23 末尾 F 系列](../architecture/23-perception-and-localization.md)を正とする。

---

## 【2026-08-18 追記】部屋スケール運用とハードウェアの関係（ADR-0009）

M1 単騎フェーズは**実際の部屋（room scale）**を走り、ジオラマは走行に使わない（[ADR-0009](../adr/0009-m1-room-scale-operation.md)）。**車体側の数値は環境に依らず不変**——M1 実寸 231.40 × 284.40mm（:302）・外接 ≈184mm・内接 115.7mm・LiDAR 上面 147.50mm はすべて robot-intrinsic である。したがって §「ROSMASTER M1 採用検討時の残課題」の C 系列と【2026-08-17 追補】（C-1 / C-2 の非円形 footprint 化）は**そのまま有効**。ただし 2 点が変わる:

- **C-3（collision_monitor）は「対象外の隣接スライス」から「部屋運用の前提条件」へ格上げ**。現行 `radius: 0.09` は M1 内接 0.1157 未満で**車体内部発火＝L1 が機能しない**。部屋では人が走行面上に立つため、**改訂完了が部屋で走らせる前提**となる（別 PR・安全レビュー必須＝[23 G-8](../architecture/23-perception-and-localization.md) / [mode-x-er/09 R-8-2](../mode-x-er/09-hand-raise-summon.md)）。上の §3「C-3 は外接円ベースのままで妥当」という判断自体は不変（保守側の外接円でよい）で、変わるのは**優先度と前提条件性**である。
- **HP60C のカメラ仰角**: 部屋では操作者が床に立つため肩を見上げる仰角がおよそ倍になり、**水平固定では肩が画角に入らない公算**（[mode-x-er/09 R-2①](../mode-x-er/09-hand-raise-summon.md)）。固定の上向き取付角は static TF のままで契約を破らないため候補に残る。取付角の確定は S2 実測と同一セッション（ADR-0009 Open）。

---

## 【2026-08-19 追記】M1 の速度性能・速度の出し方・AI 音声モジュール（agent-team 調査確定）

> 2026-08-19 の3レーン並列調査（一次情報 = 公式 `Rosmaster_Lib` V3.3.9 ソース・工場 STM32 ファーム Rosmaster V3.5.1 C ソース・520 モータ公式パラメータ表・M1 公式コース PDF）の確定事実。速度上限引き上げの決定は [ADR-0010](../adr/0010-raise-speed-cap-to-platform-max.md) が正本。**本節の未確定項目は実機到着済み（2026-08-18）につき順次実測で潰す。**

### V-1. 速度の真の上限（ファームウェア）

- ホスト側 `Rosmaster_Lib.set_car_motion(vx, vy, wz)` に**値域 clamp は存在しない**（docstring の「X3: ±1.0」等はドキュメントであって強制ではない）。`struct.pack('h', int(v*1000))` の **int16 境界 ±32.767 m/s** を超えると `struct.error` → **bare except がフレームごと黙殺**（前回速度がラッチされたまま＝fail-safe ではない）。
- **真の clamp は STM32 工場ファーム `Mecanum_Ctrl`（app_mecanum.c）の各輪 ±1000 mm/s**（car_type=`CAR_MECANUM` 0x01 のとき。`CAR_MECANUM_MAX` 0x02 は ±700）。この clamp は**4輪ミキシング後に各輪独立**で切るため、超過指令は**進行方向を歪める**（ベクトル比例縮小ではない）→ ホスト L0'（方向保存クランプ）維持の工学的根拠。
- **M1 専用の car_type 値は存在しない**（ライブラリ/ファームとも X3/X3PLUS/X1/R2 の4種のみ）。第三者 M1 実機プロジェクトは 0x01（X3）で駆動。`FUNC_MOTION(0x12)` の payload 先頭は car_type バイト（`& 0x80` は yaw-adjust フラグ）＝自前ドライバ実装時に取りこぼさない。**⚠️ ネット上の `set_speed_limit(0x16)` / `set_imu_adjust(0x17)` は推測 API で、この版のファームに実装は無い＝採用禁止。**
- `set_car_motion(0,0,0)` はファームの `Motion_Stop(STOP_BRAKE)` に落ちる＝**ゼロ送信は自由停止でなくブレーキ**。

### V-2. 520 モータと理論最高速度（`v = RPM/60 × π × D`）

| 減速比 / 無負荷RPM | 65mm 輪 | 80mm 輪 |
|---|---|---|
| 1:19 / 550 | 1.87 m/s | 2.30 m/s |
| 1:30 / 333 | **1.13 m/s** | 1.40 m/s |
| 1:56 / 205 | 0.70 m/s | 0.86 m/s |

検算: R2（1:19/65mm）→1.87 ≈ docstring 1.8 ✓ / X3 PLUS（1:56/80mm）→0.86 vs ファーム clamp 0.7 ✓ / X3（1:30/65mm）→1.13 vs clamp 1.0 ✓（式の妥当性の傍証）。**M1 の輪径・ギア比・car_type は非公開＝実機5分で確定**: ①モータラベルの RPM 印字 ②ホイール径ノギス実測 ③シリアル疎通後に `get_car_type()` 問い合わせ（**0x02 なら上限 0.7**・最優先確認）。電圧依存: 無負荷回転数は電圧比例＝3S 12.6→9.6V で **80%**（12V 定格 1.13 → 電池終盤 ~0.91 m/s 相当）。

### V-3. 速度の出し方（4案の裁定）

| 案 | 裁定 | 理由 |
|---|---|---|
| (a) `set_car_motion` に大きい値 | **採用** | エンコーダ閉ループ PID 維持・ファーム clamp が上限。L0' が方向保存で手前を絞る |
| (b) `set_motor` 直接 PWM | 却下（高速化用途） | car_type バイト無し＝逆運動学・速度 PID をバイパス。同一 PWM で左右差 ~12% 実測＝直進しない。odom 自前化。超低速の解であって上限の解ではない |
| (c) `set_pid_param` | 上限に無関係 | 追従性のみ。PID 出力は 2000 パルスにクリップ。`forever=True` は Flash 書込でパケットロス源（公式明記） |
| (d) ファーム clamp 自体の変更 | **不可能** | Yahboom 製バイナリのコンパイル時リテラル。ホストから変更手段なし |

### V-4. 高速化の既知の問題（S-SPEED 実測の観点）

| 問題 | 要点 |
|---|---|
| メカナムのスリップ | Yahboom 自身が振り子サス等をスリップ対策として設計説明。速度↑でスリップ↑＝odom 誤差↑ |
| odometry の質 | 公式スタックは**ファーム速度報告（スリップ込み）の積分**で odom を作り 4輪エンコーダ差分を使っていない → **自前 `m1_driver` はエンコーダ差分（`FUNC_REPORT_ENCODER 0x0D`）で組む** |
| 報告レート 25Hz 固定 | 1.0 m/s で1周期 40mm の未観測走行（0.3 の 3.3 倍）。L2 鮮度窓・L1 反応余裕の再導出が要る（[ADR-0010 Decision 5](../adr/0010-raise-speed-cap-to-platform-max.md)） |
| 電源サグ | 12V レールは非安定化スルー（§残課題）＝全力加速のサグが Orin ブラウンアウト直結。S-SPEED で電圧を必ず記録 |

### V-5. AI large model voice module（M1 同梱・開梱実物で確認 2026-08-19）

同梱実物: AI 音声モジュール基板 / スピーカー / Side elbow Type-C ケーブル 25cm（Standard/Superior 共通同梱。公式ページの "optional" 表記は誤解を招くが unboxing blog の同梱リストと一致）。**旧 $19 ASR-TTS モジュール（CI1302）とは別物。**

- **生 wav が録れる（最重要の確定）**: 公式 M1 コース PDF の実コード（`largemodel/asr.py`）が**ホスト側 PyAudio（=ALSA）でマイクストリームを直接開き wav に書き出している**＝通常の USB オーディオデバイスとして見える。`arecord` で録れる → **ER 音声直入力設計（[mode-x-er/04](../mode-x-er/04-er-input-modalities-and-stt.md)）にドライバ追加なしで接続可**。Yahboom の ASR 層（SenseVoiceSmall/Tongyi・zh/en のみ）は使わない＝日本語非対応は無関係。
- 基板は**オーディオ + シリアル（CH340・`/dev/ttyUSB*`）の複合デバイス**。シリアル側は中国語ウェイクワード（"你好小雅"）通知専用＝**本プロジェクトでは未使用**。スピーカーは OS 標準再生（`aplay`）で任意 wav 再生可＝到着発話（[mode-x-er/09 §11](../mode-x-er/09-hand-raise-summon.md)）に流用可。
- JetPack 6 / Ubuntu 22.04 で追加ドライバ不要の見込み（`snd-usb-audio` / `ch341` はカーネル標準。公式コースも Orin はネイティブ実行前提）。
- **要実機確認（6点）**: ①USB ディスクリプタ（VID:PID・UAC版）②オーディオ/シリアルが同一 Type-C 配下か（内部ハブ推定・未証明）③マイク ch 数と実サンプルレート（コースは 1ch/16kHz 固定）④**基板 NS/AEC 前処理が掛かった音が来るか**（静音録音のノイズフロアで切り分け・ER へ渡す音質に直結）⑤チップ型番 ⑥スピーカーコネクタ。確認コマンド: `lsusb` → `lsusb -t` → `arecord -l` → `arecord -D plughw:N,0 -f S16_LE -r 16000 -c 1 -d 5 /tmp/mic_test.wav` → `aplay /tmp/mic_test.wav`。

### V-6. 出典（一次情報）

- Rosmaster_Lib V3.3.9: <https://github.com/Roblibs/Rosmaster_Lib> / M1 実機リポ <https://github.com/Zia-kr/rosmaster_m1_dev> / 公式 zip 検証 <https://github.com/AIRclub-UdeSA/physical_rosmaster>
- 工場 STM32 ファーム V3.5.1 ソース: <https://github.com/Inouye165/Yahboom-Robot-Expansion-Board-V3.0>（app_mecanum.c / app_motion.h / protocol.h）
- 520 モータ公式表: <https://www.yahboom.net/public/upload/upload-html/1742005967/0.520%20motor%20introduction%20and%20usage.html>
- M1 音声コース PDF（asr.py 実コード転載）: <https://github.com/YahboomTechnology/ROSMASTER-M1>（`18.AI Large Model Basic Course` 配下）/ unboxing blog <https://category.yahboom.net/blogs/news/unboxing-and-reviewing-rosmaster-m1>
- `set_motor` 実機 probe: <https://github.com/kirra-systems/kirra-runtime-sdk> / M1 PWM ブリッジ実装 <https://github.com/liuwenjing613-maker/qqqqqq>（参照日: すべて 2026-08-19）

---

【2026-08-21 追記】紙説明書の転記 doc（shared/11）と開梱で確定した補足

- 同梱紙説明書（Shipping List・組立手順・配線・音声フロー）の転記と開梱記録は [11-m1-assembly-manual.md](11-m1-assembly-manual.md)（転記 doc・設計判断なし）。:393 の着荷記録と 1:1 同期。
- Shipping List の構成上、**「Jetson Orin Nano board」オプション列の同梱物（SSD・パッチアンテナ・DC5.5×2.5⇔XH2.54 ケーブル・Accessory package ⑥）は Superior-without には付かない**。帰結: ① :447 の「キット余りを先に確認」する M3 ネジは **Orin 用ネジ袋（⑥）が存在しない前提**で確認する（標準袋①/⑤の余りの範囲） ② :321 の縮退案ケーブル（XH2.54⇔DC5.5×2.5）は同梱されず単品購入時のみ、の再確認。

---

## 【2026-08-23 追記】給電・フラッシュ経路の実物確認（AC アダプタ銘板 / 昇圧 DC-DC 着荷 / microSD 書込経路）

> 一次情報 = 現物の銘板写真（`docs/assets/m1-parts/` 配下・public 公開判断前のため未コミット）と Amazon.co.jp 注文確認メール。**金額・注文番号は [01-budget-and-procurement.md](01-budget-and-procurement.md) の実購入台帳が正本**（本節は複製しない）。本節は設計を変更しない——`:320` の昇圧要求も `:433` のヒューズ算定も `:451` の手順も**不変**。

### P-1. Orin 付属 AC アダプタの実銘板（`:307` の電流値を一次確認）

| 項目 | 実読値 |
|---|---|
| メーカー / 型番 | LITEON `PA-1450-26`（POWER ADAPTER） |
| 入力 | 100-240V ~ 1.2A 50-60Hz（ユニバーサル入力） |
| **出力** | **19V ⎓ 2.37A ＝ 45.0W** |
| 極性 | センタープラス（`:307` と一致） |
| 識別 | barcode `NVIDIA45W2601002149` / REV:01 |
| 認証（判読分） | UL(c-us) / CE / UKCA / EAC / CCC / RCM / BIS IS 13252 / KC / GS / NOM / RoHS |

**帰結**: `:320` が昇圧 DC-DC に課した **連続 ≥45W** は純正アダプタ銘板の 45.0W と一致する＝要求値の置き方が事後的にも妥当だったと確認できた。一方 `:433` のヒューズ算定が使う **19V×3.5A=66.5W** は `:307` の**コネクタ規格上限**であって実消費ではない——両者は矛盾せず、**10A ヒューズは保守側の設定として維持する（変更しない）**。

`# TODO(未確認)` 銘板の認証マーク群に **PSE（◇PS / ⬡PS）は判読できていない**。同梱 AC コードは 2 本あり、**平刃2本（Type A / NEMA 1-15P）＝日本のコンセントで使用可**・**丸ピン（Europlug Type C 系）＝日本では使用不可**。アダプタ本体が 100-240V ユニバーサル入力のため Type A 側を使えば電圧上の問題は無い。PSE 表示のある国内向けコードへ差し替える場合は、先に**アダプタ側 AC インレット形状（メガネ型 IEC C7 / ミッキー型 IEC C5）**を実物で確認する。

> **Phase A（机上ブート）の給電はこの純正アダプタのみを使う。** 昇圧ハーネスは `:451` の①〜④が未通過の間は Orin に接続しない。

### P-2. 昇圧 DC-DC の着荷判定（`:442` の状態更新）

履歴: **8/19 注文 → 8/20 発送 → お届け予定 8/21–22 → 2026-08-23 にオペレーターが現物を確認**。**着荷済で確定**（`:442` を更新済）。

**帰結: `:442` の表から「発注済で未着」がゼロになった。** `:422-428` の配線設計が要求する部材——T型コネクタ・WFR-3・ヒューズホルダ＋10A ヒューズ・DC プラグケーブル・テスター（いずれも 8/20–21 着荷済＝[01](01-budget-and-procurement.md) §B）と本項の昇圧 DC-DC——が**全て揃った**。すなわち `:451` の①〜④（Orin 非接続で 19.0V に追い込む）に**着手できる状態になった**。

ただし**着手できることと、順序を省けることは別**である。`:451` の順序（初通電前の導通・＋/−分離の確認 → ① 出力測定 → ② 19.0V → ③ 再確認しポット固定 → ④ 極性確認 → Orin 接続）は**一段も飛ばさない**。P-4 の通り本モジュールは**短絡保護も逆接保護も持たない**ため、この順序を守らなければ壊れるのは Orin だけでなくモジュール自身でもある。

`# TODO(期限あり)` メーカー保証は**初期不良のみ・到着後 1 週間**（P-4）。着荷 8/21–22 起算で期限は **8/28–29 頃**。`:451` ①（Orin 非接続での出力測定）が初期不良判定を兼ねるため、**この期間内に実施する**。

### P-3. microSD 書き込み経路の確定（`:411` の「SD カードリーダーが前提」を充足）

`:411` は microSD 経路（Mac のみの環境では唯一のフラッシュ手段）の前提として **microSD と SD カードリーダー**を挙げる。開発機 MacBook Pro（`Mac16,1`）は **内蔵 SDXC カードリーダーを持つ**（`system_profiler SPCardReaderDataType` で実確認 2026-08-23）が、**フルサイズ SD 専用**であり microSD は物理的に挿さらない。

**採用: USB-C ハブ（UGREEN Revodok Pro 9-in-1）の microSD/TF スロットを書き込み経路とする**（2026-08-23 発注・8/24 着予定。金額・注文番号は [01](01-budget-and-procurement.md) §F）。パッシブ変換アダプタ（¥230–300）は購入していない。

- **選定理由（3 用途を 1 台で満たす）**: (a) 本フラッシュ経路 (b) 実機プローブで Mac から拡張ボード（Micro-USB→**USB-A**）へ繋ぐ経路——**Mac 本体に USB-A ポートが無い** (c) JetPack セットアップ時の**有線 LAN**。
- `# TODO(実施時)` **書き込み後の verify を必ず有効にする**。内蔵リーダーと異なりハブ側はサードパーティ USB ブリッジを経由するため、起動イメージは読み返し照合まで行う（Raspberry Pi Imager / balenaEtcher は既定で検証する）。verify が通らない場合は**パッシブ変換アダプタへ退避**する（Mac の内蔵リーダー経路＝Apple 純正ドライバに戻せる）。
- **共通の罠**: パッシブ変換アダプタを使う場合、側面の**書き込み禁止スライダー**が LOCK 側だと Mac から書き込めない。ハブの microSD スロット直挿しならこの罠は無い。

### P-4. 昇圧 DC-DC の現物仕様（メーカー商品説明の一次読み取り・`:453` の「不明」を一部 close）

購入品を ASIN `B01N3L2NY2`（**NFJ / (株)ノースフラットジャパン・メーカー型番 `O242`**・¥890）と特定（注文 `503-8285953-7921408` の商品名・価格・出品者が一致）。メーカー商品説明（参照日 2026-08-23）の実読値:

| 項目 | 値 | 本プロジェクトでの含意 |
|---|---|---|
| 出力電力 | 自然放熱 **100W** / 強制空冷 150W | `:442` の記載と一致。必要な 45W に対し自然放熱で足りる |
| 入力電流 | MAX **16A** | 最悪ケース 7.5A（`:433`）は定格内 |
| 出力電流 | MAX **10A** | 19V×2.37A ≈ 2.4A（末尾追記 P-1）は定格内 |
| **出力リプル** | **1%（最大）** | `:453` が「リプル不明」としていた点にメーカー公称値が付いた（19V で ±0.19V 相当）。**実測での確認は引き続き必要** |
| 最大変換効率 | 94% | `:433` の効率 90% 仮定は保守側＝**ヒューズ算定を変更しない** |
| **寸法 / 重量** | **45×65×28mm / 約65g**（基板 37×65mm・**ネジ穴間隔 26×58mm**） | **車載位置の設計に必要な実寸法**。ネジ穴があるので固定可能 |
| 動作温度 | -40〜+85℃ | — |

**⚠️ 保護回路（メーカー明記・設計判断に直結）**

- **短絡保護回路なし**。メーカー自身が「必要に応じて**入力側にヒューズ**や保護回路をご用意ください」と明記＝`:433` の 10A ヒューズ（Orin レグ）は**メーカー要求とも整合**する。
- **入力逆接続保護回路なし**。「入出力端子＋と−でショートもしくは逆接続した場合は破損する恐れ」＝`:451` が「逆接も Orin を破壊」と書く手前で、**昇圧モジュール自身が先に壊れる**。`:451` の「初通電前に導通と＋/−分離をテスターで確認」の根拠がもう一段強くなる（**手順は変更しない**）。

**⚠️ 電圧調整ボリュームの罠（`:451` の①〜③を実施する前に必読）**

- **初期値は一定でない**とメーカーが明記＝`:451` の「出荷時の設定値は不明（35V の可能性がある）」を裏付ける。
- **調整ネジは「無限回転」タイプ**。少し回して電圧が変わらなくても故障ではなく、**何回転も回す必要がある**（初端/終端を越えて回っている個体がある）。
- **時計回りに回しすぎると内部でネジが外れ、電圧が変化しなくなる**。その状態では回すたびに「カチッ」と音が鳴る。復帰は**反時計回りにしっかり押し込みながら 5〜10 回転**。
  → `# TODO(実施時)` 「回しても電圧が変わらない」を**初期不良と誤判定しない／さらに時計回りに回し続けない**こと。

**⚠️ 入力電圧の変動が出力に乗る可能性（`:336-345` の実測 ①〜④ の重要度が上がる）**

メーカーは「**入力電圧が可変するような電源を使う場合、出力電圧も入力電圧に応じて可変する**ので、出力電圧の設定は必ずテスターで確認してから使用すること」と明記する。本プロジェクトの入力は 3S バッテリー＝**12.6V→9.6V まで変動する**（`:302` 系）。

→ したがって `:451` の②で 19.0V に合わせても、**バッテリー消耗時に出力が変わらない保証はない**。`:336-345` の実測①〜④で**バッテリー電圧を振ったときの出力電圧**を必ず記録する。`# TODO(要検討)` 出力が上振れする挙動なら Orin 上限 20V に触れうるため、**設定は満充電（高い入力）側で行い、低入力側での挙動を実測で確認する**——この設定タイミングの規定は本追記時点では**未裁定**。

`# TODO(期限あり)` **メーカー保証は初期不良のみ・商品到着後 1 週間**。着荷が 8/21–22 なら期限は **8/28–29 頃**。`:451` の①（Orin 非接続で出力を測る）は初期不良判定を兼ねるため、**この期間内に実施する**。

> 出典: Amazon.co.jp 商品ページ `B01N3L2NY2`（NFJ・メーカー型番 `O242`）の商品説明・製品仕様（参照日 2026-08-23）。`# TODO(到着後)` メーカーが「入荷ロットにより外観・デザイン等が異なる場合がある」と明記しているため、現物の端子配列とボリューム位置は実物で確認する。

## 【2026-08-23 追記 2】Orin マウント方針の変更（P-5）—「3D プリント前提」を撤回し公式直付けを第一候補へ

### P-5. Yahboom 公式「M1 × Jetson ORIN NANO 組立動画」の発見と一次検証

**一次情報**（参照日 2026-08-23）: Yahboom 公式 build ページ <https://www.yahboom.net/build/id/16900/cid/427> の「0. Assembly video」に、SBC 別の M1 組立動画が **4 本**存在する — Jetson NANO 4GB (`b8Dx1Mpsxmk`) / **Jetson ORIN NANO (`QdqYvkr8_Ag`・14:06・約 8 か月前公開)** / RDK X5 (`QkSBZeKa9Xc`) / Raspberry Pi (`YzhgxadNNls`)。紙説明書 p06 で撮影範囲外だった Orin board installation の手順 2〜5（`11-m1-assembly-manual.md` `:69`）は、この動画で全編視聴できる。

**動画から読み取れた事実**（step 番号はオーバーレイ表記どおり）:

1. **step 8「Install Jetson Orin Nano board」**の部品リストは `Jetson Orin Nano board *1`・`M2.5x5mm round head screw *4`・`Patch antenna acrylic board *1`・`M2.5x16+6mm single-pass copper pillar`（オーバーレイは ×2 に見えるが紙説明書 p06 は ×3。`# TODO(現物確認)`）・`Patch antenna`。**専用取付板・3D プリント部品・中間プレートは一切登場しない**——ボードは車体側に立てた銅柱へ **M2.5×5mm ネジ ×4 で直接ネジ止め**される。アクリル板は名称どおりパッチアンテナ用であり Orin の下敷きではない。
2. 搭載ボードは**ファン付き・裏面に M.2 スロット 2 連＋ラベル**の外観で、**純正 Jetson Orin Nano Developer Kit と外観一致**（製品ページの選択肢名も「Orin NANO SUPER-8GB」= NVIDIA の Super Dev Kit 呼称。ただし**断定は現物合わせ**）。
3. **step 9 = T-MINI PLUS LiDAR**（`T-MINI PLUS LiDAR adapter board *1`・M3×6mm ×3・M2×10+4mm 銅柱 ×2）、**step 12 = top cover 取り付けで完成**。つまり**公式構成では Orin を搭載したまま top cover が閉まる**＝`11-m1-assembly-manual.md` `:229` の 🔴 高さ干渉（34.8mm 厚）は**公式配置なら不発生の傍証**（最終確定は実物）。

**NVIDIA 側の一次資料**（`:416` の補強）:

- **3D CAD STEP モデルは公式提供あり**: 公式フォーラムでモデレータ cyato が「**Jetson Orin Nano Developer Kit 3D CAD STEP model** が Jetson Download Center にある」と回答（2025-01-15・thread `320208`）。穴位置の PCB 設計ファイル（Carrier Board Reference Design Files・A04）は `:416` 記載どおり（thread `339279`）。
- Carrier Board Specification の PDF は **NVIDIA Developer ログイン無しで直接ダウンロード可能**を実測確認（2026-08-23: `developer.nvidia.com` → 302 → トークン付き CDN → 200 `application/pdf`）。STEP モデル・Reference Design Files 本体の DL に無料 Developer アカウントが要るかは**未確認**。

**設計判断の更新**（`:322` / `:414-420` / `01:149` / `11-m1-assembly-manual.md` Q-6 と 1:1 同期）:

1. **第一候補 = 公式手順どおり付属部品で直付け**。必要部材（`M2.5*22+6mm` 銅柱・`M2.5*5mm` ネジ ×4）は B01 主板配件包で**現物あり**（`01:149`）。
2. **3D プリントマウント（`:418` MakerWorld 案）は fallback に格下げ**——現物合わせで穴が合わない場合のみ起こす。その場合の穴位置一次ソースは公式 STEP モデル（上記）が Reference Design Files より扱いやすい。試し刷りは必須タスクから外し**任意の fallback 準備**とする。
3. 出品者への「純正取付板の単品販売」問い合わせ（`:322`）は**優先度低下**——公式手順に Orin 用の別売取付板は登場せず、直付けが正規の取り付け方である公算が大きい。
4. `# TODO(Phase B 現物合わせ)` 組立時に確定する 3 点: ①車体側の柱位置と手元 Dev Kit の 4 穴が実際に合うか ②柱が HUB 拡張ボード上の 2 階建てか前デッキ直かの座席位置（動画の画角では断定せず・紙説明書 p06 と突き合わせ） ③パッチアンテナ柱の本数（×2 vs ×3）。

> 出典: Yahboom build ページ＋YouTube `QdqYvkr8_Ag`（本文記載の step 8/9/12 フレームを 2026-08-23 に視聴確認）・NVIDIA Developer Forums thread `320208` / `339279`・`developer.nvidia.com` ダウンロード応答ヘッダ実測（同日）。

## 【2026-08-23 追記 3】Yahboom 公式技術資料（回路図・プロトコル V2・全説明書）の一次読み取り（P-6）

> 入手物と保存先: `docs/assets/m1-vendor/`（**意図的に untracked**・README に sha256 と内訳）。拡張ボード回路図 `ERF01v3.0-en.pdf`・公式プロトコル `ROSMASTER_control_board_protocol_V2.xlsx`（2025-05-12）・モータドライバ `AM2857` データシート・IMU データシート4点・**英語版デジタル説明書 全25ページ**（紙12頁の上位互換）。

### P-6a. 拡張ボード YB-ERF01-V3.0 回路図の要点（給電設計の回路図レベル裏取り）

1. **レギュレートされた 12V レールは存在しない**——「DC12V 出力」は電池生電圧（VM ネット）のスルー。`:320` のバッテリー直タップ+昇圧 19V 設計の根拠（出品者回答 4A/6A・保護なし）が**回路図レベルで確定**した。
2. 5V レールは MP2225 バックで**実出力 5.2V**（回路図に注記あり）。ほかにサーボ用 6.8V（MP2225）・3.3V（AMS1117）。
3. モータは **AMtek AM2857（1ch H ブリッジ・30V・連続 4.0A/ピーク 6.5A・ストール保護/過熱保護/OCP 6.5A 内蔵）×4** ＋ 各チャネル nSMD150 ポリヒューズ。MCU は APM32E103RET6/STM32F103RCT6（互換実装）。
4. SBC との通信は **CH340N（micro-USB・シリアル）**。ほかに CAN トランシーバ SN65HVD230 実装（公式 CAN プロトコルあり・1000kbps）・SBUS 入力・アクティブブザー（5V）・OLED 用 I2C 4pin ヘッダ。
5. IMU は **V3.0 実装 = ICM-20948**（MPU9250 は代替実装の注記）。`# TODO(通電後)` 自動レポートの function word（0x0E なら ICM-20948 / 0x0B なら MPU9250）で実機の HW 世代を確定する。

### P-6b. 公式プロトコル V2 の確定事項（`§プロトコル` の逆算記述を公式仕様で検証）

- **一致を確認**: 送信ヘッダ `0xFF 0xFC`／受信 `0xFF 0xFB`・checksum（Length〜check 直前の和 mod 256）・リトルエンディアン・`0x10` モータ PWM(±100)・`0x12` motion（car_type + X±1000, Y±1000, **Z±5000**・×1000 スケール）・`0x0D` エンコーダ int32×4・**40ms 自動レポート**（4 パケット×10ms）・`0x0A` に電池電圧（×10）・PID `0x13/0x14`（×1000）・FW 版取得 `0x50→0x51`。
- **car_type**: `0x15` set car type の一覧は **X3=1, X3PLUS=2, X1=4, R2=5 のみで M1 の値は未記載**（シートは 2025-05-12 版）。さらに **car type を読み出すコマンドは 0x50 カタログに存在しない** → 「board から car_type を読む」実装は公式プロトコル上あり得ず、`get_car_type()` は lib 内部保持値と解すべき（`:546`/`:551` の矛盾はこの線で決着させる。`# TODO(実機)` M1 の car_type 値は Yahboom M1 ソースコード入手または実機 FW 応答で確定）。
- **公式プロトコルに watchdog / コマンドタイムアウトの機能は存在しない**（自動レポートの ON/OFF `0x01` はあるが受信途絶時の自動停止は未記載）→ L0'（ホスト送信直前クランプ）と G-g（MCU watchdog・PHASE-1-GATE）の設計上の重みが増した。**シリアル切断時に車輪が止まる保証はプロトコル仕様からは得られない**。
- 既存記述の「**per-wheel クランプ ±1000/±700(car_type 依存)**」は公式シートに**現れない**（公式にあるのは 0x12 の軸速度 field range ±1000/±5000 のみ）→ 出所は STM32 FW/lib 内部。引用時は「公式 field range」と「FW 内部クランプ」を区別する。
- 工場リセット: `0xA0` または **KEY1 長押し 10 秒**（PID 等を Flash 保存 `0x5F` で永続化している場合の復旧手段）。

### P-6c. デジタル説明書（全25頁）による Orin 組立手順の完全確定（P-5 の残 TODO を更新）

**Orin Nano board installation step は全 6 手順**（M1-9〜M1-11。`11-m1-assembly-manual.md` Q-7 と 1:1 同期）:

1. HUB 拡張ボード（`M2.5*22+6mm` 銅柱⑥ + `M2.5*5mm` ネジ①）
2. **Remove Jetson Orin Nano base**——純正 Dev Kit を**同梱の黒い台座から取り外す**。直付けできる種明かし＝**キャリアボード側の台座固定 4 穴を車体マウントに流用**する（P-5 の TODO ①のメカニズム判明。物理フィットの最終確認は残る）
3. **SSD とパッチアンテナを取り付け**（「コアモジュールは 45° で挿す」注記あり）——**KIOXIA 1TB はこの段階（車体搭載前）で装着**する。`# TODO(現物確認)` 手持ち Dev Kit の純正 WiFi アンテナが台座側に固定されている場合、台座撤去でアンテナの置き場が失われる（Yahboom のパッチアンテナ袋は非同梱）——現物で確認し、必要なら固定方法を決める
4. Orin ボードを柱に **M2.5×5mm ×4 で直ネジ止め**
5. パッチアンテナ用アクリル板（`M2.5*16+6mm` 柱）
6. **配線図**: OLED ケーブル→Orin 40pin・冷却ファン・上エルボ USB（HUB ボードうplink）・**XH2.54 ケーブル→Orin DC ジャック（公式給電＝無保護 VM レール。本プロジェクトはここだけ `:320` の昇圧ハーネスに置換）**・横エルボ micro-USB（CH340 シリアル）

**P-5 の TODO ② close**: 座席は **HUB ボード上の2階建て**（M1-11 下段図で長柱4本の上に Orin・下に HUB ボードを確認）。一般手順側の確定事項: **LiDAR の矢印は車体前方向き**・ワイヤレス受信機/U ディスクは **SBC の USB 直挿し**・LiDAR / PTZ / 深度カメラ / 音声モジュールは HUB ボードのポートへ。

> 出典: `docs/assets/m1-vendor/`（README に sha256）。回路図 = `ERF01v3.0-en.pdf`（1枚・2025/5/9）、プロトコル = `ROSMASTER_control_board_protocol_V2.xlsx` Serial/CAN 両シート、説明書 = `instruction-manual-en/ROSMASTER M1-9..12.jpg`（いずれも 2026-08-23 実読）。
