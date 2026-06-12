# firmware/ — ESP32 micro-ROS（担当コンテキスト）

> worktree セッションがここに `cd` した瞬間に担当範囲を把握するためのコンテキスト（parallel-workflow.md）。

- **track**: #3 firmware / **branch**: `hw/firmware-esp32`
- **責務**: Yahboom ESP32 車載ファーム（micro-ROS / FreeRTOS）。pub `/<ns>/odom,scan,battery`・sub `/<ns>/cmd_vel`。
- **最重要（安全 Layer 0）**: 速度上限 **0.3 m/s を MCU 内で強制**（`clampLinear`）。ROS 側 `/cmd_vel` の値に関わらずクランプ。近接停止も MCU 内（通信非依存）。根拠 `.claude/rules/safety.md` / doc12。
- **契約**: トピック名・型は doc03 のトピック設計に従う（凍結契約・変更は contract ラベル + 予告）。
- **ビルド**: PlatformIO（colcon 非対象）。`pio run`。`firmware/.pio/` は .gitignore 済。
- **秘密情報**: WiFi パス・Agent IP は `config_secret.h`（コミット禁止）。`config.h` はプレースホルダ。
- **現状**: **雛形のみ・実機未検証**。`main.cpp` は micro-ROS 配線・各 publisher・モータ・MS200 を **stub / `# TODO(Phase 1)`** まで肉付け済（host コンパイル + クランプ R-26 unit が緑）。実機到着時の配線ゲートは [`PHASE1_CHECKLIST.md`](PHASE1_CHECKLIST.md)（micro-ROS executor/QoS、MS200 ドライバ、モータ PWM、再接続、R-37 client_key、近接 reflex）。
- **R-37（2台同時接続）**: 単一 `micro_ros_agent udp4 --port 8888`（凍結）に2台。**Phase 1 必須**＝両機に **distinct な XRCE `client_key`**（`rmw_uros_options_set_client_key()`、BOT_ID/MAC 由来）。同一/弱RNG キーだと session 衝突で pub/sub 片方向落ち（R-37）。host 先行検証 = [`spike/`](spike/)（[RESULT](spike/RESULT.md)）。正本 [doc07:242](../docs/shared/07-research-notes.md)。

## R-37 spike（ESP32 無し host 検証 / track #116）
- 場所: `firmware/spike/`（`run_spike.sh` + `uros_app/minicar_client` + `RESULT.md`）。tiryoh jazzy Docker で 1 agent + 2 ソフトクライアント。
- 結論: **distinct client_key → 単一Agentで2台双方向OK（採用a）** / 同一key強制で R-37 再現。**no-repro≠クローズ**（loopは R-43 MTU・WiFi未検証、最終 Phase 1）。
- jazzy/24.04 ビルド注意: `build_firmware.sh` は `-Werror`(rmw)・service introspection(std_srvs) で破綻 → 3フェーズ＋`-w`＋service skip で必要分のみビルド（`run_spike.sh setup` 参照）。

## テスト
- **Layer-0 速度クランプ（R-26 安全 unit）**: 純ロジックを `include/safety_clamp.h`（Arduino 非依存）に抽出し、`test/test_clamp/` で host 検証する（ESP32 不要）。
  - `pio test -e native`（PlatformIO + Unity）。`pio` 不在なら `bash test/run_host_test.sh`（g++/clang + 同梱 Unity shim、同一テスト源）。**どちらも緑が必須**。
  - 固定する契約: 境界（>上限→上限 / <−上限→−上限 / 範囲内素通し / 上限ちょうど / 0）＋ **非有限（NaN/±Inf）→ stop**（fail-safe・`safety.py:31-32` と一致・#235）＋ **`MAX_LINEAR_VELOCITY == 0.3 m/s`（safety.md / doc12:77）** ＋ `MAX_*_VELOCITY > 0`（負上限=runaway ガード）。`MAX_ANGULAR_VELOCITY=2.0` は Phase 1 実測 placeholder（境界動作のみ固定）。
- **キネマティクス host unit**: skid-steer mix（`mixSkidSteer` 差動逆運動学）+ dead-reckon odom（`integrateOdom` 順運動学）の純ロジック（`include/kinematics.h`・Arduino 非依存）を `test/test_kinematics/` で host 検証。`pio test -e native` の **`test_filter` allowlist が `test_clamp, test_kinematics` の両 suite を実行**（pio でクランプと一括／`platformio.ini`）。`pio` 不在なら `bash test/run_kinematics_test.sh`（同梱 Unity shim・**hardware 値は引数＝発明しない**・mutation 非空）。**安全 R-26 クランプ gate は shell の `run_host_test.sh`（clamp 単独・exact flags・CI `firmware-safety` でゲート）で不変に保つ**＝pio allowlist 拡張は dev 利便であり enforcement gate ではない。
- **skeleton host コンパイル**: `src/main.cpp`（micro-ROS / 各ドライバ stub）を最小 Arduino shim（`test/support/arduino_shim/Arduino.h`、`unity_shim` と同型・test 専用）で host 構文確認する。`bash test/run_host_compile.sh`（ESP32 / `pio` 不要・micro-ROS 呼出は Phase 1 TODO コメントゆえ ROS ヘッダ不要・`-c` で object まで型検査）。クランプ unit と並ぶ host ゲート。
- R-37 多重接続は `firmware/spike/run_spike.sh all`（ESP32 不要、Docker のみ）で再現・計測可能。

## 提供 (produce) / 消費 (consume)
- **produce（凍結 IF）**: `include/safety_clamp.h`（`clamp_symmetric` / `clampLinear` / `clampAngular`）＝host-unit-tested Layer-0 クランプ。`main.cpp:onCmdVel` が消費。
- **produce（純ロジック・host-unit-tested）**: `include/kinematics.h`（`mixSkidSteer`＝差動逆運動学 v,w→左右track速度 / `integrateOdom`＝dead-reckon 順運動学）。Arduino 非依存・`test/test_kinematics` で固定。`setMotorVelocity`/`publishOdom` が消費。**hardware 値（TRACK_WIDTH / duty 曲線 / encoder scale / dt）は引数＝Phase 1**（発明しない）。
- **produce（内部 stub IF・Phase 1 で実装）**: `main.cpp` に `setMotorVelocity(v,w)`（クランプ後の Layer-0 sink・`mixSkidSteer` を呼ぶ・PWM duty は TODO）/ `publishOdom|Scan|Battery`（doc03:77-79 の**凍結型**を載せる publisher stub・odom は `integrateOdom` 消費）/ `initMS200`（UART init）。**`publishImu` は placeholder で publish 未配線**（`/bot{n}/imu` は doc03:81 で契約外＝新規配線は contract-PR・判断は epic #3）。実 rclc publish・UART parse・PWM は Phase 1（[`PHASE1_CHECKLIST.md`](PHASE1_CHECKLIST.md)）。**新トピック/型/契約は産まない**（doc03 のまま）。
- **consume**: `config.h` の `MAX_LINEAR_VELOCITY`（build flag `MAX_LINEAR_VELOCITY_MMPS` 由来）/ `MAX_ANGULAR_VELOCITY` / MS200 ピン・ボーレート。doc03 トピック契約（`/bot{n}/odom,scan,battery,cmd_vel` の名前・型）は**そのまま consume**。`warehouse_interfaces` への依存なし（firmware は ROS 契約の外）。
