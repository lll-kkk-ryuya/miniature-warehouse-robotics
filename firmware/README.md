# firmware — ESP32 micro-ROS（Yahboom MicroROS Car ×2）

ESP32 車載ファームウェア（micro-ROS / FreeRTOS、PlatformIO）。**現状は雛形・実機未検証**（doc17 Step 1：実機到着後に実値確定）。設計: `docs/shared/02-hardware-design.md`, `docs/architecture/12-infrastructure-common.md`。

## 構成
```
firmware/
├── platformio.ini          # ESP32 env + [env:native] host テスト env (build_flags: BOT_ID, 速度上限)
├── include/config.h        # ピン・MAX_LINEAR_VELOCITY=0.3 ・通信(秘密は config_secret.h)
├── include/safety_clamp.h  # Layer 0 速度クランプ純ロジック(Arduino 非依存・host unit-tested)
├── include/kinematics.h    # skid-steer mix + dead-reckon odom 純ロジック(Arduino 非依存・host unit-tested)
├── src/main.cpp            # ノード骨格 + 速度クランプ(Layer 0) + モータ/各 publisher/MS200 stub
├── PHASE1_CHECKLIST.md     # 実機到着時の配線ゲート(stub→実値・実ドライバ・file:line 根拠付)
├── test/test_clamp/        # クランプ R-26 unit test (Unity・host 実行)
├── test/test_kinematics/   # キネマティクス unit test (Unity・host 実行)
├── test/run_host_test.sh   # クランプ unit を pio 不在でも g++ で(同一テスト源)
├── test/run_kinematics_test.sh # キネマティクス unit を g++ で(同梱 Unity shim)
├── test/run_host_compile.sh# skeleton(main.cpp)を Arduino shim で host 構文確認
├── test/support/           # unity_shim(クランプ/キネマ用) / arduino_shim(skeleton compile 用)
├── CLAUDE.md               # 担当コンテキスト
└── README.md               # 本ファイル
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
- 速度上限 **0.3 m/s を MCU 内で強制**（`clampLinear`、純ロジックは `include/safety_clamp.h`）。ROS 側 `/cmd_vel` の値に関わらずクランプ。
- 近接停止は MCU 内（OS/ROS 非依存、最終防衛線）。

## テスト（Layer-0 クランプ R-26・ESP32 不要）
クランプは安全機構ゆえ **unit テスト必須**（R-26 / `docs/architecture/16` §11・`docs/architecture/20-dev-quality-and-testing.md:75`）。同一テスト源（`test/test_clamp/test_clamp.cpp`）を host で2通り実行できる:
```bash
cd firmware
pio test -e native        # PlatformIO + Unity（pio がある場合）
# pio が無い環境では g++/clang フォールバック（同梱 Unity shim）:
bash test/run_host_test.sh
```
- 固定する契約: 境界（>上限→上限 / <−上限→−上限 / 素通し / 上限ちょうど / 0）＋ **非有限（NaN/±Inf）→ stop**（fail-safe・`warehouse_interfaces/safety.py:31-32` と一致）＋ `MAX_LINEAR_VELOCITY == 0.3 m/s`（safety.md / `docs/architecture/12-infrastructure-common.md:77`）＋ `MAX_*_VELOCITY > 0`（負上限=runaway ガード）。
- `MAX_ANGULAR_VELOCITY=2.0`（`include/config.h:10`）は **Phase 1 実測 placeholder**＝テストは境界動作のみ固定。
- CI 組込み（`.github/**`）は governance 所有（人間配線）。R-26 クランプ unit（`run_host_test.sh`）は CI job **`firmware-safety` でゲート化済**（#244・origin/main）。本 PR 追加の `run_kinematics_test.sh` / `run_host_compile.sh` は未配線＝同 job への追加は governance follow-up（`.github/**` は本レーン境界外）。

### キネマティクス host unit（ESP32 不要）
skid-steer mix（`mixSkidSteer`）と dead-reckon odom（`integrateOdom`）の純ロジック（`include/kinematics.h`・Arduino 非依存・差動駆動の標準モデル）を host で検証する。hardware 値（`TRACK_WIDTH`・PWM duty 曲線・encoder scale・`dt`）は**引数**で渡し、ヘッダに定数を発明しない:
```bash
cd firmware
bash test/run_kinematics_test.sh   # 9/9 で緑（直進/その場旋回/dead-reckon 積分）
```
安全 R-26 クランプ gate（`run_host_test.sh`）とは**別ゲート**（クランプ gate は不変に保つ）。

### skeleton host コンパイル（ESP32 不要）
micro-ROS / ドライバ stub を含む `src/main.cpp` を、最小 Arduino shim（`test/support/arduino_shim`・test 専用）で host 構文確認する。micro-ROS 呼出は Phase 1 の TODO コメントなので ROS ヘッダ不要:
```bash
cd firmware
bash test/run_host_compile.sh   # main.cpp を -c で型検査（PASS で緑）
```
実ドライバ・実 rclc publish・モータ PWM・UART parse は Phase 1（[`PHASE1_CHECKLIST.md`](PHASE1_CHECKLIST.md)）。

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
