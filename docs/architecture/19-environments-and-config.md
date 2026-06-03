# 環境分離と設定（dev / stg / prod）

作成日: 2026-05-29

> **方式**: 軸A「デプロイ環境の分離」。**コードは1本**のまま、環境ごとに **config / secrets / 接続先** を切り替える。
> git は `main` + feature ブランチ運用のまま（環境用の長命ブランチは作らない）。prod リリースは **git タグ**（`v0.x`）で固定する。

> **関連**: [16 §4/§5 共有パス・config単一ソース](16-repository-and-conventions.md) / [13 Hermes セットアップ](13-hermes-setup.md) / `.claude/rules/environments.md` / `.claude/rules/safety.md`

---

## 1. 環境一覧

| 環境 | sim / 実機 | LLM / Hermes 接続先 | runtime dir | 用途 |
|---|---|---|---|---|
| **dev** | Mac Docker（Gazebo Harmonic） | ローカル or dev GCP Hermes | `/tmp/warehouse/` | 日常開発・偽トピックE2E・sim |
| **stg** | クラウド統合（RunPod Isaac / cloud sim） | staging GCP Hermes | `/tmp/warehouse/` | 統合検証・撮影前リハ |
| **prod** | Jetson 実機 + 実ロボット2台 | 本番 GCP Hermes（`34.4.104.112`） | `/run/warehouse/`（systemd `RuntimeDirectory`） | 本番デモ撮影 |

- 既定は **dev**。実機・本番接続は **prod を明示選択した時のみ**。
- prod は安全機構（Emergency Guardian / 速度上限 0.3 m/s）テスト通過後にのみ使用（`.claude/rules/safety.md`）。

---

## 2. 環境の選択：`WAREHOUSE_ENV`

```bash
export WAREHOUSE_ENV=dev   # dev（既定）| stg | prod
```

- 全ノード／スクリプトはこの変数で環境を決定する。**未設定時は dev** にフォールバック。
- 環境名はコードにハードコードしない。接続先・パス・モードはすべて config から読む。

---

## 3. 設定レイアウト（base + overlay）

```
config/
├── warehouse.base.yaml      # 全環境共通のデフォルト（唯一の真実の土台）
├── dev/
│   ├── warehouse.yaml       # dev 差分のみ（base を上書き）
│   └── .env.example         # dev secrets テンプレ（実体 .env は gitignore）
├── stg/
│   ├── warehouse.yaml
│   └── .env.example
└── prod/
    ├── warehouse.yaml
    └── .env.example
```

**設定解決順（後勝ち）**: `warehouse.base.yaml` → `config/$WAREHOUSE_ENV/warehouse.yaml` → 環境変数。

- 共通値（locations のキー、robots 構成、サイクル長の既定、速度上限）は **base に一元化**。環境差分（sim/実機、Hermes 接続先、runtime dir、traffic_mode 既定）だけを `<env>/warehouse.yaml` に書く。
- 解決ロジックは `warehouse_interfaces` の設定ローダ（`load_config(env)`）に実装し、全ノードが同じ経路で読む（doc16 §4 の `WAREHOUSE_CONFIG_PATH` は本スキームに置換）。
- **用語の対応**: mode-a / mode-c / shared の各ドキュメントが言う「`config.yaml`」「config.yaml の1行変更でモード切替」は、**本スキームの倉庫 config**（`warehouse.base.yaml` + `config/<env>/warehouse.yaml`）を指す。`traffic_mode` 等のキー名・1行切替の概念は不変（大量リネームは行わない）。
- `locations` のキーは `08-llm-bridge-common.md` の LOCATIONS と完全一致させる（Policy Gate の known_locations 検証用）。

---

## 4. Secrets（APIキー・WiFi・認証情報）

- 各環境の実体は **`config/<env>/.env`**（`**/.env` で **gitignore**。コミット厳禁＝`.claude/rules/safety.md`）。
- リポジトリに置くのは **`.env.example`（プレースホルダのみ）** だけ。
- 環境ごとに別キーを使う（dev/stg/prod で API キー・Hermes トークンを分離し、本番事故・課金混在を防ぐ）。

### 4.1 Secrets は2ファイルに分かれる（重要）

LLM Bridge は各社 API を**直接叩かず Hermes Gateway（OpenAI 互換 `…/v1/chat/completions`）経由**で呼ぶ（doc13 §5.1）。したがってキーの置き場所は**消費プロセスで2つに分かれる**。混同すると Bridge が 401 で動かない。

| ファイル | 消費プロセス | 置くキー | 正本 |
|---|---|---|---|
| **`~/.hermes/.env`** | Hermes Gateway（プロバイダを直接呼ぶ） | `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`（または `GOOGLE_API_KEY`）/ `XAI_API_KEY`、`API_SERVER_*`、`LANGFUSE_*`（任意・Hermes 所有トレース時） | doc13 §3.1 |
| **`config/<env>/.env`** | ROS プロセス（LLM Bridge Node / Orchestrator） | `API_SERVER_KEY`（Bridge→Hermes 認証。`~/.hermes/.env` と**同一値**）、Langfuse 観測キー | 本書 |

- **`API_SERVER_KEY` の同一性が必須**: `config/<env>/.env` と `~/.hermes/.env` で値が一致しないと Bridge は Hermes に認証できない（LLM Bridge Node は `API_SERVER_KEY` / `HERMES_API_KEY` を読み `Authorization: Bearer` で送る）。生成は `openssl rand -hex 32`。
- **プロバイダ3社キーは `config/<env>/.env` に置かない**（ROS 側は消費しない）。`~/.hermes/.env`（dev ローカル）または Gateway VM 上の `~/.hermes/.env`（stg/prod の GCP Gateway）に置く。
- **Langfuse のキー名は消費側で異なる**（観測）: Orchestrator の `langfuse_sink` は `HERMES_LANGFUSE_PUBLIC_KEY` / `HERMES_LANGFUSE_SECRET_KEY`、Bridge の `langfuse.openai` SDK は `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` を読む → `config/<env>/.env` に**両系統へ同値**を設定する。Hermes 自身は `LANGFUSE_BASE_URL` を使う。
- **Langfuse リージョン**: `LANGFUSE_HOST`（Bridge SDK）/ `LANGFUSE_BASE_URL`（Hermes）で選ぶ。EU=`https://cloud.langfuse.com`（既定）/ US=`https://us.cloud.langfuse.com` / **JP=`https://jp.cloud.langfuse.com`**。キーの発行リージョンと一致させること（不一致だと 401/404）。
- 設定後の疎通確認手順は doc13 §4.3（health / `/v1/models` / `/v1/chat/completions`）。

---

## 5. Hermes Gateway の環境差分（hermes トラックと連携）

- dev = ローカル/開発用 Gateway、stg = staging GCP、prod = 本番 GCP（`34.4.104.112`）。
- Hermes 設定（`config.yaml` / `SOUL.md`）の配置・CD は **`feat/hermes-gcp-cd` トラックの領域**（`deploy/hermes/`）。本書は warehouse 側 config から**接続先 URL/トークンを環境別に指す**ことのみ定義し、Hermes 本体の per-env 化はそのトラックで実装する（`contract` 的な接続先キー名のみ合意）。

---

## 6. git とリリース

- 環境用の長命ブランチ（develop/staging 等）は**作らない**。`main` + feature（worktree）運用を維持。
- **prod デプロイは git タグ**（`v0.1.0` 等）で固定し、Jetson 実機はそのタグを `clone`/`checkout` して実行（doc17 §4.0: 別マシン＝clone）。
- 環境別 CD は GitHub Actions で `WAREHOUSE_ENV` と接続先 Secrets を切り替える（CI/CD 整備は dev-tooling トラック）。

---

## References

- [16 - リポジトリ構成と実装規約](16-repository-and-conventions.md) §4 共有パス / §5 config 単一ソース
- [13 - Hermes セットアップ](13-hermes-setup.md)
- `.claude/rules/environments.md` — 環境運用ルール / `.claude/rules/safety.md` — secrets 非コミット
