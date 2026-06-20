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

### 4.2 dev live 起動ハーネス（Hermes + LLM Bridge）

dev の Gazebo/RViz live run では、手順の属人化を避けるため **Hermes liveness → preflight → sim cockpit → Bridge env 注入 → full-stack 再起動 → initialpose seed** を `deploy/dev/run-mode-a-live.sh` に集約する。標準運用では Hermes Gateway を別 terminal で `API_SERVER_ENABLED=true hermes gateway` として起動しておく。完全一発起動が必要な dev run では `deploy/dev/run-mode-a-live.sh --start-hermes` が Hermes を background 起動してから同じ preflight に進む。

```bash
# 1回だけ: Bridge 側 secret を作る。値は ~/.hermes/.env の API_SERVER_KEY と同一。
cp config/dev/.env.example config/dev/.env

# 毎回の起動（Hermes は別 terminal で起動済み）:
deploy/dev/run-mode-a-live.sh

# 完全一発 dev 起動（Hermes が落ちていれば background 起動）:
deploy/dev/run-mode-a-live.sh --start-hermes
```

設計上の固定事項:

- `deploy/dev/check-hermes-live.sh` が `config/<env>/.env` を読み、秘密値を表示せずに `API_SERVER_KEY` / `HERMES_API_KEY` の存在、Hermes `/health`、認証付き `/v1/models` を確認する。必要時のみ `--chat` で `/v1/chat/completions` の有料 smoke を行う。
- Docker-on-Mac の sim コンテナから host 側 Hermes へは `localhost` ではなく `http://host.docker.internal:8642` を使う。launcher は `WAREHOUSE__HERMES__BASE_URL=http://host.docker.internal:8642` を `docker exec` に注入する。
- コンテナへ渡す secret は Bridge 認証に必要な `API_SERVER_KEY` / `HERMES_API_KEY` と観測用 `LANGFUSE_*` のみに限定する。各社 provider key は Hermes が消費するため、ROS コンテナへ渡さない。
- `.env` 変更後、既に起動済みの `llm_bridge` は新しい値を拾わない。launcher は既存 `warehouse_bringup` を止めてから再起動し、401 のまま残る古い Bridge プロセスを避ける。
- `--start-hermes` は dev 便利機能であり、Hermes Gateway を `hermes gateway start` で service 起動する。macOS/launchd では `launchctl setenv API_SERVER_ENABLED true` を先に入れ、API server が `8642` を開くまで待つ。Hermes service-start log は `/tmp/mwr_hermes_gateway.log` に出す。stg/prod はこの script ではなく systemd / デプロイ runbook 側で Gateway の常駐性を担保する。
- 既存の `mwr-sim` が別 worktree を mount している事故を避けるため、launcher の既定コンテナ名は `mwr-mode-a-live`、noVNC port は `6082` とする。既存 container を使う場合は `MWR_SIM_CONTAINER` / `MWR_SIM_PORT` で明示する。

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

## 7. 実機投入前ゲート（dev/stg → prod 昇格・忠実度ギャップ）

> **#127 の additive 追記**。**本節は §1-§6 の被参照行（`:18`/`:21`/`:54`/`:78`/`:86`/`:94` 等）を
> 動かさないため末尾（References 直前）に置く**（[doc12:502](12-infrastructure-common.md) と同じ
> line-drift 回避方針）。**詳細な忠実度ギャップ表・各ゲートの手順は新 doc
> [docs/jetson/01-fidelity-and-validation.md](../jetson/01-fidelity-and-validation.md) を正本**とし、
> 本節はその要点（昇格判断）を環境分離文書側に固定する。**本節は計画であり、実機を動かさない**。

### 7.1 忠実度ギャップ — どの環境で何が閉じるか

現状ソフトは **Mac M4 + tiryoh(ARM64-native, doc03:262)** で検証。CPU アーキは Jetson Orin Nano Super も
**ARM64**（doc06:91「Mac M4 と Jetson はどちらも ARM64」）/ Ubuntu 24.04（doc03:270）で一致するため
**ROS ロジック・凍結契約・launch 合成・pytest・config overlay・2台 Gazebo E2E は dev で高忠実に検証可**。
一方、以下は **dev(Mac) でも stg(§1 クラウド sim) でも原理的に近似不可**で、**実 Jetson でしか露見しない**：

- **GPU/CUDA**（Isaac ROS・GPU 加速。Mac に CUDA 無し。doc07:23）
- **実時間性**（50ms Guardian / 100ms State Cache の jitter。Docker Desktop は Mac 上 VM 経由＝別物。
  R-40＝doc07:250。doc12:481 が「50ms/100ms は目標値・非ハードRT」と明記）
- **micro-ROS WiFi UDP 2台同時**（R-37＝doc07:242。実 ESP32×2 が必要）
- **メモリ予算**（8GB ユニファイド・Open-RMF R-38＝doc07:243。`--memory` 制限では食合せ再現不可、doc06:100）

→ これらを閉じる rung が **「実機投入前ゲート」＝実 Jetson 上の bench bring-up（ロボット非駆動から段階的に）**。
**stg（§1:17 クラウド sim）と prod（§1:18 実ロボット駆動）の間**に位置し、**prod 昇格の前提条件**。
stg を実機含みに再定義はしない（additive・既存契約不変）。

### 7.2 昇格ゲート（合否基準・**抜粋**。G5 実センサ/G6 WiFi を含む全 G0-G7 は新 doc §4 が正本）

| ゲート | 測る | PASS 基準 | 不合格時 | 根拠 |
|---|---|---|---|---|
| **G0 安全（必須）** | Layer 0 速度クランプ・近接 estop / Layer 1 Guardian unit | ROS が 0.3 超を出しても MCU が ≤0.3 クランプ・停止。Guardian unit 緑 | **昇格不可**・motion unit を enable しない | doc12:75-84 / doc16:221（unit）/ doc19:21・safety.md |
| **G1 メモリ** | 全スタック起動時 `free -h` 残RAM（ユニファイド込み） | 残RAM **≥500MB**（Mode C は +Open-RMF で再測） | <500MB → **Open-RMF 断念＝Mode B 格下げ** or 別マシン（Go/No-Go） | doc06:98,:100 / doc07:243(R-38) |
| **G2 micro-ROS 2台** | 単一 Agent(:8888) で ESP32×2 を WiFi UDP 双方向 | 両機 distinct `client_key` で 2 session 独立・双方向 OK | key 差で不可 → USB 有線 | doc07:79,:242(R-37) / firmware/spike/RESULT.md |
| **G3 実時間 jitter** | Guardian 50ms / State Cache 100ms 周期実測 | p99/max がデッドライン内（`gc.disable()` 後） | hot path を C++ `nav2_collision_monitor`+ESP32 へ委譲（#126） | doc07:250(R-40) / doc12:47,:481,:500-551 |
| **G4 nav2/SLAM 性能（＋熱 R-09）** | CPU 版 Nav2×2 + AMCL + SLAM の実時間追従＋ `tegrastats` 熱クロック | 2台が実時間で破綻なく追従（GPU は載れば加点）。10分持続負荷で throttle 無し | 周期/解像度調整・GPU costmap・ファン/省電力 mode | doc02:90,:138,:62-63 / doc07:23,:177(R-09) |
| **G7 Hermes(GCP) E2E** | prod GCP Hermes へ Bridge 到達・司令官サイクル | `healthcheck.sh` 到達 ◯・`API_SERVER_KEY` 同値で認証 | ネットワーク/Gateway 確認（**secrets は触らない**） | doc19:18,:86 |

> 全 G0-G7（G5 実センサ・G6 WiFi 同時通信を含む全 7 ゲートは新 doc §4）PASS で prod へ昇格。
> Jetson 到着前は合否基準を凍結し、到着後に値を埋める（実測＝ハードウェアゲート）。

---

## References

- [16 - リポジトリ構成と実装規約](16-repository-and-conventions.md) §4 共有パス / §5 config 単一ソース
- [13 - Hermes セットアップ](13-hermes-setup.md)
- `.claude/rules/environments.md` — 環境運用ルール / `.claude/rules/safety.md` — secrets 非コミット
