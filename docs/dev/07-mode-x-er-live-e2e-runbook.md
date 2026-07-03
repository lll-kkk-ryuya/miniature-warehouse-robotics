# 07 — Mode X-ER live ER → L3 e2e operator runbook（音声/画像 → ER → L3 → Langfuse）

> **位置づけ**: これは「**どう動かすか**（運用手順）」の operator runbook であり、`docs/dev/02-operator-runbook.md`（汎用オペ手順）の Mode X-ER live 専用版。設計の正本は `docs/mode-x-er/`（提案）と `docs/productization/02-l4-robotics-bridge-box.md`、テスト境界の正本は [`.claude/rules/llm-observability-testing.md`](../../.claude/rules/llm-observability-testing.md) / [`.claude/rules/environments.md`](../../.claude/rules/environments.md)。本書は **turnkey 化（1コマンドで起動できる状態）** だけを目的にし、新しい契約・しきい値・トピックは発明しない。
>
> **現状（2026-06-30 時点・honest）**: offline L3 chain（envelope → handoff → Validator → `ValidationReport`）は **main(e920829) にマージ済**（#366）で、下流 forerunner の Visual Resolver（XER3 #339）も main 在（§T-OFFLINE）。一方 **live ER 実走は依然 human-gate**（課金 provider call。§3）だが、オペレーターが key を `~/.zshenv` に恒久プロビジョン済なら **agent が自走で回せる**（§4.5）。「音声 → ER → Langfuse → Validator」のデモはまだ **一本の線では繋がっていない**（live は handoff 止まり・§1・§5 の honest limits を必ず読む）。

---

## 0. 3-tier reality（期待値を正直に揃える）

Mode X-ER の「ER → L3 → 観測」は **3つの層**に分かれ、成熟度がそれぞれ違う。混同しないこと。

| Tier | 何を証明するか | 状態 | gate |
|---|---|---|---|
| **T-OFFLINE** | envelope → handoff → validator → accept/reject（L3 e2e） | **DONE・main(e920829) マージ済（#366）**。下流の Visual Resolver（XER3 #339）も main 在 | autonomous（network 無し） |
| **T-LIVE ER→Handoff** | 実 ER 呼出 → L3 handoff（`RoboticsPlanDraft` まで） | **EXISTS（main・env-gated）**。Validator には**到達しない**。adapter live-send 機構（`build_provider_request`/`ErTransportSender`/`_live_send`, #389）は main 着地済（ad563de）＝残りは稼働 Bridge cycle が `propose_plan` を呼ぶ wiring（XER6） | human-gate（課金 provider call） |
| **T-LIVE ER→Langfuse** | ER leg を Langfuse trace に乗せる | **tracer は実装済だが既定 OFF**（`LangfuseTranscriptTracer` は `eval_sdk` seam で実 span を組むが `enabled=False` 既定の no-op、`observability.py:88,106`）。実 trace 着地は未検証 | HLF spike / human-gate（#88） |

### T-OFFLINE — Validator まで offline で到達できる（#366 で land）

- 正本コード: `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/pipeline.py` の `validate_raw_output`（`pipeline.py:43`）。これが `RawModelOutput → to_robotics_plan_draft`（handoff、`handoff.py:114`）→ `PlanValidator.validate`（XER2 validator）→ `ValidationReport` を **1関数**に連結する（`pipeline.py:1-29`）。`status != accepted` で `command_candidates == []`（R-26 0-dispatch、`pipeline.py:57-59`）。
- 正本テスト: `tests/unit/test_l3_pipeline.py`。accept 経路（両 transport、`test_l3_pipeline.py:48-55`）、handoff の fail-closed（forbidden endpoint / 低レベル action / coordinate goal / unknown schema が **Validator 到達前に `ValueError`**、`test_l3_pipeline.py:61-70`）、Validator の意味判定（unknown robot REJECTED・emergency_stop・needs_clarification = いずれも 0 dispatch、`test_l3_pipeline.py:76-107`）。
- **`pipeline.py` / `tests/unit/test_l3_pipeline.py` / `robotics_planning_core/validator/` は main(e920829) に land 済（#366 マージ済）**（検証済: `validator/validator.py:90` の `PlanValidator.validate`・`pipeline.py:43` の `validate_raw_output` が main に存在）。**Validator は OFFLINE で end-to-end に到達可能**で、これは仮定でなく**今 main で動く**（`python3.12 -m pytest tests/unit/test_l3_pipeline.py`、network 無し）。
- **下流（forerunner）も main 在**: Visual Resolver（XER3 #339、`robotics_planning_core/visual_resolver/resolver.py:137` の `resolve(plan, calibration) -> ResolutionResult`、network 無しの offline core）と Task Graph Executor（XER4 #340、`robotics_planning_core/task_graph_executor/`）が main に land 済。ただし両者は **`pipeline.py` に未配線**（XER5/XER6 が `command_candidates` の下流で繋ぐ。`pipeline.py:16-23` の scope 注記）。よって offline chain は **`ValidationReport` で終端**のまま（resolver は別 entry で単体到達可能）。

### T-LIVE ER→Handoff — 実 ER は handoff で止まる（main・env-gated）

- 正本: `tests/live/test_er_handoff_live.py`（**main 在**）。`WAREHOUSE_LIVE_ER=1` でないと module ごと skip（`test_er_handoff_live.py:33-37`）。実 `gemini-robotics-er-1.6-preview`（`test_er_handoff_live.py:45`）を呼び、その**生レスポンス**を `to_robotics_plan_draft` に流して `RoboticsPlanDraft` を組む（`test_er_handoff_live.py:107-116`）。
- **重要**: この live 経路は **`RoboticsPlanDraft` で終わる**。`validate_raw_output` / Validator は**呼ばない**（= live で Validator まで通すのは今日できない。§5）。

### T-LIVE ER→Langfuse — tracer は実装済だが既定 OFF（live 着地は #88 human-gate）

- `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/observability.py` の `LangfuseTranscriptTracer`（`observability.py:68`）は **実装済**: `record_transcript` は `eval_sdk` の Langfuse seam（`LangfuseTracer._client/_open/_propagate_attributes/_close`）で ER/transcript leg の実 span を組む（`observability.py:110-144`、fail-open で decision turn とは別の決定論 seed に anchor、`observability.py:73-74,113-124`）。ただし **既定 `enabled=False`**（`observability.py:88`）で、`record_transcript` 冒頭が `if not self._enabled: return`（`observability.py:106-107`、pure no-op で langfuse に一切触れない）＝**既定では OFF**。**実 Langfuse への trace 着地は依然 human-gate（#88、`observability.py:16-17`）で未検証**（TODO/雛形の残骸は無し・tracer 自体は本物）。
- 音声 leg は **direct ER**（Hermes を迂回）するため、Hermes 内蔵 Langfuse plugin はこの leg を観測できない（`observability.py:10-15`、`docs/mode-x-er/06-unfrozen-contract-resolutions.md:162`）。
- **今日の実 Langfuse owner は commander / `eval_sdk` 経路**: `tests/live/test_langfuse_trace_tags_live.py`（**main 在**）が `eval_sdk.tracer.LangfuseTracer` で実 trace を投げて読み戻す（`test_langfuse_trace_tags_live.py:14-15,45-48`）。ER leg 専用の Langfuse owner は **未実装**。
- 例外（spike）: Hermes-plugin の **Option-D** 経路（§2.5）。`input_audio` fork ＋ plugin で **audio leg が Hermes を通り**、plugin が決定論 seed で trace を mint することは **2026-06-27 に live 観測済**（#360 spike、`deploy/dev/hermes-er/config.lean.yaml:57-63`、`spike/langfuse-plugin-d/verify_d_audio.py`）。ただし literal な HLF-G0 verdict（inbound trace_id を honor するか）と #6 scorer-leg の join は **human-gate 未検証**。

---

## 1. Turnkey live steps（オペレーターが scoped 承認後に踏む順番）

> 前提: `hermes` CLI が PATH 上にあること（`run-er-hermes.sh:30-33`）。provider key は **shell に export**（ファイルから agent に読ませない。§3・§4）。

### Step A. 専用 lean ER gateway を立てる（標準 = 8644 fork gateway・全 modality）

```bash
export GOOGLE_API_KEY=...        # または GEMINI_API_KEY。値は echo しない（run-er-gateway.sh:42-43）
deploy/hermes/er-audio-fork/run-er-gateway.sh   # 隔離 worktree→patch→lean gateway 起動（fg・port 8644・text+image+input_audio）
```

- **標準 = 8644 fork gateway（全 modality: text+image+input_audio）**: 既定 port **8644**（`run-er-gateway.sh:56`）、active model **`gemini-robotics-er-1.6-preview`** / provider `google`（`deploy/dev/hermes-er/config.lean.yaml:26-27`）、tools=[]・memory off の **lean transport**（`config.lean.yaml:30-35`）。text/image に加え `input_audio` を native 受理（`deploy/hermes/er-audio-fork/0001-input_audio-passthrough.patch`・`_AUDIO_PART_TYPES`。**wav-only first**。unforked Hermes は 400＝§5-4）。
- **隔離（専用 home）**: 隔離 worktree ＋ **`HERMES_HOME=~/.hermes-mwr-er-fork`**（`run-er-gateway.sh:57`・unforked の `~/.hermes-mwr-er-lean` と別 home＝fork は unforked の 8643 .env を継承しない・`ensure_env_port()` が起動時 8644 に再固定）。個人 `~/.hermes` も GCP prod（`gemini-2.5-flash` 司令塔）も ER でないため**転用しない**＝Hermes は server-side 単一 active model ゆえ ER 専用 gateway が要る（`run-er-gateway.sh:38-43`、`test_er_handoff_live.py:13-17`）。
- **fork-free fallback**（audio 不要・text/image のみ）: `deploy/dev/run-er-hermes.sh`（port **8643**・非 fork・**別 home `~/.hermes-mwr-er-lean`**・deprecated＝同 launcher EOF banner）。以降 §Step B/C が挙げる `8643` は **fallback 経路の port**で、標準 fork 経路では **8644** に読み替える。
- Bridge 側は標準で `http://127.0.0.1:8644/v1` に gateway の `API_SERVER_KEY` で繋ぐ（token 値は表示されない。fallback 経路なら 8643）。

### Step B. 起動前 preflight（full stack 前に必ず）

```bash
deploy/dev/check-hermes-live.sh --base-url http://127.0.0.1:8643 --skip-container
```

- `/health`（`check-hermes-live.sh:175-180`）と **認証付き `/v1/models`**（`check-hermes-live.sh:182-188`）を確認する。secret 値は出さない設計（`check-hermes-live.sh:5-7`）。
- 既定 `--base-url` は **`http://127.0.0.1:8642`**（Mode-A gateway 用、`check-hermes-live.sh:22`）なので、ER gateway を見るには **`--base-url http://127.0.0.1:8643` を明示**する。
- `--skip-container` は container→Hermes 到達確認をしない（host 直叩きのみ。`check-hermes-live.sh:113-116`）。Docker container から確認したい場合は `--container <name>` を付け、container 側は `http://host.docker.internal:8643` を見る（`environments.md:21` の 8642 と同じ仕組み・port のみ差替）。
- `--chat` は最小 `/v1/chat/completions` smoke を**実際に**叩く＝**provider quota を消費**する（`check-hermes-live.sh:117-120,190-201`）。これは課金 call なので **§3 human-gate**。preflight 段階では付けない。

### Step C. live ER probe を走らせる（課金・human-gate）

```bash
WAREHOUSE_LIVE_ER=1 GEMINI_API_KEY=... \
  python3.12 -m pytest tests/live/test_er_handoff_live.py -s
```

- env var 名: **`WAREHOUSE_LIVE_ER=1`**（module gate、`test_er_handoff_live.py:33`）＋ provider key **`GEMINI_API_KEY`**（無ければ `GOOGLE_API_KEY`、`test_er_handoff_live.py:46`）。usage は test の docstring が正本（`test_er_handoff_live.py:19-22`）。
- direct（`generateContent`）と OpenAI 互換（`/v1beta/openai/...`）の両 envelope を実 ER で叩き、どちらも同じ `RoboticsPlanDraft` に正規化されることを示す（`test_er_handoff_live.py:103-125,162-182`）。
- **Hermes-gateway 経路**（Step A の gateway を通す）も検証したい場合: 同 test に `HERMES_BASE_URL=http://127.0.0.1:8643` ＋ `HERMES_API_KEY=<gateway の API_SERVER_KEY>` を渡す（`test_er_handoff_live.py:185-199`）。無ければその test だけ skip。
- audio-direct probe は `MWR_ER_AUDIO=<wav/aiff>` を追加（`test_er_handoff_live.py:309-327`）。macOS でのクリップ生成は test docstring 参照（`test_er_handoff_live.py:316`）。

### Step D. STT lane（任意・out-of-band。ER を止めない realtime transcript）

```bash
HERMES_DASHBOARD_SESSION_TOKEN=<tok> deploy/dev/run-er-stt-http.sh   # 素 uvicorn @ :9119
```

- Hermes web app の `/api/audio/transcribe` だけを UI build 無しで loopback 起動（port 9119、`run-er-stt-http.sh:21,35`）。`/api/` は `X-Hermes-Session-Token` 必須（`run-er-stt-http.sh:7-11`）。
- two-lane probe（ER ∥ Hermes-STT）は `MWR_ER_AUDIO` ＋ `HERMES_DASHBOARD_URL=http://127.0.0.1:9119` ＋ 同 token を Step C に追加（`test_er_handoff_live.py:376-404`）。STT は **本線外**で、ER plan を**ブロックしない**（`docs/mode-x-er/06-unfrozen-contract-resolutions.md:164`、#351 land 済）。

### Step E. Langfuse leg（別 human-gate）

今日の実 Langfuse owner（commander / eval_sdk）を実 credential で確認する経路:

```bash
WAREHOUSE_LIVE_LANGFUSE_TAGS=1 LANGFUSE_PUBLIC_KEY=... LANGFUSE_SECRET_KEY=... \
  python3.12 -m pytest tests/live/test_langfuse_trace_tags_live.py -s
```

- gate env: **`WAREHOUSE_LIVE_LANGFUSE_TAGS=1`**（`test_langfuse_trace_tags_live.py:42`）＋ **`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`**（`test_langfuse_trace_tags_live.py:46-47`）。実 trace を投げて API で読み戻し、tag/session/metadata を assert（`test_langfuse_trace_tags_live.py:84-93`）。これは **ER leg ではなく** eval_sdk tracer の owner 検証。

---

## 2.5. Option-D（Hermes-plugin が ER leg trace を持つ）— spike 専用・human-gate

ER（特に audio）leg を Hermes plugin に観測させる **実験経路**。turnkey の主線ではない。

```bash
deploy/hermes/er-audio-fork/hlf-g0-langfuse/run-er-gateway-langfuse.sh --probe
```

- `input_audio` fork を当てた lean ER gateway ＋ Hermes 内蔵 Langfuse plugin ON（`run-er-gateway-langfuse.sh:1-5`）。langfuse SDK は**隔離 dir**（`/tmp/mwr-hlf-g0-langfuse-libs`）に `pip install --target` され、個人 venv / `~/.hermes` を汚さない（`run-er-gateway-langfuse.sh:97-107`）。
- Langfuse creds は `$HERMES_HOME/.env` の `HERMES_LANGFUSE_PUBLIC_KEY` / `_SECRET_KEY`（`run-er-gateway-langfuse.sh:113-120`）。**無ければ plugin は fail-open（trace 出ない・crash しない）**。
- **honest status**: `--probe` は「gateway が plugin 込みで起動し input_audio が 200 を返す」までしか検証しない。**trace が Langfuse に着地したか / HLF-G0 verdict（inbound trace_id を honor するか・mint か・workaround か）は human-gate**（`run-er-gateway-langfuse.sh:19-36,503-519`）。plugin source 上 trace_id は `create_trace_id(seed="<session_id>::<task_id>")` で **mint**（inbound を読まない）であり、#6 が同 seed を再構成して score を join できるかが live で要確認（同上）。
- 注意: 主線 launcher `deploy/dev/run-er-hermes.sh` が当てる `config.lean.yaml` は plugin key を**列挙はする**が、その launcher は langfuse を PYTHONPATH に載せない＝そこでは plugin が fail-open（`config.lean.yaml:43-48`）。**plugin-owned trace は Option-D launcher 経由でのみ意味を持つ**。

---

## 3. Gate map（どの操作がどの gate か）

正本: [`.claude/rules/environments.md`](../../.claude/rules/environments.md) / [`.claude/rules/llm-observability-testing.md`](../../.claude/rules/llm-observability-testing.md)。

| 操作 | gate | 根拠 |
|---|---|---|
| `tests/unit/test_l3_pipeline.py`（offline e2e）/ fake・noop・unit | **autonomous（CI 必須）**。network・実 provider・実 Langfuse・credential を必須化しない | `llm-observability-testing.md:30` |
| `check-hermes-live.sh`（`/health` + 認証付き `/v1/models`） | **env-gated live smoke**（Gateway 起動確認。Hermes が立っていれば PASS） | `llm-observability-testing.md:31`、`environments.md:20` |
| `check-hermes-live.sh --chat` / live ER probe（`WAREHOUSE_LIVE_ER=1`、実 `gemini-robotics-er`） | **human-gate（課金 provider call）**。CI に入れない・明示 opt-in | `llm-observability-testing.md:32`、`check-hermes-live.sh:190-201` |
| 実 Langfuse trace（`WAREHOUSE_LIVE_LANGFUSE_TAGS=1`）/ trace・score・cost・managed-prompt | **human-gate（Langfuse Phase-3 = #88）**。Hermes smoke で代替しない | `llm-observability-testing.md:33` |
| `config/<env>/.env` / `~/.hermes/.env` を読む | **human-gate（明示スコープ承認）**。値は表示しない。承認無ければ `.env.example`＋docs のみ | `environments.md:24` |
| Option-D plugin trace（`run-er-gateway-langfuse.sh`） | **human-gate（HLF spike）**。probe は build-confidence のみ、trace 着地・verdict は人が記録 | `run-er-gateway-langfuse.sh:503-519` |

> credential file は agent が読まない。必要な値は **ユーザーが実行環境へ export した env var** を使う（`llm-observability-testing.md:37`）。`API_SERVER_KEY` / `LANGFUSE_*` / provider key は log・pytest failure・PR コメントに**出さない**（`llm-observability-testing.md:38`）。

---

## 4. Scoped-approval ask（オペレーターが承認する文言）

live ER / Langfuse 実走で agent が `config/<env>/.env` 等を読む必要がある場合、オペレーターは **対象 path と目的を含む明示スコープ承認**を出す（`environments.md:24`）。値は決して表示しない。承認の雛形（このまま使える）:

```
[承認] live ER→L3 e2e のため、以下を許可する:
  - 読取対象 path : config/dev/.env （API_SERVER_KEY / HERMES_API_KEY のみ）
  - 目的         : deploy/dev/check-hermes-live.sh の Bridge token preflight
  - 表示禁止     : 値は出力・log・PR に一切出さない
  - 期限         : この live セッション中のみ
provider key（GOOGLE_API_KEY / GEMINI_API_KEY）はファイルでなく shell export で渡す。
```

**secret-guard fallback（agent が `.env` 読取を止められる場合）**（`environments.md:25`）:

```bash
# 1) ユーザーの shell 側で token を export（agent はファイルを読まない）
export API_SERVER_KEY=...        # = Hermes Gateway の API_SERVER_KEY
export HERMES_API_KEY="$API_SERVER_KEY"
# 2) .env 読取を無効化して起動
MWR_HERMES_ENV_FILE=/nonexistent deploy/dev/check-hermes-live.sh \
  --base-url http://127.0.0.1:8643 --skip-container
```

- worktree に `config/dev/.env` が無い場合は `--env-file /path/to/config/dev/.env` または `MWR_HERMES_ENV_FILE=...` を使う（`environments.md:25`、`check-hermes-live.sh:34,139-143`）。
- ER provider key は **常に shell export**（`GOOGLE_API_KEY` / `GEMINI_API_KEY`）。`run-er-hermes.sh` も `test_er_handoff_live.py` も env から読み、値を printf しない（`run-er-hermes.sh:25-29`、`test_er_handoff_live.py:46`）。

---

## 4.5. Agent-autonomous live ER（permanent setup）

通常 live ER probe は human-gate（§3）だが、**オペレーターが provider key を恒久 env プロビジョン済**なら、agent は session / worktree ごとの再 setup や **api-key 再入力なし**に live ER を回せる。ただし live ER は**有料 provider call**ゆえ **standing の無承認自走はしない**＝agent は実行前にその **batch / task の cost をオペレーターに確認**してから走らせる（恒久化が解くのは「鍵の再入力」だけで、「課金判断」は都度のまま）。前提と手順を以下に固定する。

### 前提（オペレーターが一度だけ行う）

- オペレーターが **`~/.zshenv` に一度だけ** `export GEMINI_API_KEY=...` を追記する（machine-wide。`~/.zshenv` は全 zsh シェルが起動時に読むため、以後の **どの worktree / session / agent shell も自動でこの key を継承**する。repo には絶対に置かない＝`config/<env>/.env` でも repo tracked file でもない）。
- **agent は `.env` を読まない**し、**key 値を echo / log / PR に一切出さない**（§3 末尾・`llm-observability-testing.md:37-39` と一体。恒久プロビジョンは「読む場所」を `.env` から env へ移すだけで、no-display 原則は変わらない）。

### 手順（agent が自走できる）

direct `generateContent` leg は **key ＋ network だけ**で完結し、**Hermes gateway を要しない**（`tests/live/test_er_handoff_live.py` の `_call_er`（本体 `:68-100`・URL `:85`・`x-goog-api-key` header `:89`）は `https://generativelanguage.googleapis.com/.../generateContent` を `x-goog-api-key` だけで叩く。これを呼ぶ test は `:103-125`。§Step C）。よって agent は専用 gateway（§Step A）を立てずに次を直接走らせられる:

```bash
WAREHOUSE_LIVE_ER=1 python3.12 -m pytest tests/live/test_er_handoff_live.py -s
# GEMINI_API_KEY は ~/.zshenv 由来で env に既在（コマンドに書かない・表示しない）
```

- これで **live ER → handoff（`RoboticsPlanDraft`）**まで到達する（live chain の終端は handoff のまま。§5-1）。さらに、その出力を **offline で `validate_raw_output` に流せば `ValidationReport` まで**到達でき（§T-OFFLINE・`pipeline.py:43`、network 不要）、下流の **offline Visual Resolver（forerunner、`visual_resolver/resolver.py:137`）も単体到達可能**。＝恒久 setup 後は「live ER → handoff」＋「offline → Validator/Resolver」を agent が一気通貫で検証できる（ただし live で Validator まで通す一本線は依然 XER6 の仕事。§5-1）。
- **Hermes-routed leg は任意**（`generateContent` direct では不要）。Hermes 経由も確認したい場合のみ §Step A の専用 gateway（`run-er-hermes.sh`、port 8643）を立て、§Step C の `HERMES_BASE_URL=http://127.0.0.1:8643` ＋ `HERMES_API_KEY=<gateway の API_SERVER_KEY>` を渡す（`test_er_handoff_live.py:185-199`）。
- **境界**: 恒久プロビジョンは **provider call を課金する点は変わらない**。CI（autonomous tier、§3 表 1 行目）には `WAREHOUSE_LIVE_ER` を**入れない**（offline `test_l3_pipeline.py` のみが CI 必須）。恒久 setup が解くのは「agent が毎回 key を再入力する手間」だけで、**「課金判断」は別**＝agent は有料 live run の前に **batch / task 単位で cost go をオペレーターに確認**する（standing の無承認 spend はしない・課金 call を CI 常設にもしない）。

---

## 5. Honest limits（隠さない・デモ前に必ず読む）

1. **Validator は今日 live ER 経路に乗っていない**。live ER（`test_er_handoff_live.py`）は `RoboticsPlanDraft`（handoff）で止まり、`validate_raw_output` / Validator を呼ばない（`test_er_handoff_live.py:107-116`）。「**live で Validator まで通す e2e**」は **XER6（X-lite）の仕事**で、`pipeline.py` の seam は XER6 まで verbatim で残す回帰アンカー（`pipeline.py:16-23`、`docs/mode-x-er/06-unfrozen-contract-resolutions.md:39` の `1. … DEFER(gate)`）。
2. **「音声 → ER → Langfuse → Validator」は一本の live 線としてはまだ通っていない**（offline chain は証明済・live 実走は依然 #88 + cost gate）。今ある部品は (a) offline で envelope→Validator（#366・T-OFFLINE、証明済）、(b) live で audio/text→ER→handoff（T-LIVE ER→Handoff、env-gated）、(c) eval_sdk が実 Langfuse trace を持つ（commander 経路）。**ER leg 用の Langfuse tracer は実装済だが既定 OFF**（`LangfuseTranscriptTracer.record_transcript` は `enabled=False` 既定で no-op、`observability.py:88,106-107`。実 trace 着地は #88 human-gate・未検証）。**#389 の adapter live-send 機構（`build_provider_request`/`ErTransportSender`/`_live_send`、`gemini_er.py:87/154/222`）は main 着地済（ad563de）**＝frozen per-transport 形の組立＋injectable sender で HERMES→DIRECT fail-safe を実装済み。残るは (a) 稼働 Bridge cycle が `propose_plan` を呼ぶ wiring（XER6）と (b) 8644 fork の default 配備。transport 選択は `resolve_audio_transport`（#388・main、`robotics/transport.py:29`）。
3. **専用 ER gateway は常駐ではない**。`run-er-hermes.sh` は foreground で、`~/.hermes-mwr-er-lean` の隔離 home（`run-er-hermes.sh:22,59`）。デモのたびに立ち上げる。
4. **Hermes は音声を運べない**。`/v1/chat/completions` は `input_audio` を **HTTP 400 `unsupported_content_type`** で弾く（2026-06-27 実測、`docs/mode-x-er/06-unfrozen-contract-resolutions.md:159`）。音声は **direct ER 固定**。Option-D fork（§2.5）は 400 を解消できるが **未 ship**（`docs/mode-x-er/06-unfrozen-contract-resolutions.md:263-270`）。
5. **Option-D plugin trace は audio leg で live 観測済だが、HLF-G0 verdict と #6 scorer join は未検証（human-gate）**（`run-er-gateway-langfuse.sh:503-519`）。

---

## 参照（たどれる file:line を一次ソースに）

- L3 e2e seam: `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/pipeline.py:43`（main・#366）/ `tests/unit/test_l3_pipeline.py`（main・#366）/ `…/robotics_planning_core/handoff.py:114`（main）/ `…/robotics_planning_core/validator/validator.py:90`（main・#366）
- 下流 forerunner（main・未配線）: `…/robotics_planning_core/visual_resolver/resolver.py:137`（XER3 #339）/ `…/robotics_planning_core/task_graph_executor/`（XER4 #340）
- live ER: `tests/live/test_er_handoff_live.py:21,33,45,103-116,185-199,309-327,376-404`（main）
- 観測: `…/robotics/observability.py:68`（main・`LangfuseTranscriptTracer` 実装済／既定 `enabled=False` OFF、`:88,106-107`）/ `tests/live/test_langfuse_trace_tags_live.py:14-15,42-48`（main）/ Option-D `deploy/hermes/er-audio-fork/hlf-g0-langfuse/run-er-gateway-langfuse.sh`
- live send（pending・#344/#389）/ transport 選択（#388・main）: `…/robotics/adapters/gemini_er.py:79-135`（`build_provider_request` frozen 形）/ `gemini_er.py:138-150`（`ErTransportSender`）/ `gemini_er.py:207-237`（HERMES→DIRECT fail-safe、PR pending）/ `…/robotics/transport.py:29-58`（`resolve_audio_transport`・main）
- gateway / preflight / STT: `deploy/dev/run-er-hermes.sh` / `deploy/dev/check-hermes-live.sh` / `deploy/dev/run-er-stt-http.sh` / `deploy/dev/hermes-er/config.lean.yaml`
- gate / secrets: [`.claude/rules/environments.md:20-25`](../../.claude/rules/environments.md) / [`.claude/rules/llm-observability-testing.md:30-39`](../../.claude/rules/llm-observability-testing.md)
- 設計正本: [`docs/mode-x-er/06-unfrozen-contract-resolutions.md`](../mode-x-er/06-unfrozen-contract-resolutions.md) §5 / [`docs/productization/02-l4-robotics-bridge-box.md`](../productization/02-l4-robotics-bridge-box.md):177-199（HLF gate）/ [`docs/dev/02-operator-runbook.md`](02-operator-runbook.md)
