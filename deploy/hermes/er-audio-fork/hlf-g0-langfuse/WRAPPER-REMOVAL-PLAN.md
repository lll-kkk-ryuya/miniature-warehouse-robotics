# Bridge Wrapper-Removal PLAN (Pattern B) — design only, CONTINGENT on HLF-G0

> **設計正本（たどれる file:line）**:
> - trace 所有 = Bridge（Pattern A・現採用） — `docs/architecture/08-llm-bridge-common.md:373-375`
> - Hermes plugin 無効化 / 二重計上回避 — `docs/architecture/13-hermes-setup.md:517`, `:551-570`（§7.7.1 再評価条件 1〜6）
> - Phase 3 live 検証項目（①inbound `metadata.trace_id` を Hermes が尊重するか 他） — `docs/architecture/13-hermes-setup.md:520`
> - cross-lane join = 決定的 seed（`trace_id` は契約フィールドでなく seed） — `ws/src/eval_sdk/eval_sdk/seed.py:33-42`, `:88-96`；doc参照 `docs/architecture/13-hermes-setup.md:516`
> - HLF-G0〜G5 gate（観測ゲート・L4/L3 凍結経路から独立） — `docs/mode-x-er/06-unfrozen-contract-resolutions.md:9`（→ `docs/productization/02-l4-robotics-bridge-box.md:177-199` を前方参照。**正本の gate 表は doc02:190-195**＝`docs/architecture/13-hermes-setup.md:561-566` §7.7.1 条件 1〜6 と 1:1 対応。本書はこの doc02 gate 表を権威定義として使う。**注意**: 旧版は実体パス `docs/productization/02-…` を誤って `docs/mode-x-er/productization/02-…` と書き「未着地」と誤断定していた＝両 doc とも merged main に着地済み）
>
> **本書の性格**: これは **design-only PLAN**。Bridge コードは **1 行も書かない**。本書が記述する変更はすべて
> **live HLF-G0 probe が PASS した後にのみ**着手できる（§7 / §8）。HLF-G0 が FAIL なら Pattern B は不採用で、
> 現行の Bridge-owned wrapper（Pattern A）を**そのまま正**とする。
>
> ⚠️ **「plugin を観測有効化する」≠「Bridge wrapper を除去する」を区別**: 本 package の
> `config.lean.yaml` が `plugins.enabled: [observability/langfuse]` を持ち、`run-er-gateway-langfuse.sh`
> が plugin を ON にするのは **HLF-G0/Option-D を probe するための opt-in scaffolding**であって、
> **Bridge の `langfuse.openai` wrapper 除去（Pattern B）ではない**。後者のみが本書の HLF-G0/G5 PASS gate に
> 縛られる。probe 中の「plugin ON」状態でも **shipped default の trace owner は Bridge-owned のまま**
> （doc06:9 / doc02:179）＝二重計上回避のため、比較 run では plugin を OFF にする（doc13:568-570）。
> **#360 spike で Option D（predict-seed）= live PASS（trace `d1477eef…`）**＝「plugin-owned trace が
> 実体として観測でき、seed 一致で score-join できる」ことは実証済みだが、それは「inbound trace_id を
> honor する literal HLF-G0」とは別解（§6 OPTION B）であり、#6 scorer 脚まで通した end-to-end join は
> 依然 human-gate（§9）。

---

## 0. One-paragraph summary

ユーザーの "Pattern B" 直感 = 「Bridge 側の `from langfuse.openai import AsyncOpenAI` wrapper を外し
（→ 素の `from openai import AsyncOpenAI`）、Hermes 内蔵 Langfuse plugin を ON にして generation 所有を
Hermes に移す」。これが **clean に成立するか否かを決める唯一の分岐点が HLF-G0** ＝
**「Hermes Langfuse plugin が、request metadata で渡した INBOUND `trace_id` を尊重して、その同一 trace に
generation を載せるか」**。YES なら Warehouse Orchestrator (#6) は今と同じ決定的 seed
（`eval_sdk.seed.seed_for(run_id, gen_id)` → `create_trace_id(seed)`）で同一 trace に outcome score
（SR / SPL / collision / result）を `create_score` で付けられ、**plugin-ON + no-wrapper が成立**する。
NO なら Hermes が自前で別 trace_id を採番し score が orphan 化する（join 破綻）ので、**fork tweak か wrapper 継続**が要る。
本書は (1) wrapper を落とす差分の所在、(2) plugin が代わりに所有するもの、(3) score-join の含意、
(4) managed-prompt link の扱い、(5) R-26/safety（A/B/C 全モードの commander-cycle 観測に触れる）、
(6) DoD と HLF-G0 contingency を、すべて file:line / symbol で設計する。

---

## 1. Current state — verified by reading the bridge this session (2026-06-27)

凍結・実装済みの「現状（Pattern A）」は以下（読んだ実ファイル:行）:

| 要素 | 実体（file:line / symbol） | 現挙動 |
|---|---|---|
| **wrapper import** | `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/hermes_client.py:308-309`（`import openai` ＋ `from langfuse.openai import AsyncOpenAI`、lazy） | langfuse.openai が generation を **Bridge 所有 trace 下にネスト** |
| client 構築 | `hermes_client.py:313`（`AsyncOpenAI(base_url=self._base_url, api_key=…)`） | base_url = `…/v1`（`hermes_client.py:289`） |
| call | `hermes_client.py:329`（`await client.chat.completions.create(**create_kwargs)`） | generation は wrapper が自動捕捉 |
| **managed-prompt link** | `hermes_client.py:326-327`（`if self._langfuse_prompt is not None: create_kwargs["langfuse_prompt"] = …`） | langfuse.openai 独自 kwarg。素の openai には**存在しない** |
| **trace 所有** | `tracing.py`（re-export）→ `ws/src/eval_sdk/eval_sdk/tracer.py:55-201`（`LangfuseTracer.turn`） | 1 trace/turn を Bridge が open。`create_trace_id(seed=seed_for(run_id,gen_id))`（`tracer.py:180-181`） |
| turn を開く側 | `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/scheduler.py:307`（`async with self._tracer.turn(gen):`）内で `self._llm.decide(situation)` を `wait_for` | wrapper の generation がこの turn の中にネスト |
| tool span | `scheduler.py:373`, `:403`（`async with self._tracer.tool_span(tool_call.tool, gen):`） | MCP tool 実行を同 trace の子 span に |
| **#6 score join** | `ws/src/warehouse_orchestrator/warehouse_orchestrator/score_send.py:98`（`trace = trace_id_for(gen_id, run_id_value=run_id, …)`）→ `:102`（`sink.send_report(report, trace, …)`） | **同じ seed** から trace_id を再導出して `create_score`。Hermes も Bridge も見ない純 join key |
| seed（唯一の join key） | `eval_sdk/eval_sdk/seed.py:33-42`（`seed_for`）/ `:88-96`（`resolve_run_id`）。run_id env = `WAREHOUSE_RUN_ID`（`warehouse_orchestrator/trace_id.py:35`） | #4 と #6 が `WAREHOUSE_RUN_ID` を共有 |
| **deploy 申し送り（現状の前提）** | `warehouse_llm_bridge/CLAUDE.md`「deploy 申し送り」＝① Hermes 内蔵 Langfuse plugin **無効化**（二重計上回避 doc13:517）② bridge プロセスに `LANGFUSE_*` env 投入 | Pattern A は **plugin OFF** が前提 |
| fail-open | `eval_sdk/eval_sdk/tracer.py:55-65`, `:105-116`（langfuse 不在/誤設定 → no-op・raise しない） | langfuse 障害が commander loop を落とさない |

**load-bearing 不変条件（Pattern B でも絶対に壊さない・§5 で再掲）**:
`warehouse_llm_bridge/CLAUDE.md`「触らないもの」と同型 ＝ action_map idempotency mint・Policy Gate・
timeout 時 0-dispatch・eval_sdk outcome scores。**Pattern B は transport/observability seam のみ**で、
これら orchestration/safety には触れない。

---

## 2. HLF-G0 — the deciding probe (what makes Pattern B clean or not)

**HLF-G0 = 「Hermes Langfuse plugin は inbound `trace_id`（request metadata で渡す）を尊重するか」**
（doc13:520 ① / doc13:561 §7.7.1 条件1「外部指定 `trace_id` または同等の correlation id を尊重できる」）。

- **PASS（plugin が inbound trace_id を採用）** → Hermes が generation をその trace に載せる
  → #6 は **同じ seed** で同 trace_id を導出し score を付けられる（§3 PASS 経路）→ **Pattern B 成立**
  （wrapper drop ＋ plugin ON ＋ generation 一度だけ）。
- **FAIL（plugin が自前 trace_id を採番／inbound を無視）** → generation が別 trace に行く
  → #6 の score は **どこにも join しない孤児**（Pattern A の決定的 seed は Hermes の自前 id と一致しない）
  → **Pattern B 不成立**。fork tweak（plugin に inbound trace_id を honor させる patch）か wrapper 継続が要る。

**HLF-G0 probe は本ディレクトリの `probe-hlf-g0.sh`（design-only harness・§7）で human-gated に実行する。**
本 PLAN は probe を **走らせない**（live は main session が creds 投入後に逐次実行）。

---

## 3. (1) DROP the wrapper / (2) what Hermes then owns — design

### 3.1 (1) wrapper を落とす差分の所在（CONTINGENT・コードは書かない）

> 着手は **HLF-G0 PASS 後のみ**。下記は「どの行を・どう変えるか」の設計記述であって diff ではない。

1. **import 行**: `hermes_client.py:308-309`
   - `from langfuse.openai import AsyncOpenAI` → **`from openai import AsyncOpenAI`** に置換。
   - `import openai`（:308）は **残す**（`openai.OpenAIError` を `:330` で捕捉して `LLMUnavailableError` に変換する failure contract がそのまま要る）。
   - lazy import / `ImportError → LLMUnavailableError`（`:310-311`）の fail-open 形は **維持**（素の openai でも pip extra が無ければ Nav2-only に縮退）。
2. **inbound trace_id を request に載せる**: 現在 generation 所有は wrapper 任せ（`hermes_client.py:329`）。
   wrapper を外すと Bridge は **自分では generation を作らない**。代わりに **HLF-G0 で確認した経路**で
   `trace_id` を request に渡す＝ `create_kwargs` に **plugin が読む metadata channel**
   （HLF-G0 probe で確定する具体キー名＝`extra_body`/`metadata`/header のいずれか・**未確定**）に
   `trace_id = eval_sdk.seed.normalize_trace_id(create_trace_id(seed_for(run_id, gen_id)))` を載せる。
   - この `trace_id` の **算出は今と同一**（`tracer.py:180-181` と同じ seed）。**変えるのは「誰が generation を作るか」だけ**で、
     **trace_id の決定式（join key）は不変**＝ここが Pattern B が #6 と互換でいられる急所。
3. **`langfuse_prompt=` kwarg**: `hermes_client.py:326-327` を **削除**（素の openai に当該 kwarg は無い）。managed-prompt link は §4 で別経路に移す。
4. **`tracing.py` / `tracer.turn` の役割縮退**:
   - `scheduler.py:307` の `async with self._tracer.turn(gen):` は **generation を内包しなくなる**。
   - Pattern B では Hermes が generation を所有するので、`turn` の責務は「`session_id` + tags + metadata を
     **その trace に propagate するか**」に縮む。**HLF-G0 PASS でも、tags/metadata を Bridge 側で同 trace に
     足せるか（doc13:520②/§7.7.1 条件2）は HLF-G2 の別判定**＝§7 で probe する第2軸。
     - HLF-G2 PASS（Bridge から同 trace に attribute を足せる）→ `turn` は `start_as_current_observation` を
       やめ `propagate_attributes`（`tracer.py:152-169`）だけ残す縮退。
     - HLF-G2 FAIL（trace を所有しない Bridge からは attribute を足せない）→ tags/metadata は
       **Hermes plugin 側 config（`HERMES_LANGFUSE_*` ＋ plugin が載せる metadata）に移譲**。この場合 `turn` は
       `tool_span` の親コンテキストとしてのみ残る（tool span 自体は Bridge in-process dispatch ＝ Hermes 非経由なので
       **Bridge が引き続き所有**＝§3.3）。
5. **deploy 申し送りの反転**: `warehouse_llm_bridge/CLAUDE.md`「deploy 申し送り」の
   「① Hermes 内蔵 Langfuse plugin **無効化**」を **「① Pattern B run では plugin を ON にする」** に反転する
   docs PR（doc13:517 / :551-570 / doc08:375 と同期）。**これは docs 同期であって Bridge code ではない**。

### 3.2 (2) Hermes plugin が代わりに所有するもの

HLF-G0 PASS 時、**generation の所有が Bridge → Hermes plugin に移る**。Hermes plugin が持つもの:

- **model generation 1 本**（token / cost / latency / error count）＝ doc13:554-556 の「同 model call が 2 generation
  に二重計上」を **wrapper を外すことで構造的に解消**（§7.7.1 条件6「generation が一度だけ」）。
  これが Pattern B の本来の動機。
- generation に対する **provider/model 実体の可視性**: 現状 Bridge は `model:"hermes-agent"` 固定で provider を
  見ない（`hermes_client.py:37`, doc13:171）。plugin は Hermes server-side の active_provider を知る立場なので、
  provider 実体 metadata を **plugin 側で**載せられる可能性がある（HLF-G1 で確認・doc13:520②/§7.7.1 条件2）。
- **plugin が読む env**（GROUNDED）: `HERMES_LANGFUSE_PUBLIC_KEY` / `_SECRET_KEY` / `_BASE_URL` / `_ENV` /
  `_SAMPLE_RATE` / `_RELEASE` / `_DEBUG` / `_MAX_CHARS`。これらは **gateway の `HERMES_HOME/.env`**
  （`er-audio-fork/README.md:123-127` の secrets 規約と同型・gitignore・**値は echo しない**）に置く。
- **plugin の python 依存**: `langfuse` package（GROUNDED: Hermes venv に未インストール）。
  **絶対隔離ルール**（§7）＝ personal venv/home を一切触らず `pip install --target <ISOLATED_DIR>` ＋ PYTHONPATH 前置で供給。

### 3.3 Bridge が **依然所有**するもの（Pattern B でも Hermes に渡らない）

- **MCP tool span**: `scheduler.py:373`, `:403` の `tool_span` は **Bridge in-process dispatch**
  （`WarehouseTools().dispatch`・doc08:169）＝ Hermes を経由しない。よって **Hermes plugin はこれを見られない**。
  §7.7.1 条件4「MCP tool span と model generation が同じ trace に入る」を満たすには、
  **tool span を Bridge 側で inbound trace_id（= generation と同 trace）の子として open し続ける**必要がある
  ＝ HLF-G2（Bridge から同 trace に observation を足せるか）に依存する **HLF-G3 判定**（§7 第3軸）。
- **outcome score**: §3.1 の通り #6 が `create_score`（Hermes は robot result を見ない）。

---

## 4. (4) managed-prompt link handling（CONTINGENT）

現状: `hermes_client.py:326-327` が langfuse.openai 専用 kwarg `langfuse_prompt=` で generation を managed-prompt 版に
紐付ける（doc08:532 / doc13:520④）。**素の openai にこの kwarg は無い**ので Pattern B では別経路が要る:

- **prompt 取得（変えない）**: `prompts.resolve_commander_prompt(mode,cfg)` の Langfuse Prompt Management 取得
  （`warehouse_llm_bridge/prompts.py`・fail-open）は **Pattern B でも不変**。Bridge は引き続き起動時に prompt を取得し
  system prompt を注入する。**変わるのは generation への "どの prompt 版か" の link だけ**。
- **link 経路の候補（HLF-G4 で確定）**:
  1. **plugin が prompt metadata を載せられる**なら、prompt name/version を §3.1-2 の metadata channel に
     `prompt_name` / `prompt_version`（現 `tracer.py` の extra_metadata と同キー）として渡し plugin に載せさせる。
  2. plugin が **prompt object link を理解しない**なら、managed-prompt の **generation-level link は Pattern B では失う**
     （prompt 単位分析が劣化）＝ honest trade-off。tags での `prompt:<name>`（doc08 §Langfuse Prompt Management 方針）は
     §3.1-4 の attribute 経路で残せる可能性がある。
- **判定**: managed-prompt の **完全な generation link**（doc13:520④）が Pattern B で維持できるかは **HLF-G4**
  ＝ §7 probe の第4軸。FAIL なら「prompt link は wrapper（Pattern A）の方が優れる」を honest に記録し、
  trace-owner 判断の入力にする（doc06:9 が trace-owner 決定を L4/L3 凍結経路から独立に置いている通り）。

---

## 5. (5) R-26 / safety — これは throwaway ではない

Pattern B は **A/B/C 全モードの commander-cycle 観測**に触れる（wrapper は mode 非依存＝`hermes_client.decide` は
全モード共通経路）。したがって以下を gate とする:

- **触ってはいけない不変条件（assert で固定・編集禁止）**:
  - **timeout 時 0-dispatch**: `scheduler.py:309-314`（`wait_for` timeout → `_on_timeout` → `return False`）。
    wrapper を外しても **この制御フローを変えない**（generation 所有が変わるだけ）。
  - **fail-open**: langfuse/openai 不在・plugin 障害は **commander loop を落とさない**
    （`hermes_client.py:310-311` / `tracer.py:55-65`）。§7.7.1 条件5「plugin 障害時も robot 制御が
    fail-open / 0 dispatch」＝ Pattern B でも **死守**。素の openai 化で `openai.OpenAIError → LLMUnavailableError`
    （`:330-331`）の failure contract を維持。
  - **action_map idempotency / Policy Gate / B-3 gen guard / C replay reject**: 排他3層
    （`warehouse_llm_bridge/CLAUDE.md`「排他3層」）は observability と無関係＝**触れない**。
  - **Mode C no-actuation**: `tests/unit/test_modec_noactuation.py`（R-26）が forwarder=None で 0 actuation を固定。
    Pattern B はここに無関係（観測 seam のみ）＝ **再実行して緑を確認**。
- **gate**: 本変更は **HLF-G0／G5 が PASS した後にのみ**着手し、**careful testing 付き**で進める
  （doc13:568-570「通過するまでは Bridge-owned trace を正とし、plugin は比較 run では OFF」）。
  - **HLF-G5 = end-to-end 二重計上ゼロ確認**（§7.7.1 条件6「Bridge wrapper と Hermes plugin を併用せず、
    generation が一度だけ記録される」）。wrapper drop ＋ plugin ON で **generation が Langfuse 上に 1 本だけ**
    出ることを live で確認するまで Pattern B を「完了」と呼ばない。
- **比較公平性**: 4社単一コードパス（`assert_fairness`・doc08:375「4社単一コードパス」）を Pattern B でも保つ
  （plugin ON は provider に依らず一様＝公平性を破らない）。

---

## 6. (3) SCORE-JOIN implications（#6 が同 trace に score を付け続けられるか）

これは Pattern B の **成否を分ける本質**で、§2 の HLF-G0 結果に従って 2 経路に分かれる:

### 6.1 PASS 経路（plugin が inbound trace_id を尊重）

- #4 が request metadata に `trace_id = normalize_trace_id(create_trace_id(seed_for(WAREHOUSE_RUN_ID, gen_id)))` を載せる（§3.1-2）。
- Hermes plugin が **その trace_id に generation を載せる**。
- #6 は **今と同一コード**（`score_send.py:98` → `trace_id_for(gen_id, run_id_value=run_id)` → `:102` `send_report`）で
  **同じ seed から同 trace_id を再導出**して `create_score`。
- ＝ **score join は今と完全に同じ決定的 seed で成立**。eval_sdk（`seed_for` / `derive_trace_id`）は **そのまま**
  （`eval_sdk/CLAUDE.md`「死守する1不変条件」＝同 seed → 同 trace_id の property test が守る）。
  **#6 にも eval_sdk にも変更不要**＝これが Pattern B の魅力。
- **依存**: この経路は **HLF-G0 PASS に完全に依存**（GROUNDED FACTS の deciding factor）。

### 6.2 FAIL 経路（plugin が自前 trace_id を採番）

- generation は Hermes 自前 id に行き、#6 の決定的 trace_id（Bridge と同 seed）と **一致しない** → score 孤児化。
- 回避策（いずれも追加コスト）:
  - **(a) fork tweak**: plugin に「inbound `trace_id` を honor する」最小 patch を当てる
    （er-audio-fork と同型の薄い overlay・transport ではなく observability の 1 段）。HLF-G0 を patch 後に再 probe。
  - **(b) wrapper 継続（Pattern A 維持）**: Pattern B を見送り、現行 `langfuse.openai` wrapper を正とする
    （doc13:570 のデフォルト）。この場合 **本 PLAN の変更は着手しない**。
- **eval_sdk / #6 は FAIL でも変更なし**（join key は seed のまま）＝ どちらの経路でも outcome score の所有は #6 で不変
  （Hermes は robot result を見ない・`tracing.py:19-20` の設計）。

---

## 7. Probe harness — `probe-hlf-g0.sh`（design-only・human-gated・本ディレクトリ）

本 PLAN に付随する **isolated probe harness**（`./probe-hlf-g0.sh`）の設計契約。**本書では走らせない**
（live は main session が creds 投入後に逐次実行）:

- **鉄則（er-audio-fork/README.md:65-71 と同型）**:
  - `set -euo pipefail`。**personal `~/.hermes` / personal venv を一切触らない**（指していたら REFUSE）。
  - `langfuse` は **`pip install --target "$ISOLATED_DIR"`**（`langfuse>=2,<3` または plugin の import が要求する版＝
    plugin の imports を読んで確定）＋ **PYTHONPATH 前置**で供給。Hermes venv に install しない。
  - `HERMES_HOME` は **隔離 home**（既定 `~/.hermes-mwr-er-lean`・**`~/.hermes` 禁止**）。
  - secrets（`HERMES_LANGFUSE_*`）は **`HERMES_HOME/.env` を source するのみ**・**値を echo/print しない**。
- **probe が判定する軸（gate ID は doc02:190-195 正本＝doc13:561-566 §7.7.1 条件 1〜6 と 1:1・PASS/FAIL を 1 行で出す）**:
  - **HLF-G0**（doc02:190 / cond.1）: inbound `trace_id`（または同等 correlation id）→ 同 trace に generation が載るか。
  - **HLF-G1**（doc02:191 / cond.2）: Bridge から同 trace に tags/metadata（gen_id・run_id・provider・mode・env・prompt）を足せるか。
  - **HLF-G2**（doc02:192 / cond.3）: Bridge 外の Warehouse Orchestrator が同 trace に `create_score` できるか（score join）。
  - **HLF-G3**（doc02:193 / cond.4）: MCP tool span（Bridge in-process）と model generation が同じ trace に入るか。
  - **HLF-G4**（doc02:194 / cond.5）: plugin / Langfuse 障害時も robot 制御が 0-dispatch / fail-open を破らないか。
  - **HLF-G5**（doc02:195 / cond.6）: wrapper drop ＋ plugin ON で **generation が 1 本だけ**（二重計上ゼロ）。
  （managed-prompt link は doc02 の独立 gate ではなく HLF-G1 metadata / Pattern-A edge＝§4 / `README-hlf-g0.md` の caveat で扱う。）
- **出力**: probe は **PASS/FAIL の判定行のみ**を `RESULT.md`（人が転記）に残す形を想定。**creds 値や trace 内容は出さない**。
- **正本の gate 表**: `docs/productization/02-l4-robotics-bridge-box.md`:190-195（HLF-G0〜G5・**merged main に着地済み**）。
  本 probe はこれを権威定義に使い、`docs/architecture/13-hermes-setup.md`:561-566 §7.7.1 条件 1〜6 と対応させる。

---

## 8. DoD（完了ゲート）— CONTINGENT on the live HLF-G0 probe passing

**この PLAN の "完了" は 2 段に分かれる。**

### 8.1 本 PLAN（design 成果物）の DoD — **今のセッションで満たす**

- [x] `WRAPPER-REMOVAL-PLAN.md`（本書）＝ (1)〜(6) を file:line/symbol で設計。**Bridge コードは 0 行**。
- [ ] `probe-hlf-g0.sh`（§7・isolated・`set -euo pipefail`・personal path 拒否・secret 非 echo）＝ design-only harness。
- [ ] `.env.example`（`HERMES_LANGFUSE_*` の **placeholder のみ**・値なし・gitignore は実 `.env`）。
- [ ] `RESULT.md`（空欄テンプレ・probe 結果転記先・HLF-G0/G2/G3/G4/G5 の PASS/FAIL 欄）。
- 設計正本リンク（doc08:373-375 / doc13:517,520,551-570 / doc06:9 / eval_sdk seed）を本書に明記済み。

### 8.2 実装 PR（`feat/mode-x-er` 上・Bridge code）の DoD — **HLF-G0 PASS 後にのみ着手**

> **着手条件（gate）**: `RESULT.md` の **HLF-G0 = PASS**（doc13:561 §7.7.1 条件1）＋ **HLF-G5 = PASS**
> （二重計上ゼロ）。FAIL なら **§6.2 の (a) fork tweak か (b) Pattern A 維持** に分岐し、本 §8.2 は着手しない。

- [ ] `hermes_client.py:308-309` を素の `from openai import AsyncOpenAI` に（`import openai` は残す）。
- [ ] `hermes_client.py:326-327` の `langfuse_prompt=` 削除＋ §4 の link 経路（HLF-G4 結果に従う）。
- [ ] inbound `trace_id`（§3.1-2・同 seed）を request metadata に載せる（HLF-G0 で確定した channel）。
- [ ] `tracing.py` / `tracer.turn` の役割を §3.1-4 に従って縮退（HLF-G2/G3 結果に従う）。tool span 所有は §3.3 を維持。
- [ ] **safety 再検証**: timeout 0-dispatch（`scheduler.py:309-314`）・fail-open（`hermes_client.py:310-311,330-331`）・
      Mode C no-actuation（`test_modec_noactuation.py` R-26 緑）・排他3層 不変 を **assert/test で固定**。
- [ ] **#6 score join 不変**を確認（`score_send.py:98,102` 無改修で同 trace に score・§6.1）。
- [ ] **二重計上ゼロ**を live で再確認（HLF-G5・generation 1 本）。
- [ ] docs 同期 PR: `warehouse_llm_bridge/CLAUDE.md`「deploy 申し送り」の plugin OFF→ON 反転、
      doc08:375 / doc13:517,551-570 の trace-owner 記述更新（docs-first・`scripts/check_consistency.py` 0 ERROR）。
- [ ] PR テスト層分離（`.claude/rules/llm-observability-testing.md`）: unit/fake は通常 CI、HLF-G0〜G5 は #88 系 human gate に隔離。

---

## 9. Honest "not-yet" / unverified list

- **literal HLF-G0〜G5（plugin が *inbound* trace_id を honor するか 他）はすべて未検証（human-gated live）**:
  Hermes Langfuse plugin の inbound-trace_id honor 挙動は **本 package の probe では実測していない**
  （静的予測 = stock FAIL・`PLUGIN-TRACEID-ANALYSIS.md`）。**ただし Option D（predict-seed・§6 OPTION B）は
  #360 spike で live-observed PASS**（`run-er-gateway-langfuse.sh` 経由・観測 trace `d1477eef…`・
  `spike/langfuse-plugin-d/verify_d_audio.py`）＝「plugin-owned trace が seed 一致で score-join できる」は実証済み。
  **未だ human-gate なのは #6 scorer 脚まで通した end-to-end join の live 実証**（#360 review 参照）。
  本 PLAN は「PASS した場合の設計」と「FAIL 時の分岐」を両方記述する設計文書であり、
  **end-to-end join の probe 結果が出るまで Pattern B（wrapper 除去）採用は宣言しない**（doc13:568-570）。
- **metadata channel（trace_id を載せるキー名）未確定**: `extra_body` / `metadata` / header のどれを plugin が読むかは
  HLF-G0 probe で確定する（plugin source の `metadata` 参照を読んで pin）。
- **`langfuse` package の必要バージョン未確定**: plugin の imports を読んで `>=2,<3` か別レンジかを確定（GROUNDED FACTS 準拠）。
- **HLF gate 正本表 = `docs/productization/02-l4-robotics-bridge-box.md`:190-195（merged main に着地済み）**:
  本 PLAN はこれを権威 gate 表に使い、`docs/architecture/13-hermes-setup.md`:561-566 §7.7.1 条件 1〜6 と 1:1 対応させる。
  （旧版は実体パスを `docs/mode-x-er/productization/02-…` と誤記して「未着地」と書いていた＝訂正済み。）
- **managed-prompt link の劣化可能性**: §4 の通り Pattern B では generation-level prompt link が失われうる（HLF-G4 次第）。
  これは Pattern A（wrapper）の優位点として honest に残す。
- 本 PLAN は **Bridge code を 1 行も含まない**（design only）。実装は §8.2 の gate を満たした後の別 PR。

---

## References（read live this session, 2026-06-27）

- `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/hermes_client.py:37,289,308-313,326-331`（wrapper / managed-prompt / failure contract）
- `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/tracing.py:1-56`（trace 所有 re-export・plugin 無効化申し送り :19-20）
- `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/scheduler.py:307,309-314,373,403`（turn / 0-dispatch / tool_span）
- `ws/src/eval_sdk/eval_sdk/tracer.py:55-201`（LangfuseTracer.turn / propagate_attributes / fail-open）
- `ws/src/eval_sdk/eval_sdk/seed.py:33-42,45-55,88-96`（seed_for / normalize / resolve_run_id ＝ join key）
- `ws/src/warehouse_orchestrator/warehouse_orchestrator/score_send.py:86,98,102`（#6 score join）
- `ws/src/warehouse_orchestrator/warehouse_orchestrator/trace_id.py:10,35,88`（WAREHOUSE_RUN_ID・trace_id_for）
- `docs/architecture/08-llm-bridge-common.md:373-375,524,532`（Pattern A trace 所有・managed-prompt link）
- `docs/architecture/13-hermes-setup.md:517,520,551-570`（plugin 無効化 / Phase3 検証項目 / §7.7.1 再評価条件1〜6）
- `docs/mode-x-er/06-unfrozen-contract-resolutions.md:9`（HLF-G0〜G5 は L4/L3 凍結経路から独立・trace-owner は Bridge-owned 継続）
- `deploy/hermes/er-audio-fork/README.md:65-71,123-127`（isolation 鉄則・secrets 規約・本書が踏襲する形）
- `.claude/rules/llm-observability-testing.md`（テスト層分離・#88 human gate）
