[worktree: mwr-derisk-latency | branch: feat/derisk-latency | track: #200]

# spike/latency — 実行手順（4 provider レイテンシ実測）

司令官 LLM 呼び出しレイテンシを Hermes Gateway 経由で実測し、3秒サイクルの妥当性
（doc06:104）と `BLOCKED_TIMEOUT` 関数化（R-46）を確定する。判定の正本は
[`RESULT.md`](RESULT.md)、設計/契約の記録は [`CLAUDE.md`](CLAUDE.md)。

## 何を測るか
- **end-to-end**（client → Gateway → active_provider → 応答）の wall-clock。`hermes_client.py`
  と同一経路（`model="hermes-agent"`）＝**Hermes オーバーヘッド込みの実態**（R-07）。
- 1 run = gateway の**現 `active_provider` 1社**。measure.py は provider を**切替えない**。

## 安全（dev のみ・必読）
- `WAREHOUSE_ENV=prod` と**非 loopback gateway** は measure.py が **fail-closed で拒否**
  （safety.md / environments.md）。prod 鍵・prod Gateway へは接続しない。
- measure.py が読む秘密は `API_SERVER_KEY` のみ（**provider 秘密鍵は読まない**＝Hermes が
  `~/.hermes/.env` で消費）。鍵値は print も出力ファイルへの書込も**しない**。

## 前提
1. **Hermes Gateway を dev で起動**（`hermes gateway`・`~/.hermes/.env` に4社 provider 鍵）。
   `127.0.0.1:8642` で listen（doc13:24）。`API_SERVER_ENABLED=true` なら `API_SERVER_KEY` 必須。
2. **`API_SERVER_KEY` を measure.py に渡す**（いずれか）:
   - `export API_SERVER_KEY=...`、または
   - `--env-file <repo>/config/dev/.env`（既定は実行 checkout の `config/dev/.env`）。
   - 注意: **fresh worktree には gitignore された `config/dev/.env` が無い**。live は
     **main checkout から**実行するか `--env-file` で main の `.env` を指す。
3. python は `python3.12`（host `python3` は 3.7）。`openai` SDK が要る（lazy import）。
   無ければ `python3.12 -m pip install openai`。

## 動作確認（API を叩かない）
```bash
# dry-run: 設定検証のみ・課金なし。鍵は present/ABSENT のみ表示（値は出さない）
python3.12 spike/latency/measure.py -p anthropic --condition fairness-off --dry-run
# pure unit（live SDK/network 非依存）
python3.12 -m pytest spike/latency/test_stats.py -q
python3.12 -m ruff check spike/latency/
```

## 4 provider スイープ（live・課金あり ≈ 4×120 call）
各社ごとに **Hermes config の `active_provider` を1行切替**（doc13:175・`anthropic | openai
| google | xai`）→ **gateway 再起動** → run。`--condition` で公平性条件を宣言（R-36・判定
ベースラインは `fairness-off` 推奨。doc08:307-313）。

```bash
# 1) anthropic（~/.hermes config: active_provider: anthropic → 再起動 後）
python3.12 spike/latency/measure.py -p anthropic --condition fairness-off -n 120
# 2) openai
python3.12 spike/latency/measure.py -p openai    --condition fairness-off -n 120
# 3) google（gemini-2.5-flash）
python3.12 spike/latency/measure.py -p google     --condition fairness-off -n 120
# 4) xai（grok-4.3）
python3.12 spike/latency/measure.py -p xai        --condition fairness-off -n 120
```

各 run は `out/<provider>_<condition>_120_<utc>.json` を吐き、サマリを stdout に出す。

## 結果の取り込み
1. 4社の `out/*.json` の `summary_s`（秒）を **ms に直して** `RESULT.md` §1 表へ転記。`err` と `missed_cycle_rate`（%）も転記（判定入力）。
2. §2 のルールで**サイクル長判定**を1行確定: まず **viability ゲート**（missed-cycle 率 ≤ 閾値）→ 次に worst-case p95 vs 2.5s。survivor p95 単独で判定しない（doc08:140）。
3. §3 の表で確定 `cycle_total` から `blocked_timeout = max(10, 3×cycle)` を再計算。
4. §0 の日付・Hermes バージョン・`--condition` を埋める。`out/` は gitignore＝生 dump は
   非コミット、RESULT.md（蒸留表）が成果物。

## オプション
- `-n N`（既定120, doc06:103）/ `--warmup K`（既定3・破棄）/ `--timeout S`（既定60・tail 取得）。
- `--base-url`（既定 `http://127.0.0.1:8642` or `$HERMES_BASE_URL`）/ `--no-floor`（`/v1/models`
  floor probe をスキップ）/ `--allow-remote`（非 loopback を許可・dev のみ・既定 off）。

## 結果の機械導出（collect.py・転記ミス防止）

4社の run 後、`out/*.json` から RESULT.md §1/§2/§3 を**機械導出**して手転記の誤りを防ぐ（read-only・課金/network なし）:

```bash
python3.12 spike/latency/collect.py --condition fairness-off
```

出力 = §1 表（秒→**ms** 変換済）＋ §2 verdict（**viability gate**: 各社 `missed_cycle_rate ≤ 5%` → cross-provider **worst-case p95 vs 2.5s**。survivor p95 単独で判定しない・doc08:140）＋ §3 `max(10,3×cycle)` 表。閾値は `measure.py` から import（single source）。**EXTEND 時の `cycle_total` は自動採用しない**（§2 step4 の「待機＋p95」は operator 判断＝発明しない）。**Grok は xAI 鍵不在で DEFERRED**（silent drop しない）。collect.py は **RESULT.md を書かない**（print のみ）＝表は operator がセル上書きで転記する（中段挿入で file:line をズラさない）。pure unit: `python3.12 -m pytest spike/latency/test_collect.py -q`。
