# 22. Web Observability — Mode A 会話・稟議のリアルタイム観測基盤

> **正本**: 本書は「Mode A キャラLLM会話・稟議（稟議制 ringi）を、撮影・検証のために**ソフトウェアからリアルタイム観測**する Web 基盤」の設計正本。観測専用（observe-only）であり、ブラウザ→ロボットの操作経路を**持たない**。
> 関連正本: トピック契約=[doc03](03-software-architecture.md) / LLM Bridge=[doc08](08-llm-bridge-common.md)・[08a](../mode-a/08a-llm-bridge-mode-a.md) / キャラLLM交渉=[doc14](14-character-llm-negotiation.md) / 共通基盤(State Cache/Emergency)=[doc12](12-infrastructure-common.md) / 統合(rclpy+asyncio 共存)=[12a](../mode-a/12a-integration-mode-a.md) / Langfuse=[doc08 §比較計測](08-llm-bridge-common.md) / 環境・secrets=[doc19](19-environments-and-config.md) / テスト=[doc20](20-dev-quality-and-testing.md)。
> **状態**: 設計のみ（未実装）。実装は §13 のスライス計画で段階的に進める。

---

## 1. 目的・スコープ

### 1.1 解く問題
Mode A（および Mode B）でキャラLLM Bot1/Bot2 が会話・交渉し、司令官LLMが稟議を承認する様子を、**ソフトウェア側からリアルタイムに観測**したい。具体的には会話タイムライン・稟議フロー・司令官の判断（reasoning）・各ロボットの状態・緊急イベントを 1 画面で追える Web コンソール。撮影（YouTube）と検証（LLM 比較）の両方に使う。

### 1.2 設計を決めた最重要事実
**会話・判断はすでに ROS トピックに publish されている。しかし購読者が 1 人もいない。**

- 会話ターン: `character_session` が 1 ターンごとに `/character/speech`（`negotiation_messages.py:93-101`）→ `/negotiation/turn`（`:88-90`）を逐次 publish（`character_session.py:89-90`）。合意時のみ `/negotiation/proposal`（凍結 `Proposal`、`schemas.py:209-214`）。
- 司令官の判断: `/llm/reasoning`・`/llm/command` を **llm_bridge ノードが publish**（`llm_bridge.py:132-133`、publish method `:231-235`）。scheduler は publisher を持たず注入 callback を呼ぶだけ（`scheduler.py:187-188` 配線 / `:359-360` 発火）。
- にもかかわらず、これら表示系トピックの subscriber は ws/src 内に存在しない（grep 確認、live persona の `character_node` が start/abort/snapshot/reasoning を購読するのみで `/llm/command` すら購読しない `character_node.py:91-94`）。

→ つまり本基盤は「新しい producer を作る」のではなく、**subscriber を 1 つ足して Web へ fan-out する**ことに帰着する。これが設計を最小・最安全にしている。

### 1.3 非ゴール（重要）
- **ブラウザ→ロボット操作は持たない**（観測専用。§12 で R-26 unit により証明）。
- 生 ROS グラフ（`/scan`・`/map`・TF・costmap）のブラウザ直結は**しない**（doc03:112 スコープ外。rosbridge/Foxglove をブラウザに置かない）。安全・帯域・Jetson コストのため。
- LLM 比較の**クロス集計**（4社×3モード）はリアルタイムでなく事後（Langfuse-backed、§7）。

---

## 2. アーキテクチャ概観

新規 ROS ノード **`web_bridge`**（rclpy subscriber + FastAPI/uvicorn）を 1 個だけ追加する。Nav2 Bridge と同じ **rclpy+asyncio 共存パターン**（`executor.spin()` を daemon thread、uvicorn を main loop。[doc12a:200-234](../mode-a/12a-integration-mode-a.md)）。フロントは **`web/console`**（Next.js）で、**静的エクスポート（`output: 'export'` ＝ CSR-only SPA）として build し、その `web_bridge`（FastAPI `StaticFiles`）が同一オリジン（:8646）で配信する**。**Jetson に Node ランタイムを常駐させない（Node は build-time のみ・§15/§2.1 で確定）**。

```
  ┌──────── 既存 producer（新規コード不要・現在 購読者ゼロ）────────┐
  │ character_session │ /character/speech {speaker,text,negotiation_id}  │
  │  (live persona は │ /negotiation/{start,turn,proposal,abort}         │
  │   Slice3/#288)    │                                                  │
  │ llm_bridge node   │ /llm/reasoning(text)  /llm/command(Command JSON) │
  │ state_cache node  │ /state_cache/snapshot (StateSnapshot, 10Hz)      │
  │ emergency_guardian│ /emergency/event (JSON)                          │
  └────────────────────────────┬────────────────────────────────────────┘
         全て std_msgs/String（Phase4まで・doc03:99-108/doc16 §3）。payload は JSON、ただし /llm/reasoning は**生テキスト**（JSON decoder を作らない）
                               ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │  web_bridge （新規 ament_python / rclpy + FastAPI/uvicorn）            │
  │  ① subscribe（各 producer の QoS に matching・§6）                    │
  │  ② ObsEvent 封筒へ正規化（seq 採番・malformed never-raise・§5）       │
  │  ③ snapshot は coalesce/throttle（state-not-event・§8）               │
  │  ④ events-<run_id>.jsonl に append（SSD・rotation/retention・§9）     │
  │  ⑤ trace_id 導出（gen_id を持つ negotiation event のみ・§7）         │
  │  default bind 127.0.0.1（LAN 公開は opt-in+token・§11）               │
  └──────┬──────────────────────────────────┬───────────────────────────┘
         │ 同一オリジン :8646（FastAPI が下記すべてを 1 host:port で提供）
         │ WS /ws (live・per-client bounded queue・§10) ／ REST /events?run_id&since_seq (replay)
         │ GET /runs ／ GET /health ／ GET /config(browser-facing 値・§5.1) ／ / (静的 SPA)
         ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │  web/console （Next.js App Router・**static export(output:export)** ） │
  │  TS + Tailwind + Zustand。build 成果物 out/ を web_bridge が配信       │
  │  会話TL / 稟議フロー / 司令官判断 / Situation・fleet / 緊急 / map      │
  │  per-mode UI（Mode C は会話/稟議 hide・§12）・撮影向け skin（§12.4）   │
  └──────┬──────────────────────────────────────────────────────────────┘
         │ trace_id deep-link（事後・batch・30s eventually-consistent）
         ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │  Langfuse （write-mostly 耐久 sink）← llm_bridge が自分で書く          │
  │  cost / latency / token / score、join key = trace_id（gen_id 由来）    │
  └─────────────────────────────────────────────────────────────────────┘
```

- **gateway が唯一の ROS↔Web 境界**。UI は ROS / DDS / `.msg` Phase4 移行を一切知らない。
- パッケージ名は snake_case `warehouse_web_bridge`（[doc16:84](16-repository-and-conventions.md)）。

### 2.1 rendering mode の確定 — **Next.js static export（`output:'export'`・CSR SPA）**

**裁定（load-bearing）**: `web/console` は **Next.js static export（CSR-only SPA）**とし、build 成果物 `out/` を `web_bridge`（FastAPI `StaticFiles`）が同一オリジンで配信する。**Node SSR / `standalone` は却下**（Jetson に第2の常駐 Node ランタイムを増やさない）。Node は **build-time のみ**（`next dev` の HMR ＋ CI の `next build`）。

| 観点 | static export（採用） | Node SSR / standalone（却下） |
|---|---|---|
| Jetson #187 | FastAPI 1 プロセスのみ。新規常駐 = WS queue + 静的配信 | **第2の常駐 Node ランタイム（~80-150MB RSS）** が G1 残RAM≥500MB（doc19:129）に食い込む。**#187 R-38 は段階2 Jetson 実測が未通過**（[doc07:243](../shared/07-research-notes.md)）＝未通過 gate に常駐を積めない |
| secret | サーバ側 secret ゼロ。データは全て client（WS/REST） | Node も secret を持たず付加価値ゼロ |
| security | **同一オリジン化で CORS 面が消える**（§11 再導出） | 別オリジン → 実 CORS + 別プロセスの bind/token 管理 |
| systemd | 既存 5-unit set 不変（[jetson-deploy](../setup/jetson-deploy.md)）。web_bridge bind 配下 | 6番目の unit + Node toolchain が Jetson image に必要（存在しない） |
| dev 体験 | `next dev`（HMR）。runtime に Node 不要 | 同等（差分なし） |

- SSR/ISR/route-handler を一切使わない WS-fed SPA には SSR の動機が無い。env 差（gateway URL / mode / token 要否）は **build-time の `NEXT_PUBLIC_*` に焼かず、runtime `GET /config`（§5.1）で解決**＝同一 build 成果物を dev/prod に config overlay だけで配る（[doc19:5,56](19-environments-and-config.md)・[environments.md](../../.claude/rules/environments.md) のハードコード禁止と整合）。
- `next.config`: `output:'export'` ＋ `images:{ unoptimized:true }`（image optimizer はサーバ必須＝export 非互換）。
- この決定は observe-only / R-26（§12.3）を**強化**する（SSR data-fetch 層が無く、actuation sink ゼロの証明が機械的に容易）。

### 2.2 WO画面（KPI/control ダッシュボード）との調停 — **別アプリ（統合しない）**

既に **「WO画面（Warehouse Orchestrator dashboard・Phase 4）」** が計画されている（[doc03:212-226](03-software-architecture.md)・[doc06:267-269](06-implementation-phases.md)、`web/e2e` の Playwright が対象とする画面、owner=`feat/wo-metrics`）。本 console との関係を明示する。

- **WO画面 = CONTROL 面**: WO Bridge Node が Nav2 にゴール送信（doc03:218）／Before-After 切替制御（doc03:226）。
- **web/console = OBSERVE-ONLY 面**: ブラウザ→ロボット操作なし（§1.3・§12.3 R-26）。
- **裁定: 別アプリ・別 owner・別安全 posture として棲み分け、1 つの shell に control 経路を畳み込まない。** observe-only gateway に Nav2-goal control を載せると **web_bridge の R-26 保証（§12.3）が壊れる**＝これが「統合しない」最強の根拠。両者は未着工（`grep wo_bridge ws/src`=0 件）なので重複は実装でなく product/port/Playwright scaffold の競合に留まる。
- rmf-web（Mode C fleet・doc12c）／Foxglove・rosbridge（ブラウザに置かない・§1.3）／Langfuse（事後 sink・§4）との棲み分けは既に各節で確定済み。
- 「1 app に統合 vs 2 app」は最終的に owner 判断（§18 未決）。統合する場合も **control gateway は物理的に別プロセス/別 port 必須**（R-26 不可侵）。

---

## 3. 消費するトピック（既存契約のみ・新規 ROS 契約を出さない）

`web_bridge` は **purely consumer**。下表はすべて doc03 既存契約（[doc03:98-108](03-software-architecture.md)）。

| トピック | 型 | web_bridge での扱い |
|---|---|---|
| `/state_cache/snapshot` | `std_msgs/String`（StateSnapshot JSON, 10Hz） | **state（last-write-wins）として coalesce**（§8） |
| `/llm/command` | `std_msgs/String`（Command JSON, `schemas.py:187-196`） | event（司令官判断） |
| `/llm/reasoning` | `std_msgs/String`（生 text） | event（司令官の思考ログ） |
| `/character/speech` | `std_msgs/String`（`{speaker,text,negotiation_id}`） | event（会話ターン） |
| `/negotiation/start` | `std_msgs/String`（`NegotiationStart`、gen_id 同梱 `negotiation_messages.py:49-53`） | event（稟議開始・**gen_id あり**） |
| `/negotiation/turn` | `std_msgs/String`（`{turn,next}`） | event（バトン） |
| `/negotiation/proposal` | `std_msgs/String`（凍結 `Proposal`、gen_id 付 `schemas.py:209-214`） | event（合意・**gen_id あり**） |
| `/negotiation/abort` | `std_msgs/String`（**canonical `{reason, bot, event_id}`**＝producer `warehouse_safety.guard_logic.build_abort` `guard_logic.py:181,191` / [doc03:108](03-software-architecture.md)。`event_id` で `/emergency/event` と join 可） | event（中断）。※`negotiation_messages.decode_abort`(`:131-141`) は `reason` のみ拾う **lenient consumer**＝producer 契約ではない |
| `/emergency/event` | `std_msgs/String`（コア形 `event_id/robot/type/severity/action_taken/timestamp/requires_llm_review[+detail]` doc12:141-150・edge-trigger doc12:185） | event（緊急） |

> **ObsEvent は ROS トピックではなく WS/REST 上の封筒**（§5）。よって doc03 トピックカタログには `web_bridge` を producer として追加しない（既存契約の consumer であり、生 plumbing 同様 doc03 スコープ外 doc03:112）。doc03 には可視化・モニタリング表（doc03:276-281）に本コンソールを 1 行追記するに留める。

---

## 4. 役割分担：リアルタイム vs Langfuse（「互換性」の正体）

| | ライブ経路（ROS→web_bridge→WS） | Langfuse（事後） |
|---|---|---|
| 流すもの | 会話・稟議・司令官 reasoning・fleet 状態・緊急 | per-generation の cost / latency / token / score |
| 遅延 | ~100ms | **30秒 eventually-consistent**（`tests/live/test_langfuse_trace_tags_live.py` のポーリング前提） |
| 方向 | ストリーム | write-mostly（本番に read/stream 経路は未配線） |
| read 形 | 自前 ObsEvent（確定） | **v4 read-back フィールド形は未検証**（doc08:510・#88 human gate 未実行） |

**結論**: ライブの真実 = ROS via web_bridge、耐久・比較の真実 = Langfuse + `audit.jsonl`（[doc13:518](13-hermes-setup.md)）。両者は `trace_id` で join する（§7）。Langfuse はライブ feed の代替に**物理的になり得ない**。これが「Langfuse と互換性がある箇所／無い箇所」の切り分け。

---

## 5. ObsEvent 封筒（UI 向け唯一の wire 形・凍結契約を wrap）

凍結契約を**一切拡張しない**。`ObsEvent` は `web_bridge` 所有の新規 envelope で、凍結 schema を `payload` として包むだけ。

```jsonc
ObsEvent {
  schema_version: 1,        // Phase4 .msg 移行・additive kind 追加に耐える
  seq: int,                 // per-run monotonic（web_bridge が ingest 時採番）= 唯一の ordering / since_seq backfill key（§後述）
  receive_ts: float,        // web_bridge 受信時刻（wall-clock・表示専用）
  source_topic: str,
  kind: "reasoning"|"command"|"speech"|"turn_baton"|"nego_start"
        |"proposal"|"abort"|"snapshot"|"emergency"|"run_header"|"malformed",
  run_id: str|null,         // §後述 run boundary 由来
  gen_id: int|null,         // negotiation event のみ持つ（§7）
  negotiation_id: str|null,
  robot: str|null,
  trace_id: str|null,       // gen_id を持つ event のみ導出。無ければ null（fail-open・Langfuse リンク無し）
  persona_source: "canned"|"live"|null,  // §後述（現状 canned）
  payload: object           // decode した元メッセージ
}
```

- **凍結契約は read-only 消費のみ**。`payload` のうち凍結なのは `proposal`（`Proposal` `schemas.py:209-214`）だけ。他（speech/turn/start/abort）は凍結契約**外**の wire 形（`negotiation_messages.py:14-17` が明言。**`schemas.py:190-195` という古い docstring 引用をコピーしない**＝実体は `:209`）。
- **malformed never-raise**: 全 producer の decoder は lenient（`negotiation_messages.py:10-12`、`persona.py:86-93`）。`web_bridge` も同様に、壊れた payload では crash せず `kind:"malformed"` の ObsEvent（raw 同梱）にして append+fanout を継続する（events.jsonl に書かれ永遠に replay されるため必須）。
- **seq が唯一の順序キー**: トピックには timestamp が乏しく、唯一の時刻は wall-clock（Docker-Mac で ~6s drift＝**プロジェクト観測値・#233 由来で doc 非裏付け**。snapshot も `datetime.now(UTC)` wall-clock `state_cache.py:124`）。よって順序・`since_seq` backfill は **web_bridge が採番する `seq`** を唯一の権威とし、producer timestamp は display-only にする（seq-only 結論は drift 数値に依存せず自立）。

### 5.1 `GET /config`（runtime config・static export の env 解決）

static export では `NEXT_PUBLIC_*` が **build 時に bundle へ焼き込まれる**ため、gateway URL / token をそこに置くと dev/prod で別 build が要り [doc19:5,56](19-environments-and-config.md)（コード1本・env は config 切替）に反する。よって SPA は build-time env-agnostic とし、起動時に `GET /config` を取得する:

```jsonc
GET /config → {
  ws_path: "/ws",            // 同一オリジンなら相対。dev cross-origin 時のみ絶対 URL
  mode: "none"|"simple"|"open-rmf",   // §12.1 per-mode gating 用
  lan: bool,                 // LAN 公開中か（UI 表示用）
  token_required: bool       // token 本体は返さない（§11）
}
```

web_bridge は base+overlay 解決済みの **browser-facing 値のみ**を返す（secret は返さない）。これで**同一 build 成果物が dev/prod に config overlay だけで配れる**。

---

## 6. QoS と late-join

- **現状の producer はすべて VOLATILE**（latch しない）: State Cache=RELIABLE/KEEP_LAST/depth10/VOLATILE（`state_cache.py:59-61`）、`/llm/*`=depth 10 既定 volatile（`llm_bridge.py:132-139`）、Emergency=reliable but volatile（`emergency_guardian.py:117-122`）。
- 帰結: 後から繋ぐ `web_bridge` は**次の publish まで何も受け取らない**（snapshot のみ 10Hz で self-heal）。
- 方針:
  - `web_bridge` は各 producer の QoS に **matching** subscribe（reliable トピックは RELIABLE）。
  - 新規 WS クライアントの初期状態は **DDS latch ではなく `events.jsonl` の tail（`since_seq`）で seed** する（§9/§10）。
  - （任意・別契約）State Cache に TRANSIENT_LOCAL depth1 を足すと late-join が綺麗になる。これは state track 所有の contract-adjacent 変更（§14）。

---

## 7. trace_id join 戦略（gen_id の現実）

**設計上の落とし穴**: 「全 ObsEvent に trace_id を打つ」は**不可能**。`Command` schema は `gen_id` を持たず（`schemas.py:187-196`）、`/llm/reasoning` は生 text、`/state_cache/snapshot` も gen_id を持たない。**gen_id を wire に載せているのは `/negotiation/start` と `/negotiation/proposal` のみ**（`negotiation_messages.py:49-53` / `schemas.py:210`）。

- **v1 方針（軽量・推奨）**: gen_id を持つ negotiation event のみ trace_id を導出。reasoning/command/snapshot は「Langfuse join key 無し」と明記し、UI は deep-link を出さない。
- trace seed: `seed_for(run_id, work_id) = f"{run_id}:{work_id}"`（`seed.py:33-42`、verbatim `:42`）→ `derive_trace_id`（`seed.py:70-85`）。**`create_trace_id` は seed.py に存在せず Langfuse SDK 由来**（None のとき fail-open）。`seed.py` は domain-free で env を読まない（`:16`）。`WAREHOUSE_RUN_ID` の実読込は呼び手 `llm_bridge.py:165`。
- Langfuse タグ: `[provider, mode, "prompt:<name>", env=<v>]`（`llm_bridge.py:181-187`、最終順は `tracer.py:194`）。consumer は**タグ値で filter**（順序非依存。`tracer.py:70-71` は "normalize stored tag order" と述べる。※"alphabetical" はコード上未検証＝記憶ノートのみ。doc に "alphabetical" と断定しない）。
- **より良い経路（additive・§14）**: `/llm/situation` publisher を新設すると、`Situation` は `gen_id` を持つ（`schemas.py:125-132`）ので bus に gen_id を載せられ、司令官判断パネルの Langfuse join が成立する。これは llm-bridge track の additive contract PR。

---

## 8. throttle / coalesce（最重要・設計の欠落を補完）

`/state_cache/snapshot` は **10Hz**（`state_cache.py:43` `write_period_s=0.1`）で full StateSnapshot を出す。multi-client（録画＋オペレータ）で 20+ frame/s × N となり、background tab や OBS 負荷下のブラウザが drain できず queue 無限成長 → latency / OOM。**[#187 Jetson メモリゲート](../STATUS.md)と直接衝突する**。

方針:
- snapshot は **event でなく state（last-write-wins）** として扱い、`web_bridge` が **`web_bridge.snapshot_hz`（既定 2Hz）**に coalesce してから配信。map パネル用は別途 ~5Hz 可。
- append-only event（会話/稟議/緊急/judgment）は coalesce しない（取りこぼし不可）。
- **永続化（§9）も coalesce 後の snapshot のみ書く**（生 10Hz を events.jsonl に流さない）。

---

## 9. 永続化・retention（events.jsonl）

`audit.jsonl`（[doc13:518](13-hermes-setup.md)）と同族の JSON Lines を追加する additive sink。これにより合意に至らない交渉（timeout/no-agreement、現状 `NegotiationOutcome` は in-process のみ `character_session.py:103`）も耐久化され、**決定的リプレイ**（seq 順）が無料で手に入る。

**落とし穴**: prod の `runtime_dir()` は **`/run/warehouse`（tmpfs/RAM）**（`paths.py:22-30`、prod 判定 `:30`）。無制限 append は RAM を食い #187 を悪化させる。

方針:
- ファイルは per-run **`events-<run_id>.jsonl`**。
- 置き場は **tmpfs でない明示 recordings ディレクトリ（SSD）**。`/run/warehouse` には置かない（config `web_bridge.recordings_dir`、environments.md の base+overlay に従いハードコードしない）。
- size/件数ベースの **rotation + retention**（N runs / M MB）。
- coalesce 後 snapshot のみ永続（§8）。
- disk/RAM budget を #187 と並記して実測（§13 S6）。

---

## 10. WebSocket robustness

- **transport は WebSocket**（SSE はフォールバック）。理由: doc03 が WO bridge を「REST/WebSocket」と位置づけ、将来のオペレータ制御（pause / replay-scrub / mode toggle）に双方向が自然。SSE 的な一方向利用も内包する。
- per-client **bounded `asyncio.Queue`** + overflow policy:
  - snapshot/map（state）: **drop-oldest**（最新だけ要る）。
  - append-only event: **never-drop**。overflow したクライアントは切断し、再接続時に `since_seq` で `events.jsonl` から backfill（source of truth は events.jsonl + seq）。
- **max-clients cap**。ROS callback はブロックせず per-client queue へ non-blocking hand-off（slow client 1 つが rclpy executor や他 client を stall させない）。
- 再接続: クライアントは保持する最終 `seq` を `GET /ws?since_seq=N` で送り、backfill→live に継ぐ。
- **reconnect-storm 対策（ブラウザ特有・必須）**: client 側は **exponential backoff + jitter**、server 側は **per-IP の接続/再接続レート cap**（static max-clients cap に加えて）。tethering 切断・background-tab throttle で再接続が暴走すると events.jsonl tail を反復再読し #187 を増幅するため。録画 client（OBS）の background-tab 時の挙動も規定する。

エンドポイント（**全て同一オリジン :8646**。static export を web_bridge が配信するため CORS は原則不要・§11）:

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/ws?since_seq=N` | WS live tail（backfill→live） |
| GET | `/events?run_id&since_seq&to_seq&kind` | REST replay/pagination（events.jsonl 由来） |
| GET | `/runs` | 観測した run_id 一覧 |
| GET | `/config` | **browser-facing 値**（ws base〔同一オリジンなら相対 `/ws`〕・mode〔§12.1 gating〕・LAN/token 要否）。`NEXT_PUBLIC_*` に焼かず runtime 取得＝同一 build を dev/prod に配る（§5.1）。**token 本体は絶対に返さない/焼かない** |
| GET | `/health` | ヘルス（[doc12a:234](../mode-a/12a-integration-mode-a.md) の慣習） |
| GET | `/`（+静的 asset） | **static export `out/` を `StaticFiles` 配信**（API route 登録の**後**に mount＝API 優先）。observe-only（upload/POST route を持たない＝§12.3 R-26 を配信面にも拡張） |

---

## 11. セキュリティ・公開モデル（observe-only の externally-reachable 面）

新規に外から到達可能な read 面を増やすため、安全先例（Nav2 Bridge の `DEFAULT_HOST='127.0.0.1'`）から逸脱しない。**static export を web_bridge が同一オリジン（:8646）配信する（§2.1）ため、セキュリティ面は「1 origin + 1 port + 1 token」に collapse する**。

- **default bind = `127.0.0.1`**。LAN 公開（撮影用）は **opt-in** とし、その場合のみ **共有トークン必須**（`config` 経由・**ハードコード禁止**。secret は `config/<env>/.env`＝[doc19:76](19-environments-and-config.md) `API_SERVER_KEY` 同様の扱い、`.env.example` にプレースホルダ）。token は static bundle にも `/config` 応答にも**入れない**。
- **CORS は同一オリジンでは原則不要**（SPA・REST・WS が全て :8646＝cross-origin caller が存在しない）。**CORS allowlist（`*` 禁止・`web_bridge.allowed_origins` overlay）が効くのは dev の `next dev`（:3000）→ web_bridge（:8646）cross-origin の場合のみ**にスコープする（prod の dead-config 化を避ける）。
- **wire に secret が乗らない証明**（doc に明記）: publish されるのは LLM の出力 text（`reasoning` は出力であり system prompt ではない `scheduler.py:359`）、snapshot/emergency に資格情報なし、`trace_id` は非秘密の opaque join key。→ [safety.md](../../.claude/rules/safety.md)（鍵漏洩禁止）と整合。

---

## 12. per-mode UI 挙動 と observe-only 証明

### 12.1 mode 別の見え方（構造的事実）
キャラLLM（`character_session`/`character_node`）は **`traffic_mode in {none, simple}`（Mode A/B）でのみ launch**され、`open-rmf`（Mode C）では launch されない（`bringup.launch.py:255-276`＝character_llm Node・gate 条件 `:262`、nav2_bridge 同 gate `:233`）。よって:

| mode | 会話/稟議パネル | 主に見えるもの |
|---|---|---|
| Mode A (`none`) | あり（メイン回） | 会話TL・稟議・司令官判断・全状態。ただし交渉は deadlock/escalation 時のみ発火（steady state では reasoning ログが主役） |
| Mode B (`simple`) | あり（Mode A と同じ扱い） | 同上 |
| Mode C (`open-rmf`) | **hide/disable**（会話 `/character/speech`・`/negotiation/*` は publish されない） | situation/fleet・map・emergency・司令官判断。Open-RMF fleet/task は将来 rmf-web を side panel |

- UI は会話パネルの **idle/empty 状態**を明示的に持つ（交渉が無い通常 cycle・Mode C）。会話 panel は **ModeGate**（Mode C で会話/稟議を hide）で囲む。
- **doc-vs-code 衝突を解消（本 PR で適用済み・再編集しない）**: doc03:105 は以前「Mode A/C 両方で発動」だったが launch gate と doc14 実装フェーズ表（`doc14:255`：Mode C 交渉は Phase 4）と矛盾していた。**本 PR の doc03 reconcile commit で既に「Mode A/B で発動（Mode C 交渉は Phase 4）」へ是正済み**（doc03:105・以後は verify のみ）。

### 12.2 会話は現在 canned（#288 依存）
live persona は Slice 3（Hermes persona・human-gated・Phase 3、≈ #288）まで未 land。現状は **`ScriptedPersona` + `default_offline_script`（固定 2 ターン yield）**（`persona.py:114-138,141-160`、配線 `character_node.py:19-22,52,154-156`）。

- v1 コンソールは #288 land まで **canned content** を描画する。ObsEvent の `persona_source`（canned|live）でラベル可能にする。
- 「実 LLM 会話の観測」というデモ価値は **#288 に依存**（§13 S4 で順序化）。S0–S3 は canned でも独立に開発・テスト可能。

### 12.3 observe-only 証明（R-26 必須）
`web_bridge` は read-only だが、ロボットプロセス内に新規 HTTP/WS server を立てる＝将来 actuation 経路が紛れ込む典型箇所。prose でなく **unit で lock** する（既存先例 `tests/unit/test_modec_noactuation.py`、[doc16 §11](16-repository-and-conventions.md)、規律先例 `character_node.py:15-17` が executor/action_map/Nav2 client を import しない）。

- **R-26 風 unit を必須化**: `web_bridge` が actuation sink（`/cmd_vel*`・`navigate_to_pose`・MCP tools）への publisher/client/forwarder を**ゼロ**生成し、subscription + FastAPI app のみ作る／どの REST・WS route も Nav2・MCP sink に到達しないことを assert。**static 配信 mount（`/`）も upload/POST を持たない**ので、配信面も含めて actuation ゼロを保つ。

### 12.4 撮影・i18n・アクセシビリティ（capture skin）
本プロジェクトの核は YouTube 撮影 + LLM 比較（§1.1・[doc05](../shared/05-video-storyboard.md)）なので、UI は撮影耐性を最初から設計に入れる。

- **日本語 first**: 全 UI 文言は日本語（会話 / 稟議 / 司令官判断 / 緊急 / 状況）。CJK font・適切な wrap。コード/識別子は英語。
- **capture skin**: 高コントラスト・大型 font・固定（reflow しない）グリッド・`presentation mode` トグル（chrome を隠し transcript を拡大）を **layout レベル**で持つ（per-component でなく）。
- **a11y / 非カラー cue**: battery（§18）・emergency などの状態は色だけに依存せずアイコン/ラベルも併記。

---

## 13. スライス計画（依存順）

各スライスは docs-first 完了ゲート（実装↔docs 再照合 → `python3 scripts/check_consistency.py` 0 ERROR → `/consistency-audit` → 残未決を PR 列挙）で締める。観測専用で **browser→robot 経路は無し**（0.3 m/s cap と Emergency Guardian は独立に権威・doc12:77/§Emergency Guardian）。

| Slice | 内容 | 主な DoD / 検証 |
|---|---|---|
| **S0**（docs+governance・本 PR 群） | doc22 + README/doc03 整合 + governance（label/template/rules）。doc03:105 Mode C 矛盾の是正 | `check_consistency.py` 0 ERROR・`track:web` 作成・epic 起票可能化 |
| **S1**（gateway offline core・rclpy-free） | ObsEvent 正規化＋seq 採番＋malformed never-raise＋`events-<run_id>.jsonl`(rotation/per-run)＋`since_seq` replay。**R-26 no-actuation unit を最初から同梱** | fake-publisher pytest（canned→ObsEvent/events.jsonl/WS）・`colcon build` |
| **S2**（rclpy node + FastAPI） | matching-QoS subscribe（§6）＋snapshot coalescer（§8）＋per-client bounded WS queue/backpressure＋reconnect-storm 対策（§10）＋**`GET /config`**（§5.1）＋**`StaticFiles` で `out/` を `/` に mount（API route の後）**。**default bind 127.0.0.1**。config `web_bridge` ブロック（port=8646） | host pytest・health/config 確認 |
| **S2.5**（producer-side additive・**llm-bridge track / contract**） | `/run/header`（latched, run_id/mode/provider/scenario・§後述）＋`/llm/situation` publisher（gen_id を bus へ・§7）＋任意 `/negotiation/outcome`（timeout/no-agreement 可視化） | additive・既存購読者無影響を unit 固定・doc03/doc14 docs PR・**予告→合意（§14）**。※ `GET /config` は S2.5 に依存しない（`/run/header` 未 land の間は最初に観測した event から synthetic run_id を fallback） |
| **S3**（web/console frontend） | Next.js **static export（`output:'export'`）** app + App Router（/live 既定・/runs picker・`/runs?run_id` replay-scrub）+ ConnectionProvider に WS 1本 + Zustand store + per-mode UI（Mode C は会話/稟議 hide §12.1）+ idle/empty + 撮影 skin（§12.4）+ transcript virtualization | **`next build` で `out/` 生成→ web_bridge `StaticFiles` 配信**・web-quality CI（eslint/`tsc --noEmit`/`next build`）green・Playwright（web-e2e 有効化・BASE_URL→web_bridge） |
| **S4**（live persona 連動・**#288 gate**） | canned→live を `persona_source` でラベル。「実会話観測」デモ claim を #288 land に gate | #288 land 後に live 確認 |
| **S5**（LAN 公開・security hardening・opt-in） | token gate + CORS allowlist + `WEB_BRIDGE_TOKEN` + no-secret-on-wire 証明（§11） | 公開前レビュー |
| **S6**（#187 budget 統合） | events.jsonl SSD path + retention 実測・`snapshot_hz` tuning・Jetson メモリゲート突合 | 実測値を #187 / STATUS へ |

**run boundary（S2.5 の `/run/header`）**: run_id は per-run env `WAREHOUSE_RUN_ID`（`llm_bridge.py:165`）だが、購読トピックはどれも run_id を wire に載せない。`web_bridge` は long-lived なので、Bridge 起動時に `/run/header`（TRANSIENT_LOCAL depth1, latched）で `{run_id, mode, provider, scenario}` を publish し、`web_bridge` が (a) event stamp (b) events.jsonl roll (c) console reset (d) `/runs` 構築 に使う。これは UI でなく**欠落した producer-side contract**（llm-bridge track 所有・additive）。

---

## 14. additive 契約の提案（別 PR / 別 track）

いずれも **additive-first**（新トピック・既存購読者に無影響）で、`Command`/`Proposal` の凍結フィールドは触らない。owner は **llm-bridge track**（web track でない）。doc03 トピックカタログ（skeleton 共有契約・[doc16:205](16-repository-and-conventions.md)）に触れるため **`contract` ラベル＋依存トラック予告**（[parallel-workflow.md §4](../../.claude/rules/parallel-workflow.md)）。

1. `/run/header`（latched）— run 境界（§13）。
2. `/llm/situation` publisher — doc03:100 にカタログ済だが publisher 不在（grep 確認）。新設すれば「司令官が見た正確な JSON」（gen_id 同梱）を観測でき、§7 の join が成立。
3. `/negotiation/outcome`（任意）— `{negotiation_id, gen_id, status: AGREED|TIMEOUT|NO_AGREEMENT|ABORTED, turns}`。現状 outcome は in-process（`character_session.py:103`）＝ TIMEOUT/NO_AGREEMENT が wire 上不可視。これが無いと UI は「考え中」と「諦めた」を区別できない（v1 は proposal/abort 有無から近似）。

> 既存ドリフトの flag（別 doc fix・llm-bridge track）: `character_node.py:10` の docstring は `/llm/command` を購読すると述べるが実コードは購読しない（`:91-94`）。

---

## 15. 技術スタック

- **gateway**: `ws/src/warehouse_web_bridge`（ament_python・rclpy + FastAPI + uvicorn + websockets）。共存パターン [doc12a:200-234](../mode-a/12a-integration-mode-a.md)。依存は原則 `warehouse_interfaces`（凍結 schema / `paths`）のみ。会話 decoder（`negotiation_messages`）の再利用は §16 の所有判断に従う。
- **frontend**: `web/console`（**Next.js App Router・static export `output:'export'`（CSR SPA・§2.1）** + TypeScript(strict) + Tailwind + Zustand）。runtime に Node server を持たず、build 成果物 `out/` を web_bridge が配信。※"Next.js" は repo 未記載＝本設計が新規採用（既存 `web/e2e` は別目的の Playwright scaffold）。
  - **App Router 構成**（全て export 互換・runtime dynamic param なし）: `app/layout.tsx`（root に `<ConnectionProvider>`/`<ModeProvider>` を `'use client'` で**1回** mount＝navigation で破棄されない）／`/live`（既定の単一画面）／`/runs`（picker・`GET /runs`）／replay は **`/runs?run_id=...`（query param）**にして export の `generateStaticParams` 要件を回避（runs は runtime 発見のため）。`next.config`: `output:'export'` ＋ `images:{ unoptimized:true }`（next/image optimizer はサーバ必須＝SVG/plain `<img>`）。
  - **data 層**: live は `useWebSocket`（`GET /ws?since_seq`・再接続で last seq 再送 backfill→live・**drop policy は server をミラー**＝snapshot は drop-oldest / event は never-drop→overflow 切断時は `/events?since_seq` 再 sync）。状態は **Zustand**（`connection{status,lastSeq}` / `run{run_id,mode,provider,scenario}`〔`/run/header` 由来〕 / `snapshot` last-write-wins by robot / `negotiationsById` append by negotiation_id / `commanderLog`・`emergencies` append）。**seq が唯一の apply 順序**（§5）。**TanStack Query は live に使わない**＝`/runs`・`/events` 履歴 pagination・Langfuse 事後パネルのみ。
  - **コンポーネント**: `<RunHeader>`（canned/live バッジ §12.2）/ `<ConversationTimeline>`（**virtualization 必須**・idle/empty「交渉なし」）/ `<RingiFlow>` / `<CommanderDecision>`（trace_id deep-link は gen_id 持つ negotiation event のみ §7）/ `<SituationFleet>`（battery band §18）/ `<EmergencyPanel>` / `<MapView2D>`（SVG・9 KNOWN_LOCATIONS `config/warehouse.base.yaml:36-44`）。Mode C は `<ModeGate>` で会話/稟議を hide（§12.1）。
- **通信**: WebSocket（§10）。env 値は build に焼かず **`GET /config`（§5.1）で runtime 取得**（同一 build を dev/prod に配る）。secret は `NEXT_PUBLIC_*` にも `/config` にも出さない。
- **port**: `web_bridge.port = 8646`（既使用: Hermes 8642 / Nav2 Bridge 8645 / rmf-web 8000 / **:3000 は dev の `next dev` のみ**）。port registry は §17。

---

## 16. パッケージ・トラック・環境・CI

- **置き場**: gateway=`ws/src/warehouse_web_bridge`、frontend=`web/console`（doc16 は frontend 規約未整備＝本設計で新規定義）。
- **frontend 規約（新規定義・stub）**: rendering = **static export（`output:'export'`）**／build output = `web/console/out/`／**配信者 = web_bridge（FastAPI `StaticFiles`）**／Node = 20（**build-time のみ**・Jetson runtime には置かない）／`web/console` と `web/e2e` は **2 つの独立 sibling npm project**（workspace 化しない＝`web/e2e` は Playwright 専用で install lifecycle が別・既存どおり）。両者は BASE_URL（HTTP）越しにのみ結合し import しない。
- **build/ship/serve flow**: prod は git-tag clone + Jetson 上 `colcon build`（[doc19:94](19-environments-and-config.md)・[jetson-deploy](../setup/jetson-deploy.md)）。**Node toolchain を Jetson に置かないため、CI（web-quality job）が `next build`→`out/` を生成し git tag 同梱**（または release artifact）。Jetson 上 build は不採用（Node toolchain + node_modules を memory/disk 制約デバイスに増やさない）。web_bridge は同梱 `out/` を `StaticFiles` 配信。
- **トラック**: 新規 `track:web`（§S0 で governance 整備）。`web/e2e/` と `web/console` は web track 所有、ただし `ci.yml` 内の web 系 job は governance（`.claude/**`・`.github/**` 所有 [parallel-workflow.md §7.1](../../.claude/rules/parallel-workflow.md)）。**`web/e2e` の所有が `track:web` か `feat/wo-metrics`（WO画面）かは §2.2 の app 分離判断に連動＝未決（§18）**。
- **decoder 再利用の判断**: `negotiation_messages` の decoder を別 track から import するのは疎結合違反（parallel-workflow §2.1）。**推奨 = decoder を `warehouse_interfaces` へ promote する小さな contract PR**（import clean・将来の web track 独立にも効く）。代替 = gateway を一時的に llm-bridge track 内に置く。
- **config schema**（additive・ハードコード禁止 [environments.md](../../.claude/rules/environments.md)）:
  - `web_bridge.port: 8646`（**base**・bringup/skeleton 所有・予告必要）／`web_bridge.snapshot_hz`（**base**・§8）
  - `web_bridge.host`（**overlay**・dev=127.0.0.1）／`web_bridge.allowed_origins`（**overlay**・dev cross-origin のみ）／`web_bridge.recordings_dir`（**overlay**・**明示 SSD path**・例 Jetson `/opt/warehouse/recordings`・**`paths.runtime_dir()`=tmpfs `/run/warehouse` を流用禁止** `paths.py:22-30`・§9）
  - `WEB_BRIDGE_TOKEN`（`config/<env>/.env`・`.env.example` にプレースホルダ）
- **CI**（governance PR・`.github/**`）: 既存 web-e2e job（`if:false` `ci.yml:168`）を有効化＋`web/**` paths filter、`web-quality` job（setup-node 20 + eslint + `tsc --noEmit` + `next build`〔=export〕）を追加。**同一 governance PR で [doc20 §4](20-dev-quality-and-testing.md) の CI job 一覧を更新**（job 数ドリフト防止・firmware-safety 先例）。

---

## 17. port 一覧（drift 防止の単一参照）

| service | port | 出所 |
|---|---|---|
| Hermes Gateway | 8642 | doc13 |
| Nav2 Bridge (Mode A/B) | 8645 | [doc12a:221-224](../mode-a/12a-integration-mode-a.md) |
| rmf-web (Mode C) | 8000 | doc12c |
| **web_bridge（本書・SPA も同一オリジン配信）** | **8646** | 本書 §15・§2.1 |
| Next.js dev server（**dev のみ**） | 3000 | `next dev` HMR。`web/e2e` Playwright fallback と共有（衝突注意）。**prod は :8646 同一オリジン配信＝:3000 listener なし** |
| WO画面（KPI/control・将来 build 時） | （未割当・予約） | 別 owner `feat/wo-metrics`（§2.2）。割当時に本表へ追記し 8642/8645/8646/8000/3000 と衝突回避 |

---

## 18. 未決事項（human が決める）

**確定済み（本書で裁定・RESOLVED）**:
- **rendering mode = Next.js static export（`output:'export'` CSR SPA）・web_bridge 同一オリジン配信**（§2.1）。Node SSR/standalone は却下。
- **WO画面と web/console は別アプリ（統合しない）**＝observe-only と control の安全 posture 分離（§2.2）。ただし「最終的に 1 app へ寄せるか」は下記 #1。
- transport = WebSocket（§10。SSE はフォールバック）。

**残る未決（owner 判断）**:
1. **WO画面と web/console を 1 app に寄せるか 2 app のままか**（§2.2 は 2 app 推奨）。寄せても control gateway は別プロセス/別 port 必須（R-26 不可侵）。
2. **`web/e2e` の owner**（`track:web` vs `feat/wo-metrics`・#1 に連動）。
3. **static 成果物の git tag 同梱（推奨）vs Jetson 上 build**（§16 build/ship/serve flow）。
4. decoder 再利用: `warehouse_interfaces` へ promote（推奨）/ llm-bridge track 内に gateway を置く。
5. `web_bridge` config block（port=8646 ほか §16）の base.yaml land タイミング（今予告 PR か S2 slice 内か・bringup/skeleton 所有）。
6. 公開モデル: 撮影時 LAN bind 可否 / token / CORS（§11・非 loopback bind 前に security review）。
7. **`recordings_dir` の具体 SSD path**（Jetson KIOXIA・例 `/opt/warehouse/recordings`）と rotation 予算（N runs / M MB）。#187 段階2 実測（[doc07:243](../shared/07-research-notes.md)）後に確定。
8. additive 提案の採否: `/run/header`（推奨）・`/llm/situation`（推奨）・`/negotiation/outcome`（任意）（§14）。
9. battery の **UI color band は doc12 に無く本書/コンソールで新規定義**（doc12:250-252 は POLICY band のみ）。閾値: 10/20/30%（doc12:250-252）。
10. mode 値域（A/B/C vs none/simple/open-rmf）は doc20 §8.4 で未決＝RunHeader/ModeGate/provider×mode 比較パネル前に確定。
11. virtualization lib（react-window / virtua / TanStack Virtual）＝frontend 所有・export bundle を小さく保つ前提で pin。
12. web workspace の package manager（pnpm 推奨 / npm＝既存 `web/e2e` の `npm ci` 踏襲）。

---

## 19. 参照（たどれる file:line）

- 共存パターン / health: [docs/mode-a/12a-integration-mode-a.md:200-234](../mode-a/12a-integration-mode-a.md), :234
- トピックカタログ: [docs/architecture/03-software-architecture.md:98-108](03-software-architecture.md), :112, :276-281
- 会話 producer: `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/negotiation_messages.py:88-101,49-53,10-17`（decode_abort `:131-141` は **lenient consumer**） / `character_session.py:89-90,103` / `character_node.py:15-17,91-94,154-156` / `persona.py:114-160`
- abort producer（canonical）: `ws/src/warehouse_safety/warehouse_safety/guard_logic.py:181,191`（`build_abort`→`{reason, bot, event_id}`・[doc03:108](03-software-architecture.md)・doc14:241-247 R2）
- 司令官 publisher: `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/llm_bridge.py:132-133,165,181-187,231-235` / `scheduler.py:146(noop),187-188(callback配線),350(proposal注入),359-360(発火)`
- 凍結契約: `ws/src/warehouse_interfaces/warehouse_interfaces/schemas.py`（Situation :125-132 / Command :187-196 / Proposal :209-214 / StateSnapshot :95,:104 / RobotState :38-61）
- trace seed / paths: `ws/src/eval_sdk/eval_sdk/seed.py:16,33-42,70-85` / `tracer.py:70-71,194` / `ws/src/warehouse_interfaces/warehouse_interfaces/paths.py:22-30`
- QoS / rate: `state_cache.py:43,59-61,124` / `emergency_guardian.py:117-122`
- launch gate: `ws/src/warehouse_bringup/launch/bringup.launch.py:233(nav2 gate),255-276(character_llm Node),262(gate 条件)`
- MCP tool 7: `ws/src/warehouse_mcp_server/warehouse_mcp_server/tools.py:404-448`（mint :435 / audit :446 / publish :447）
- KPI: `ws/src/warehouse_orchestrator/warehouse_orchestrator/kpi.py:185,199,307` / `warehouse_orchestrator/CLAUDE.md:15`
- Langfuse / 比較: [doc08:373-375](08-llm-bridge-common.md)（Pattern A）, :348-352,396-397（score 定義/比較指標）, :504（Grok 価格）, :510（read 形未検証）, :514（4×3 grid）, :517（Metrics API・score name 符号化）
- 緊急 / battery / 周期: [doc12:77](12-infrastructure-common.md)（0.3 m/s）, :141-150（event コア形）, :185（edge-trigger）, :191（near 距離閾値）, :250-252（battery band）, :262（100ms）
- キャラLLM: [doc14:40-46](14-character-llm-negotiation.md)（実況）, :239-247（abort）, :255（Phase 4 Mode C 交渉）
- 環境 / テスト / デプロイ: [doc19:5,56](19-environments-and-config.md)（コード1本・env は config）, :76（.env）, :94（git tag deploy）, :129（G1 残RAM≥500MB）, [doc20](20-dev-quality-and-testing.md), [doc16:84](16-repository-and-conventions.md)（命名）, :205（topic catalog 所有）, :188-200（branch 表）, [jetson-deploy](../setup/jetson-deploy.md)（5-unit systemd）
- WO画面 / 撮影 / #187: [doc03:212-226](03-software-architecture.md)（WO画面=control）, [doc06:267-269](06-implementation-phases.md)（WO Bridge Phase4）, [doc05](../shared/05-video-storyboard.md)（撮影）, [doc07:243](../shared/07-research-notes.md)（#187 R-38 段階2 未通過）
- config: `config/warehouse.base.yaml:29-31`（cycle）, :36-44（locations）, :71（nav2_bridge :8645）
