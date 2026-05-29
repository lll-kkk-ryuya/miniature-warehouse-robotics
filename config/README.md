# config/ — 倉庫設定（環境分離 dev/stg/prod）

ROS 2 パッケージ非依存の設定。**軸A（デプロイ環境の分離）**で、`WAREHOUSE_ENV`（dev|stg|prod、既定 dev）により切り替える。設計の正本: [`docs/architecture/18-environments-and-config.md`](../docs/architecture/18-environments-and-config.md)。運用ルール: `.claude/rules/environments.md`。

```
config/
├── warehouse.base.yaml   # 全環境共通の土台
├── dev/  warehouse.yaml + .env.example   # Mac Docker / Gazebo sim
├── stg/  warehouse.yaml + .env.example   # クラウド統合（RunPod Isaac / staging GCP）
└── prod/ warehouse.yaml + .env.example   # Jetson 実機 + 本番 GCP Hermes
```

- **解決順**: `warehouse.base.yaml` → `config/$WAREHOUSE_ENV/warehouse.yaml` → 環境変数（後勝ち）。
- **Secrets**: 実体 `config/<env>/.env` は **gitignore・コミット厳禁**（`.claude/rules/safety.md`）。追跡するのは `.env.example` のみ。dev/stg/prod で別キーを使う。
- `warehouse.yaml` のスキーマ正本は `docs/architecture/13-hermes-setup.md §3.3`。`locations` は `08-llm-bridge-common.md` の LOCATIONS とキーを一致させる（Policy Gate 検証用）。
- Hermes Gateway 本体の設定（`config.yaml`/`SOUL.md`）と per-env CD は `deploy/hermes/`（hermes トラック）の領域。本 config からは接続先 URL/トークンを環境別に指すのみ。
