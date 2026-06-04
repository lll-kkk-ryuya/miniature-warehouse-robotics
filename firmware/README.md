# firmware — ESP32 micro-ROS（Yahboom MicroROS Car ×2）

ESP32 車載ファームウェア（micro-ROS / FreeRTOS、PlatformIO）。**現状は雛形・実機未検証**（doc17 Step 1：実機到着後に実値確定）。設計: `docs/shared/02-hardware-design.md`, `docs/architecture/12-infrastructure-common.md`。

## 構成
```
firmware/
├── platformio.ini        # ESP32 / micro-ROS / build_flags(BOT_ID, 速度上限)
├── include/config.h      # ピン・MAX_LINEAR_VELOCITY=0.3 ・通信(秘密は config_secret.h)
├── src/main.cpp          # ノード骨格 + 速度クランプ(Layer 0) + 各ドライバ TODO
├── CLAUDE.md             # 担当コンテキスト
└── README.md             # 本ファイル
```

## ビルド / 書込（実機時）
```bash
cd firmware
pio run                 # bot1（platformio.ini の BOT_ID=1）
pio run -t upload
pio device monitor
```
bot2 は `BOT_ID=2` でビルド（namespace `/bot2`）。

## 安全（最重要 / safety.md Layer 0）
- 速度上限 **0.3 m/s を MCU 内で強制**（`clampLinear`）。ROS 側 `/cmd_vel` の値に関わらずクランプ。
- 近接停止は MCU 内（OS/ROS 非依存、最終防衛線）。

## トピック（doc03 契約）
- Pub: `/<ns>/odom`(`nav_msgs/Odometry`), `/<ns>/scan`(`sensor_msgs/LaserScan`, ORBBEC MS200), `/<ns>/battery`(`sensor_msgs/BatteryState`)
- Sub: `/<ns>/cmd_vel`(`geometry_msgs/Twist`)
- transport: WiFi UDP → micro-ROS Agent（Jetson）

## micro-ROS Agent 多重接続（R-37）— **Phase 1 必須要件**
2台を**1つの `micro_ros_agent udp4 --port 8888`**（凍結 launch）に接続する。先行検証（[`spike/`](spike/) =
ESP32 無しの host 再現）で確定した**最重要要件**:
- **両機は distinct な XRCE `client_key` を持つこと**。Agent はクライアントを **UDP ポートではなく `client_key`
  （session 識別子）で識別**し、**同一キーだと session を奪い合い pub/sub の片方向が落ちる**（= R-37 を host で強制再現）。
- distinct キーなら**単一 Agent で2台双方向 OK**（spike fixA 実証。[spike/RESULT.md](spike/RESULT.md)）。
- micro_ros_arduino 既定キーが両機で同一/弱 RNG だと踏むため、**Phase 1 で `rmw_uros_options_set_client_key()`
  に BOT_ID/MAC 由来の値を明示設定**し、起動時にキー差を確認する。# TODO(Phase 1): set distinct client_key per board。
- 不可なら **USB 有線（serial）** へフォールバック（2 Agent/別ポートは不要・別問題＝降格）。
- 根拠/正本: [docs/shared/07-research-notes.md:242](../docs/shared/07-research-notes.md)（R-37）, [spike/RESULT.md](spike/RESULT.md), Issue #116。

## 秘密情報
`config_secret.h`（WiFi SSID/PASS・Agent IP）は**コミット禁止**。`config.h` がプレースホルダを提供。
