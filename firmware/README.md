# firmware/ — ESP32 micro-ROS ファームウェア

Yahboom MicroROS Car (ESP32) 用。micro-ROS + MS200 LiDAR ドライバ + 速度クランプ(≤0.3m/s, Layer 0 最終防衛線)。
担当agent: hardware-integrator。設計: `docs/shared/02-hardware-design.md`, `docs/architecture/12-infrastructure-common.md`。

> 速度制限はMCU内で強制する（`.claude/rules/safety.md`）。WiFiパスワード/認証情報をコミットしない。
