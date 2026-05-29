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

## 秘密情報
`config_secret.h`（WiFi SSID/PASS・Agent IP）は**コミット禁止**。`config.h` がプレースホルダを提供。
