# config/ — プロジェクト共通設定

ROS 2 パッケージ非依存の設定。`.claude/rules/safety.md` に従い、APIキー/認証情報/WiFiパスワードは含めない（`.env` 経由）。

| ファイル(予定) | 内容 | 設計ドキュメント |
|--------------|------|-----------------|
| `config.yaml` | traffic_mode / robots / locations / hermes / nav2_bridge / rmf | `docs/architecture/13-hermes-setup.md §3.3` |
| `hermes.config.yaml` | Hermes Gateway 設定（Provider/MCP/Langfuse） | `docs/architecture/13-hermes-setup.md` |

> `locations` は `08-llm-bridge-common.md` の LOCATIONS テーブルとキーを一致させること（Policy Gate 検証用）。
