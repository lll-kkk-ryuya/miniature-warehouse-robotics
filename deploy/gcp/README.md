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
| `.env.example` | Hermes 用の環境変数雛形 (実 `.env` は **絶対にコミット禁止**) |

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

`READY` と返れば、Python 3.12 + swap 2GB + pipx 準備完了。

### Step 4: Hermes Gateway インストール (次フェーズ)

VM 上で:
```bash
pipx install hermes-agent
hermes init
# .env を編集、Slack token と LLM API キーを入れる
hermes gateway
```

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
