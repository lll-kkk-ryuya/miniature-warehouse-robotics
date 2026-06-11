# firmware/ — ESP32 micro-ROS（担当コンテキスト）

> worktree セッションがここに `cd` した瞬間に担当範囲を把握するためのコンテキスト（parallel-workflow.md）。

- **track**: #3 firmware / **branch**: `hw/firmware-esp32`
- **責務**: Yahboom ESP32 車載ファーム（micro-ROS / FreeRTOS）。pub `/<ns>/odom,scan,battery`・sub `/<ns>/cmd_vel`。
- **最重要（安全 Layer 0）**: 速度上限 **0.3 m/s を MCU 内で強制**（`clampLinear`）。ROS 側 `/cmd_vel` の値に関わらずクランプ。近接停止も MCU 内（通信非依存）。根拠 `.claude/rules/safety.md` / doc12。
- **契約**: トピック名・型は doc03 のトピック設計に従う（凍結契約・変更は contract ラベル + 予告）。
- **ビルド**: PlatformIO（colcon 非対象）。`pio run`。`firmware/.pio/` は .gitignore 済。
- **秘密情報**: WiFi パス・Agent IP は `config_secret.h`（コミット禁止）。`config.h` はプレースホルダ。
- **現状**: **雛形のみ・実機未検証**。`# TODO(Phase 1)` を実機到着後に実値化（micro-ROS executor/QoS、MS200 ドライバ、モータ PWM、再接続）。
- **R-37（2台同時接続）**: 単一 `micro_ros_agent udp4 --port 8888`（凍結）に2台。**Phase 1 必須**＝両機に **distinct な XRCE `client_key`**（`rmw_uros_options_set_client_key()`、BOT_ID/MAC 由来）。同一/弱RNG キーだと session 衝突で pub/sub 片方向落ち（R-37）。host 先行検証 = [`spike/`](spike/)（[RESULT](spike/RESULT.md)）。正本 [doc07:242](../docs/shared/07-research-notes.md)。

## R-37 spike（ESP32 無し host 検証 / track #116）
- 場所: `firmware/spike/`（`run_spike.sh` + `uros_app/minicar_client` + `RESULT.md`）。tiryoh jazzy Docker で 1 agent + 2 ソフトクライアント。
- 結論: **distinct client_key → 単一Agentで2台双方向OK（採用a）** / 同一key強制で R-37 再現。**no-repro≠クローズ**（loopは R-43 MTU・WiFi未検証、最終 Phase 1）。
- jazzy/24.04 ビルド注意: `build_firmware.sh` は `-Werror`(rmw)・service introspection(std_srvs) で破綻 → 3フェーズ＋`-w`＋service skip で必要分のみビルド（`run_spike.sh setup` 参照）。

## テスト
- **Layer-0 速度クランプ（R-26 安全 unit）**: 純ロジックを `include/safety_clamp.h`（Arduino 非依存）に抽出し、`test/test_clamp/` で host 検証する（ESP32 不要）。
  - `pio test -e native`（PlatformIO + Unity）。`pio` 不在なら `bash test/run_host_test.sh`（g++/clang + 同梱 Unity shim、同一テスト源）。**どちらも緑が必須**。
  - 固定する契約: 境界（>上限→上限 / <−上限→−上限 / 範囲内素通し / 上限ちょうど / 0）＋ **非有限（NaN/±Inf）→ stop**（fail-safe・`safety.py:31-32` と一致・#235）＋ **`MAX_LINEAR_VELOCITY == 0.3 m/s`（safety.md / doc12:77）** ＋ `MAX_*_VELOCITY > 0`（負上限=runaway ガード）。`MAX_ANGULAR_VELOCITY=2.0` は Phase 1 実測 placeholder（境界動作のみ固定）。
- R-37 多重接続は `firmware/spike/run_spike.sh all`（ESP32 不要、Docker のみ）で再現・計測可能。

## 提供 (produce) / 消費 (consume)
- **produce**: `include/safety_clamp.h`（`clamp_symmetric` / `clampLinear` / `clampAngular`）＝host-unit-tested Layer-0 クランプ。`main.cpp` が `onCmdVel` で消費。**新トピック/型/契約は産まない**（ROS 契約は doc03 のまま）。
- **consume**: `config.h` の `MAX_LINEAR_VELOCITY`（build flag `MAX_LINEAR_VELOCITY_MMPS` 由来）/ `MAX_ANGULAR_VELOCITY`。`warehouse_interfaces` への依存なし（firmware は ROS 契約の外）。
