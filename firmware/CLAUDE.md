# firmware/ — ESP32 micro-ROS（担当コンテキスト）

> worktree セッションがここに `cd` した瞬間に担当範囲を把握するためのコンテキスト（parallel-workflow.md）。

- **track**: #3 firmware / **branch**: `hw/firmware-esp32`
- **責務**: Yahboom ESP32 車載ファーム（micro-ROS / FreeRTOS）。pub `/<ns>/odom,scan,battery`・sub `/<ns>/cmd_vel`。
- **最重要（安全 Layer 0）**: 速度上限 **0.3 m/s を MCU 内で強制**（`clampLinear`）。ROS 側 `/cmd_vel` の値に関わらずクランプ。近接停止も MCU 内（通信非依存）。根拠 `.claude/rules/safety.md` / doc12。
- **契約**: トピック名・型は doc03 のトピック設計に従う（凍結契約・変更は contract ラベル + 予告）。
- **ビルド**: PlatformIO（colcon 非対象）。`pio run`。`firmware/.pio/` は .gitignore 済。
- **秘密情報**: WiFi パス・Agent IP は `config_secret.h`（コミット禁止）。`config.h` はプレースホルダ。
- **現状**: **雛形のみ・実機未検証**。`# TODO(Phase 1)` を実機到着後に実値化（micro-ROS executor/QoS、MS200 ドライバ、モータ PWM、再接続）。

## テスト
- 純ロジック（`clampLinear`/`clampAngular`）は実機なしで検証可能（将来 native env でユニットテスト化を検討）。
