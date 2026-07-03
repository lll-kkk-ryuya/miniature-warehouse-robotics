# 環境（dev / stg / prod）運用ルール

> 方式は軸A「デプロイ環境の分離」。設計の正本は [docs/architecture/19](../../docs/architecture/19-environments-and-config.md)。本書は守るべき運用規約のみ。

## 必須

- **環境は `WAREHOUSE_ENV`（dev | stg | prod）で選択**。未設定時は **dev** にフォールバック。
- 接続先 URL・パス・モード・sim/実機 はすべて **config から読む**。**環境名・エンドポイント・キーをコードにハードコードしない**。
- 設定は **base + overlay**: `config/warehouse.base.yaml` → `config/$WAREHOUSE_ENV/warehouse.yaml` → 環境変数（後勝ち）。共通値は base に一元化し、環境差分のみ `<env>/` に書く。

## Secrets（最重要・safety.md と一体）

- 実体は **`config/<env>/.env`**（`**/.env` で gitignore）。**コミット厳禁**（APIキー/トークン/WiFiパスワード）。
- リポジトリに置くのは **`.env.example`（プレースホルダのみ）**。
- **dev / stg / prod で別キーを使う**（本番事故・課金混在の防止）。

## dev live Hermes / LLM Bridge 起動

- Gazebo/RViz で Hermes + LLM Bridge を使う dev live run は、手作業で `ros2 launch` せず **`deploy/dev/run-mode-a-live.sh`** を使う。標準: `deploy/dev/run-mode-a-live.sh`。Hermes も含めて起動したい dev では `deploy/dev/run-mode-a-live.sh --start-hermes`。
- 起動前診断だけ行う場合は **`deploy/dev/check-hermes-live.sh`** を使う。必ず Hermes `/health`、認証付き `/v1/models`、必要に応じて container→Hermes (`host.docker.internal`) を確認してから full stack に進む。
- Docker-on-Mac の ROS/sim container から host 側 Hermes Gateway へは `localhost` ではなく **`http://host.docker.internal:8642`** を使う。launcher は `WAREHOUSE__HERMES__BASE_URL` にこの値を注入する。
- `API_SERVER_KEY` / `HERMES_API_KEY` は Bridge→Hermes 認証だけに使う。各社 provider key は Hermes 側 `~/.hermes/.env` が消費し、ROS/sim container へ渡さない。
- `.env` 変更後、起動済み `llm_bridge` は新しい値を拾わない。launcher は既存 `warehouse_bringup` を再起動するため、401 のまま残る古い Bridge を避けられる。
- Claude/Codex が `config/<env>/.env` や `~/.hermes/.env` を読む必要がある live 検証では、ユーザーから **対象 path と目的を含む明示スコープ承認**を得る。値は表示しない。承認がない場合は `.env.example` と docs のみ参照する。
- worktree ごとに `config/dev/.env` が無い場合は `--env-file /path/to/config/dev/.env` または `MWR_HERMES_ENV_FILE=/path/to/config/dev/.env` を使う。Agent の secret guard が `.env` 読み取りを止める場合は、ユーザーの shell 側で `API_SERVER_KEY` / `HERMES_API_KEY` を export し、`MWR_HERMES_ENV_FILE=/nonexistent` で起動する。
- **Mode X-ER（ER 視覚司令官）の live は専用 gateway を使い、標準 Mode A の 8642 とは分ける**: **標準（TARGET）= fork gateway `deploy/hermes/er-audio-fork/run-er-gateway.sh`（**8644**・`input_audio`・#357）1 本で全 modality（text/image/audio）を Hermes 経由**にする方針（fork は input_audio を足すだけで text/image を保持。[ADR 0002](../../docs/adr/0002-er-in-hermes-standard.md)）。素 gateway `deploy/dev/run-er-hermes.sh`（**8643**・text/image leg）は 8644 に統合・retire していく CURRENT 実体。個人 `~/.hermes` は触らない（`HERMES_HOME` で隔離）。**`direct` は緊急 fail-safe / 恒久 fallback へ格下げ**（CURRENT〔shipped〕音声は wire 着地まで依然 direct ER）。turnkey 手順・cost/scoped 承認ゲートは [docs/dev/07-mode-x-er-live-e2e-runbook.md](../../docs/dev/07-mode-x-er-live-e2e-runbook.md)。

## prod の扱い（実機・本番）

- prod 接続・実機動作は **Emergency Guardian / 速度上限 0.3 m/s のテスト通過後のみ**（safety.md / doc16 §11）。
- **prod デプロイは git タグ**（`v0.x`）で固定。Jetson（別マシン）はそのタグを clone/checkout して実行（doc17 §4.0）。
- prod の config / secrets を変更する PR は安全レビューを必須とする。

## やってはいけない

- `config/<env>/.env`（実体）の commit。
- 環境別エンドポイント/モードのコード内ハードコード。
- 環境差分を base に書く（base は共通のみ）。
- dev のキーで prod へ接続、またはその逆。
