[worktree: mwr-derisk-latency | branch: feat/derisk-latency | track: #N（要起票・R-07/R-46）]

# RESULT — Hermes Gateway 司令官呼び出しレイテンシ実測（R-07 / R-46）

4 provider（Claude / GPT / Gemini / Grok）の司令官 LLM 呼び出しレイテンシを Hermes
Gateway 経由で実測し、docs06 が宙吊りにしている2つの設計判断を**測定データで確定**する。

- **判定正本**: [`docs/architecture/06-implementation-phases.md:103-104`](../../docs/architecture/06-implementation-phases.md) —「約120回呼出しの p50/p95/p99 を記録」「**p95 > 2.5秒の場合、サイクル間隔を 4-5秒に変更**」。本レーンは doc06 の「Claude のみ」を**4社へ拡張**する。
- **R-07**: [`docs/shared/07-research-notes.md:176`](../../docs/shared/07-research-notes.md) —「LLM API レイテンシ + **Hermes オーバーヘッド**が3秒サイクルに収まるか」（Phase 0.5）。
- **R-46**: [`docs/shared/07-research-notes.md:256`](../../docs/shared/07-research-notes.md) —「`BLOCKED_TIMEOUT=10秒` は『3サイクル(9秒)余裕』前提だが、p95>2.5s でサイクルが4-5秒に延びると余裕計算が破綻」「`BLOCKED_TIMEOUT` を**サイクル長の関数**(例 `max(10, 3×cycle)`)にしレイテンシ実測後再計算」。

> **本レーンの境界**: 測定と推奨のみ。`CYCLE_WAIT_SEC`/`CYCLE_TIMEOUT_SEC`（`scheduler.py:48,53`）・`blocked_timeout`（`config/warehouse.base.yaml:17`）は**変更しない**（§7 で follow-up へ予告）。閾値は**発明せず**上記 doc を引用する。

---

## §0 測定条件と方法（透明性）

| 項目 | 値 / 方針 |
|---|---|
| 日付 | **PENDING LIVE RUN**（実行時に `measure.py` 出力 `run_utc` を転記） |
| Hermes バージョン | v0.14.0（memory `reference_hermes_v013` / `project_gcp_hermes_deployment`。実行時に確定） |
| Gateway | `127.0.0.1:8642`（loopback・dev。`measure.py` は非 loopback / `WAREHOUSE_ENV=prod` を fail-closed で拒否） |
| 認証 | `API_SERVER_KEY`（Bridge↔Gateway。dev のみ）。**provider 秘密鍵は measure.py が読まない**（Hermes が `~/.hermes/.env` で消費） |
| provider model（doc13:184-195） | anthropic=`claude-opus-4-8` / openai=`gpt-4o` / google=`gemini-2.5-flash` / xai=`grok-4.3` |
| transport | OpenAI SDK で `{base_url}/v1/chat/completions`・`model="hermes-agent"`（`hermes_client.py:3,37,105-106` と同一経路＝**Hermes オーバーヘッド込みの実態**） |
| サンプル数 | n=120/社（doc06:103）。warmup=3 を破棄（cold connection 除外） |
| transport timeout | 60s（**tail を取り切るため**。サイクルの 2.5s 締切=doc08:140 は判定の入力であって測定のクリップではない） |
| percentile 法 | **nearest-rank**（1-indexed・補間なし。`stats.py` で固定・`test_stats.py` で検証）。再現性のため明示 |
| エラー扱い | 非2xx/timeout は latency **分布から除外**し件数を別記（percentile を汚さない）。**ただしエラー/timeout は「ミスサイクル」**（doc08:140＝応答が2.5s 以内に返らねば前回指示継続）＝§2 の **missed-cycle 率**で別途 viability 判定する。**survivor p95 だけで「成立」と読むと、429/timeout の多い provider を誤って合格にする**（survivorship bias） |
| **比較公平性条件（R-36）** | **PENDING**: 下記いずれかを**実行時に宣言**（`--condition`）— ① `fairness-off`（Hermes の memory/skills/session_search OFF。doc08:307-313・[`13-hermes-setup.md`](../../docs/architecture/13-hermes-setup.md) §OFF機構）／② `default`（素の API）。**判定ベースラインは `fairness-off`** を推奨（Phase 4 比較 run は OFF で回る＝サイクル長はその条件で成立する必要がある）。両条件で測れば memory/session_search のレイテンシ影響も可視化できる |

**方法**: `python3.12 spike/latency/measure.py -p <provider> --condition <cond> -n 120`。1 run = gateway の**現 `active_provider` 1社**を測る（measure.py は provider を切替えない）。4社は `active_provider` を1行切替（doc13:175）→ gateway 再起動 → 各社 run（手順 `README.md`）。

---

## §1 結果（4 provider × p50/p95/p99）— **PENDING LIVE RUN**

> 値は**未取得**（fabricate しない＝docs-first「発明しない」）。下表は live run で `out/<provider>_<cond>_120_*.json` の `summary_s` を ms に直して転記する。判定列は §2 のルールで機械的に埋まる。

| provider | model | p50 (ms) | p95 (ms) | p99 (ms) | mean (ms) | max (ms) | err | missed% | floor (ms) | 判定（§2） |
|---|---|---|---|---|---|---|---|---|---|---|
| Claude | claude-opus-4-8 | — | — | — | — | — | — | — | — | — |
| GPT | gpt-4o | — | — | — | — | — | — | — | — | — |
| Gemini | gemini-2.5-flash | — | — | — | — | — | — | — | — | — |
| Grok | grok-4.3 | — | — | — | — | — | — | — | — | — |

- **`err` / `missed%` は判定入力**（参考値ではない）: `missed_cycle_rate = (n_err + 応答>2.5s の本数) / n_requested`（measure.py が算出）。**survivor p95 が 2.5s 以下でも missed% が閾値超ならサイクル不成立**（§2 viability ゲート）。
- `floor` = `GET /v1/models` 往復（LLM 非経由・best-effort）。**真の Hermes オーバーヘッド分解ではない**下限値（§5）。
- **n=120 の p99 注意**: nearest-rank で p99 は第119順位統計量＝**ほぼ第2位**（tail に約1サンプルしか無い）。**判定は p50/p95 で行う**（doc06:104 も p95 が閾値）。p99 を安定させたい場合のみ n≥300 を別途取得（doc06 は ~120 を規定＝本測定は準拠）。

---

## §2 サイクル長判定（doc06:104 / doc08:140 / doc08:121-128）

**「3秒」の正確な意味**: doc08:121-128 で Mode A の**総サイクル長 ~3秒 = 待機1秒 + 応答~2秒**（レスポンス駆動・固定間隔ポーリングではない）。doc06/Issue の「3秒サイクル」はこの**総サイクル長**を指す。サイクル内タイムアウトは **2.5秒**（doc08:140・応答が2.5s 以内に返らねば前回指示継続）。

**判定ルール（機械的）**:

0. **viability ゲート（survivorship-bias 回避・最初に通す）**: 各 provider の **missed-cycle 率** `= (n_err + 応答>2.5s の本数) / n_requested` を計算（doc08:140＝応答が2.5s 以内に返らねば前回指示継続＝そのサイクルは「外した」）。エラーを latency 分布から除いた p95 だけで判定すると、**429/timeout の多い provider を「成立」と誤読**する（survivor p95=2.0s でも 30% ミスならサイクルは実用不能）。**missed-cycle 率 > 閾値なら「provider 不安定/サイクル不成立」**（survivor p95 が良くても 4-5s 化 or provider 見直し）。**閾値は spike-local 前提（暫定 5%・doc 値なし＝発明せず明示。§6 で要確定）**。
1. 各 provider の **p95 を 2.5s（doc08:140）と突合**。
2. **判定ベースライン = 司令官として使う provider 群の worst-case p95**（複数社を比較運用するなら最も遅い社で律速。R-45 §4 参照）。
3. **viability ゲート通過 かつ worst-case p95 ≤ 2.5s** → Mode A 総サイクル **3秒を維持**（待機1s + 応答 が ~3s に収まる）。
4. **viability ゲート不通過 または worst-case p95 > 2.5s** → **サイクル間隔を 4-5秒へ**（doc06:104）/ provider 見直し。新総サイクル長 `cycle_total` は「待機 + p95 応答」を満たす値（例: p95≈3.5s なら待機1.5s+応答3.5s=5s）を **live データから確定**。
5. 結論（維持 or 4-5s・採用値）を**ここに1行で記す**: **PENDING LIVE RUN**。

> 注: 待機（`CYCLE_WAIT_SEC`）は「応答後のアイドル」（doc08:121,47）であり応答時間とは別。総サイクル長 ≈ 待機 + 応答。判定は応答 p95 が 2.5s 締切を破るかで行い、破れば待機+応答を 4-5s に再設計する。

---

## §3 `BLOCKED_TIMEOUT` 関数化推奨（R-46 / doc07:256）

**現状**: `config/warehouse.base.yaml:17` `blocked_timeout: 10.0`（フラット値・`# TODO: Phase 2 実測で確定`）。Emergency Guardian の pose 変位ベース recovery トリガ（[`docs/architecture/12-infrastructure-common.md:193,210`](../../docs/architecture/12-infrastructure-common.md)・`warehouse_safety/guard_logic.py:125`）。「`BLOCKED_TIMEOUT=10秒` は Claude の3サイクル(9秒)分の余裕」前提（[`docs/mode-a/08a-llm-bridge-mode-a.md:372`](../../docs/mode-a/08a-llm-bridge-mode-a.md)）。

**問題（R-46）**: サイクルが 3s→4-5s に延びると「3サイクル=9s 余裕」が破綻（4-5s × 3 = 12-15s > 10s）。司令官が 3 サイクル見逃す前に Guardian が誤発火しうる。

**推奨**: `blocked_timeout` を**サイクル長の関数**にする。`max(10, 3×cycle)` は **(b) docs 例示**（doc07:256・**凍結契約値ではない**）。実測 `cycle_total` で再計算:

| 確定サイクル長 `cycle_total` | `3×cycle` | `max(10, 3×cycle)` | 採用 `blocked_timeout` |
|---|---|---|---|
| 3.0s（維持） | 9.0 | **10.0** | 10.0（現状維持） |
| 4.0s | 12.0 | **12.0** | 12.0 |
| 5.0s | 15.0 | **15.0** | 15.0 |

→ **推奨採用値 = PENDING**（§2 で確定する `cycle_total` を上表に当てる）。**適用は本レーンでやらない**（§7・所有=safety-state/bringup の `config/warehouse.base.yaml`）。

---

## §4 R-45 への含意（モデル格差混在 / doc07:255）

R-45: Claude(Opus) / GPT-4o / **Gemini 2.5 Flash** / Grok で**フラッグシップ級と Flash 級が混在**。比較は「**同格モデルで揃える**」方針（doc07:255）。

レイテンシ測定への含意（live データ取得後に具体化）:

1. **Flash 級は構造的に速い**: `gemini-2.5-flash` は Flash 級＝他の flagship より p50/p95 が小さく出る公算。**レイテンシ順位 ≠ 能力順位**（速さは model-class を反映する）。
2. **サイクル長は worst-case で律速**（§2-2）: 司令官に flagship 級を使うなら、その worst-case p95 でサイクルを設計する。Flash はその枠内に余裕で収まる（過小設計の危険は無いが、Flash 基準でサイクルを詰めると flagship 運用時に破綻）。
3. **Phase 4 比較設計**（doc07:255 の「同格で揃える」）: レイテンシ比較を公平にするなら model-class を揃える（例: 各社の同格 tier）か、tier 差を明示した上で比較する。本測定の生レイテンシ差は**この設計判断の入力**であり、そのまま「LLM の速度ランキング」と読まない旨を Phase 4 設計（doc08:305-317 公平性）に申し送る。
4. 具体的な社間差（どの社が最速/最遅か、Flash の優位幅）は **PENDING LIVE RUN**。

---

## §5 Hermes オーバーヘッド分離（R-07 / doc07:176）

R-07 の核は「LLM API レイテンシ **＋ Hermes オーバーヘッド**が3秒に収まるか」。

- **本測定 = end-to-end のみ**（client → Gateway → active_provider → 応答の wall-clock）。これは Bridge が実際に払う実態（`hermes_client.py` 同一経路）であり、**サイクル長判定にはこれで十分**。
- **真の分解（上流 provider 単体 vs Gateway 加算）は未取得**: provider ネイティブ API を直接叩く baseline（4社別 SDK + provider 鍵）が要るため本スパイクでは実装せず。**正直に「end-to-end のみ」と明記**（docs-first「発明しない」）。
- **`gateway_floor`**（`GET /v1/models` 往復・LLM 非経由）を best-effort で記録＝**Gateway 制御プレーンの下限**。これは routing+LLM のオーバーヘッド分解ではないが、「Gateway 自体は軽い/重い」の粗い目安になる。
- 将来 provider 直 baseline を足すなら: `overhead ≈ e2e_p50 − direct_provider_p50`。**follow-up 候補**（本レーンの DoD 外）。

---

## §6 残未決・暫定値（隠さず列挙）

- **[データ] 全 latency 数値が PENDING**: live run（gateway 起動 + 4社 active_provider 切替 + 各 ~120 paid call ≈ 計 480 call）が**未実施**。本 RESULT は方法・判定ルール・推奨式を**確定済みの枠**として提供し、数値は live で埋める。実行は API コスト + daemon 起動 + `~/.hermes/config.yaml` 編集を伴う**外向き操作**＝ユーザー承認/実行を要する（memory `project_gazebo_sim_milestones`「live実行は鍵注入が harness secret 境界でブロック」）。
- **[条件] fairness-off vs default 未確定**: どちらを判定ベースラインにするかは §0 の通り `fairness-off` 推奨だが、実測時に `--condition` で宣言し本書に転記。
- **[統計] n=120 の p99 は不安定**（§1 注記）。判定は p50/p95。p99 安定化は n≥300 の別取得（doc06 規定外）。
- **[閾値] missed-cycle 率の許容上限 5% は spike-local 前提**（doc 値なし）。docs-first「発明しない」に従い**doc 由来でなく明示的な運用前提**として置いた（measure.py `MAX_MISS_RATE`）。live データ取得時にレビューで確定する（viability 閾値を doc に足すか、Phase 4 比較設計で実測根拠を与えるか）。判定は survivor p95 単独ではなく **p95 ＋ missed-cycle 率**の双方で行う（survivorship bias 回避）。
- **[分解] Hermes オーバーヘッドは end-to-end のみ**（§5）。上流単体は未分離。
- **[env] 実行環境の鍵**: worktree checkout には gitignore された `config/dev/.env` が**無い**（fresh worktree は untracked secret を持たない）。live run は **main checkout から**実行するか `--env-file <main>/config/dev/.env` か `export API_SERVER_KEY=...`（README §実行）。
- **[follow-up] config/scheduler の実変更は本レーン対象外**（§7）。

---

## §7 follow-up（予告のみ・本レーンは「測定+推奨」で閉じる）

§2/§3 の推奨を**実適用**する変更は、所有が異なるため別 Issue（orchestrator/ユーザー承認で起票・create-issue skill §0）:

| 変更 | 対象 | 所有トラック | 根拠 |
|---|---|---|---|
| サイクル長 `CYCLE_WAIT_SEC`/総サイクル設計 | `ws/src/warehouse_llm_bridge/.../scheduler.py:48,53` | **llm-bridge**（`feat/llm-bridge`） | doc06:104 / scheduler.py |
| `blocked_timeout` 関数化（`max(10,3×cycle)`） | `config/warehouse.base.yaml:17` | **safety-state / bringup**（base.yaml 所有=bringup/skeleton・parallel-workflow §7.1） | R-46 / doc12:193 / doc08a:372 |

> 予告は本 RESULT を入力に **両トラックの Issue へコメント**（先頭 worktree タグ）して合意を得てから動く（実装ではなく予告）。`max(10,3×cycle)` は docs 例示＝凍結契約ではないので、確定値は live データで再計算した上で contract でなく config/コード変更として扱う。

---

## 設計正本リンク

- [`docs/architecture/06-implementation-phases.md:103-104`](../../docs/architecture/06-implementation-phases.md)（p50/p95/p99・p95>2.5s→4-5s）
- [`docs/shared/07-research-notes.md:176`](../../docs/shared/07-research-notes.md)（R-07）/ `:255`（R-45）/ `:256`（R-46）
- [`docs/architecture/08-llm-bridge-common.md:121-128`](../../docs/architecture/08-llm-bridge-common.md)（Mode A 総サイクル ~3s 内訳）/ `:140`（in-cycle timeout 2.5s）/ `:305-317`（Phase 4 公平性 OFF）
- [`docs/architecture/13-hermes-setup.md:24`](../../docs/architecture/13-hermes-setup.md)（8642）/ `:175`（active_provider 1行切替）/ `:184-195`（各社 model）
- `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/hermes_client.py:3,37,105-106`（transport）/ `scheduler.py:48,53`（現行 cycle 定数）
- `config/warehouse.base.yaml:17`（`blocked_timeout: 10.0`）/ [`docs/architecture/12-infrastructure-common.md:193`](../../docs/architecture/12-infrastructure-common.md) / [`docs/mode-a/08a-llm-bridge-mode-a.md:372`](../../docs/mode-a/08a-llm-bridge-mode-a.md)
