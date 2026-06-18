# Langfuse Phase-3 実トレース検証 結果（doc13:520 ①〜⑤）— **PENDING（未計測 / scaffold）**

実行日: _TBD_ / Langfuse: _4.7.x（実 `__version__` を転記）_ / Hermes: _TBD（`hermes --version`）_ /
project: _minicar（jp.cloud.langfuse.com）_。**live 実測値は human gate（実 Langfuse + Hermes +
4provider 鍵が要る課金ラン）＝未計測**。判定の正本は本ファイル、各 assertion は
[`CHECKLIST.md`](CHECKLIST.md)、論理は `./run.sh selftest`（offline・fake client）で検証済。
**2026-06-15 の operator session `op-langfuse` で live を試行したが、ローカル Hermes が warehouse
4-provider dev gateway ではない（個人 daily-driver の openai-codex/gpt-5.5・`providers: {}` 空・xAI 鍵
不在）ため課金ランを実施せず defer した。実査の詳細・実走に必要な条件は下記 [§実行ログ](#実行ログ--2026-06-15-operator-session-op-langfuselive-未実施の理由環境実査) を参照。**

> **結論一行（実測後に記入）**: _「① inbound trace_id 尊重: 有/無 → ② 4社 cost≠0: 達成/未達
> (Grok 経路=登録/offline) → ③ 二重 generation: 無/有 → ④ managed-prompt: 可/不可 → ⑤ SDK 4.7.x:
> OK/NG → Phase 4 比較の観測前提: 成立/要修正」_

> **この harness が示すこと**: 実 Langfuse(4.7.x) + Hermes に対し、Phase 4 の 4 provider × 3 mode
> 比較が依拠する観測層（1 Bridge-owned trace/サイクル・決定的 trace_id・4社 cost≠0）を **turnkey で
> 検証可能**にする。offline で①〜⑤の**判定ロジック**を fake client で固め（自律完成）、live で
> **実トレースを読み戻して assert**（human gate）。
> **示さないこと（＝Phase 3 を閉じない理由）**: ① read-back の v4 fetch フィールド形は**未確定**
> （[doc08:510](../../docs/architecture/08-llm-bridge-common.md) / [doc20 §8.4 item2](../../docs/architecture/20-dev-quality-and-testing.md)）＝`fetch_ok=N` なら API からは未検証で
> **Langfuse UI 目視が正**。② score metadata `mode` 値域（Mode-letter vs traffic_mode）は #4 と
> Phase 3/4 確定（doc20 §8.4 item1）＝本 harness は判定しない。③ Grok 実価格/literal model 文字列は
> live 確認まで CHECKLIST の**期待値**に留め、`grok_cost.py`（wo PLACEHOLDER）はコード固定しない。

## 環境 / 版数（live 実行時に転記）
| 項目 | 値 |
|---|---|
| Langfuse SDK | **4.7.1**（2026-06-15 local install 確認・`openai 2.41.1`。⑤ の version-range `[4.7,5.0)` は充足。full live smoke は PENDING）|
| Langfuse host / project | jp.cloud.langfuse.com / minicar（[project_api_keys_dev_setup]）|
| Hermes Gateway | _TBD（`hermes --version`・`active_provider` 切替で4社）_ |
| Python | **3.12**（host python3 は 3.7 不可）|
| 測定条件 | dev のみ・`WAREHOUSE_ENV=dev`・loopback gateway（fail-closed） |

## ①〜⑤ 結果（`./run.sh report` → `out/*.json` 集計を転記）
> `Y`=pass `N`=fail `?`=API から未検証（Langfuse UI 確認）。`fetch_ok=N` の行は①②③④が UI 確認のみ。

| provider | ① inbound id | ② cost≠0 | ③ 1 generation | ④ managed-prompt | ⑤ SDK 4.7.x | fetch_ok | 備考 |
|---|---|---|---|---|---|---|---|
| anthropic | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | |
| openai | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | |
| google | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | |
| xai (Grok) | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | cost 経路=登録/offline |

## ② Grok cost 詳細（doc08:506-510）
- live 確認項目（転記）:
  - Hermes が転送する literal `model` 文字列: _TBD_（例 `grok-4.3` / `xai/grok-4.3`）。
  - v4 価格フィールドの形: _TBD_（`prices:{input,output}` ネスト vs flat）。
  - Grok `cost_details.total`: _TBD_ USD（カスタムモデル登録後 or wo offline fallback）。
  - 期待単価（CHECKLIST・取得日 2026-06-11）: `grok-4.3` = $1.25/$2.50 per 1M。**相違あれば
    `grok_cost.py`（wo 所有・PLACEHOLDER $3/$15）の更新を follow-up**（本レーンは触らない）。

## v4 score metadata group-by 可否（doc08:520 / doc20 §8.4 item2）
- live 確認: v4 で score `metadata` に group-by できるか → _TBD_。
- **不可の場合**: doc08:517 の score `name` 符号化（例 `result__claude__open-rmf`）を採用（Phase 4 集計設計）。

## 残課題 / 未決（隠さず列挙・docs-first）
1. **live ①〜⑤ は未計測**（human gate・本表 _TBD_）。harness は turnkey 完成・実測は鍵+Hermes 到着後。
2. **read-back フィールド形が未確定**（doc08:510 / doc20 §8.4 item2）＝`fetch_trace` は防御パース、
   `fetch_ok=N` 時は UI 目視が正。確定したら follow-up で defensive キーを絞る docs 反映。
3. **score metadata `mode` 値域**（Mode-letter vs traffic_mode）= #4 と Phase 3/4 確定（doc20 §8.4 item1）。
4. **Grok 実価格 / literal model 文字列**は live 確認まで CHECKLIST 期待値に留め code 固定しない（doc08:510）。
5. **docs 反映したい知見**（確定した fetch フィールド形・実価格・group-by 可否）は follow-up docs PR で
   doc08/doc13/doc20 に反映（本レーンは docs 本体 read-only）。

## 実行ログ — 2026-06-15 operator session `op-langfuse`（live 未実施の理由・環境実査）

**結論**: live ①〜⑤ は**未実施のまま PENDING**。offline DoD ゲートは緑（`./run.sh selftest` =
ruff + `ruff format --check` + pytest **47 pass / 0 skip**（`langfuse 4.7.1` 導入済の実測。**SDK 未導入時は
46 pass / 1 skip**＝`create_trace_id` 決定性 test が `importorskip` でスキップ〔`test_verify.py:406`〕）。SDK は導入済（`langfuse 4.7.1` / `openai 2.41.1`）・`config/dev/.env` の Langfuse 3 鍵
（`API_SERVER_KEY` / `HERMES_LANGFUSE_PUBLIC_KEY` / `HERMES_LANGFUSE_SECRET_KEY`）は present・
`--dry-run` clean。**ただし live sweep の前提＝「ローカル loopback の warehouse 4-provider dev gateway」が
本マシンに存在しない**ことを実査で確認したため、課金ランを実施せず defer した（ユーザー判断 2026-06-15）。

### 実査した事実（file:line 裏取り）
- ローカル Hermes は**個人用 Hermes Agent v0.15.1**（daily-driver）であり warehouse 専用 gateway ではない:
  - `~/.hermes/config.yaml:1-5` → `model.default: gpt-5.5` / `model.provider: openai-codex` /
    `base_url: https://chatgpt.com/backend-api/codex` / **`providers: {}`（空）** / `fallback_providers: []`。
    `~/.hermes/auth.json:13` → `active_provider: "openai-codex"`。
  - doc13:175 の `active_provider: anthropic|openai|google|xai` ＋ 4-provider `providers:` ブロック（doc13:183-195）は
    **ローカルに無く**、実体は [`deploy/hermes/gcp/config.yaml`](../../deploy/hermes/gcp/config.yaml)
    （GCP prod VPS 34.4.104.112・**非 loopback・prod**）にのみ存在。
- **xAI/Grok 鍵がローカルに無い**: `~/.hermes/.env` の provider 鍵は `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
  `GOOGLE_API_KEY` / `GEMINI_API_KEY` のみで `XAI_API_KEY` **不在** → ② の xai leg は鍵追加
  （`hermes login xai` OAuth or `XAI_API_KEY`）無しには不可。
- v0.15.1 の `hermes model` は**対話 OAuth picker**（`hermes model --help`）であり、doc13:446 の
  `hermes config set active_provider openai`（旧/設計 CLI）では切替できない。provider 切替は daily-driver の
  既定 provider/model（auth.json）を書換える＝個人環境への破壊的変更。
- OpenAI 互換エンドポイント :8642 は `hermes gateway run`（messaging gateway ＋ `API_SERVER_*` の api_server。
  `~/.hermes/.env`: `API_SERVER_ENABLED=true` / `HOST=127.0.0.1` / `PORT=8642`）で立つが、
  `config.yaml: kanban.dispatch_in_gateway: true` のためユーザーの **cron / kanban も同伴起動**＝起動・再起動に副作用あり。
  現況 `~/.hermes/gateway_state.json` → `"gateway_state":"stopped"`。

### live ①〜⑤ を実走するのに必要な条件（どれか）
1. **ローカル dev warehouse Hermes**（推奨）: doc13:169-195 の 4-provider `providers:` ブロックを持つ
   **loopback dev gateway** を個人 daily-driver とは**分離**して用意し、`XAI_API_KEY` を追加 →
   `active_provider` を anthropic/openai/google/xai に切替えつつ `./run.sh verify <provider>`。
2. **GCP prod Hermes 経由**（要・明示 prod 承認）: `verify.py --allow-remote --base-url <gcp>` で 4-provider
   gateway を叩く。**prod 鍵/課金 ＋ dev-only fail-closed の bypass**＝ environments.md の prod 接続規約 gate。
   比較ランに prod/二次 leg を混ぜるバイアス注意（doc13:526 = §7.6「Vertex/二次 leg を比較ランに混ぜない」原則の一般化）。

### stale だった前提（訂正済）
- memory `project_api_keys_dev_setup`（2026-06-03 / Hermes **v0.14.0** 時点）は provider 鍵 **3社**
  〔anthropic / openai / gemini(+google)〕＋ Langfuse ＋ 動作する `hermes-agent` エンドポイントを記録していた。
  2026-06-15 実査では install が**個人 openai-codex daily-driver**（v0.15.1・`providers: {}` 空・`XAI_API_KEY` 不在）に
  なっており、**warehouse 4-provider gateway が即使える**という前提は **stale**（xAI 鍵は当初も無し）。当該 memory に訂正注記を追記。

## 再現
```bash
cd spike/langfuse
./run.sh selftest            # OFFLINE: ruff + pytest（fake client・SDK/鍵/network 不要）= 自律ゲート
./run.sh setup               # pip install langfuse+openai（LIVE のみ）
WAREHOUSE_RUN_ID=run_A_claude_s1 ./run.sh verify anthropic   # LIVE（課金・human gate）
# active_provider を切替えつつ openai/google/xai も → ./run.sh report → 本表へ転記
```
証跡は `out/`（`<provider>_<run_id>_<gen_id>.json`・秘密非含有・gitignore）。

## 設計正本 / 関連
- [docs/architecture/13-hermes-setup.md:512-520](../../docs/architecture/13-hermes-setup.md)（§7.5 trace_id 契約・①〜⑤）
- [docs/architecture/08-llm-bridge-common.md:502-510](../../docs/architecture/08-llm-bridge-common.md)（Grok cost）/ `:489-498`（比較スコア）
- [docs/architecture/20-dev-quality-and-testing.md:79-123](../../docs/architecture/20-dev-quality-and-testing.md)（§8 観測 taxonomy・§8.4 未決）
- [docs/architecture/14-character-llm-negotiation.md:268-320](../../docs/architecture/14-character-llm-negotiation.md)（trace/observation モデル・post-#226）
- 先例: [spike/latency/RESULT.md](../latency/RESULT.md)・[spike/memory-gate/RESULT.md](../memory-gate/RESULT.md)
