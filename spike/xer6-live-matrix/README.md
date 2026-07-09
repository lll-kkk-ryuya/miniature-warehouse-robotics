# spike/xer6-live-matrix — XER6 live 一気通貫 matrix（複数 run manifest × per-box 計測）

[worktree: mwr-xer6-live-matrix | branch: feat/mode-x-er-live-matrix | track: #342]

live Gemini Robotics-ER（標準 8644 fork Hermes gateway 経由）を `x_er_bridge` の backbone
（`build_x_er_runtime` → `run_x_er_cycle` → dispatch 記帳・**0 actuation**）に流し、
**複数の run manifest バリアント**で plugin composition の実挙動を検証しつつ
**box ごとの wall time / token** を JSONL に記録するハーネス。production コードは一切編集しない。

## Box map（計測点・すべて file:line 検証済）

| box | 対象 | 観測方法 |
|---|---|---|
| `composition_startup` | `build_x_er_runtime` (x_er_composition.py:119) | 直接計時 |
| `er_propose` | `adapter.propose_plan` (x_er_cycle.py:199) | 注入 `TimingAdapter` |
| `er_send` | 実 HTTP send（per-send・fallback 可視） | 注入 `TimingSender` |
| `handoff_draft` | `to_robotics_plan_draft` (x_er_cycle.py:212) | module-global wrap patch |
| `plugin_gate` | `validate_with_plugins` (x_er_cycle.py:214-216) | 同上 |
| `l3_compile` | `compile_raw_output` (x_er_cycle.py:233-238) | 同上（`--l3-substages` で内訳） |
| `gen_mint` | gen get+set (x_er_cycle.py:252-253) | 注入 `TimingGenStore` |
| `align_task_ids` | `_align_task_ids` (x_er_cycle.py:264) | wrap patch |
| `dispatch` | `tool_executor.execute` (x_er_cycle.py:269) | 注入 `TimingToolExecutor` |
| `mark_running` ほか | lifecycle commit (x_er_cycle.py:276) | 注入 `TimingExecutorProxy` |
| `completion_apply` | `apply_pending_completions` (x_er_completion.py:221) | harness が caller |
| `cycle_total` | `run_x_er_cycle` 全体 | 直接計時 |

## バリアント（manifests/ + plugins/・すべて `status: draft` = 非 production）

| key | manifest | 検証内容 |
|---|---|---|
| A | variant_a.yaml | zero-plugin 基準（preflight vacuous pass・core validator のみ） |
| B_in | variant_b.yaml | `l3.zone_policy` 実 hookimpl・全域 zone → pass（モデル出力非依存） |
| B_out | variant_b.yaml | 非交差 zone → **決定的** `l3.zone_policy:target_out_of_zone` reject・R-26 store 無傷 |
| C | variant_c.yaml | 2 plugin + customer_b site 差替・warnings 帰属・witness diff |
| D | variant_d.yaml | `emergency_stop` 要求 → policy clamp で BLOCK・`clamped_from` 記録 |

## 使い方

```bash
# 無課金（fixture replay・WAREHOUSE_LIVE_ER 不使用）
spike/xer6-live-matrix/run-live-matrix.sh --offline            # 4+1 バリアント × 3 reps
spike/xer6-live-matrix/run-live-matrix.sh --offline --l3-substages
"$PYTHON" spike/xer6-live-matrix/harness.py --selftest-budget  # budget guard リハーサル

# 安全確認（鍵存在・gateway health のみ・値非表示）
spike/xer6-live-matrix/run-live-matrix.sh --check

# 課金（operator の batch cost 承認後のみ・hard cap 12 sends・doc07 §4.5）
spike/xer6-live-matrix/run-live-matrix.sh
```

## Budget / secrets 規約

- **課金判断は都度 operator**（docs/dev/07-mode-x-er-live-e2e-runbook.md §4.5）。runner の
  default モードだけが `WAREHOUSE_LIVE_ER=1` を内部で立てる（`run-live-er-chain.sh:80` 先例）。
- **予算台帳は sender 層**（`BudgetedSender`）: hermes→direct fallback（gemini_er.py:231-243）は
  **2 send = 2 課金**として数え、cap 到達で送信前に `BudgetExceededError`。
- 鍵は env のみ（`GEMINI_API_KEY`/`GOOGLE_API_KEY`・bearer `HERMES_API_KEY`/`API_SERVER_KEY`）。
  値は echo/log/JSONL のどこにも出さない。gateway bearer は runner が fork home `.env` を
  script 内で source（agent プロセスは `.env` を読まない・environments.md:24-25）。

## 結果スキーマ（out/<batch>/results.jsonl・out/ は gitignore）

- `batch` ヘッダ / `box_timing`（variant・rep・cycle・box・wall_s・status・transport・tokens）
- `cycle_summary`（skipped_reason・dispatched・committed・plan_id・composed_status・
  plugin_error_codes（namespaced full code）・plugin_warning_ids・clamped_from・exception）
- `variant_summary`（pass・failures・witness_dir）/ `budget_checkpoint` / `batch_summary`
- composition witness: `out/<batch>/runs/<variant>/<run_id>/{manifest.yaml,effective_composition.json}`

## Honest limits

- live のモデル出力（検出 pixel・plan 形）は非決定的 → live tier の assert は不変条件
  （KNOWN_LOCATIONS・navigate のみ・非 accept ⇒ 0 dispatch）＋「plugin が走り宣言 code を出した」
  に限定（`tests/live/test_xer_full_chain_live.py:16-25` の規律）。
- cycle 2 は既定で captured envelope の replay（`test_x_er_offline_e2e.py:211-212` と同じ意味論）。
  `--cycle2-live` は本物の 2nd call（予算 2 倍・継続 assert は観測扱いに降格）。
- fork gateway 経由の token 数（OpenAI 互換 `usage`）は欠落しうる（実測裏付けは direct の
  `usageMetadata` のみ）→ 欠落時は `tokens: null` を記録。
- これは **RUNNING（G5 sim actuation）ではない**（doc07 impl-status: OFFLINE-WIRED ≠ RUNNING）。
