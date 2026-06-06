# spike/latency — Hermes Gateway 司令官呼び出しレイテンシ実測（R-07 / R-46）

- **担当トラック / ブランチ**: derisk-latency / `feat/derisk-latency`
- **Phase**: 0.5（#156 slice3 録画より前に確定し手戻りを断つ）
- **編集境界**: `spike/latency/**` の**新規ファイルのみ**。`ws/src/warehouse_llm_bridge/**`（特に `scheduler.py:48,53` 定数）・`config/**`・`warehouse_interfaces/**` は**触らない**（測定+推奨のみ／適用は follow-up＝RESULT.md §7）。
- **依存**: なし（pure-python・rclpy/`warehouse_interfaces` 非 import＝完全独立。parallel-workflow §2.1）。`openai` SDK は **lazy import**（`make_caller` 内のみ）で `--dry-run`・unit は SDK 不要。
- **テスト**: `python3.12 -m pytest spike/latency/test_stats.py -q`（pure unit・live SDK/network 非依存）。ruff: `python3.12 -m ruff check spike/latency/`。**安全機構なし＝R-26 対象外**（測定スパイク）。

## 提供 (produce)
- `spike/latency/measure.py` — Hermes Gateway 経由で4 provider（1 run=1社）のレイテンシを ~120 回測定。**dev-only**（非 loopback / `WAREHOUSE_ENV=prod` は fail-closed 拒否）。`API_SERVER_KEY` のみ消費（provider 秘密鍵は読まない）。
- `spike/latency/stats.py` — nearest-rank percentile + summarize（pure・stdlib のみ）。
- `out/<provider>_<condition>_<n>_<utc>.json` — 1 run の report（n_ok/n_err/`n_over_in_cycle_timeout`/**`missed_cycle_rate`**・`summary_s`={p50,p95,p99,mean,min,max,stdev}・gateway_floor・条件・**秘密値は含まない**）。**`out/` は gitignore**（生 dump は非コミット）。`missed_cycle_rate=(n_err+応答>2.5s)/n_requested`＝**viability 判定入力**（survivor p95 単独で「成立」と読まない・doc08:140）。
- `spike/latency/RESULT.md` — 4社 p50/p95/p99 表 + **サイクル長判定**（p95 vs 2.5s, doc06:104/doc08:140）+ **`BLOCKED_TIMEOUT` 関数化推奨**（`max(10,3×cycle)`=docs 例示, R-46/doc07:256）+ **R-45 含意** + **測定条件の透明性**（fairness-off/default, R-36）。**判定の正本ドキュメントであり、live 数値は PENDING**。

## 消費 (consume)
- env / secret: `API_SERVER_KEY`（Bridge↔Gateway 認証。env→`config/dev/.env` の順で解決。**値は print/書込しない**）。`HERMES_BASE_URL`（任意・既定 `http://127.0.0.1:8642`）/ `WAREHOUSE_ENV`（既定 dev・prod は拒否）。
- net: Hermes Gateway `POST /v1/chat/completions`（`model="hermes-agent"`・OpenAI 互換。`hermes_client.py:3,105-106` と同一経路）+ best-effort `GET /v1/models`（gateway floor）。
- 固定値の出所: 閾値は**全て docs 引用**（doc06:104 / doc08:140 / doc07:256）。`max(10,3×cycle)` は **(b) docs 例示**（doc07:256・凍結契約値でない）。`SYSTEM_PROMPT` は `hermes_client.py:44-52` の**fidelity copy**（測定 fixture・contract でない）。`REPRESENTATIVE_SITUATION` は doc08a Situation 形を模した代表 JSON（値は illustrative・SHAPE/size が要点）。
- 契約: **消費しない**（`warehouse_interfaces` 非 import）。**契約変更なし**。

## 測定方法（要点。詳細 README.md）
- 1 run = gateway の現 `active_provider` 1社を測る（measure.py は provider を**切替えない**）。4社は `active_provider` 1行切替（doc13:175）→ gateway 再起動 → 各社 run。
- 測定条件は `--condition fairness-off|default` で**宣言**（R-36/doc08:307-313。判定ベースライン=`fairness-off` 推奨）。
- transport timeout=60s（**tail を取り切る**。2.5s 締切=doc08:140 は判定入力であって測定クリップでない）。warmup=3 破棄。エラーは分布から除外し件数別記。

## 前提・未確定 (TODO / seam)
- **live 数値 PENDING**: gateway 起動 + 4社切替 + ~480 paid call は**外向き操作**＝ユーザー承認/実行。本スパイクは枠（方法・判定ルール・推奨式）を確定し数値は live で埋める。
- **env 鍵 in worktree**: fresh worktree に gitignore された `config/dev/.env` は無い＝live は main checkout から or `--env-file <main>/config/dev/.env` or `export API_SERVER_KEY`。
- **Hermes オーバーヘッド = end-to-end のみ**（上流 provider 単体は未分離・§5）。`gateway_floor` は制御プレーン下限の目安。
- **n=120 p99 は不安定**（第119順位＝ほぼ第2位）。判定は p50/p95。
- **適用（config/scheduler 変更）は本レーン対象外**: サイクル長=llm-bridge / `blocked_timeout`=safety-state/bringup（RESULT.md §7・所有別）。

## 設計ドキュメント
- 判定: `docs/architecture/06-implementation-phases.md:103-104`
- R-07/R-45/R-46: `docs/shared/07-research-notes.md:176,255,256`
- サイクル/timeout/公平性: `docs/architecture/08-llm-bridge-common.md:121-128,140,305-317`
- Hermes: `docs/architecture/13-hermes-setup.md:24,175,184-195`
- transport/現行定数: `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/{hermes_client.py:3,105-106, scheduler.py:48,53}`
