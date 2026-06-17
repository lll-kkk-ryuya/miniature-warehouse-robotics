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
| `systemd/warehouse-microros-agent.service` | micro-ROS Agent（ESP32 minicar / WiFi・UDP, doc02:81） |
| `systemd/warehouse-state-cache.service` | State Cache（`/run/warehouse/state.json`, path 正本 doc19:18・paths.py:22-30・100ms 周期 doc12:477） |
| `systemd/warehouse-safety.service` | Emergency Guardian（Layer 1, doc12:80-84） |
| `systemd/warehouse-nav2.service` | Nav2 bring-up（`bringup.launch.py`・**prod は `sim:=false llm:=false` で nav2-only**・guardian を BindsTo） |
| `systemd/warehouse-bridge.service` | LLM Bridge Node（→ GCP Hermes, doc19:18,86） |
| `env/warehouse.env.example` | `/etc/warehouse/warehouse.env` の雛形（**secrets 無し**） |
| `bin/ros-exec.sh` | ROS 2 underlay + workspace overlay を source して node を exec |
| `bin/install.sh` | unit/env/サービスアカウント導入（enable/start しない） |
| `bin/preflight.sh` | 到着前 static check + 到着後 G0/G1/G7 読み取り preflight（enable/start しない） |
| `bin/healthcheck.sh` | unit liveness + state.json 鮮度 + Hermes 到達性（監視 scaffold） |

## クイックスタート（prod・安全ゲート通過後）

```bash
# 1. リリースタグを /opt/warehouse に clone（doc19:94）し colcon build
# 2. secrets を配置（config/prod/.env + ~/.hermes/.env, doc19 §4）
deploy/jetson/bin/preflight.sh --offline  # 到着前/導入前の静的検査
sudo deploy/jetson/bin/install.sh          # 導入のみ
deploy/jetson/bin/preflight.sh --arrival   # Jetson 到着後の読み取り検査
# G0 通過後のみ:
sudo systemctl enable --now warehouse.target
deploy/jetson/bin/healthcheck.sh
```

詳細・前提・ロールバックは [docs/setup/jetson-deploy.md](../../docs/setup/jetson-deploy.md)。

## 忠実度ギャップと実機投入前ゲート（#127）

現状ソフトは **Mac M4(arm64) + tiryoh(ARM64)** で検証中。実 Jetson Orin Nano Super
（arm64 Ubuntu 24.04）との **忠実度ギャップ**（GPU/CUDA・実時間性 R-40・micro-ROS 2台 R-37・
8GB ユニファイドメモリ R-38）と、**実機投入前ゲート（G0-G7・合否基準付き）**の正本は
[docs/jetson/01-fidelity-and-validation.md](../../docs/jetson/01-fidelity-and-validation.md)。
要点は [doc19 §7](../../docs/architecture/19-environments-and-config.md) にも固定。

> **Jetson が単体（ESP32 ロボット未着）で先着しても robot-free で通せるゲート**（setup + G1 メモリ / G3 jitter / G4 sim / G7 Hermes E2E）と **実ロボット必須**（G0 安全 / G2 / G5 / G6）の切り分け・到着後の実行順は同 doc **§4.1**（[01-fidelity-and-validation.md](../../docs/jetson/01-fidelity-and-validation.md) §4.1）。

**この scaffold の整合（doc19 / doc17 §4 と突合・修正不要）**:

| 項目 | 期待（正本） | 本 scaffold | 判定 |
|---|---|:---:|:---:|
| prod=別マシン clone | doc17:88 / doc19:94（git タグ） | `install.sh` が clone 先自動検出・ExecStart 書換 | ◯ |
| prod runtime dir | doc19:18（`/run/warehouse`） | data unit が `RuntimeDirectory=warehouse`+`Preserve=yes` | ◯ |
| 起動順 | doc02:138 / doc12 層構造 | microros→state-cache→safety→nav2→bridge | ◯ |
| 安全トポロジ | doc12:80-84 / safety.md | nav2 が safety を **`BindsTo=`**（guardian クラッシュで nav2 停止） | ◯ |
| 安全ゲート | doc16:216-219 / doc19:21 | `install.sh` は導入のみ（enable/start しない） | ◯ |
| Hermes=GCP | doc19:18,86 | bridge/healthcheck が GCP を read-only 言及 | ◯ |
| prod launch 引数 | #156: `bringup.launch.py` 既定 `sim:=true`/`llm:=true`（Mac capstone, :148-149,154-155） | `nav2.service` が `sim:=false llm:=false` 固定＝nav2-only・gz/bridge 二重起動防止 | ◯ |

## 検証（実機なしでできる）

実機を動かさず静的に検査できる範囲（**G0-G7 の実機ゲートは Jetson 到着後**＝忠実度 doc §4）:

```bash
# unit 構文・依存（After/BindsTo/Wants）の静的検査（ROS 不要）
deploy/jetson/bin/preflight.sh --offline
# Linux/Jetson では preflight 内で下記も実行（macOS では SKIP）
systemd-analyze verify deploy/jetson/systemd/*.service deploy/jetson/systemd/*.target
# スクリプト構文
bash -n deploy/jetson/bin/*.sh && shellcheck deploy/jetson/bin/*.sh
# env 解決（prod=/run/warehouse）は既存 unit テストで回帰（WAREHOUSE_ENV=prod）
```

> 実機投入は §0 安全ゲート（G0: Layer 0 ≤0.3 m/s クランプ・近接 e-stop / Layer 1 Guardian unit）
> 通過後のみ。メモリ Go/No-Go（G1・残RAM≥500MB）が Mode C 採否を分岐する（doc06:98 / 忠実度 doc §4）。
> Jetson 到着後は `deploy/jetson/bin/preflight.sh --arrival --gates G0,G1,G7` を使い、
> 自動判定できない Layer 0 実測や Bridge 認証サイクルは MANUAL 項目として記録する。

## まだ無い unit（Phase 1 で追加）

`warehouse_mcp_server` / `warehouse_nav2_bridge`（Mode A/B）/ WO Bridge は
別トラック実装が揃い次第 unit 化（bridge unit のコメント参照）。**Hermes Gateway
は prod では Jetson でなく GCP**（`34.4.104.112`, doc19:18,86）。
