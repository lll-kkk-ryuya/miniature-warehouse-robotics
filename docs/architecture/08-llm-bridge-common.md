# LLM Bridge Node 技術設計 -- 共通設計

作成日: 2026-05-21
更新日: 2026-05-28

> **関連ドキュメント**:
> - [08a - LLM Bridge Mode A/B](../mode-a/08a-llm-bridge-mode-a.md) -- LLM単独交通管理
> - [08c - LLM Bridge Mode C](../mode-c/08c-llm-bridge-mode-c.md) -- LLM + Open-RMF
> - [12 - 共通基盤](12-infrastructure-common.md) -- Emergency Guardian, State Cache
> - [15 - MCPプラットフォーム](15-mcp-platform.md) -- Hermes Agent, Warehouse MCP Server, Policy Gate, 競合状態の防止
> - [14 - キャラLLM + 交渉プロトコル](14-character-llm-negotiation.md)
> - [11a - 交通管理 Mode A/B](../mode-a/11a-traffic-mode-a.md) | [11c - 交通管理 Mode C](../mode-c/11c-traffic-mode-c.md)

## 概要

LLM Bridge Node は、LLM API（Claude / ChatGPT / Gemini / Grok）と ROS 2 の間を仲介する ROS 2 ノード。ロボットの状態を構造化JSONにまとめてLLMに送り、LLMの判断をROS 2コマンドに変換する。

実装基盤としてHermes Agent Gateway + 自作 Warehouse MCP Serverを採用しており、実装方式の詳細は `15-mcp-platform.md` を参照。Nav2 MCP Server（ajtudela）は不採用（マルチロボット非対応・場所名非対応・モードCと不整合のため）。

### 前提条件

- Jetson Orin Nano がインターネットに接続されていること（LLM API呼出しに必要）
- WiFi環境: テザリング or ルーターで、ローカル通信（micro-ROS）とインターネット（LLM API）を同時利用
- Python 3.10+、rclpy、hermes-agent

## LLM Client インターフェース

LLMの差し替えを1行で行えるよう、共通インターフェースを定義する。

```python
from abc import ABC, abstractmethod

class LLMClient(ABC):
    """全LLM共通のインターフェース"""

    @abstractmethod
    def decide(self, situation: dict) -> dict:
        """状況JSONを受け取り、指示JSONを返す"""
        pass


class ClaudeClient(LLMClient):
    def decide(self, situation: dict) -> dict:
        response = self.client.messages.create(
            model="claude-sonnet-4",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(situation)}]
        )
        return json.loads(response.content[0].text)


class ChatGPTClient(LLMClient):
    def decide(self, situation: dict) -> dict:
        response = self.client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(situation)}
            ]
        )
        return json.loads(response.choices[0].message.content)


class GeminiClient(LLMClient):
    def decide(self, situation: dict) -> dict:
        response = self.client.models.generate_content(
            model="gemini-2.5-flash",  # 2026/10/16に非推奨化。後継: gemini-3.5-flash（5倍高価）or gemini-3.1-flash-lite（安価）
            contents=json.dumps(situation),
            config={"system_instruction": SYSTEM_PROMPT}
        )
        return json.loads(response.text)


class GrokClient(LLMClient):
    def decide(self, situation: dict) -> dict:
        # xAI API は OpenAI互換インターフェース
        response = self.client.chat.completions.create(
            model="grok-4.3",  # ※モデル名・価格は2026-05-23時点で未確定
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(situation)}
            ]
        )
        return json.loads(response.choices[0].message.content)
```

**注意**: 上記のクラス定義は設計意図の説明用。実装では Hermes Agent の内蔵プロバイダーで4社を切り替えるため、これらのクラスを自作する必要はない（`12-infrastructure-common.md` 参照）。

切り替え（Hermes Agent 使用時）:

```yaml
# hermes config — active_provider を変更するだけ
active_provider: anthropic   # Claude
# active_provider: openai    # ChatGPT
# active_provider: google    # Gemini
# active_provider: xai       # Grok
```

## 場所名→座標変換テーブル

```python
LOCATIONS = {
    "shelf_1":          {"x": 0.2, "y": 0.3},
    "shelf_2":          {"x": 0.7, "y": 0.3},
    "shelf_3":          {"x": 1.2, "y": 0.3},
    "berth_A":          {"x": 0.2, "y": 0.8},
    "berth_B":          {"x": 0.7, "y": 0.8},
    "shipping_station": {"x": 0.2, "y": 0.1},
    "charging_station": {"x": 1.2, "y": 0.1},
}
```

※座標はジオラマの実測後に確定する。

## サイクル設計

サイクルは**レスポンス駆動**: 前回の応答受信（またはタイムアウト）後に一定時間待機して次サイクルを発火する。固定間隔ポーリングではない。

### モード別サイクル長

| モード | 待機時間 | 総サイクル長 (応答2sの場合) | 理由 |
|---|---|---|---|
| **Mode A** | **1秒** | ~3秒 | 司令官が交通管理まで担当、反応速度を優先 |
| **Mode C** | **3秒** | ~5秒 | Open-RMFが即時調整、コスト最小化 |

待機時間の設計理由は「重なり防止」ではなく以下:
1. 状態変化粒度に合わせる（0.3m/s × 1〜3秒で意味ある状態差）
2. APIコストとレート制限
3. 動画的な思考ログ可読性
4. Phase 4 比較検証の公平性

重なり防止は A+B（HTTPキャンセル + MCP gen_id 検証）で別解決済。

### タイムアウトとフォールバック

- **サイクル内タイムアウト**: 2.5秒（応答が2.5秒以内に返らなければ前回の指示を継続し、次サイクルへ進む）
- **HTTP接続障害**: 5秒以上応答なしで API 障害判断、Nav2 単体での自律走行にフォールバック（下記フォールバック設計参照）

### タイムライン例（Mode A の場合）

```
t=0.0s  LLM Bridge Node: current_gen += 1 → gen_store に publish
        State Cache JSON読取 + emergency情報付加
t=0.1s  LLM Bridge Node: POST → Hermes Gateway（situation JSON に gen_id 同梱）
t=2.0s  応答受信（正常時）→ Warehouse MCP Server経由で実行 → 1秒後に次のサイクル（Mode A）
t=2.5s  応答未受信 → HTTPキャンセル → 前回指示を継続 → 1秒後に次のサイクル
        ↑ Hermes セッション中断、後続 tool call の発火を止める
        ↑ 既に発火済 tool call は MCP の gen_id 検証で reject される
t=3.0s  次サイクル開始（Mode A）
```

Mode C の場合は最終行が `t=5.0s` になる。

## 同時発火制御（A+B: HTTPキャンセル + MCP側gen_id検証）

本プロジェクトのサイクルは上記の通り**レスポンス駆動**（前回の応答受信後に3秒カウント開始）であり、固定間隔ポーリングではない。したがって正常系では「LLM呼び出し中に次サイクルが発火する」競合は原理的に発生しない（Single-flight / Coalescing は不要）。

問題は **Hermes が LLM ↔ MCP の往復を内部で持つこと**にある。LLM の tool call は **Warehouse MCP Server で即時実行され、その時点で Nav2 等にコマンドが発行される**。Bridge が Hermes の最終応答を受け取った後に検証してももう手遅れである。よって対策は2層で行う:

| 層 | 役割 |
|---|---|
| **A. HTTPキャンセル** | Bridge タイムアウト（2.5s）時に Hermes への HTTP リクエストを切断し、進行中の LLM セッションを中断する。**後続の tool call の発火を止める** |
| **B. MCP Server gen_id 検証（B-3 方式）** | Bridge が cycle 開始時に `current_gen` を共有ストレージに書き込み、situation JSON に同値を含めて LLM に渡す。**Warehouse MCP の全ツール定義に `gen_id: int` を required 引数として追加**し、LLM に「gen_id を必ず引数に含めよ」と system prompt で指示。MCP は tool 呼び出し時に `args.gen_id` と共有ストレージの `current_gen` を比較し、古いなら reject |

A 単独では「タイムアウト直前に始まり、その後完了する tool call」を防げない。B 単独では「Hermes セッションがゾンビ的に LLM を呼び続け、tool call を連発する」事態を防げない。**両方必要**。

### B案を B-3 にした理由（過去の訂正）

当初は HTTP ヘッダ `X-Bridge-Gen` を Hermes 経由で MCP に転送する方式を検討したが、**Hermes Gateway は OpenAI 互換 API を使用しており、クライアントの HTTP ヘッダが内部の MCP tool 呼び出しに転送される保証がない**。よって、より堅牢な「**tool schema の required 引数として gen_id を強制**」方式（B-3）を採用する。JSON Schema 検証で LLM の出力漏れを検出できるため信頼性が高い。詳細は `15-mcp-platform.md` の MCP Server gen_id 検証セクション参照。

### Mode A / Mode C への適用

| モード | コマンド頻度 | 本対策の重要度 |
|---|---|---|
| **Mode A** | 毎サイクル必ず（交通管理まで担当）| **必須**。遅延応答がデッドロック誘発しうる |
| **Mode C** | エスカレーション時のみ | A は推奨、B は必須（沈黙時に古いescalation応答が漏れて来うる）|

実装は両モード共通（MCPプラットフォーム層）。詳細は `15-mcp-platform.md` の「MCP Server gen_id 検証」を参照。

### 実装パターン（Bridge 側）

```python
class BridgeScheduler:
    def __init__(self, gen_store, cycle_wait_sec):
        self.current_gen = 0
        self.gen_store = gen_store      # MCP Server と共有（Redis / file / ROS param）
        self.cycle_wait_sec = cycle_wait_sec  # Mode A: 1.0, Mode C: 3.0

    async def run_cycle(self):
        """前回の応答受信後（またはタイムアウト後）に呼ばれる"""
        self.current_gen += 1
        gen = self.current_gen
        await self.gen_store.set(gen)         # B: MCP Server から読める場所に公開
        snapshot = build_situation()
        snapshot["gen_id"] = gen              # B-3: situation JSON にも明示
        try:
            # A: タイムアウト時に内部タスクごとキャンセル（shieldなし）
            response = await asyncio.wait_for(
                call_hermes(snapshot),
                timeout=2.5,
            )
            apply(response)
        except asyncio.TimeoutError:
            log.warn(f"cycle timeout gen={gen}, HTTP cancelled, continue previous command")
            # wait_for が内部タスクを cancel → httpx 接続クローズ → Hermes セッション中断
            # ただし Hermes 内で既に発火済の tool call は MCP の gen_id 検証で reject される（B-3）
        await asyncio.sleep(self.cycle_wait_sec)
```

System prompt には以下を追加する:

```
状況JSON の `gen_id` フィールドを、すべての tool 呼び出しの `gen_id` 引数に必ずそのまま含めてください。これは安全機構です。
```

### 設計判断の要点

| 項目 | 採用 | 理由 |
|---|---|---|
| Single-flight / Coalescing | **不採用** | レスポンス駆動サイクルなので原理的に並列発火が起きない |
| A. HTTPキャンセル（タイムアウト時） | **採用** | Hermes セッションを中断、後続 tool call の発火を止める |
| B-3. MCP Server gen_id 検証（tool schema required引数）| **採用** | 既に発火済の tool call が遅れて MCP に届くケースを止める唯一の手段。JSON Schema 検証で LLM の出力漏れも検出 |
| Priority Queue | **不採用** | YAGNI |
| イベント駆動エスカレーション | **不採用** | 次サイクルに `escalation` フィールドで投入する方式で十分（最大3秒遅延を許容） |

> **注**: 以前「Cancel & Restart 不採用」と書いていたのは「ユーザー入力で能動的に割り込む」想定の話。今回の「タイムアウト時の防御的キャンセル」は意味が違うため採用する。Langfuse trace に `cancelled` ステータスが混じるが、Phase 4 比較検証では集計から除外することで対応。

### ロックの粒度

司令官LLMは1人で2台分の指示を1呼び出しに含めるため、**Mode A / Mode C 共にグローバル1本のサイクルループ**を持つ（Bot単位ループは持たない）。会話演出のキャラLLMレイヤを追加する場合は、そのレイヤは司令塔とは別プロセス・別サイクルループで動かす。

なお、本セクションの仕組みはフォールバック設計表の「古い状況に基づく指示」項目に対応する実装手段である。

## 緊急状態の検出と対応

緊急事態の物理的安全はNav2（50ms）とOpen-RMF（即時）が担保する。Claudeは1-3秒の応答遅延があるため、緊急時の即時対応はできない。

| 緊急条件 | 即時対応（Nav2/Open-RMF） | Claudeへの通知 |
|---------|--------------------------|--------------|
| 2台が0.3m以内に接近 | Emergency Guardian（50ms周期）→ Nav2 cancel + cmd_vel停止 | `/emergency/event` 経由で次サイクルに付加 |
| ロボットがblocked > 10秒 | Emergency Guardian → Nav2リカバリー要求 | `/emergency/event` 経由で次サイクルに付加 |
| バッテリー < 10% | Emergency Guardian → Nav2 cancel + cmd_vel停止 | `/emergency/event` 経由で次サイクルに付加 |
| バッテリー 10-20% | — | 次サイクルでbattery報告 → Claude判断 |

**Emergency Guardian（50ms周期、LLM非経由）が安全を担保する。** LLM Bridge Nodeのサイクル（Mode A: 3秒 / Mode C: 5秒）は戦略判断用であり、安全機能ではない。Emergency Guardianが発行した `/emergency/event` は State Cache Node経由でLLM Bridge Nodeに伝達され、次回のHermes Gateway POSTに緊急情報として付加される。詳細は `12-infrastructure-common.md` の安全レイヤー設計を参照。

## コマンドバリデーション（Policy Gate）

Claudeの指示はWarehouse MCP Server内の **Policy Gate** で検証される（`15-mcp-platform.md` 参照）。以下は論理的なチェック項目:

| チェック項目 | 動作 |
|------------|------|
| 場所名存在チェック | 不明な場所名は拒否 |
| 同一目的地に2台を送っていないか | 重複タスクを拒否 |
| ロボット状態チェック（stale/unavailable） | stale時はdispatch拒否、cancel/chargingは許可 |
| バッテリーポリシー（< 10%全拒否、< 20%新規タスク禁止） | Policy Gateが強制 |
| Emergency中のロボットへの指示 | 拒否 |
| レートリミット | 短時間の連続コマンドを拒否 |

## フォールバック設計

| 異常 | 検出方法 | 動作 |
|------|---------|------|
| LLM API サイクル内タイムアウト（2.5秒） | レスポンス待ち時間 | 前回の指示を継続、次サイクルへ |
| LLM API 接続障害（5秒超応答なし） | HTTP接続タイムアウト | Nav2単体で自律走行を継続 |
| LLM API エラー（500等） | HTTPステータス | Nav2単体で自律走行を継続 |
| 不正なJSON返答 | json.loads失敗 | 無視して次回リクエスト |
| 未知のaction | action名チェック | 無視してログ記録 |
| 存在しない場所名 | LOCATIONS辞書チェック | 無視してログ記録 |
| 物理的に不可能な移動 | Nav2が拒否 | Nav2の安全機構に委任 |
| インターネット切断 | 接続チェック | Nav2フォールバックモードに移行 |
| 同一目的地への2台送信 | Policy Gate（Warehouse MCP Server内） | タスク拒否 |
| 古い状況に基づく指示 | A. HTTPキャンセル + B-3. MCP tool schema required gen_id | 古い tool call を MCP 層で reject、次サイクルで再判断（「同時発火制御」セクション参照） |

## 比較検証ログ（Langfuse統合）

比較検証のログ記録には Langfuse（LLMオブザーバビリティプラットフォーム）を使用する。Hermes Agent のビルトインプラグインとして標準搭載されており、環境変数の設定のみで全LLM呼出しが自動記録される。

### Langfuse 設定

```bash
export HERMES_LANGFUSE_PUBLIC_KEY=pk-lf-xxx
export HERMES_LANGFUSE_SECRET_KEY=sk-lf-xxx
export HERMES_LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

Hermes Agent が自動的にLangfuseに送信する内容（コード変更不要）:
- 1 trace / ターン（3秒サイクルの1回分）
- 1 generation / LLM API呼出し（入力JSON、出力JSON、トークン数、レイテンシ、コスト）
- 1 observation / ツール呼出し（navigate_to_pose等の引数と結果）

Langfuseはfail-open設計: SDK未インストール・認証エラー・通信障害時はサイレントにスキップし、エージェントループに影響しない。

### DecisionLog とLangfuseの対応

```python
@dataclass
class DecisionLog:
    turn: int                    # → Langfuse trace name
    timestamp: str               # → Langfuse 自動記録
    llm_name: str                # → Langfuse provider/model
    situation: dict              # → Langfuse generation input
    response: dict               # → Langfuse generation output
    response_time_ms: int        # → Langfuse latency（自動）
    reasoning: str               # → Langfuse output 内の reasoning フィールド
    commands: list               # → Langfuse output 内の commands フィールド
    result: str                  # → Langfuse score（自作で追加）
    task_completion_time: float  # → Langfuse score（自作で追加）
```

上記のうち `result` と `task_completion_time` のみ自作コードで Langfuse に送信する:

```python
# Nav2ゴール到達後にスコアを送信
langfuse.score(trace_id=current_trace_id, name="result", value="success")
langfuse.score(trace_id=current_trace_id, name="task_completion_time", value=29.3)
```

これにより自作の DecisionLog ファイル出力は不要となり、全データが Langfuse Dashboard で閲覧・比較・分析できる。

### セッション命名規則

```python
session_name = f"demo_{llm_name}_{scenario}_{datetime}"
# 例: demo_claude_deadlock_20260715
```

同一シナリオの4社比較がLangfuse上でフィルタ・比較可能。

### 比較指標

| 指標 | Langfuseでの取得方法 | 重要度 |
|------|---------------------|--------|
| 応答速度 | generation.latency（自動） | 高 |
| 判断の正確性 | score "result"（自作送信） | 高 |
| タスク完了時間 | score "task_completion_time"（自作送信） | 高 |
| 効率性 | ロボットの総移動距離（自作計算→score送信） | 中 |
| 推論の質 | generation.output.reasoning（自動、人間評価） | 中 |
| コスト | generation.cost（自動計算） | 低 |
| エラー率 | generation.error（自動検出） | 高 |

### デプロイ方針

| フェーズ | Langfuse環境 | 理由 |
|---------|-------------|------|
| Phase 0-6（本プロジェクト） | Langfuse Cloud（無料、50K obs/月） | 導入30秒、規模十分（約500 obs/デモ） |
| 将来のPhysical AI展開 | Langfuse セルフホスト（Docker） | データ外部送信不可の環境向け |

移行時の変更: `HERMES_LANGFUSE_BASE_URL` を自社サーバーに変更するのみ。

## コスト見積もり

10分デモあたりの司令官LLM呼出回数（モード別）:

| モード | サイクル | 呼出回数/10分 |
|---|---|---|
| Mode A | ~3秒 | 約200回 |
| Mode C | ~5秒 | 約120回 |

### トークン消費の前提

| 項目 | 推定値 |
|---|---|
| System prompt | 約550 tokens |
| Situation JSON（2台分・gen_id・履歴含む） | 約1000 tokens |
| MCP tool 定義（7ツール、gen_id含む） | 約550 tokens |
| **入力合計 / call** | **約2000 tokens** |
| 出力（reasoning + commands JSON） | 約200 tokens |

### コスト推定（実測前の暫定値）

| LLM | モデル | 入力単価 | 出力単価 | Mode A 1デモ (~200回) | Mode C 1デモ (~120回) |
|---|---|---|---|---|---|
| Claude | Sonnet 4 | $3/MTok | $15/MTok | **~$1.80** | **~$1.08** |
| ChatGPT | GPT-4o | $2.50/MTok | $10/MTok | ~$1.40 | ~$0.84 |
| Gemini | 2.5 Flash | $0.30/MTok | $2.50/MTok | ~$0.22 | ~$0.13 |
| Grok | 4.3 | 未確定 | 未確定 | ~$1.50（推定） | ~$0.90 |

4社合計: **Mode A ~$5/デモ、Mode C ~$3/デモ**（実測前の暫定推定値）。

> ⚠️ **注意**: 以前は1呼出あたり ~600 tokens で試算していたが、MCP tool 定義 (約550 tokens) と gen_id 機構の追加で実質約 2000 tokens に増えた。Phase 0.5 (Gazebo) で実測してから本数値を確定する。Phase 4 の 4社比較本番では **$20-30 / 1セット (4社) の予算枠**を確保しておくこと。

別途、キャラLLM（Haiku、Bot1/Bot2）のコストは Sonnet 4 の約1/10で別計上（Phase 4 比較対象外）。詳細は `15-mcp-platform.md` 参照。

**注意**: Gemini 2.5 Flash は**2026年10月16日**に非推奨化（2026-05-26調査で確認。当初「6月」としていたのは gemini-2.0-flash の話）。後継は `gemini-3.5-flash`（$1.50/$9.00/MTok、約5倍高価）。安価代替として `gemini-3.1-flash-lite`（$0.25/$1.50/MTok）が利用可能。移行はモデル名1行変更のみ。Phase 4まで2.5-flashで問題なし。

※緊急時の安全対応はEmergency Guardian（50ms周期、LLM非経由）が担当し、LLM Bridgeのサイクル長（Mode A: 3秒 / Mode C: 5秒）は変動しない。LLM呼出し回数への影響はない。

## ROS 2 トピック

### 購読（入力）

| トピック | 型 | 用途 |
|---------|-----|------|
| `/bot{n}/odom` | `nav_msgs/Odometry` | オドメトリ（移動量・速度） |
| `/bot{n}/amcl_pose` | `geometry_msgs/PoseWithCovarianceStamped` | AMCL推定位置（地図上の位置） |
| `/bot{n}/scan` | `sensor_msgs/LaserScan` | 障害物検知 |
| `/bot{n}/battery` | `sensor_msgs/BatteryState` | バッテリー残量 |

### 発行（出力）

| トピック | 型 | 用途 |
|---------|-----|------|
| `/bot{n}/goal_pose` | `geometry_msgs/PoseStamped` | Nav2ゴール送信（※doc12設計ではWarehouse MCP Server/Fleet Adapterが発行。LLM Bridge Nodeは直接発行しない） |
| `/bot{n}/cmd_vel` | `geometry_msgs/Twist` | 停止指令（速度0）（※doc12設計ではEmergency Guardianが発行） |
| `/llm/reasoning` | `std_msgs/String` | LLM判断理由（表示用） |
| `/llm/command` | `std_msgs/String` | LLM指示JSON（ログ用） |

## 開発順序

1. **Phase 0.5（Gazebo）**: Hermes Gateway + Warehouse MCP Server + Claude でGazebo上テスト
2. **Phase 3（実機）**: Gazebo版をベースに実機用に微調整
3. **Phase 4（比較）**: ChatGPT / Gemini / Grok Provider を追加、ログ記録・分析

## Python依存パッケージ

```
# requirements.txt
hermes-agent>=0.14.0
rclpy
```

注: LLM SDK（anthropic, openai, google-genai, xai）は hermes-agent の依存関係として自動インストールされる。

## References

- [Anthropic API Documentation](https://docs.anthropic.com/) -- 参照日: 2026-05-21
- [OpenAI API Reference](https://platform.openai.com/docs/api-reference) -- 参照日: 2026-05-21
- [Google AI Gemini API](https://ai.google.dev/gemini-api/docs) -- 参照日: 2026-05-21
- [rclpy -- ROS 2 Python Client Library](https://docs.ros2.org/latest/api/rclpy/) -- 参照日: 2026-05-21
- [Langfuse -- 公式サイト](https://langfuse.com/) -- 参照日: 2026-05-23
- [Langfuse -- GitHub](https://github.com/langfuse/langfuse) -- 参照日: 2026-05-23
- [Hermes Agent -- Built-in Plugins (Langfuse)](https://hermes-agent.nousresearch.com/docs/user-guide/features/built-in-plugins) -- 参照日: 2026-05-23
