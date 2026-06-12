# firmware Phase 1 配線チェックリスト（実機到着ゲート）

> ESP32 + Yahboom 車体が届いたとき（doc06 Phase 1・約2週間後）に、本リポジトリの
> **stub/skeleton を実値・実ドライバに置き換える**ための配線ゲート。各項目は実 file:line
> 由来（記憶でなく実 Read 裏取り）。**現状はすべて `firmware/src/main.cpp` の stub /
> TODO(Phase 1)** で、host コンパイル（`test/run_host_compile.sh`）と Layer-0 クランプ
> R-26 unit（`test/run_host_test.sh`）だけが緑。実機要素は本リストで開く。
>
> 設計正本: [doc02 hardware](../docs/shared/02-hardware-design.md) /
> [doc03 topics](../docs/architecture/03-software-architecture.md) /
> [doc06 Phase 1](../docs/architecture/06-implementation-phases.md) /
> [doc12 Layer 0](../docs/architecture/12-infrastructure-common.md) /
> [doc07 research/risks](../docs/shared/07-research-notes.md) / [CLAUDE.md](CLAUDE.md)。
>
> ⚠️ **根拠の出所に注意**: `doc06:124-134`（Phase 1 タスク表）に **無い**項目がある。
> R-37 distinct client_key と近接 reflex は **doc06 ではなく** `firmware/CLAUDE.md:12` /
> `doc07:242` / `doc12:76,78` 由来。各行に正しい一次ソースを付す。

---

## A. 通信・micro-ROS 配線

- [ ] **micro-ROS Agent を Jetson にインストール** — `doc06:126`（「micro-ROS Agent を Jetson にインストール」）。
- [ ] **WiFi UDP 疎通**（ESP32 ↔ Jetson）— `doc06:127`（「WiFi UDP でロボット ↔ Jetson の通信確認」）。transport endpoint（SSID/PASS/`AGENT_IP`/`AGENT_PORT`）は `config_secret.h`（`config.h:25-26`・.gitignore、**コミット禁止** `.claude/rules/safety.md`）。`main.cpp:setup()` の `WiFi connect → micro-ROS UDP transport` TODO を実装。
- [ ] **R-37: 両機に distinct な XRCE `client_key` を付与** — ⚠️**doc06 外**＝`firmware/CLAUDE.md:12` / `doc07:242`（「第一対策＝両ESP32に distinct `client_key`（`rmw_uros_options_set_client_key()`、BOT_ID/MAC由来）→ 単一Agent(:8888)で2台双方向OK」）/ [`spike/RESULT.md`](spike/RESULT.md)。同一/弱RNG キーは session 衝突で pub/sub 片方向喪失（host spike で再現済）。`main.cpp:setup()` の client_key TODO を実装し、起動時にキー差を確認。
- [ ] **Agent 切断時の再接続**（rclc_support 再初期化）— `firmware/CLAUDE.md:11`（「再接続」）+ 既存 `main.cpp:setup()` reconnect TODO。WiFi 遅延が大きければ **USB 有線へフォールバック**（`doc06:144-145` リスク節 / `doc07:242` #21 Case5）。

## B. cmd_vel（Sub）・モータ — Layer 0

- [ ] **`/<ns>/cmd_vel` subscriber を `onCmdVel` にバインド** — `doc03:88`（`/bot{n}/cmd_vel` = `geometry_msgs/Twist`）+ `doc06:128`（「`/cmd_vel` でロボットを遠隔操作（teleop）」）。`main.cpp:setup()` の `sub(cmd_vel → onCmdVel)` TODO を実装。
- [ ] **`setMotorVelocity(v,w)` を実 PWM へ**（4輪スキッドステア・左右2ch）— `doc02:14`（「310エンコーダモーター × 4（4輪スキッドステアリング、左右2チャンネル制御）」）。`TRACK_WIDTH` と速度→duty 曲線を**実機実測で `config.h` に確定**（現状 stub の差動ミックスはコメント契約のみ）。**クランプ済み (v,w) 前提を崩さない**（`main.cpp:onCmdVel` → `clampLinear/clampAngular` → `setMotorVelocity`）。
- [ ] **Layer-0 速度クランプの回帰確認**（実 build でも保持）— `safety_clamp.h`（凍結・触らない）+ `doc12:77,112`（MCU 内 0.3 m/s 上限＝最終防衛線）。実 PWM 配線後も `test/run_host_test.sh` が **9/9 緑**＝R-26（`doc16:213` §11 / `doc20:75`）を維持。
- [ ] **近接センサ → motor enable OFF reflex** — ⚠️**doc06 外**＝`doc12:76`（「ToF/LiDAR近接物体検出 → モータPWM停止 / motor enable OFF（MCU内、通信不要）」）+ `doc12:78`（「bumper / 近接センサ → モータ停止（MCU内、OS・ROS 非依存）」）+ `doc12:112`（最終防衛線）。速度クランプの**下**にある MCU 内 reflex（通信非依存）。`main.cpp` の近接停止 TODO を実装。

## C. センサ publisher（Pub）— doc03 凍結契約

- [ ] **MS200 LiDAR UART init + フレームパース → `/<ns>/scan`** — `doc02:15`（ORBBEC MS200 dToF 360°/0.03〜12m/4500Hz/0.4°）+ `doc02:26`（→ `/bot{n}/scan`）+ `doc03:78`（`sensor_msgs/LaserScan` 凍結）+ ピン `config.h:20-22`（RX18/TX17/230400）。`main.cpp:initMS200()` は `Serial1.begin(...)` まで stub 済 → 実フレームパースと `publishScan()` の rcl_publish を実装。**R-43 注意**: 360°/0.4°≈3.6KB/scan が UDP MTU 512B を超える → ダウンサンプル/フラグメンテーション対策（`doc07:253`、host spike 未検証）。
- [ ] **エンコーダ dead-reckon → `/<ns>/odom`** — `doc02:28`（エンコーダ ×4 → オドメトリ）+ `doc03:77`（`nav_msgs/Odometry` 凍結）+ `doc06:129`（「`/odom` でオドメトリデータ受信確認」）。`main.cpp:publishOdom()` の [0,0,0] 起点 stub を実エンコーダ積分に置換。
- [ ] **バッテリー電圧 ADC → `/<ns>/battery`** — `doc02:17`（7.4V リポ）+ `doc03:79`（`sensor_msgs/BatteryState`・実機は micro-ROS firmware が供給 Phase 1+）。`publishBattery()` の `percentage` は config `safety.battery_percentage_scale` と**同一スケール**で出す（State Cache / Emergency Guardian の split-brain 回避、`doc03:79`）。
- [ ] **IMU（6軸）読取** — `doc02:16`（6軸IMU）+ `doc02:27`（`/bot{n}/imu`「※要確認 / sim 未橋渡し」）。⚠️ **`/bot{n}/imu` は `doc03:81` で凍結トピック契約の外** → `publishImu()` は **publish 配線しない**（stub のまま）。必要になったら firmware 内で確定せず **epic #3 に上げて contract-PR 判断**（`implementation-and-dependencies.md §3`）。
- [ ] **odom/scan/battery publisher を rclc に登録** — `doc03:77-79`（3トピックの名前・型は凍結＝そのまま consume）。`main.cpp:setup()` の `register pub(odom,scan,battery)` TODO を実装。**imu は登録しない**（上記）。

## D. リソース・検証（doc06 完了条件）

- [ ] **ESP32 メモリ / CPU 使用率を確認**（MS200 + micro-ROS 同時動作・課題 T4）— `doc06:131`（「ESP32のメモリ・CPU使用率を確認（MS200 + micro-ROS同時動作、課題T4）」）。
- [ ] **ロボット実寸を計測**（幅・長さ・高さ）→ 通路幅最終決定 + `TRACK_WIDTH` 確定 — `doc06:125`（「ロボットの実寸を計測（幅・長さ・高さ）→ 通路幅を最終決定」）。モータミックスの実測定数（上記 B）にも反映。
- [ ] **RViz2 でロボット位置を可視化** — `doc06:130`（「RViz2 でロボット位置を可視化」）。`/odom` + `/scan` 受信が前提。
- [ ] **`MAX_ANGULAR_VELOCITY` の実測確定** — `config.h:10`（現状 `2.0 rad/s` は **Phase 1 実測 placeholder**）。実車旋回で安全・実用な上限を計測し確定（クランプ境界テストは値非依存で既に固定）。

---

## 完了条件（doc06:136-140 / 本リスト）

- Jetson から1台のロボットを ROS 2 で遠隔操作できる（`doc06:138`）。
- RViz2 にロボットの位置が表示される（`doc06:139`）。
- 上記 A〜D が緑 → micro-ROS skeleton の TODO(Phase 1) が実値・実ドライバに置換され、
  **Layer-0 クランプ R-26 unit が引き続き 9/9 緑**（`safety_clamp.h` 不変）。

> 本リストの完了をもって #3 の Phase 1 実機分が閉じる（Phase 2 = SLAM/Nav2 自律走行は doc06:149〜）。
