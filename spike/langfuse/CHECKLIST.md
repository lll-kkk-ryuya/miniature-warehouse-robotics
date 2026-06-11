[worktree: mwr-derisk-langfuse | branch: feat/derisk-langfuse | track: #88]

# Langfuse Phase-3 検証チェックリスト（doc13:520 ①〜⑤ ＋ Grok cost）

> 各 gated assertion を `[ ] 項目 — 根拠doc:line — 期待結果 — live 取得値（空＝未計測）` で列挙。
> **offline 列 = harness が fake で検証済の論理**（`./run.sh selftest`）。**live 列 = human gate で
> 実 Langfuse(4.7.x)+Hermes+4provider 鍵が要る**（`./run.sh verify`）。値は転記したら `RESULT.md`
> を正本にする。**コードに価格/フィールド形を焼かない**（doc08:508）— 本書は期待値の記録のみ。

## ①〜⑤（doc13:512-520 §7.5）

### ① Hermes が inbound `metadata.trace_id` を尊重（Bridge-owned → seed 導出 id 一致）
- 根拠: `docs/architecture/13-hermes-setup.md:516-520`（:516 32hex-no-dash・`create_trace_id(seed)` / :517 Bridge-owned Pattern A / :520①）/ `docs/architecture/14-character-llm-negotiation.md:318`（Hermes が inbound trace_id を尊重するか＝Phase3 暫定）。
- offline 検証済: `check_inbound_trace_id` + cross-lane 決定性 `trace_ids_match`（同一 seed `f"{run_id}:{gen_id}"` → 両脚 byte 一致）。
- 期待（live）: read-back した generation の trace_id == `create_trace_id(seed=f"{WAREHOUSE_RUN_ID}:{gen_id}")`。尊重しない場合は司令官 generation が別 trace に落ち、`gen_id`+timestamp 突合へ降格（doc13:518 / doc14:318）。
- [ ] live 取得値: _未計測_

### ② 4社とも `usage_details`/cost ≠0（xAI Grok はカスタムモデル定義 or offline fallback）
- 根拠: `docs/architecture/13-hermes-setup.md:520②` / `docs/architecture/08-llm-bridge-common.md:502-508`（:502 既定価格表に xAI 無し / :504 カスタムモデル登録 / :505 offline fallback / :506 `cost_details.total>0` assert / :508 価格フィールド形・literal model 未確定）。
- offline 検証済: `cost_is_nonzero` + `grok_cost_usd`（`tokens × 注入価格 → USD`・alias キー防御パース・bool 除外・0境界）。
- 期待（live）: claude/openai/google は Langfuse 既定価格表で cost>0、**xai は (a) カスタムモデル登録 or (b) wo offline fallback** で cost>0。1社でも空欄なら比較破綻。
- [ ] live 取得値（4社の cost_details.total）: _未計測_

### ③ 二重 generation 無し（1サイクル = 1 generation）
- 根拠: `docs/architecture/13-hermes-setup.md:520③`,:517（Hermes 側 Langfuse プラグイン無効化＝二重計上回避）。
- offline 検証済: `single_generation`（generation 数==1。2 個 = Hermes プラグインが Bridge-owned trace に重畳した失敗形 → False）。
- 期待（live）: 1 司令官サイクル trace に generation はちょうど 1 個。Hermes ビルトイン Langfuse プラグインが off（deploy ハンドオフ・別所有）であることの実証。
- [ ] live 取得値: _未計測_

### ④ managed-prompt `prompt=` 連携の可否
- 根拠: `docs/architecture/13-hermes-setup.md:520④` / `docs/architecture/14-character-llm-negotiation.md:320`（prompt 連携は doc14 範囲外＝doc08/doc13/#88 が担当）。
- offline 検証済: `managed_prompt_linked`（候補キー `prompt`/`prompt_name`/`promptName` を防御パース）。
- 期待（live）: Langfuse Prompt Management の prompt を `prompt=` で渡し、generation に prompt link が載るか。**フィールド形は未確定**（防御パース）。
- [ ] live 取得値（prompt link フィールド名 + 連携可否）: _未計測_

### ⑤ SDK 4.7.1 スモーク
- 根拠: `docs/architecture/13-hermes-setup.md:514`（v4 = 4.7.1, OTEL ベース）,:520⑤。
- offline 検証済: `sdk_version_ok`（`[4.7, 5.0)` 範囲判定）。
- 期待（live）: `langfuse.__version__` が `>=4.7,<5`、`create_trace_id`/`get_client`/`create_score`/`langfuse.openai` が import 可・基本動作。
- [ ] live 取得値（実 `langfuse.__version__`）: _未計測_

## Grok カスタムモデル価格（doc08:504・xAI 公開価格）

- **取得**: `https://docs.x.ai/developers/models/grok-4.3`（xAI 公開価格）— **取得日 2026-06-11**。
- **`match_pattern`（doc08:504 例）**: `(?i)^(xai/)?grok-4.*$`（現公開 model `grok-4.3` にマッチ）。
- **単価（USD per 1M tokens）**:

| model（公開） | input /1M | output /1M | cached input /1M | per-token（注入用 IN,OUT） |
|---|---|---|---|---|
| `grok-4.3` | **$1.25** | **$2.50** | $0.20 | `0.00000125,0.0000025` |
| `grok-build-0.1`（参考） | $1.00 | $2.00 | — | `0.000001,0.000002` |

- `unit: TOKENS`。ユーザ定義価格は組込より優先（doc08:504）。
- **⚠️ 未確定（doc08:508・推測で固定しない）**:
  - **Hermes が Grok に転送する literal `model` 文字列**（`grok-4.3` か `xai/grok-4.3` か別か）= live 確認。wo `grok_cost.py` の表は `grok-4`/`grok-3` の **PLACEHOLDER**（値 $3/$15 per 1M・本書実価格と相違）＝live verify で `grok_cost.py` の値とパターンを更新する（**本レーンは触らない**・wo 所有）。
  - **v4 価格フィールドの形**（`prices:{input,output}` ネスト vs flat `input_price`/`output_price`）= live 確認。
  - 本書の単価は**期待値**。verify.py は価格を `--grok-prices`/`LANGFUSE_GROK_PRICES` で**注入**（コード固定しない）。

## v4 score metadata group-by 可否（doc08:518 / doc20 §8.4 item2）
- 期待（live）: v4 で score の `metadata` に group-by できるか。**不可なら** doc08:515 の score `name` 符号化（例 `result__claude__open-rmf`）を採用。
- [ ] live 取得値: _未計測_

## 完了条件（本 harness の DoD）
- [x] offline selftest 緑（`./run.sh selftest` = ruff + pytest・fake client）＝**自律完成の核**。
- [x] xAI 公開価格を取得し期待値を記録（上表・取得日 2026-06-11）。
- [ ] live ①〜⑤ + Grok cost + group-by = **human gate（PENDING）** → `RESULT.md` 転記。
