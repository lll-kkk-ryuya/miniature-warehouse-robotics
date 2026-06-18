[worktree: mwr-derisk-langfuse | branch: feat/derisk-langfuse | track: #88]

# spike/langfuse — 実行手順（Langfuse Phase-3 実トレース検証）

Phase 4 の **4 provider × 3 交通モード**比較が依拠する Langfuse 観測層を、実 Langfuse(4.7.x)
+ Hermes に対して検証する **再実行可能 harness**。判定の正本は [`RESULT.md`](RESULT.md)、各
gated assertion は [`CHECKLIST.md`](CHECKLIST.md)、設計/契約の記録は [`CLAUDE.md`](CLAUDE.md)。

検証項目は **doc13:520 ①〜⑤**（① inbound `metadata.trace_id` 尊重 / ② 4社 cost≠0・Grok
カスタムモデル / ③ 二重 generation 無し / ④ managed-prompt 連携 / ⑤ SDK 4.7.1 スモーク）。

## 2 つのモード（spike/latency・spike/memory-gate と同型）
- **OFFLINE selftest（自律・CI 緑）** — 検証ロジックの純関数コアを **fake Langfuse client** で
  unit テスト（trace_id 決定性 / Grok cost 算術 / ①〜⑤ 判定）。**SDK 不要・network 不要・鍵不要**。
  これが完了ゲート（DoD）。
- **LIVE verify（PAID human gate）** — 実 Langfuse + Hermes + provider 鍵で1社ずつ叩き、trace を
  読み戻して①〜⑤を assert。**dev-only / fail-closed**。鍵/Hermes が無ければ拒否。

## 動作確認（鍵不要・課金なし）
```bash
# OFFLINE 自律ゲート（ruff + pytest・fake client）。これだけで harness の論理は検証できる
./run.sh selftest
# or 直接:
python3.12 -m pytest spike/langfuse/test_verify.py -q
python3 -m ruff check spike/langfuse/

# live driver の設定検証のみ（API を叩かない・鍵は present/ABSENT のみ表示）
python3.12 verify.py -p xai --dry-run
```

## 安全（dev のみ・必読）
- `WAREHOUSE_ENV=prod` と**非 loopback gateway** は verify.py が **fail-closed で拒否**
  （safety.md / environments.md）。prod 鍵・prod Gateway へは接続しない。
- verify.py が読む秘密は `API_SERVER_KEY` ＋ `HERMES_LANGFUSE_PUBLIC_KEY`/`HERMES_LANGFUSE_SECRET_KEY`
  のみ。**鍵値は print も出力ファイルへの書込もしない**。`out/` は gitignore（秘密非含有）。

## LIVE verify（課金あり・人間ゲート）
### 前提
1. **Langfuse v4 SDK + openai を install**（live のみ・selftest には不要）:
   ```bash
   ./run.sh setup    # python3.12 -m pip install 'langfuse>=4.7,<5' 'openai>=1.0'
   ```
2. **Hermes Gateway を dev で起動**（`hermes gateway`・`~/.hermes/.env` に4社 provider 鍵）。
   `127.0.0.1:8642` で listen（doc13:24）。
3. **Langfuse project 鍵 + `API_SERVER_KEY` を渡す**（いずれか）:
   - `config/dev/.env` に `HERMES_LANGFUSE_PUBLIC_KEY` / `HERMES_LANGFUSE_SECRET_KEY` /
     `API_SERVER_KEY`（既定で読む。`--env-file` で別パス可）、または `export`。
   - 注意: **fresh worktree には gitignore された `config/dev/.env` が無い**。`--env-file
     <main>/config/dev/.env` で main の `.env` を指すか `export` する。
4. **`WAREHOUSE_RUN_ID` を設定**（#4/#6 共有・trace seed 前半、doc13:519）:
   `export WAREHOUSE_RUN_ID=run_A_claude_s1_phase3`。
5. **Grok 価格を注入**（②の offline fallback 検証用・任意。CHECKLIST.md の期待値を USD/token で）:
   `export LANGFUSE_GROK_PRICES=0.00000125,0.0000025`（grok-4.3 = $1.25/$2.50 per 1M）。
   ※ **コードに焼かない**（doc08:510）— 注入のみ。

### 4 provider スイープ（live・課金あり）
各社ごとに **Hermes config の `active_provider` を1行切替**（doc13:175・`anthropic | openai |
google | xai`）→ **gateway 再起動** → run。spike/latency の live run と**同窓**で回せる。

```bash
# 1) anthropic（~/.hermes config: active_provider: anthropic → 再起動 後）
./run.sh verify anthropic
# 2) openai
./run.sh verify openai
# 3) google
./run.sh verify google
# 4) xai（Grok。② は LANGFUSE_GROK_PRICES 注入時のみ offline fallback も判定）
./run.sh verify xai
```

各 run は `out/<provider>_<run_id>_<gen_id>.json`（秘密非含有）を吐き、①〜⑤サマリを stdout に出す。

### 結果の取り込み
1. `./run.sh report` で `out/*.json` を①〜⑤マトリクスに集計。
2. Langfuse UI で trace を開き、①〜⑤を目視確認（read-back フィールド形は**未確定** doc08:510 →
   `fetch_ok=N` なら API からは未検証＝UI 確認が正）。
3. [`RESULT.md`](RESULT.md) の①〜⑤ + Grok cost + **v4 score metadata group-by 可否**
   （doc08:520 / doc20 §8.4 item2）の欄へ転記。`out/` は gitignore＝生 dump は非コミット、RESULT.md が成果物。
4. CHECKLIST.md の live 取得値欄（literal model 文字列・実価格フィールド形・cost 値）を埋める。

## オプション
- `verify.py -p <provider>`（必須・gateway の現 active_provider ラベル）/ `--run-id` / `--gen-id`
  （既定1）/ `--grok-prices IN,OUT` / `--base-url`（既定 `$HERMES_BASE_URL` or `127.0.0.1:8642`）
  / `--env-file`（既定 `config/dev/.env`）/ `--allow-remote`（非 loopback 許可・dev のみ・既定 off）
  / `--dry-run`（課金なし・設定検証）。
- `./run.sh clean` で `out/` 削除。
