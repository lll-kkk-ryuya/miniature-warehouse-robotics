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

## 関連ドキュメント

- [08c-llm-bridge-mode-c](08c-llm-bridge-mode-c.md) — situation JSON, system prompt, 3アクション定義
- [11c-traffic-mode-c](11c-traffic-mode-c.md) — RMFTrafficManager, Open-RMF要件
- [12c-integration-mode-c](12c-integration-mode-c.md) — Fleet Adapter, systemd構成, タイミング図
- [共通: LLM Bridge](../architecture/08-llm-bridge-common.md) — LLM Client IF, Langfuse, フォールバック
- [共通: インフラ](../architecture/12-infrastructure-common.md) — Emergency Guardian, State Cache, Policy Gate
