# LLM Bridge Node 技術設計 -- 共通設計

作成日: 2026-05-21
更新日: 2026-05-25

> **関連ドキュメント**:
> - [08a - LLM Bridge Mode A/B](../mode-a/08a-llm-bridge-mode-a.md) -- LLM単独交通管理
> - [08c - LLM Bridge Mode C](../mode-c/08c-llm-bridge-mode-c.md) -- LLM + Open-RMF
> - [12 - 共通インフラ](12-infrastructure-common.md) -- Emergency Guardian, State Cache, Policy Gate等
> - [11a - 交通管理 Mode A/B](../mode-a/11a-traffic-mode-a.md) | [11c - 交通管理 Mode C](../mode-c/11c-traffic-mode-c.md)

## 概要

LLM Bridge Node は、LLM API（Claude / ChatGPT / Gemini / Grok）と ROS 2 の間を仲介する ROS 2 ノード。ロボットの状態を構造化JSONにまとめてLLMに送り、LLMの判断をROS 2コマンドに変換する。

実装基盤としてHermes Agent Gateway + 自作 Warehouse MCP Serverを採用しており、実装方式の詳細は `12-infrastructure-common.md` を参照。Nav2 MCP Server（ajtudela）は不採用（マルチロボット非対応・場所名非対応・モードCと不整合のため）。

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

通常は3秒間隔でClaudeに状況を送信し、戦略判断を受け取る。

**重要**: Claudeの応答時間は1-3秒のため、サイクルは「前回の応答受信後に3秒カウント開始」とする（固定間隔ではない）。サイクル内タイムアウトは2.5秒（応答が2.5秒以内に返らなければ前回の指示を継続し、次サイクルへ進む）。これとは別に、HTTP接続自体が5秒以上応答しない場合はAPI障害と判断し、Nav2単体での自律走行にフォールバックする（下記フォールバック設計を参照）。

```
t=0.0s  LLM Bridge Node: State Cache JSON読取 + emergency情報付加
t=0.1s  LLM Bridge Node: POST → Hermes Gateway
t=1.5s  応答受信（正常時）→ Warehouse MCP Server経由で実行 → 3秒後に次のサイクル
t=2.5s  応答未受信 → タイムアウト → 前回指示を継続 → 3秒後に次のサイクル
```

## 緊急状態の検出と対応

緊急事態の物理的安全はNav2（50ms）とOpen-RMF（即時）が担保する。Claudeは1-3秒の応答遅延があるため、緊急時の即時対応はできない。

| 緊急条件 | 即時対応（Nav2/Open-RMF） | Claudeへの通知 |
|---------|--------------------------|--------------|
| 2台が0.3m以内に接近 | Emergency Guardian（50ms周期）→ Nav2 cancel + cmd_vel停止 | `/emergency/event` 経由で次サイクルに付加 |
| ロボットがblocked > 10秒 | Emergency Guardian → Nav2リカバリー要求 | `/emergency/event` 経由で次サイクルに付加 |
| バッテリー < 10% | Emergency Guardian → Nav2 cancel + cmd_vel停止 | `/emergency/event` 経由で次サイクルに付加 |
| バッテリー 10-20% | — | 次サイクルでbattery報告 → Claude判断 |

**Emergency Guardian（50ms周期、LLM非経由）が安全を担保する。** LLM Bridge Nodeの3秒タイマーは戦略判断用であり、安全機能ではない。Emergency Guardianが発行した `/emergency/event` は State Cache Node経由でLLM Bridge Nodeに伝達され、次回のHermes Gateway POSTに緊急情報として付加される。詳細は `12-infrastructure-common.md` の安全レイヤー設計を参照。

## コマンドバリデーション（Policy Gate）

Claudeの指示はWarehouse MCP Server内の **Policy Gate** で検証される（`12-infrastructure-common.md` 参照）。以下は論理的なチェック項目:

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
| 古い状況に基づく指示 | 位置差分チェック | 指示を破棄、次サイクルで再判断 |

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
| Phase 0-6（本プロジェクト） | Langfuse Cloud（無料、50K obs/月） | 導入30秒、規模十分（800 obs/デモ） |
| 将来のPhysical AI展開 | Langfuse セルフホスト（Docker） | データ外部送信不可の環境向け |

移行時の変更: `HERMES_LANGFUSE_BASE_URL` を自社サーバーに変更するのみ。

## コスト見積もり

10分デモ、3秒間隔 = 約200回呼び出し:

| LLM | モデル | 入力単価 | 出力単価 | 200回の推定コスト |
|---|---|---|---|---|
| Claude | Sonnet 4 | $3/MTok | $15/MTok | ~$0.45 |
| ChatGPT | GPT-4o | $2.50/MTok | $10/MTok | ~$0.35 |
| Gemini | 2.5 Flash | $0.30/MTok | $2.50/MTok | ~$0.20 |
| Grok | 4.3 | 未確定 | 未確定 | ~$0.40（※推定） |

全4社合計で約$1.40/デモ（Grok価格は推定値）。Warehouse MCP Server（6ツール、約500トークン/ターン）のオーバーヘッド込み。詳細は `12-infrastructure-common.md` 参照。

**注意**: Gemini 2.5 Flash は**2026年10月16日**に非推奨化（2026-05-26調査で確認。当初「6月」としていたのは gemini-2.0-flash の話）。後継は `gemini-3.5-flash`（$1.50/$9.00/MTok、約5倍高価）。安価代替として `gemini-3.1-flash-lite`（$0.25/$1.50/MTok）が利用可能。移行はモデル名1行変更のみ。Phase 4まで2.5-flashで問題なし。

※緊急時の安全対応はEmergency Guardian（50ms周期、LLM非経由）が担当し、LLM Bridgeの3秒タイマーは変動しない。LLM呼出し回数への影響はない。

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
