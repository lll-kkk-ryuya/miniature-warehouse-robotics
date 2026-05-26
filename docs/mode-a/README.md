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

## 関連ドキュメント

- [08a-llm-bridge-mode-a](08a-llm-bridge-mode-a.md) — situation JSON, system prompt, 6アクション定義
- [11a-traffic-mode-a](11a-traffic-mode-a.md) — NoTrafficManager / SimpleTrafficManager
- [12a-integration-mode-a](12a-integration-mode-a.md) — Nav2 Bridge, systemd構成, タイミング図
- [共通: LLM Bridge](../architecture/08-llm-bridge-common.md) — LLM Client IF, Langfuse, フォールバック
- [共通: インフラ](../architecture/12-infrastructure-common.md) — Emergency Guardian, State Cache, Policy Gate
