# Jetson（prod）デプロイ手順 — systemd 常駐 + 監視

作成日: 2026-06-03

> **対象**: prod 環境（Jetson Orin Nano 実機 = 司令塔）で倉庫ロボットスタックを
> systemd 常駐起動する手順。本書は **実機不要で準備できる scaffold 段階**（unit 定義・
> 起動/監視・手順）を正本化する。実 OS 焼き込み・実機制御は **Phase 1**（Jetson 到着後、
> doc02:140-164 / doc06 Phase 0.5→1）。
>
> **設計正本**: [doc19 環境分離](../architecture/19-environments-and-config.md)（prod 行・`/run/warehouse`・git タグ）/ [doc02 ハードウェア](../shared/02-hardware-design.md):134-164（Jetson 準備）/ [doc12 安全4層](../architecture/12-infrastructure-common.md):75-84,380-390（Layer 0/1）/ `paths.py`:22-57（runtime/config パス）/ `.claude/rules/safety.md`・`environments.md`。
> **実装**: [deploy/jetson/](../../deploy/jetson/)（unit・スクリプト・env 雛形）。

---

## ⚠️ 0. 安全ゲート（最優先・必読）

prod 接続・実機動作は次を**すべて通過してから**のみ（doc19:21 / safety.md / doc16 §11）:

1. **Layer 0**: ESP32/MCU の速度上限 **≤ 0.3 m/s** クランプ・近接 e-stop が実機で有効
   （doc12:75-78。ROS 側 `cmd_vel` に関わらず MCU が最終クランプ＝最終防衛線）。
2. **Layer 1**: Emergency Guardian のユニットテスト通過（doc16 §11）。

`install.sh` は unit を**インストールするだけで enable/start しない**。motion を担う
`warehouse-nav2.service` は `warehouse-safety.service` を `BindsTo=`（+`After=`）で結合し、
guardian が**異常終了/クラッシュしても Nav2 を停止**する（`systemd.unit(5)`。`Requires=` は
予期せぬ終了を伝播しない）。**ゲート未通過で `systemctl enable --now` しない**。

---

## 1. 前提（到着前に準備可 / 到着後に実行）

| 段階 | 作業 | Jetson 要否 |
|---|---|:---:|
| 到着前 | JetPack 6.x イメージ DL・本リポジトリ／unit の準備 | 不要 |
| 到着後 | SSD へ JetPack 焼込（microSD 初回ブート→SSD 移行） | **必要** |
| 到着後 | Super 化（`sudo nvpmodel -m 2` + `sudo jetson_clocks`） | **必要** |
| 到着後 | ROS 2 Jazzy + `micro_ros_agent` 導入・8GB メモリ実測（doc06 Phase 0.5 段階2） | **必要** |

詳細は doc02:140-164。本書はその後の **systemd 常駐化**を扱う。

---

## 2. リリース取得（git タグ）

prod は **git タグ固定**（`v0.x`）を clone/checkout して実行（doc19:94 / doc17 §4.0 別マシン=clone）。
規約パスは `/opt/warehouse`（`install.sh` は実 clone 先を自動検出するので別パスでも可）。

```bash
sudo git clone --branch v0.x https://github.com/lll-kkk-ryuya/miniature-warehouse-robotics.git /opt/warehouse
cd /opt/warehouse
```

## 3. ビルド

```bash
source /opt/ros/jazzy/setup.bash
cd /opt/warehouse/ws && colcon build && cd /opt/warehouse
```

## 4. Secrets（コミット厳禁）

doc19 §4 / §4.1 の2ファイルに分けて配置（**リポジトリには置かない**。`.gitignore` で実体除外）:

- `config/prod/.env` — ROS 側（`API_SERVER_KEY` = `~/.hermes/.env` と同値、Langfuse 観測キー）。
- `~/.hermes/.env` — Hermes Gateway 側（各社プロバイダキー）。**prod Hermes は GCP**（`34.4.104.112`, doc19:18,86）なので Jetson 側 Bridge は `config/prod/.env` の `API_SERVER_KEY` のみで足りる。

`/etc/warehouse/warehouse.env`（secrets 無し・パス/環境）は `install.sh` が雛形から生成
（`env/warehouse.env.example` 参照）。生成後 **`WAREHOUSE_MAP` と `WAREHOUSE_TRAFFIC_MODE`** を確認。

## 5. 導入（enable/start しない）

```bash
sudo /opt/warehouse/deploy/jetson/bin/install.sh
```

実施内容（冪等）: サービスアカウント `warehouse`（dialout 追加）作成 → `/etc/warehouse/warehouse.env`
生成（既存は保持）→ unit を `/etc/systemd/system/` へ導入（ExecStart の `/opt/warehouse`
を実 clone 先へ書換）→ `daemon-reload`。**enable/start はしない**（§0 ゲート）。

## 6. 起動（安全ゲート通過後のみ）

```bash
# WAREHOUSE_ENV は /etc/warehouse/warehouse.env で prod 固定（paths.py:22-30 → /run/warehouse）
sudo systemctl enable --now warehouse.target
```

起動順は unit の `After=/BindsTo=` で制御: micro-ROS Agent → State Cache → Emergency Guardian
→ Nav2（guardian に `BindsTo`）→ LLM Bridge。

> **Depends on #75**: `warehouse-nav2.service` は `ros2 launch warehouse_bringup bringup.launch.py`
> に `traffic_mode` / `map` 等を渡すが、**#75 マージ前の `bringup.launch.py` は空の placeholder**
> （`LaunchDescription([])`）で、これら launch 引数は**無視される no-op**。#75 着地後に有効化。
> `traffic_mode` の prod 値は `config/prod/warehouse.yaml:13`（`open-rmf`＝Mode C）と一致させる
> （`/etc/warehouse/warehouse.env` の `WAREHOUSE_TRAFFIC_MODE`。doc19:54 単一ソース）。

## 7. 監視

```bash
deploy/jetson/bin/healthcheck.sh            # unit liveness + state.json 鮮度 + Hermes 到達性
journalctl -u warehouse-safety.service -f   # 個別ログ
systemctl status warehouse.target
```

`healthcheck.sh` は core unit のいずれかが非 active、または `/run/warehouse/state.json`
が欠落/陳腐化（既定 10s 超）で exit≠0（cron/監視プローブ兼用）。

## 8. 更新・ロールバック

```bash
cd /opt/warehouse && sudo git fetch --tags && sudo git checkout v0.y
source /opt/ros/jazzy/setup.bash && (cd ws && colcon build)
sudo /opt/warehouse/deploy/jetson/bin/install.sh   # unit 差分反映
sudo systemctl restart warehouse.target
```

問題時は前タグへ `checkout` → rebuild → `restart`（環境昇格はブランチでなく
同一 main コミットの config デプロイ。merge-and-communication.md §1）。

---

## systemd unit 一覧

| unit | 役割 | 依存 | runtime dir |
|---|---|---|:---:|
| `warehouse-microros-agent.service` | micro-ROS Agent（WiFi/UDP, doc02:71） | `network-online.target` | — |
| `warehouse-state-cache.service` | State Cache（`state.json`, doc12:384） | microros | `/run/warehouse`（作成・Preserve） |
| `warehouse-safety.service` | Emergency Guardian（Layer 1, doc12:80-84） | state-cache | `/run/warehouse` |
| `warehouse-nav2.service` | Nav2 bring-up（`bringup.launch.py`・**#75 着地後**有効） | **BindsTo safety** | — |
| `warehouse-bridge.service` | LLM Bridge（→ GCP Hermes） | nav2 / state-cache | `/run/warehouse` |

`RuntimeDirectory=warehouse` + `RuntimeDirectoryPreserve=yes` で `/run/warehouse`（prod
runtime dir = `paths.runtime_dir()`）を共有・個別再起動でも保持。実体ファイル: `state.json` /
`gen_store/` / `idempotency_store/` / `audit.jsonl`（paths.py:33-51）。

### Phase 1 で追加する unit（別トラック実装待ち）

- `warehouse-mcp-server.service`（`warehouse_mcp_server`）/ `warehouse-nav2-bridge.service`
  （`warehouse_nav2_bridge`, Mode A/B）/ WO Bridge。LLM Bridge が稼働するには MCP Server が必要
  （bridge unit コメント参照）。
- **Hermes Gateway は prod では Jetson に置かない**（GCP `34.4.104.112`, doc19:86）。

---

## 検証（実機なしでできる）

- `systemd-analyze verify deploy/jetson/systemd/*.service deploy/jetson/systemd/*.target`
  （unit 構文・依存の静的検査。ROS 環境不要）。
- `bash -n` / `shellcheck` で `bin/*.sh`・env 雛形を検査。
- env 解決（`paths.runtime_dir()` prod=`/run/warehouse`）は既存 unit テストで回帰カバー
  （`tests/unit/test_*` の `WAREHOUSE_ENV=prod`）。
- **実機投入は Phase 1**（Jetson 到着後、§0 安全ゲート通過後）。
