# Langfuse Phase-3 実トレース検証 結果（doc13:520 ①〜⑤）— **PENDING（未計測 / scaffold）**

実行日: _TBD_ / Langfuse: _4.7.x（実 `__version__` を転記）_ / Hermes: _TBD（`hermes --version`）_ /
project: _minicar（jp.cloud.langfuse.com）_。**live 実測値は human gate（実 Langfuse + Hermes +
4provider 鍵が要る課金ラン）＝未計測**。判定の正本は本ファイル、各 assertion は
[`CHECKLIST.md`](CHECKLIST.md)、論理は `./run.sh selftest`（offline・fake client）で検証済。

> **結論一行（実測後に記入）**: _「① inbound trace_id 尊重: 有/無 → ② 4社 cost≠0: 達成/未達
> (Grok 経路=登録/offline) → ③ 二重 generation: 無/有 → ④ managed-prompt: 可/不可 → ⑤ SDK 4.7.x:
> OK/NG → Phase 4 比較の観測前提: 成立/要修正」_

> **この harness が示すこと**: 実 Langfuse(4.7.x) + Hermes に対し、Phase 4 の 4 provider × 3 mode
> 比較が依拠する観測層（1 Bridge-owned trace/サイクル・決定的 trace_id・4社 cost≠0）を **turnkey で
> 検証可能**にする。offline で①〜⑤の**判定ロジック**を fake client で固め（自律完成）、live で
> **実トレースを読み戻して assert**（human gate）。
> **示さないこと（＝Phase 3 を閉じない理由）**: ① read-back の v4 fetch フィールド形は**未確定**
> （[doc08:508](../../docs/architecture/08-llm-bridge-common.md) / [doc20 §8.4 item2](../../docs/architecture/20-dev-quality-and-testing.md)）＝`fetch_ok=N` なら API からは未検証で
> **Langfuse UI 目視が正**。② score metadata `mode` 値域（Mode-letter vs traffic_mode）は #4 と
> Phase 3/4 確定（doc20 §8.4 item1）＝本 harness は判定しない。③ Grok 実価格/literal model 文字列は
> live 確認まで CHECKLIST の**期待値**に留め、`grok_cost.py`（wo PLACEHOLDER）はコード固定しない。

## 環境 / 版数（live 実行時に転記）
| 項目 | 値 |
|---|---|
| Langfuse SDK | _TBD（`python3.12 -c "import langfuse;print(langfuse.__version__)"` → ⑤ 判定入力）_ |
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

## ② Grok cost 詳細（doc08:504-508）
- live 確認項目（転記）:
  - Hermes が転送する literal `model` 文字列: _TBD_（例 `grok-4.3` / `xai/grok-4.3`）。
  - v4 価格フィールドの形: _TBD_（`prices:{input,output}` ネスト vs flat）。
  - Grok `cost_details.total`: _TBD_ USD（カスタムモデル登録後 or wo offline fallback）。
  - 期待単価（CHECKLIST・取得日 2026-06-11）: `grok-4.3` = $1.25/$2.50 per 1M。**相違あれば
    `grok_cost.py`（wo 所有・PLACEHOLDER $3/$15）の更新を follow-up**（本レーンは触らない）。

## v4 score metadata group-by 可否（doc08:518 / doc20 §8.4 item2）
- live 確認: v4 で score `metadata` に group-by できるか → _TBD_。
- **不可の場合**: doc08:515 の score `name` 符号化（例 `result__claude__open-rmf`）を採用（Phase 4 集計設計）。

## 残課題 / 未決（隠さず列挙・docs-first）
1. **live ①〜⑤ は未計測**（human gate・本表 _TBD_）。harness は turnkey 完成・実測は鍵+Hermes 到着後。
2. **read-back フィールド形が未確定**（doc08:508 / doc20 §8.4 item2）＝`fetch_trace` は防御パース、
   `fetch_ok=N` 時は UI 目視が正。確定したら follow-up で defensive キーを絞る docs 反映。
3. **score metadata `mode` 値域**（Mode-letter vs traffic_mode）= #4 と Phase 3/4 確定（doc20 §8.4 item1）。
4. **Grok 実価格 / literal model 文字列**は live 確認まで CHECKLIST 期待値に留め code 固定しない（doc08:508）。
5. **docs 反映したい知見**（確定した fetch フィールド形・実価格・group-by 可否）は follow-up docs PR で
   doc08/doc13/doc20 に反映（本レーンは docs 本体 read-only）。

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
- [docs/architecture/08-llm-bridge-common.md:500-508](../../docs/architecture/08-llm-bridge-common.md)（Grok cost）/ `:489-498`（比較スコア）
- [docs/architecture/20-dev-quality-and-testing.md:79-123](../../docs/architecture/20-dev-quality-and-testing.md)（§8 観測 taxonomy・§8.4 未決）
- [docs/architecture/14-character-llm-negotiation.md:268-320](../../docs/architecture/14-character-llm-negotiation.md)（trace/observation モデル・post-#226）
- 先例: [spike/latency/RESULT.md](../latency/RESULT.md)・[spike/memory-gate/RESULT.md](../memory-gate/RESULT.md)
