# Mode A/B: LLM単独交通管理（Open-RMFなし）

## 概要

Claudeが戦略判断に加えて交通管理（衝突回避・経路選択・待機指示）も行うモード。Open-RMFを使用しない。

- **Mode A（none）**: 交通管理なし。Claudeが全判断を担当
- **Mode B（simple）**: 自作ルールベースの通路排他制御 + Claude

## プロセス構成

```
Emergency Guardian (50ms) → State Cache (100ms) → LLM Bridge (3秒)
    → Hermes Gateway → Warehouse MCP Server → Nav2 Bridge → Nav2 × 2
```

Open-RMFの代わりに**Nav2 Bridge**（rclpy + BasicNavigator）を使用。

## 切替方法

```yaml
# config.yaml
traffic_mode: "none"     # Mode A
# traffic_mode: "simple" # Mode B
```

## 動画的位置づけ（メイン回 = 「LLMでminicarを動かしてみた」の主役）

Mode A は **YouTube動画のメイン回**として位置づける。本プロジェクトの動画コンテンツはこちらが主役。

- 司令官LLM（1人）が**交通管理まで担当**するため、衝突回避・デッドロック解消の思考ログ（試行錯誤・葛藤）がそのまま映像化できる
- LLMの判断遅延（1-3秒）がそのまま物理挙動に出るため「AIが考えてる感」がドラマになる
- **キャラLLM（Bot1/Bot2）** が実況 + 重要シーンで**交渉**を行う。デッドロック時に「俺が譲るから先行って」と話し合って合意 → 司令官が承認 → 実機反映、という稟議制
- サイクル: **3秒**（応答2s + 待機1s）。司令官の反応速度を優先
- 排他制御は **グローバル1本**（司令官LLMは1人）— 詳細は [共通: LLM Bridge](../architecture/08-llm-bridge-common.md) の「同時発火制御」セクション参照
- gen_id 検証（MCP tool の required 引数として強制、B-3 方式）によるMCP層での古い指示破棄、twist_mux による cmd_vel 優先度制御は MCP プラットフォームとして実装 — 詳細は [15-mcp-platform](../architecture/15-mcp-platform.md) の「競合状態の防止」セクション

### キャラLLM + 交渉プロトコル

Mode A の見せ場の中核。詳細は [14-character-llm-negotiation](../architecture/14-character-llm-negotiation.md) 参照。

- キャラLLM は **Opus（最新世代）** で発話（実況モード。旧 Haiku 設計から全 Claude Opus 統一に変更、`../architecture/16-repository-and-conventions.md` §7。応答テンポは要実測）
- デッドロック時は司令官が `/negotiation/start` を発火 → Bot1/Bot2 が会話で合意 → 司令官承認 → Nav2実行
- キャラLLM は **Nav2/MCP に直接書き込めない**（書き込み権限なし、稟議制）

詳細な撮影シナリオ・速度設計は memory `project_video_entertainment` `project_character_llm` を参照。

## 関連ドキュメント

- [08a-llm-bridge-mode-a](08a-llm-bridge-mode-a.md) — situation JSON, system prompt, 5アクション定義
- [11a-traffic-mode-a](11a-traffic-mode-a.md) — NoTrafficManager / SimpleTrafficManager
- [12a-integration-mode-a](12a-integration-mode-a.md) — Nav2 Bridge, systemd構成, タイミング図
- [共通: LLM Bridge](../architecture/08-llm-bridge-common.md) — LLM Client IF, Langfuse, フォールバック
- [共通: 基盤](../architecture/12-infrastructure-common.md) — Emergency Guardian, State Cache, Emergency後同期
- [MCPプラットフォーム](../architecture/15-mcp-platform.md) — Hermes / Warehouse MCP / Policy Gate / 競合状態の防止
- [キャラLLM + 交渉プロトコル](../architecture/14-character-llm-negotiation.md) — Mode A メイン中核
