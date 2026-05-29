# Mode C: LLM + Open-RMF（主方針）

## 概要

交通管理をOpen-RMFが即時処理し、Claude（LLM）はタスク割当・優先順位・バッテリー管理の戦略判断のみを行うモード。**本プロジェクトの主方針。**

## プロセス構成

```
Emergency Guardian (50ms) → State Cache (100ms) → LLM Bridge (3秒)
    → Hermes Gateway → Warehouse MCP Server → Open-RMF → Fleet Adapter → Nav2 × 2
```

## 切替方法

```yaml
# config.yaml
traffic_mode: "open-rmf"  # Mode C
```

## 動画的位置づけ（実用検証回 = 「Open-RMFというチートを使ったらどうなるか」）

Mode C は **YouTube動画の実用検証回**として位置づける。Mode A（メイン）の対比として「**プロ用ツール Open-RMF を使えば LLM 最小限でこんなに上手く動く**」という実用性提示。

- LLM（司令官1人）はタスク割当・優先順位のみ判断。**交通管理は Open-RMF が即時処理**
- 正常系では `escalation: null` のため Claude はほとんど沈黙。「**LLM出番なし＝全自動解決**」をテロップで強調する演出
- 1回だけ意図的に過負荷をかけて Open-RMF が解決できないエスカレーション → Claude 登場、で「Open-RMF にも限界がある瞬間」をクライマックスとして見せる
- サイクル: **5秒**（応答2s + 待機3s）。Open-RMFが即応するためLLMは寡黙でOK、コスト最小化
- キャラLLM は実況中心（司令官沈黙時間が長いので、キャラが間を埋める）。交渉モードは Open-RMF エスカレーション時のみ発動
- 排他制御は **グローバル1本**（司令官1人のため）— 詳細は [共通: LLM Bridge](../architecture/08-llm-bridge-common.md) の「同時発火制御」セクション参照
- gen_id 検証（MCP tool の required 引数として強制、B-3 方式）、twist_mux 等の安全機構は MCP プラットフォームとして実装 — [15-mcp-platform](../architecture/15-mcp-platform.md) 参照

### キャラLLM の役割（実況中心）

詳細は [14-character-llm-negotiation](../architecture/14-character-llm-negotiation.md) 参照。Mode C ではキャラLLMは実況に徹し、Open-RMF が解決できないエスカレーション時のみ交渉が発動する（稀少だがクライマックス）。

詳細な動画構成方針は memory `project_video_entertainment` `project_mode_positioning` `project_character_llm` を参照。

## 関連ドキュメント

- [08c-llm-bridge-mode-c](08c-llm-bridge-mode-c.md) — situation JSON, system prompt, 3アクション定義
- [11c-traffic-mode-c](11c-traffic-mode-c.md) — RMFTrafficManager, Open-RMF要件
- [12c-integration-mode-c](12c-integration-mode-c.md) — Fleet Adapter, systemd構成, タイミング図
- [共通: LLM Bridge](../architecture/08-llm-bridge-common.md) — LLM Client IF, Langfuse, フォールバック
- [共通: 基盤](../architecture/12-infrastructure-common.md) — Emergency Guardian, State Cache, Emergency後同期
- [MCPプラットフォーム](../architecture/15-mcp-platform.md) — Hermes / Warehouse MCP / Policy Gate / 競合状態の防止
- [キャラLLM + 交渉プロトコル](../architecture/14-character-llm-negotiation.md) — Mode C ではクライマックス時のみ発動
