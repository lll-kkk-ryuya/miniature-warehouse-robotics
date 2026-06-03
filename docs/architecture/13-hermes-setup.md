# Hermes Agent セットアップ・運用ガイド

作成日: 2026-05-26
対象バージョン: Hermes Agent **v0.13.x (v2026.5.7 以降)**

> **位置づけ**
> 本書は `15-mcp-platform.md` で示した Hermes Agent Gateway 構成を、実際にインストール・起動・切替できる形に落とし込んだ手順書。設計思想ではなくオペレーションを扱う。
>
> **関連ドキュメント**
> - [15-mcp-platform](15-mcp-platform.md) — Hermes / Warehouse MCP / Policy Gate / 競合状態の防止
> - [12-infrastructure-common](12-infrastructure-common.md) — 共通基盤（Emergency / State Cache / 責務分離）
> - [08-llm-bridge-common](08-llm-bridge-common.md) — LLM Bridge共通設計
> - [Mode A/B](../mode-a/README.md) / [Mode C](../mode-c/README.md) — モード別構成

---

## 1. 前提

| 項目 | 値 |
|------|-----|
| 実行ホスト | Jetson Orin Nano Super (Ubuntu 24.04) |
| Hermes バージョン | v0.13.0 以降（リリースタグ `v2026.5.7`、2026-05-07） |
| 動作モード | **Gateway** (`hermes gateway`) — ヘッドレスデーモン |
| デフォルトポート | `127.0.0.1:8642` |
| API 形式 | OpenAI Chat Completions 互換 (`/v1/chat/completions`) |
| ROS 2 ディストリ | Jazzy |

開発時 (Mac M4) は Docker (`tiryoh/ros2-desktop-vnc:jazzy`) 内に Hermes を入れてもよいが、Jetson 実機での動作が本番構成。

---

## 2. インストール

### 2.1 Hermes 本体

```bash
# Linux (Jetson Ubuntu 24.04)
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash

# シェル再読込
exec $SHELL -l

# 動作確認
hermes --version   # v0.13.x が出ればOK
```

### 2.2 初期セットアップ (対話ウィザード)

```bash
hermes setup
```

ウィザードでは以下が聞かれる:
- Default provider (本PJでは **anthropic** を選択)
- API キーの貼付 (後で `.env` で上書き可)
- メモリ機能の有効化 (有効推奨)
- Skills 機能 (有効推奨)

ウィザード完了後、以下が生成される:

```
~/.hermes/
├── config.yaml        # 主設定 (YAML)
├── .env               # APIキー・シークレット
├── auth.json          # OAuth資格情報
├── SOUL.md            # エージェントID・コアプロンプト
├── memories/          # MEMORY.md, USER.md
├── skills/            # 自動生成スキル + カスタム
├── cron/              # スケジュールジョブ
├── sessions/          # Gatewayセッション
└── logs/              # エラー・Gatewayログ (機密自動マスク)
```

### 2.3 必要パッケージ (ROS 2 側ノードから利用)

LLM Bridge Node からの POST に使用:

```bash
pip install httpx pydantic  # ROS 2 ワークスペースの requirements に追記
```

### 2.4 Warehouse MCP Server のインストール

本プロジェクト自作の MCP Server。Hermes Gateway が config.yaml に従って **stdio 子プロセスとして起動** するため、独立 daemon 化は不要。ただし `python -m warehouse_mcp_server` で起動できるよう、事前に Python パッケージとして導入しておく必要がある。

> ▶ **dispatch 経路の補足（S2-PR2 HALF B / #4）**: この stdio 子プロセス起動は **Hermes ネイティブのツール実行経路 / 外部 MCP client 用**。S1+S2 採用の commander サイクルでは、Bridge が LLM の Command JSON を `action_map` で写像し `WarehouseTools().dispatch` を **同一トラック in-process** で呼ぶ（#81 / `docs/architecture/08-llm-bridge-common.md:166-168`）。本注記は MCP server 起動形態（§2.4）に対する補足で、Langfuse 観測（§5・#73 所有）には触れない。

```bash
# 開発環境（リポジトリ直下）
cd ws/src/warehouse_mcp_server   # Phase 0.5 で作成予定の自作パッケージ（doc16 §2）
pip install -e .              # editable install
```

実装は本書のスコープ外。`15-mcp-platform.md §Warehouse MCP Server` の責務定義 + Phase 1 タスクで実装する。Hermes Gateway 起動時に `command/args` の解決ができない場合は `journalctl -u hermes-gateway` でモジュール not found エラーが出る。

---

## 3. 設定ファイル

### 3.1 `~/.hermes/.env` テンプレート

**コミット禁止**。`safety.md` ルール準拠。

```bash
# ───────────────────────────────────────────────
# Gateway API Server
# ───────────────────────────────────────────────
API_SERVER_ENABLED=true
API_SERVER_HOST=127.0.0.1
API_SERVER_PORT=8642
API_SERVER_KEY=<ローカル生成: openssl rand -hex 32>

# ───────────────────────────────────────────────
# LLM Providers (4社比較用、全てセット推奨)
# ───────────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AIza...
XAI_API_KEY=xai-...

# ───────────────────────────────────────────────
# Langfuse (4社比較トレース、Phase 4 で必須)
# ───────────────────────────────────────────────
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
# リージョンをキー発行先に合わせる: EU=cloud.langfuse.com(既定) / US=us.cloud.langfuse.com / JP=jp.cloud.langfuse.com / セルフホスト時は差替
LANGFUSE_HOST=https://cloud.langfuse.com
# ※ Hermes 自身は LANGFUSE_BASE_URL を読む。Bridge(langfuse.openai SDK)は LANGFUSE_HOST。Orchestrator は HERMES_LANGFUSE_* (doc19 §4.1)

# ───────────────────────────────────────────────
# Warehouse MCP Server (子プロセスへ渡される)
# ───────────────────────────────────────────────
WAREHOUSE_STATE_CACHE_PATH=/tmp/warehouse/state.json
WAREHOUSE_AUDIT_LOG_PATH=/tmp/warehouse/audit.jsonl
WAREHOUSE_CONFIG_PATH=config/warehouse.yaml
# ※ 上記は dev 既定（16-repository-and-conventions.md §4）。本番(Jetson)は systemd RuntimeDirectory=warehouse で /run/warehouse/ に切替
```

> **API_SERVER_KEY の生成**: `openssl rand -hex 32` で生成し `.env` に貼る。LLM Bridge Node の `Authorization: Bearer <key>` ヘッダで使用。

#### 3.1.1 `.env.example`（リポジトリにコミット可）

実値を含む `~/.hermes/.env` は **コミット禁止**（`safety.md` 準拠）。リポジトリには **値を空にしたテンプレート**を `deploy/hermes/.env.example` として配置し、新規セットアップ時の参照源とする（実ファイルは Phase 0.5 で配置）。

```bash
# deploy/hermes/.env.example（イメージ）
API_SERVER_ENABLED=true
API_SERVER_HOST=127.0.0.1
API_SERVER_PORT=8642
API_SERVER_KEY=    # openssl rand -hex 32 で生成

ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GOOGLE_API_KEY=
XAI_API_KEY=

LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com

WAREHOUSE_STATE_CACHE_PATH=/tmp/warehouse/state.json
WAREHOUSE_AUDIT_LOG_PATH=/tmp/warehouse/audit.jsonl
WAREHOUSE_CONFIG_PATH=config/warehouse.yaml
# ※ 上記は dev 既定（16-repository-and-conventions.md §4）。本番(Jetson)は systemd RuntimeDirectory=warehouse で /run/warehouse/ に切替
```

`.gitignore` に `**/.env` を必ず追加する。

### 3.2 `~/.hermes/config.yaml` テンプレート（両モードスイッチャブル）

```yaml
# ───────────────────────────────────────────────
# Active provider (1行変更で比較対象を切替)
# ───────────────────────────────────────────────
active_provider: anthropic    # anthropic | openai | google | xai

# ───────────────────────────────────────────────
# Provider 設定
# ───────────────────────────────────────────────
# Note: ここで指定するモデルは Hermes Gateway 配下の「司令官 LLM」の選定。
#       Phase 4 比較検証のため各社のフラッグシップ級を設定する。
#       これは Claude Code（開発支援アシスタント）のモデル設定とは無関係。
providers:
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
    model: claude-opus-4-8    # 最新世代Opus（全Claude Opus統一、16-repository-and-conventions.md §7）
  openai:
    api_key: ${OPENAI_API_KEY}
    model: gpt-4o
  google:
    api_key: ${GOOGLE_API_KEY}
    model: gemini-2.5-flash   # 2026/10/16 非推奨予定、Phase 4 までは継続使用
  xai:
    api_key: ${XAI_API_KEY}
    model: grok-4.3

# ───────────────────────────────────────────────
# Memory / Skills (Hermes 内蔵機能)
# ───────────────────────────────────────────────
memory:
  enabled: true
  cross_session_recall: true

skills:
  enabled: true
  auto_create: true

# ───────────────────────────────────────────────
# Langfuse (環境変数があれば自動有効)
# ───────────────────────────────────────────────
observability:
  langfuse:
    enabled: true

# ───────────────────────────────────────────────
# MCP Servers (自作 Warehouse MCP Server のみ)
# ───────────────────────────────────────────────
mcp_servers:
  warehouse:
    command: "python"
    args: ["-m", "warehouse_mcp_server"]
    env:
      WAREHOUSE_STATE_CACHE_PATH: ${WAREHOUSE_STATE_CACHE_PATH}
      WAREHOUSE_AUDIT_LOG_PATH: ${WAREHOUSE_AUDIT_LOG_PATH}
      WAREHOUSE_CONFIG_PATH: ${WAREHOUSE_CONFIG_PATH}
    tools:
      include:
        - dispatch_task
        - cancel_task
        - get_fleet_status
        - get_task_queue
        - send_to_charging
        - escalation_response
        - start_negotiation   # Phase 3 で追加（キャラLLM交渉、14-character-llm-negotiation.md 参照）
      prompts: false
      resources: false
```

> **トークンコスト**: `tools.include` で7ツールに絞ることで MCP の自動列挙トークンを最小化（約550トークン/ターン）。これは `feedback_mcp_token_cost` でも確認した運用方針。
> **段階導入**: `start_negotiation` はキャラLLM交渉（Phase 3）で初めて使用する。Phase 0.5〜2 のプロトタイプ時点では残り6ツールで運用してよい。

### 3.3 倉庫側 `config.yaml`（モードスイッチ）

Hermes 外、Warehouse MCP Server / LLM Bridge Node が読む設定。**正本は `config/warehouse.base.yaml` + `config/<WAREHOUSE_ENV>/warehouse.yaml`（base + overlay、doc19）**。キー規約（実体に合わせる）: 接続 URL は **`base_url`**（旧 `endpoint` 表記は廃止）／`locations` は**座標マップ**（doc08 LOCATIONS とキー一致）／`robots` は **`id`**。

`config/warehouse.base.yaml`（全環境共通の土台。環境差分は `config/<env>/warehouse.yaml` で上書き）:

```yaml
# ───────────────────────────────────────────────
# 交通管理モード (1行変更で切替)
# ───────────────────────────────────────────────
traffic_mode: "open-rmf"    # Mode C: LLM + Open-RMF (主方針)
# traffic_mode: "simple"    # Mode B: LLM + 自作ルールベース
# traffic_mode: "none"      # Mode A: LLM 単独

# ───────────────────────────────────────────────
# ロボット定義
# ───────────────────────────────────────────────
robots:
  - id: bot1
  - id: bot2
# namespace は /<id> 規約。charging_station は locations を共有参照（2台共有・同時充電不可・先着順）

# ───────────────────────────────────────────────
# 場所定義 (Policy Gate の known_locations 検証用)
# ───────────────────────────────────────────────
locations:                       # 座標マップ（doc08 LOCATIONS とキー一致。座標は暫定＝Phase 2 実測で確定）
  shelf_1: {x: 0.2, y: 0.3}
  shelf_2: {x: 0.7, y: 0.3}
  shelf_3: {x: 1.2, y: 0.3}
  berth_A: {x: 0.2, y: 0.8}
  berth_B: {x: 0.7, y: 0.8}
  shipping_station: {x: 0.2, y: 0.1}
  charging_station: {x: 1.2, y: 0.1}
  retreat_A: {x: 0.45, y: 0.85}
  retreat_B: {x: 0.95, y: 0.85}

# ───────────────────────────────────────────────
# Hermes Gateway 接続情報 (LLM Bridge Node が読む)
# ───────────────────────────────────────────────
hermes:
  base_url: ""                  # 接続 URL（旧 endpoint）。環境側で設定（dev: http://localhost:8642 / prod: GCP）
  # token は config/<env>/.env の API_SERVER_KEY、timeout は doc08 のモード別（2.5s/5.0s）で扱う

# ───────────────────────────────────────────────
# Mode A/B のみで使う Nav2 Bridge エンドポイント
# ───────────────────────────────────────────────
nav2_bridge:
  base_url: "http://localhost:8645"   # 旧 endpoint。12a-integration-mode-a.md の Nav2 Bridge 実装ポートと一致

# ───────────────────────────────────────────────
# Mode C のみで使う Open-RMF エンドポイント
# ───────────────────────────────────────────────
rmf:
  enabled: false   # dev 既定無効。RMF API エンドポイント(8000) は 12c-integration-mode-c.md が所有（warehouse config 外）
```

LLM Bridge Node / Warehouse MCP Server は起動時に `traffic_mode` を読み、内部の TrafficManager 実装を切替える（`15-mcp-platform.md §Warehouse MCP Server`）。

---

## 4. Gateway の起動・停止

### 4.1 フォアグラウンド起動（開発・デバッグ）

```bash
hermes gateway
# → 127.0.0.1:8642 で待受
```

### 4.2 systemd ユニット（本番運用）

`/etc/systemd/system/hermes-gateway.service`:

```ini
[Unit]
Description=Hermes Agent Gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=jetson
Environment="HOME=/home/jetson"
ExecStart=/home/jetson/.local/bin/hermes gateway
Restart=on-failure
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-gateway
sudo systemctl status hermes-gateway
journalctl -u hermes-gateway -f   # ライブログ
```

### 4.3 ヘルスチェック

```bash
# 認証なし (loopback では許可)
curl -s http://127.0.0.1:8642/health

# 詳細メトリクス
curl -s http://127.0.0.1:8642/health/detailed

# 認証ありエンドポイント
curl -s http://127.0.0.1:8642/v1/models \
  -H "Authorization: Bearer $API_SERVER_KEY"
```

---

## 5. LLM Bridge Node からの呼び出し

`15-mcp-platform.md` 記載のリクエスト形式（変更なし）。本書では運用差分のみ補足。

### 5.1 Chat Completions（stateless、推奨）

> `situation` JSON のスキーマ（pose, velocity, battery, predicted_position_3s, obstacle_ahead 等）と `system_prompt` の正本は以下に定義済み:
> - 共通: [`08-llm-bridge-common.md`](08-llm-bridge-common.md)
> - Mode A/B: [`mode-a/08a-llm-bridge-mode-a.md`](../mode-a/08a-llm-bridge-mode-a.md) — `predicted_position_3s` は LLM Bridge Node が State Cache の `pose`+`velocity` から線形外挿で計算
> - Mode C: [`mode-c/08c-llm-bridge-mode-c.md`](../mode-c/08c-llm-bridge-mode-c.md) — `predicted_position_3s` は Open-RMF が計画する経路に置換

```python
# llm_bridge_node.py 抜粋（実装は後続フェーズで作成）
import httpx, json, os

HERMES_URL = os.environ["HERMES_ENDPOINT"]      # http://127.0.0.1:8642
HERMES_KEY = os.environ["API_SERVER_KEY"]

def ask_hermes(situation: dict, system_prompt: str) -> dict:
    resp = httpx.post(
        f"{HERMES_URL}/v1/chat/completions",
        json={
            "model": "hermes-agent",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(situation)},
            ],
        },
        headers={
            "Authorization": f"Bearer {HERMES_KEY}",
            "Content-Type": "application/json",
        },
        timeout=5.0,
    )
    resp.raise_for_status()
    return resp.json()
```

### 5.2 Responses API（マルチターンが必要な場合のみ）

`/v1/chat/completions` は stateless。Hermes 側に会話履歴を保持させたい場合のみ `/v1/responses` + `previous_response_id` を使用する。本PJの 3秒サイクル設計ではターン独立で十分なため Chat Completions を採用。

### 5.3 ストリーミング（本PJでは非採用）

`/v1/runs` + SSE が利用可能だが、3秒サイクル + Policy Gate 検証のシンプル化のため、本PJでは同期 POST のみ使用。

---

## 6. プロバイダ切替手順（4社比較）

Phase 4（比較検証）で繰り返し行う操作。

```bash
# 方法1: コマンドで切替
hermes config set active_provider openai
sudo systemctl restart hermes-gateway

# 方法2: config.yaml を直接編集
hermes config edit
# active_provider: openai に変更して保存
sudo systemctl restart hermes-gateway

# 確認
curl -s http://127.0.0.1:8642/v1/models \
  -H "Authorization: Bearer $API_SERVER_KEY"
```

LLM Bridge Node 側のコード変更は **不要**。Hermes が透過的に切替える。

---

## 7. Langfuse 連携

### 7.1 有効化

`.env` に `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` が存在すれば Hermes が自動的に有効化する（プラグイン内蔵）。

```bash
# 動作確認
hermes config check
# → "langfuse: enabled" が出ればOK
```

### 7.2 トレース内容

| 自動記録項目 | 説明 |
|-------------|------|
| LLM呼出し全件 | input messages / output / token usage / latency / cost |
| Provider 名 | anthropic / openai / google / xai のいずれか |
| MCP ツール呼出し | warehouse.dispatch_task 等の引数と返値 |
| エラー | timeout / rate limit / 4xx / 5xx |

Phase 4 では Langfuse ダッシュボードで 4社のメトリクス（成功率・平均latency・コスト・幻覚率）を比較する。

### 7.3 Audit Log との分離

- **Langfuse**: LLM レイヤのトレース（Hermes が出力）
- **Command Audit Log**: ロボット制御レイヤのトレース（Warehouse MCP Server が `$WAREHOUSE_AUDIT_LOG_PATH`、dev既定 `/tmp/warehouse/audit.jsonl` に出力。16 §4）

両者は独立。Langfuse 障害時もロボット側の audit は継続する。

### 7.4 Phase 4 比較検証時の分析フロー

```
LLM呼出し (4社)
   │
   ├─→ Langfuse                    Command Audit Log ←─┐
   │     ├─ provider 名             ├─ Policy Gate 判定（accept/reject）
   │     ├─ token / latency / cost  ├─ TrafficManager への入力
   │     ├─ system + user 入力      ├─ 実 Nav2 / RMF への送信値
   │     └─ ツール呼出し引数         └─ 実行結果（成功/失敗）
   │
   └────────────  trace_id / timestamp で突合  ────────┘
                          ↓
                   12構成 × KPI 集計
        （成功率・平均応答時間・コスト・幻覚率・拒否率）
```

`trace_id` は LLM Bridge Node が発行する **Langfuse 側の id**。**実装済の突合キーは `gen_id` + timestamp**（§7.5）。`trace_id` の Audit Log 記録は任意（現行 `CommandAuditLog` は持たない。将来 audit エントリに追加する場合は doc15 と整合）。Phase 3 後半で確定。

### 7.5 trace_id / 突合キー契約（Langfuse v4・Phase 3 実トレース検証）

> 前提: Langfuse Python SDK **v4（4.7.1, OTEL ベース）**。本節は trace 所有と突合キーの契約を固定する。

- **trace_id 形式**: Langfuse の trace id は **32 桁小文字 hex・ダッシュ無し**（W3C trace-context）。**採用実装（#78）= 下記(b) `create_trace_id(seed=f"{run_id}:{gen_id}")`（決定的・#6 と同一導出）**。自前 32hex 採番（`uuid7().hex` 等）も形式上は可だが、(a)/(b) のうち **(b) seed 派生を採用**（ランダム `uuid7` は #6 が独立導出できず Audit 記録依存になる＋`uuid7` は py3.12 stdlib 非搭載）。ダッシュ付き UUID 文字列は v4 で拒否され orphan score になる。
- **trace 所有 = Bridge（推奨・Pattern A）**: Bridge が `from langfuse.openai import OpenAI` を `base_url`=Hermes（OpenAI 互換）で使い、自分で generation/trace を所有する。二重計上回避のため **Hermes 側 Langfuse プラグインは無効化**（本 doc の Langfuse 自動有効化記述は、Bridge-owned トレースを採る比較セットアップでは off に上書きする）。これで `trace_id`/`metadata`/managed-prompt をネイティブに乗せられ、4社単一コードパス（比較公平性）を保てる（[doc08 「trace 所有」](08-llm-bridge-common.md)）。
- **Langfuse ↔ Audit Log の突合キー = `gen_id` + timestamp（実装済の経路）**。現行 `CommandAuditLog`（doc15）は `{timestamp, tool, result, detail, robot}` を書き **`trace_id` を持たない**。MCP ツールは `gen_id`（B-3 の required 引数）を受けるため、audit から `gen_id` + timestamp で Langfuse trace（同 `gen_id` を metadata に持つ）と突合できる。「3脚同一 `trace_id` リテラル」は audit 脚が未対応のため**前提にしない**。
- **#6（wo）が `trace_id` を得る方法**: (a) Audit Log から読む（trace_id を記録する設計にした場合）、または **(b) `langfuse.create_trace_id(seed=f"{run_id}:{gen_id}")` で #4 と #6 が決定的に同一 id を導出（クロスレーンのデータ依存ゼロ＝#6 が契約変更なしで着手可）。(b) 推奨**。`warehouse_interfaces` 凍結契約への trace_id 追加は不要（trace_id は ROS メッセージ契約でなく Langfuse/Audit 突合キー）。
- **Phase 3 実トレース検証項目**: ① Hermes が inbound `metadata.trace_id` を尊重するか（Bridge-owned 方式なら非依存）／② `usage_details`/cost が4社で ≠0 か（**xAI Grok はカスタムモデル定義が必要な可能性**＝無いと cost 空欄で比較破綻）／③ 二重 generation の有無／④ managed-prompt `prompt=` 連携の可否／⑤ SDK 4.7.1 スモーク。

### 7.6 LLM provider access 決定（Vertex AI SDK 不採用・Hermes 単一経路）

- **決定**: 4社比較（Claude / GPT / Gemini / Grok）の統一インターフェイスは **Hermes（OpenAI 互換ゲートウェイ）を維持**する。**Vertex AI SDK は比較経路に採用しない。**
- **理由**: ① Vertex は **専有 OpenAI GPT に到達不可**（`gpt-oss` オープンウェイトのみ。Claude/Grok/Gemini は到達可）／② いわゆる "Vertex AI SDK"（`vertexai.generative_models`）は **2026-06-24 削除**（新規は `google-genai`、`genai.Client(vertexai=True)`）／③ Vertex 経由でも Claude=Messages 形・Gemini/Grok=OpenAI 形・GPT=外 と **スキーマ分裂**し、単一コードパス＝比較公平性（同一の prompt 組立・tool 解析・retry/timeout・token 会計）を壊す。
- **Vertex の位置づけ（OPTIONAL・二次）**: 本番デモ機の Gemini/Claude を GCP IAM（ADC / service account）で回す production leg などに限り、**同一 `LLMClient` IF の裏の二次バックエンド**として config（`WAREHOUSE_ENV` overlay）で差し込む。**比較ラン（Phase 4 の 12構成）には混ぜない**（network/region/課金が非対称になりバイアス）。使う場合は Langfuse の `environment` タグ（例 `env=prod_vertex`）で分離記録する。Vertex AI Agent Engine / ADK は不採用（既存オーケストレーション層と競合・Gemini ロックイン）。

---

## 8. モード別・起動順序

両モード共通の起動順（Hermes は MCP Server を子プロセス起動するため、Warehouse MCP Server を独立起動する必要はない）。

### Mode C (Open-RMF) 起動順

```
1. micro-ROS Agent (ESP32 接続)
2. Nav2 (× 2台分)
3. Open-RMF (core + fleet adapters)
4. Emergency Guardian
5. State Cache Node
6. Hermes Gateway          ← 本書のスコープ
7. LLM Bridge Node
```

### Mode A/B 起動順

```
1. micro-ROS Agent
2. Nav2 (× 2台分)
3. Nav2 Bridge (REST → BasicNavigator)
4. (Mode B のみ) SimpleTrafficManager
5. Emergency Guardian
6. State Cache Node
7. Hermes Gateway          ← 本書のスコープ
8. LLM Bridge Node
```

systemd の `After=` で依存を表現。Hermes Gateway は State Cache の起動を待つ必要がある（MCP Server が State Cache JSON を読むため）。

```ini
# /etc/systemd/system/hermes-gateway.service の Unit セクションに追加
After=network-online.target warehouse-state-cache.service
Requires=warehouse-state-cache.service
```

---

## 9. トラブルシューティング

| 症状 | 切り分け | 対処 |
|------|---------|------|
| `hermes gateway` が即終了 | `journalctl -u hermes-gateway` で stack trace | 多くは `.env` の API キー欠落。`hermes config check` で診断 |
| `/v1/chat/completions` が 401 | `API_SERVER_KEY` 未設定 or LLM Bridge ヘッダ不一致 | `.env` の値と `Authorization: Bearer` の値を一致させる |
| MCP ツールが LLM から見えない | `hermes mcp list` で登録確認 | `mcp_servers.warehouse.command` のパス / `warehouse_mcp_server` モジュール解決を確認 |
| Hermes 応答が tools を呼ばない | system prompt にツール使用指示があるか / `tools.include` に対象ツールが入っているか | `08-llm-bridge-common.md` の system prompt 仕様を再確認 |
| Langfuse にトレースが出ない | `LANGFUSE_HOST` 到達性 / keys 正当性 / Hermes は失敗時サイレント | `curl $LANGFUSE_HOST/api/public/health` で疎通確認、`HERMES_LOG_LEVEL=debug hermes gateway` でフォアグラウンド再起動して確認 |
| `traffic_mode` 変更が反映されない | Warehouse MCP Server は Hermes の子プロセスで `config.yaml` を起動時 1 回だけ読込む（SIGHUP ホットリロード未実装） | `sudo systemctl restart hermes-gateway` で子プロセス再生成 |
| Jetson でメモリ不足 | `free -h` で確認。Hermes Gateway 単体 ≈ 100-200MB、+ MCP 子プロセス 50-100MB が目安 | ローカルLLMは非採用（`12-infrastructure-common.md §ローカルモデル`）。クラウドAPI 前提 |

---

## 10. セキュリティ・運用注意

`safety.md` 準拠:

- `~/.hermes/.env` を **コミット禁止**。プロジェクトリポジトリには `deploy/hermes/.env.example`（§3.1.1）のみ。`.gitignore` に `**/.env` を追加すること。
- `API_SERVER_HOST=127.0.0.1` を維持。外部公開しない。外部からの操作が必要な場合は SSH トンネルを使う。
- `API_SERVER_KEY` は最低 32 バイトのランダム値（`openssl rand -hex 32`）。
- Isaac Sim クラウドGPU（RunPod）へ Hermes を持ち出さない。クラウド側に LLM API キーを置かない。
- Hermes ログ (`~/.hermes/logs/`) は機密自動マスクされるが、共有時は再度確認。

---

## 11. Phase 別のマイルストーン

`06-implementation-phases.md` との対応:

| Phase | Hermes 関連の達成基準 |
|-------|--------------------|
| Phase 0 (機材調達・環境準備) | Jetson 上で `hermes --version` が通る。`.env.example` をリポジトリにコミット |
| Phase 0.5 (Gazebo シミュレーション) | `hermes gateway` 単独起動、`curl /v1/chat/completions` 成功、Anthropic 経路で応答取得。Hermes Gateway 単体のメモリ baseline 計測 |
| Phase 1 (ロボット1台セットアップ) | Warehouse MCP Server を Hermes 経由で呼出し成功、Policy Gate 拒否ケース確認 |
| Phase 2 (SLAM + Nav2) | Hermes 経由で `get_fleet_status` から 2 台の AMCL pose を取得確認 |
| Phase 3 (2台協調 + LLM Bridge) | `traffic_mode` を `none` / `simple` / `open-rmf` の3パターンで LLM Bridge → Hermes → MCP → 各 TrafficManager の往復確認 |
| Phase 4 (LLM比較検証) | `active_provider` 4社（Anthropic/OpenAI/Google/xAI）× `traffic_mode` 3種 = 12構成の Langfuse トレース + Command Audit Log 取得 |
| Phase 5 (Isaac Sim連携) | デジタルツイン側で Hermes Gateway を再現（同一 config.yaml）。Langfuse タグで `env=isaac_sim` / `env=real` を分離記録 |
| Phase 6 (撮影・編集・公開) | 撮影時の LLM 思考ログ（Langfuse）と制御ログ（Command Audit Log）を時刻同期で取得し、編集素材として export 可能であること |

---

## 12. References

### Hermes Agent 一次情報（2026-05-26 参照）

- [Hermes Agent — GitHub](https://github.com/NousResearch/hermes-agent)
- [Release v0.13.0 / v2026.5.7 — The Tenacity Release](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.5.7)
- [Hermes Agent Documentation](https://hermes-agent.nousresearch.com/docs/)
- [Configuration Guide](https://hermes-agent.nousresearch.com/docs/user-guide/configuration)
- [API Server (Gateway) Reference](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server)
- [MCP Integration](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)

### 関連標準・規格

- [OpenAI Chat Completions API](https://platform.openai.com/docs/api-reference/chat) — `/v1/chat/completions` 互換仕様
- [Model Context Protocol Specification (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [Langfuse Documentation](https://langfuse.com/docs)

### プロジェクト内関連

- [15-mcp-platform](15-mcp-platform.md) — Hermes / Warehouse MCP / Policy Gate / 競合状態の防止
- [12-infrastructure-common](12-infrastructure-common.md) — 共通基盤（Emergency / State Cache）
- [08-llm-bridge-common](08-llm-bridge-common.md) — system prompt・situation JSON
- [Mode A/B README](../mode-a/README.md) / [Mode C README](../mode-c/README.md)
- [06-implementation-phases](06-implementation-phases.md) — Phase 別計画
