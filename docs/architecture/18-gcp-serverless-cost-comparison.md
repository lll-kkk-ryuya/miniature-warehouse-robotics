# GCP サーバーレス化 コスト比較・PoC 計画（Slack Gateway）

作成日: 2026-05-29
対象: GCP 上の Slack Gateway（`minicar` bot）のホスティング方式
関連: [deploy/gcp/README.md](../../deploy/gcp/README.md) / [13-hermes-setup.md](13-hermes-setup.md)

> **検討のきっかけ**: 「Slack から呼ばれた時だけ起動するサーバーレスにして料金を最小化できないか（Cloud Functions 等）」という問い。

---

## 0. 結論（TL;DR）

- **ホスティング費用は現状すでに ≒ $0**（e2-micro が GCP Always Free 枠）。
- **「呼ばれた時だけ起動」にしても料金は下がらない**。Cloud Run も低トラフィックなら ≒$0 で、VM と同じ。実際にかかるのは **LLM API（Gemini）課金だけで、これはどこにホスティングしても不変**。
- **料金最小化が唯一の目的なら現状維持（VM）が最良**。VM は Always Free のため **トラフィックに関係なく $0・乱用リスクなし**。Cloud Run は無料枠を超えると課金され、スパム時にコストが青天井になりうる。
- Cloud Run 化の価値は「常時起動サーバーを持たない」という**運用思想**であり、コスト論ではない。やるなら本書 §6 の PoC で**実コールドスタートと UX を実測してから**判断する。

---

## 1. 現状コスト（実測前提）

| 項目 | 料金 | 根拠 |
|------|------|------|
| e2-micro VM (us-west1, 24h 稼働) | **$0** | Always Free: us-west1/us-central1/us-east1 の e2-micro 1台/月 |
| ブートディスク 30GB pd-standard | **$0** | Always Free: 30GB-月 |
| 静的 IP `34.4.104.112` | **$0** | 稼働 VM に紐付く限り無料 |
| egress | **$0** | 1GB/月まで無料。Slack/LLM API のトラフィックは極小 |
| **ホスティング小計** | **≒ $0/月** | |
| LLM API (Gemini) | 従量 | **方式に依存しない固定費。Cloud Run でも同額** |

→ idle 時間も無料枠の範囲。**「使っていない時間に課金されている」わけではない**。

---

## 2. 実測データ（2026-05-29、現行 VM 上で計測）

| 指標 | 実測値 | サーバーレス化への含意 |
|------|--------|----------------------|
| コールドスタート（process restart → `/health` ok） | **8 秒**（npm キャッシュ温） | Cloud Run ではこれに**イメージ pull 時間が上乗せ** |
| Hermes RSS（稼働中） | **122 MB** | Cloud Run は **≥512MB、安全には 1GB** |
| 系全体メモリ使用 | 474 MB / 955 MB | |
| `hermes-agent`（venv 込） | **990 MB** | コンテナイメージが巨大化の主因 |
| `~/.local`（hermes bin） | 176 MB | |
| `~/.npm`（GitHub MCP キャッシュ） | 201 MB | コンテナに**事前同梱**しないと毎回 npx download |
| node | v20.20.2 | GitHub MCP（`@modelcontextprotocol/server-github`）の実行に必須 |
| **推定コンテナイメージサイズ** | **≈ 1.3〜1.5 GB** | pull が遅くコールドスタートを悪化／AR ストレージ課金対象 |

> **重要**: 現行の Slack 連携は **Socket Mode**（`.env` に `SLACK_APP_TOKEN=xapp-...`）。
> Socket Mode は**常時張りっぱなしの outbound WebSocket** が必要で、scale-to-zero と**原理的に両立しない**。
> サーバーレス化＝**Slack を Events API（公開 HTTPS Webhook）へ切替**が前提条件。

---

## 3. アーキテクチャ比較

### 現状: e2-micro 常駐 + Socket Mode
```
Slack ──(WebSocket / Socket Mode)── hermes gateway (常駐, systemd)
                                       ├─ Gemini API
                                       └─ GitHub MCP (npx 子プロセス)
```
- 常時起動。イベントは即処理（コールドスタートなし）。
- 状態（memory/sessions/kanban.db）はローカルディスクに永続。

### 案: Cloud Run scale-to-zero + Events API
```
Slack ──(HTTPS POST / Events API)── Cloud Run (min-instances=0)
   ▲  3秒以内に 200 ack 必須                  │ コンテナ起動 (cold)
   └──(chat.postMessage で非同期返信)─────────┘ Hermes 実行 → 返信
```
- 呼ばれた時だけ起動 → idle で 0 インスタンス。
- **3 秒 ack 制約**: Hermes はコールドで間に合わない → **即 ack + 非同期処理**が必須（§5）。
- 状態はコンテナ揮発 → **GCS / Cloud SQL に外部化**しないと memory/session が毎回消える。

---

## 4. コスト試算（Cloud Run、1回 30 秒・1 vCPU・1GiB と仮定）

Cloud Run 無料枠（月）: リクエスト 2,000,000 / vCPU 180,000 秒 / メモリ 360,000 GiB秒 / egress 1GiB。

| トラフィック | vCPU 秒/月 | 無料枠 | ホスティング費 |
|---|---|---|---|
| 低 (20回/日 = 600/月) | 18,000 | 内 | **≒$0**（+ AR イメージ ~0.8GB×$0.10 ≈ **$0.08**） |
| 中 (200回/日 = 6,000/月) | 180,000 | **ちょうど境界** | ≒$0〜わずかに超過（リスク帯） |
| 高/スパム (1000回/日) | 900,000 | 超過 | **≈ $19/月**（vCPU $17 + メモリ $2） |

**対比**: e2-micro は**トラフィックに関係なく $0**（固定無料インスタンス）。
→ **コストの観点では VM が常に同等以上に有利**。Cloud Run は「無料枠内なら $0、超えたら課金、乱用で青天井」。

その他の小コスト（Cloud Run 化時のみ）: Artifact Registry（0.5GB 超で $0.10/GB月）、Secret Manager（6 version まで無料）、Cloud Build（無料枠内）。

---

## 5. 移行に必要な作業（Cloud Run 採用時）

1. **Slack アプリ改修**: Socket Mode → Events API。Request URL（Cloud Run URL）登録、URL challenge 応答、`app_mention`/`message.im` 購読、**署名シークレット検証**。
2. **3 秒 ack + 非同期返信**: Events 受信 → 即 200 → 処理は別系統。推奨は **Cloud Tasks 経由**:
   `Slack → Cloud Run(受付: ack+enqueue) → Cloud Tasks → Cloud Run(worker: Hermes 実行 → chat.postMessage)`。
   （request 終了で CPU が止まるため、ack 後の処理に「CPU always allocated」か Tasks 分離が必要。）
3. **コンテナ化**: Dockerfile 作成。**GitHub MCP を事前同梱**（npx download をビルド時に固める）。イメージ ≈1.3GB。
4. **状態の外部化**: `memories/` `sessions/` `*.db` を GCS（gcsfuse）or Cloud SQL へ。ステートレス Q&A で割り切るなら不要だが会話記憶は失う。
5. **Secret 管理**: Slack/Gemini/GitHub トークンを Secret Manager へ。
6. **デプロイ配線**: Cloud Build → Artifact Registry → Cloud Run（min-instances=0）。CD は本リポジトリの GitHub Actions から `gcloud run deploy` に拡張。

工数感: **数時間〜1日**（特に §5-2 の非同期パターンと §5-4 の状態外部化が重い）。

---

## 6. PoC 計画（段階・受け入れ基準・ロールバック）

現行 `minicar`（VM/Socket Mode）は**触らず**、PoC は**別 Slack アプリ**で並行検証する（ロールバック＝何もしない）。

| 段階 | 内容 | 受け入れ基準 |
|---|---|---|
| **P1: 配管 PoC** | Hermes 抜きの最小 Cloud Run（echo）で Events API 署名検証＋3秒 ack＋Cloud Tasks 経由の `chat.postMessage` を実証 | Slack メンション → 数秒後に bot が定型文返信。重複発火なし |
| **P2: Hermes コンテナ** | Hermes をコンテナ化（MCP 同梱）し Cloud Run(min=0) へ。状態は一旦ステートレス | **実コールドスタート（イメージ pull 込）を実測**。初回応答時間を記録 |
| **P3: 判断** | P2 の UX（初回 20〜40s 想定）と実コスト、状態外部化の要否を評価 | 「VM と比べて得か」を数値で判断 |

**中止条件**: コールドスタート初回応答が許容外（例: >40s が常態）、または無料枠超過が見込まれる場合は P3 で打ち切り、VM 継続。

---

## 7. 推奨

- **料金最小化が目的なら → 現状維持（VM, Always Free, $0）。** これ以上は下がらない。
- **「常時起動サーバーを持ちたくない／自動スケールの設計を試したい」なら → §6 の PoC を P1→P2 まで実施**し、実コールドスタートを見てから本移行を判断。
- いずれにせよ **LLM API 課金は不変**なので、本当のコスト最適化はホスティングではなく **プロンプト/トークン削減・モデル選定**側にある（コスト方針は [08-llm-bridge-common](08-llm-bridge-common.md) 参照）。

> 将来の [Physical AI 展開（完全ローカル環境）](12-infrastructure-common.md) に沿って Jetson ローカルへ寄せる場合、クラウド常駐自体が不要になる。その意味でも現状の「無料 VM を Phase 0 のつなぎ」とする位置づけが整合的。
