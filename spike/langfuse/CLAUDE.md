# spike/langfuse — Langfuse Phase-3 実トレース検証 turnkey harness（doc13:520 ①〜⑤）

- **担当トラック / ブランチ**: derisk-langfuse / `feat/derisk-langfuse`（epic #88 の Phase-3 verify サブセット・**Refs #88 / Closes ではない**）
- **Phase**: 3（Phase 4 の 4 provider×3 mode 比較の前提を turnkey 化し律速を外す）
- **編集境界**: `spike/langfuse/**` の**新規ファイルのみ**。`ws/src/warehouse_orchestrator/**`（wo 所有・`grok_cost.py` 価格は意図的 PLACEHOLDER 凍結）・`ws/src/warehouse_llm_bridge/**`（#4 所有・`tracing.py` の trace/provider_tags emit）・`docs/**` 本体・`config/**`・`deploy/**`・`docs/STATUS.md`（orchestrator）は**触らない**（参照のみ／docs 反映は PR follow-up 提案）。
- **依存**: なし（pure-python・rclpy / `warehouse_interfaces` / `warehouse_orchestrator` を**import しない**＝完全独立。parallel-workflow §2.1）。`langfuse` / `openai` SDK は **lazy import**（`make_traced_call` / `sdk_version` / `_require_create_fn` 内のみ）で `--dry-run`・offline selftest は SDK 不要。
- **テスト**: `python3.12 -m pytest spike/langfuse/test_verify.py -q`（pure unit・live SDK/network/keys 非依存）＝**自律完成の核・CI 緑ゲート**。ruff: `python3 -m ruff check spike/langfuse/` + `format --check`。または `./run.sh selftest`。**安全機構（Guardian/Policy Gate）に触れない＝R-26 安全 unit 対象外**（検証スパイク）。

## 提供 (produce)
- `spike/langfuse/verify.py` — Langfuse Phase-3 検証ロジック。**offline 純関数コア**（`normalize_trace_id`/`seed_for`/`derive_trace_id`/`trace_ids_match`/`check_inbound_trace_id`〔①〕/`grok_cost_usd`/`generation_cost`/`cost_is_nonzero`〔②〕/`single_generation`〔③〕/`managed_prompt_linked`〔④〕/`sdk_version_ok`〔⑤〕/`evaluate_trace`）＋ **live driver**（`run_live`/`make_traced_call`/`fetch_trace`/`main`）。**dev-only fail-closed**（`WAREHOUSE_ENV=prod` / 非 loopback gateway を拒否）。秘密は `API_SERVER_KEY` + `HERMES_LANGFUSE_PUBLIC_KEY`/`HERMES_LANGFUSE_SECRET_KEY` のみ消費し**値を print も書込もしない**。
- `spike/langfuse/test_verify.py` — fake Langfuse `create_trace_id` + 合成 readback で①〜⑤ロジックを offline 検証（44 collected = 43 offline 実行 ＋ 1 は langfuse 導入時のみ `importorskip` で実 `create_trace_id` 決定性）。
- `spike/langfuse/run.sh` — `selftest | setup | verify <provider> | report | clean`。`selftest`=offline 自律ゲート（ruff+pytest）。`verify`=**PAID human gate**（loud banner・Hermes liveness hint）。`report`=`out/*.json` を①〜⑤マトリクスに集計（RESULT.md 転記用）。
- `spike/langfuse/CHECKLIST.md` — 各 gated assertion を `根拠doc:line / 期待 / live 取得値（空＝未計測）` で列挙 ＋ **xAI 公開価格**（`grok-4.3`=$1.25/$2.50 per 1M, 取得日 2026-06-11・**期待値であってコード固定ではない**＝doc08:508）。
- `spike/langfuse/RESULT.md` — ①〜⑤ + Grok cost + **v4 score metadata group-by 可否**（doc08:518 / doc20 §8.4.2）の結果欄。**未計測 scaffold＝human run 後に転記**。
- `out/<provider>_<run_id>_<gen_id>.json` — 1 run の report（①〜⑤ verdict・**秘密値を含まない**）。**`out/` は gitignore**（生 dump 非コミット）。

## 消費 (consume)
- env / secret: `API_SERVER_KEY`（Bridge↔Gateway 認証）+ `HERMES_LANGFUSE_PUBLIC_KEY`/`HERMES_LANGFUSE_SECRET_KEY`（Langfuse・`langfuse_sink.py:58-59` と同一）+ `WAREHOUSE_RUN_ID`（#4/#6 共有・trace seed 前半、doc13:519）。env→`config/dev/.env` の順で解決・**値は print/書込しない**。`HERMES_BASE_URL`（任意・既定 `127.0.0.1:8642`）/ `WAREHOUSE_ENV`（既定 dev・prod 拒否）/ `LANGFUSE_GROK_PRICES`（任意・`IN,OUT` USD/token・CHECKLIST 由来＝**注入**）。
- net（live のみ）: Hermes Gateway `POST /v1/chat/completions`（`model="hermes-agent"`・Bridge-owned `langfuse.openai` 経由 doc08:517）+ Langfuse trace read-back（fetch API のフィールド形は**未確定** doc08:508 → 防御パース）。
- 値の出所: trace_id 形式 / `create_trace_id(seed)` / 突合キー = **(b) docs 例示 + SDK 契約**（doc13:516-519、`warehouse_interfaces` 凍結契約**ではない**＝doc13:519）。Grok 価格 = **(b) xAI 公開価格**（doc08:504-508・取得日併記）＝**コードに固定せず注入/CHECKLIST 記録**。
- 契約: **消費しない**（`warehouse_interfaces` 非 import・wo/bridge 内部も非 import）。**契約変更なし**（`contract` ラベル不要・doc13:519）。

## 検証項目（doc13:520 ①〜⑤・offline で論理／live で実測）
| # | 検証 | offline（fake で論理） | live（human gate・実 Langfuse） | 根拠 |
|---|---|---|---|---|
| ① | Hermes が inbound `metadata.trace_id` 尊重（Bridge-owned なら seed 導出 id 一致） | `check_inbound_trace_id` + cross-lane 決定性 `trace_ids_match` | read-back trace id == seed 導出 id | doc13:516-520① |
| ② | 4社 cost≠0（Grok はカスタムモデル登録 or offline fallback） | `cost_is_nonzero` + `grok_cost_usd` 算術（価格注入） | `cost_details.total>0` 全4社 | doc13:520② / doc08:504-506 |
| ③ | 二重 generation 無し（1サイクル=1 generation） | `single_generation`（2 gen→False） | read-back の generation 数=1 | doc13:520③ / doc08:517 |
| ④ | managed-prompt `prompt=` 連携 | `managed_prompt_linked`（防御キー） | generation に prompt link | doc13:520④ / doc14:320 |
| ⑤ | SDK 4.7.x スモーク | `sdk_version_ok`（[4.7,5.0)） | `langfuse.__version__` 実測 | doc13:514,520⑤ |

## 前提・未確定 (TODO / seam)
- **live ①〜⑤ は PENDING（human gate）**: 実 Langfuse(4.7.x) + Hermes(:8642) + 4provider キー必須＝**外向き・課金・harness secret 境界**＝agent 実行不可。本スパイクは harness を turnkey にするまでが DoD、live 実測値は RESULT 空欄（`spike/memory-gate` #187 / `spike/latency` #200 と同じ完了定義）。**同窓で回せる**（latency live と同じ Hermes 4provider 叩き）。
- **read-back フィールド形が未確定**（doc08:508 / doc20 §8.4.2）: `fetch_trace` は候補キーを**防御パース**し raw を記録、`fetch_ok=False` 時は①②③④を UNVERIFIED と loud 表示（推測で pass させない）。最終確認は Langfuse UI ＋ human。
- **Grok 価格はコード固定しない**（doc08:508）: `grok_cost_usd` は価格を**引数注入**、`grok_cost.py`（wo PLACEHOLDER）は**触らない**。期待値は CHECKLIST に（`grok-4.3`=$1.25/$2.50 per 1M, 取得日 2026-06-11）。literal model 文字列も live 確認まで固定しない。
- **score metadata `mode` 値域未確定**（Mode-letter A/B/C vs traffic none/simple/open-rmf）＝#4 と Phase 3/4 確定（doc20 §8.4.1）。本スパイクは判定しない（trace tag taxonomy は #4/#6 所有）。
- **env 鍵 in worktree**: fresh worktree に gitignore された `config/dev/.env` は無い＝live は `--env-file <main>/config/dev/.env` or `export` で渡す（latency と同じ）。

## 設計ドキュメント（正本・実 Read で pin 済）
- **trace_id / 突合キー契約**: `docs/architecture/13-hermes-setup.md:512-520`（§7.5。:516 32hex-no-dash / :517 Bridge-owned・Hermes プラグイン無効化 / :518 gen_id+timestamp / :519 `create_trace_id(seed)` 決定的同一・ROS 契約でない / **:520 ①〜⑤**）。
- **Grok cost**: `docs/architecture/08-llm-bridge-common.md:500-508`（:502 既定価格表に xAI 無し / :504 カスタムモデル登録 / :505 offline fallback / :506 Phase3 cost>0 assert / :508 フィールド形・literal model は推測固定しない）。比較スコア: `:489-498`。
- **観測 taxonomy**: `docs/architecture/20-dev-quality-and-testing.md:79-123`（§8。:83 正本=doc08/doc13 / :87-95 弁別子→格納先 / :117-123 §8.4 未決＝mode 値域 / group-by 可否 / 実 Langfuse でしか検証不可）。
- **trace/observation モデル**: `docs/architecture/14-character-llm-negotiation.md:268-320`（post-#226。:296 robot=observation 属性 / :316-320 Phase3 暫定＝inbound trace_id 尊重 / prompt・Grok cost・SDK smoke は doc14 範囲外＝doc08/doc13/#88 が担当）。
- **wo 実装（READ-ONLY・突合対象・import 禁止）**: `ws/src/warehouse_orchestrator/warehouse_orchestrator/{trace_id.py,grok_cost.py,langfuse_sink.py,tags.py,score_send.py}` ＋ `CLAUDE.md`（Phase 3 残課題の wo 側宣言）。
- **spike 先例（構造の手本）**: `spike/latency/{measure.py,stats.py,test_stats.py,CLAUDE.md}`（pure helper + dev-only guard + lazy SDK）/ `spike/memory-gate/{run.sh,RESULT.md}`（subcommand + loud FLOOR + 未計測 scaffold）。
