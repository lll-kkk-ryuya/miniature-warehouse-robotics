# deploy/jetson — Jetson（prod 司令塔）systemd / 監視 たたき台

Jetson Orin Nano（prod 実機の中央コンピュータ。doc02:134-138）で倉庫ロボット
スタックを **systemd** で常駐起動するための雛形。**実機不要で準備できる範囲**
（unit 定義・起動/監視スクリプト・手順 doc）のみを置く。実機投入は Phase 1
（Jetson 到着後）。

> **正本手順**: [docs/setup/jetson-deploy.md](../../docs/setup/jetson-deploy.md)
> **設計正本**: [doc19 環境分離](../../docs/architecture/19-environments-and-config.md)（prod=Jetson / `/run/warehouse` / git タグ）・[doc02 ハードウェア](../../docs/shared/02-hardware-design.md)（Jetson 準備）・[doc12 安全4層](../../docs/architecture/12-infrastructure-common.md)（Layer 0 速度クランプ 0.3 m/s）。

## ⚠️ 安全ゲート（最優先）

prod 接続・実機動作は **Layer 0（MCU 速度クランプ ≤ 0.3 m/s・e-stop）と
Emergency Guardian のテスト通過後のみ**（[safety.md](../../.claude/rules/safety.md) /
doc16 §11 / doc19:21）。`install.sh` は unit を **インストールするだけで
enable/start しない**。motion 系（nav2）は guardian を `BindsTo=`（+`After=`）し、
guardian が**異常終了/クラッシュしても nav2 を停止**する（`Requires=` は予期せぬ終了を
伝播しない。`systemd.unit(5)`。詳細は jetson-deploy.md §0）。

## 構成

| パス | 役割 |
|---|---|
| `systemd/warehouse.target` | スタック一括 target（5 unit を Wants） |
| `systemd/warehouse-microros-agent.service` | micro-ROS Agent（ESP32 minicar / WiFi・UDP, doc02:71） |
| `systemd/warehouse-state-cache.service` | State Cache（`/run/warehouse/state.json`, doc12:384） |
| `systemd/warehouse-safety.service` | Emergency Guardian（Layer 1, doc12:80-84） |
| `systemd/warehouse-nav2.service` | Nav2 bring-up（`bringup.launch.py`・**#75 着地後**有効・guardian を BindsTo） |
| `systemd/warehouse-bridge.service` | LLM Bridge Node（→ GCP Hermes, doc19:18,86） |
| `env/warehouse.env.example` | `/etc/warehouse/warehouse.env` の雛形（**secrets 無し**） |
| `bin/ros-exec.sh` | ROS 2 underlay + workspace overlay を source して node を exec |
| `bin/install.sh` | unit/env/サービスアカウント導入（enable/start しない） |
| `bin/healthcheck.sh` | unit liveness + state.json 鮮度 + Hermes 到達性（監視 scaffold） |

## クイックスタート（prod・安全ゲート通過後）

```bash
# 1. リリースタグを /opt/warehouse に clone（doc19:94）し colcon build
# 2. secrets を配置（config/prod/.env + ~/.hermes/.env, doc19 §4）
sudo deploy/jetson/bin/install.sh          # 導入のみ
sudo systemctl enable --now warehouse.target
deploy/jetson/bin/healthcheck.sh
```

詳細・前提・ロールバックは [docs/setup/jetson-deploy.md](../../docs/setup/jetson-deploy.md)。

## まだ無い unit（Phase 1 で追加）

`warehouse_mcp_server` / `warehouse_nav2_bridge`（Mode A/B）/ WO Bridge は
別トラック実装が揃い次第 unit 化（bridge unit のコメント参照）。**Hermes Gateway
は prod では Jetson でなく GCP**（`34.4.104.112`, doc19:18,86）。
