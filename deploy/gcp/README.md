# GCP デプロイ手順 (Hermes Gateway)

GCP Always Free 枠 (e2-micro) 上で Hermes Gateway + Slack adapter を動かすための一式。

## 前提

- Project: `gen-lang-client-0487062066` (hermes-minicar)
- Region: `us-west1` (Oregon) — Always Free 対象
- Machine: `e2-micro` (2 vCPU shared / 1 GB RAM)
- Disk: `pd-standard` 30GB
- Network tier: `STANDARD` (PREMIUM だと課金)
- gcloud CLI が `ryu3124ruyu@gmail.com` で認証済みであること

## ファイル

| ファイル | 用途 |
|---------|------|
| `create-vm.sh` | VM 作成スクリプト (gcloud 一発) |
| `cloud-init.yaml` | VM 初回起動時に swap + Python 環境を準備 |

> 環境変数の雛形は `deploy/hermes/gcp/.env.example`（変数名は `API_SERVER_KEY`、doc13 §3.1.1 と一致）が正本。実 `.env` は **絶対にコミット禁止**。

## 実行手順

### Step 1: 請求アカウント有効化 (ブラウザ操作)

1. https://console.cloud.google.com/billing で閉じている請求アカウントを再オープン
2. https://console.cloud.google.com/billing/linkedaccount?project=gen-lang-client-0487062066 でプロジェクトに紐付け

### Step 2: VM 作成 (自動)

```bash
cd deploy/gcp
./create-vm.sh
```

成功すると以下が表示される:
```
External IP: xxx.xxx.xxx.xxx
SSH:        ssh <username>@xxx.xxx.xxx.xxx
```

### Step 3: cloud-init 完了確認

```bash
ssh <username>@<external-ip> 'test -f /var/log/hermes-init-done && echo READY'
```

`READY` と返れば、Python 3.12 + venv + swap 2GB + git/curl 準備完了（install.sh 実行の前提）。

### Step 4: Hermes Gateway インストール

VM 上で (公式 install.sh による git インストール。doc 13 §2 と一致):
```bash
# Hermes 本体 (git clone + venv、~/.local/bin/hermes が生成される)
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
exec $SHELL -l
hermes --version            # v0.14.x

# 初期セットアップ (provider / Slack / API キー)
hermes setup

# .env を配置 (deploy/hermes/gcp/.env.example を参照。実値はコミット禁止)
# その後 systemd で常駐させる (下記 CD の前提)
hermes gateway              # フォアグラウンド確認用
```

> 実機 (現行 VM) は **git インストール** (`~/.hermes/.install_method` = `git`、
> ExecStart = `~/.local/bin/hermes gateway`)。`pipx install hermes-agent` ではない。

---

## 継続的デプロイ (CD) — main → VM 自動反映

`main` の Hermes Gateway **設定** (`SOUL.md` / `config.yaml`) を push したら、
GitHub Actions が自動で VM へ反映し、`hermes-gateway` を再起動する。

### 何が CD 対象で、何が対象でないか

| 対象 | 仕組み |
|------|--------|
| **bot 自身の設定** (`SOUL.md` / `config.yaml`) | 本 CD。repo が source of truth → push で VM へ |
| **bot のリポジトリ知識** (docs / コード本体) | CD 不要。bot は GitHub MCP で `main` を**ライブ参照**するため常に最新 |
| **シークレット** (`~/.hermes/.env`) | CD 対象外。手動で VM 上のみに置く (コミット禁止) |
| **Hermes 本体バージョン** | CD 対象外。git インストールなので公式 install.sh を再実行して手動更新 (破壊的更新リスクのため自動追従しない) |

> **重要**: `SOUL.md` / `config.yaml` は **repo 側を編集**して push する。
> VM 上で直接編集しても次回デプロイで上書きされる (`SOUL.md` 冒頭にも明記)。
> VM 上で `hermes config set` 等を使った場合は、その差分を repo に取り込むこと。

### ファイル構成

| パス | 用途 |
|------|------|
| `deploy/hermes/gcp/config.yaml` | Gateway 設定 (repo 管理の正本)。秘密値は含まず `${ENV}` 参照のみ |
| `deploy/hermes/gcp/SOUL.md` | bot ペルソナ / コアプロンプト |
| `deploy/hermes/gcp/deploy.sh` | デプロイ本体 (ローカル・CI 共通で使える) |
| `deploy/hermes/gcp/.env.example` | `.env` の変数名テンプレ (実値はコミット禁止)。Jetson 倉庫側の `.env.example` は doc 13 §3.1.1 で別途 `deploy/hermes/.env.example` に配置予定 |
| `.github/workflows/deploy-hermes-gcp.yml` | CD ワークフロー |

### トリガ

- `main` への push で上記いずれかが変化したとき
- Actions タブから手動実行 (`workflow_dispatch`)

### 必要な GitHub Secrets (設定済み)

| Secret | 値 |
|--------|----|
| `GCP_VM_SSH_KEY` | CI 専用 ed25519 **秘密鍵** (ユーザー個人鍵とは別) |
| `GCP_VM_HOST` | VM 外部 IP (現在 `34.4.104.112`) |
| `GCP_VM_USER` | SSH ユーザー (`systemctl` 用に passwordless sudo) |
| `GCP_VM_KNOWN_HOSTS` | VM ホスト鍵 (MITM 防止のため固定) |

CI 鍵の公開鍵は VM の `~/.ssh/authorized_keys` に登録済み。鍵をローテートする場合:
```bash
ssh-keygen -t ed25519 -f /tmp/hermes_cd_key -N "" -C "hermes-cd-github-actions"
# VM の authorized_keys から古い hermes-cd-github-actions 行を削除し、新公開鍵を追記
gh secret set GCP_VM_SSH_KEY < /tmp/hermes_cd_key
shred -u /tmp/hermes_cd_key   # 秘密鍵はローカルに残さない
```

### 手動デプロイ (CI を介さず Mac から)

```bash
GCP_VM_HOST=34.4.104.112 GCP_VM_USER=kawaguchiryuya \
  bash deploy/hermes/gcp/deploy.sh
# ssh-agent / ~/.ssh の既定鍵を使う。CI 鍵を使うなら SSH_KEY=... を渡す
```

`deploy.sh` は **差分があるファイルのみ**更新し、更新時だけ
タイムスタンプ付きバックアップ (`~/.hermes/backups/<ts>/`) を取ってから
`hermes-gateway` を再起動し、`/health` で起動確認する。

### 外部 IP (静的予約済み)

外部 IP は **静的予約済み** (`34.4.104.112`)。VM 再起動でも変わらないため、
通常運用で Secrets の更新は不要。

```
予約名:        hermes-gateway-ip
リージョン:     us-west1   (network tier: STANDARD)
アドレス:       34.4.104.112
状態:          IN_USE (hermes-gateway に紐付き → 起動中 VM では無料)
```

予約は 2026-05-29 に、稼働中だったエフェメラル IP を同一値のまま昇格させて作成:
```bash
gcloud compute addresses create hermes-gateway-ip \
  --addresses=34.4.104.112 --region=us-west1 --network-tier=STANDARD
```

> **課金注意**: 静的 IP は **VM に紐付き、かつ VM が起動中なら無料**。
> VM を削除/停止して IP を未使用のまま放置すると課金されるので、
> VM を畳む際は `gcloud compute addresses delete hermes-gateway-ip --region=us-west1` で解放する。

もし将来 IP を変更/再割り当てした場合のみ Secrets を更新:
```bash
NEW_IP=$(gcloud compute instances list --format="value(EXTERNAL_IP)")
printf '%s' "$NEW_IP" | gh secret set GCP_VM_HOST
ssh-keyscan -t ed25519 "$NEW_IP" | gh secret set GCP_VM_KNOWN_HOSTS
```

---

## 課金リスク監視

予算アラート (月額 100 円) を必ず設定:
- https://console.cloud.google.com/billing/budgets

無料枠を逸脱する設定の例 (このスクリプトでは避けている):
- ❌ Machine type が `e2-small` 以上
- ❌ Network tier `PREMIUM`
- ❌ Boot disk が 30GB 超
- ❌ `us-west1` / `us-central1` / `us-east1` 以外のリージョン
- ❌ Ops Agent 有効

## トラブルシュート

### `Quota 'CPUS' exceeded`
請求アカウントが未紐付け。Step 1 をやり直す。

### `Out of resources` (e2-micro)
us-west1-a でダメなら `ZONE=us-west1-b ./create-vm.sh` を試す。

### SSH つながらない
最初の数分は cloud-init 実行中で sshd が再起動することがある。1〜2分待って再試行。
