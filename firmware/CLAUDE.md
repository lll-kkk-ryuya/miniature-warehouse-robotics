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
- 純ロジック（`clampLinear`/`clampAngular`）は実機なしで検証可能（将来 native env でユニットテスト化を検討）。
- R-37 多重接続は `firmware/spike/run_spike.sh all`（ESP32 不要、Docker のみ）で再現・計測可能。
