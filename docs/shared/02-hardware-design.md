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
| 6軸IMU | （基板内蔵） | 姿勢推定・旋回検出 | `/bot{n}/imu`（※要確認） |
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

- ROS 2 Jazzy のホスト（司令塔）
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
