# 07 Mode X-ER 実装ステータス（想定 vs 現状・ER→L3）

> **目的**: Mode X-ER の「ER（Gemini Robotics-ER）→ L3 → frozen Command → L2/L1/L0」パイプラインが、**設計 docs が想定する完成形**に対して **`origin/main`（`1609515`）で今どこまで実装・検証されているか**を、実コードの file:line で裏取りして可視化する。図解版は [implementation-status.html](implementation-status.html)（[mode-x-er-explainer.html](mode-x-er-explainer.html) と同スタイル）。
> **正本の切り分け**: 「想定」= 設計 docs（[01](01-architecture-and-flow.md)/[02](02-l3-planning-core.md)/[03](03-er-adapter-skeleton.md)/[04](04-er-input-modalities-and-stt.md)/[06](06-unfrozen-contract-resolutions.md)）。「現状」= main の実コード＋テスト。両者がズレたら**コードが真**（本 doc は現状の記録であり、設計正本を上書きしない）。
> 生成 2026-07-02（re-pin 2026-07-04）・対象 `origin/main 1609515`。live 手順は [dev/07 runbook](../dev/07-mode-x-er-live-e2e-runbook.md)。

<!-- impl-status-pin: 1609515 -->
<!-- 鮮度ゲート: `python3 scripts/check_impl_status.py`（開発が進んで pin より main が動いたら drift を報告）。更新規律は末尾「鮮度と更新」節。 -->

## 総括（どこまで終えているか）

- **offline の ER→L3 チェーンは「frozen Command まで」完成・テスト済**。手組みの `RawModelOutput` が Handoff → Validator（R-26 0-dispatch）→ Visual Resolver → Task Graph Executor → Command Compiler を通り、**本物の `warehouse_interfaces.schemas.Command`** に着地する（`pipeline.compile_raw_output`＝[pipeline.py:90-187](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/pipeline.py)）。core chain（validator/pipeline/handoff/adapter/transport の 5 file）の offline unit **63 本が緑**（L3 各 stage の個別 suite はさらに多数＝下記マトリクスの per-stage 本数参照。要 python3.12。host `python3`=3.7 は `frozenset[str]` で落ちる）。
- **live-send は #389 で main に着地**（2026-07-02 merge）＝`build_provider_request` + `HttpErTransportSender` + `_live_send` + HERMES→DIRECT fallback + `WAREHOUSE_LIVE_ER` gate。offline-unit-tested・dormant（gate 越し・**稼働 Bridge サイクルからの呼び出しは未接続**＝XER6/wiring）。
- **本来の完成形（実 ER → live Command → L2 MCP/Policy Gate → Nav2/RMF 作動）はなお未達**。frozen Command を L2 以降へ運ぶ X-ER テストは存在しない（＝XER6 が PENDING）。
- 要するに: **transport の「選択」＋ offline L3 全チェーン＋ live-send 機構＝実装済み**、**live-send を稼働 cycle が呼ぶ配線 と L2/L1/L0 作動＝未接続 / 未実走**。

## 想定 vs 現状マトリクス（file:line 裏取り）

| ステージ | 想定（設計 doc） | 現状 | 主な根拠（file:line） | ギャップ |
|---|---|---|---|---|
| **L4 ER-in-Hermes 境界**（text/image=8643, audio=direct） | text+image は専用 lean Hermes（8643, OpenAI互換）経由、audio は direct（vanilla Hermes は input_audio に 400）。audio-through-Hermes fork(8644) は将来 target（doc06 §5） | **PARTIAL** | `deploy/dev/run-er-hermes.sh:2-8,15,23`・`config.lean.yaml:1-7`（gemini-robotics-er-1.6-preview）・`deploy/hermes/er-audio-fork/`・PROBE-2 400=`doc06:159` | text/image は inside-Hermes で完了。**audio は direct が shipped**（fork 8644 は probe target のみ）＝「ER inside Hermes」は text/image で真・audio で偽 |
| **L4 adapter: transport 選択 + offline seam** | `propose_plan(ErTaskRequest)→RawModelOutput`、`resolve_audio_transport`（fail-safe DIRECT）、observation-only enums | **DONE** | `transport.py:29-58`・`adapters/enums.py:23-40`・`er_task.py:31-73`・`gemini_er.py:39-75`（offline seam）。25 offline tests | なし（選択面のみ。実行ではない） |
| **L4 adapter: LIVE-SEND 実行** | frozen per-transport 組立 + HTTP sender + HERMES→DIRECT fail-safe（doc06 §5 tail:277-279） | **DONE**（機構）／ 呼び出し配線は PENDING | **#389 で main 着地**（2026-07-02）＝`gemini_er.py` の `build_provider_request`/`ErTransportSender`/`HttpErTransportSender`/`_live_send`・HERMES→DIRECT fallback・`WAREHOUSE_LIVE_ER` gate（未設定で RuntimeError）。offline HTTP unit（urlopen monkeypatch）で URL/header/gate/missing-base_url を検証 | 実 HTTP は gate 越し（有料）。**稼働 Bridge サイクルが `propose_plan` を呼ぶ配線は未接続**（XER6/wiring）＝主線で自動発火しない |
| **知覚レーン + STT + observability** | ER critical lane ∥ out-of-band STT（レイテンシ非追加・fail-open）、Bridge 所有 Langfuse trace（deterministic seed） | **DONE** | `perception_lanes.py:48-125`・`observability.py:88-147`（`LangfuseTranscriptTracer` は実 fail-open span・既定 `enabled=False` no-op＝#382/`75f6af3` で旧 no-op を置換）。11 offline tests | `HermesTranscriber` の実 HTTP path・`JsonlTranscriptSink` の書込 path は offline unit なし（live-gated のみ）。実 Langfuse 着地は #88 human gate |
| **L3 Validator（0-dispatch + ValidationReport 語彙）** | `validate(raw,ctx)→ValidationReport`、status≠accepted→0 dispatch、正確に 9 code、注入式 PlanPolicy、反復サイクル検出 | **DONE** | `validator.py:90`・`report.py:69-87`（**厳密に 9 code**）・`report.py:163-181`（3層 0-dispatch double-guard・frozen）・`validator.py:55-84`（反復 Kahn）。53 tests | `normalized_plan` は意図的 defer stub（doc02:346）＝下流の責務 |
| **L3 Visual Resolver（pixel→map→snap）** | `resolve(plan,calibration)→ResolutionResult`、3×3 homography・valid-polygon・最近傍 KNOWN_LOCATION snap（注入半径）・座標ゴール禁止 | **PARTIAL** | `resolver.py:58-74`(homography・w≤0 fail-closed)・`:77-102`(polygon)・`:105-127`(snap)・`models.py:81-94`(unresolved→None)。19 tests・`pipeline.py:180` で wire | doc02:150 の「距離 **かつ** object class」で snap のうち **object-class 節が defer**（`# TODO` `resolver.py:192-194`・`Detection.color` 未使用） |
| **L3 Task Graph Executor** | 6-state・自作 runtime state（NetworkX なし）・after 順 ready_tasks・二重/重複/サイクル guard・swappable store・zero actuation | **DONE** | `states.py:30-38`・`executor.py:84-146`(ready_tasks)・`:115-127`(dedup)。17 tests・`pipeline.py:185-186` で wire | doc-drift のみ（`executor.py:9-11` の「pipeline に wire しない」注記が XER6 後 stale） |
| **L3 Command Compiler（XER5）** | `WarehouseNavCompiler` が resolved task を frozen `Command` へ・0-dispatch skip・1:1 audit・gen_id/速度/座標なし・`ExecutionProfile` x_lite/x_rmf | **PARTIAL** | `compiler.py:95-134`（実 `warehouse_interfaces.schemas.Command/CommandItem` を構築）・`:113-130`(0-dispatch skip)・`:79-83`(x_rmf は NotImplementedError)。`pipeline.py:187` で wire。42+ tests | **x_rmf 半分が defer**（#346・`RmfTaskCompiler` は設計/stub のみ）。doc-drift（`__init__.py:6`/`CLAUDE.md:6` の「wire しない」注記が stale） |
| **L3 pipeline/handoff 配線** | 常在 Handoff seam が L4 raw を決定論的 Validator 入力へ正規化（fail-closed KEY-NAME gate）・full L3 chain to frozen Command・transport 非依存 | **DONE** | `handoff.py:114-164`（endpoints/velocity/coords/unknown を reject）・`pipeline.py:58-87`(validate_raw_output)・`pipeline.py:90-187`(compile_raw_output・R-26 short-circuit)。41 tests・transport 等価性検証 | 配線は **offline のみ**＝`gemini_er.py` は `RawModelOutput` を作るが**それを pipeline に渡す稼働 Bridge サイクルが無い**（live path は #344 defer） |
| **テスト + live インフラ** | offline XER1-5 suite・env-gated live 前駆 probe・lean gateway + safe `--check`・XER6 sanctioned live e2e（sim） | **PARTIAL** | offline: `test_l3_pipeline.py:197-317`（full chain to frozen Command）・~63 tests。live: `tests/live/{test_er_handoff_live,test_xer3_chain_live,test_xer_full_chain_live}.py`（`WAREHOUSE_LIVE_ER=1`+key で module-skip 解除）・`run-live-er-{smoke,chain}.sh`(`--check` 安全)・`run-er-hermes.sh`(8643) | live probe は**前駆（forerunner）**＝Command で終端・作動なし。**XER6（Command を L2/Nav2 へ運ぶ sim e2e）は PENDING**（該当テストなし・`tests/e2e/*` は Mode A nav） |
| **L3 composition 層（run manifest + plugin）** | run_manifest.v1 + startup fail-closed preflight + effective-composition record + typed validate_plan hookspec + namespaced plugin code + policy clamp + safety-critical profile hash gate（[productization/09](../productization/09-run-manifest-and-plugin-composition.md) / [ADR-0003](../adr/0003-bridge-local-manifest-composition.md)） | **PROPOSAL→IN-PROGRESS**（設計標準化済・offline spike が確認） | 設計=productization/09 + ADR-0003・offline spike が run_manifest.v1 / preflight / effective-composition / typed hookspec / clamp を実装（bridge-local `robotics/composition/`） | 稼働 Bridge cycle への配線は XER6 pending・root `.gitignore` の `out/runs/` entry は follow-up |

## 一気通貫（e2e）の現状 — 2 tier・どちらも作動には届かない

- **OFFLINE（無料・通常 CI/`pytest`・network/provider/ROS 不要）**: 手組み `RawModelOutput` が L3 全チェーンを通り frozen Command に着地。正確な範囲=[`tests/unit/test_l3_pipeline.py:197-317`](../../tests/unit/test_l3_pipeline.py) が `compile_raw_output`（handoff→validator→visual_resolver→task_graph_executor→command_compiler）を両 transport で駆動し、R-26 0-dispatch short-circuit を `RaisingCompiler` で保護。**core chain 5 file で 63 unit が緑**（要 python3.12。host `python3`=3.7 は `frozenset[str]` で失敗。L3 各 stage の個別 suite〔validator/resolver/executor/compiler〕は別途より多数）。
- **LIVE（有料 Gemini Robotics-ER・human gate・`WAREHOUSE_LIVE_ER=1`+`GEMINI_API_KEY`/`GOOGLE_API_KEY`）**: [`tests/live/test_xer_full_chain_live.py`](../../tests/live/test_xer_full_chain_live.py) が**実課金 generateContent**（`_er_live_client.py:call_er_direct`）→ `RawModelOutput(direct)` → `compile_raw_output` → `Command` かつ全 item.destination が frozen KNOWN_LOCATIONS ∧ action==NAVIGATE を assert。runner=[`deploy/dev/run-live-er-chain.sh`](../../deploy/dev/run-live-er-chain.sh)（安全 `--check` あり）。text/image は lean Hermes gateway(8643)経由も可、audio は direct。**これらは forerunner（不変条件の assert であり acceptance ではない）で、チェーンは Command で終端＝L2 MCP/Policy Gate/L1/L0 へ続かない**。
  - **2026-07-02 live 実走（operator 承認・課金）**: `test_xer_full_chain_live.py` **PASS**。実 `gemini-robotics-er-1.6-preview` 呼び出し（**607 tokens**）→ `compile_raw_output` → **valid frozen Command（`command_items=0`・`destinations=[]`）**。この fixture では live ER 出力が KNOWN_LOCATIONS へ解決せず **R-26 0-dispatch**（安全に空 Command）＝**チェーンが live で end-to-end 動作し安全に終端すること**は実証。ただし**非空 live Command（ER 出力が KNOWN_LOCATIONS に解決するケース）は未実証**。この test は adapter の `propose_plan` live-send（**#389 で main 着地したが本 test は経由しない**）ではなく `_er_live_client.call_er_direct`（test 側直呼び）を使う。gateway は `run-er-hermes.sh`(8643) を起動済で health OK（`/v1/models`=`hermes-agent`）。
- **どの run もカバーしないもの**: (1) `gemini_er.propose_plan` の実 live-send を**稼働 Bridge サイクルが呼ぶ**経路（#389 で機構は main 着地したが、それを呼ぶ cycle が未接続＝主線で自動発火しない）、(2) Command を L3 の先（MCP/Policy Gate/Nav2/RMF 作動）へ sim で運ぶ **XER6 closure**（[dev/07:188](../dev/07-mode-x-er-live-e2e-runbook.md)・[README:91](README.md)）＝main に該当テストなし。

## トップダウン連結（running で上から届くか）

STATUS（実装済みか）とは**直交する第 2 軸＝CONNECTIVITY**（稼働している rclpy node から、上＝operator 入力 → 下＝motor へ各 seam に到達できるか）。file:line で裏取りした結論: **上から running で走る pipeline はゼロ hop**（部品は揃うが背骨＝X-ER commander node が無い）。全 seam は constructible/offline だが、**稼働 node から呼ばれるものは一つも無い**。唯一稼働している commander node（Mode A の `llm_bridge`）には **ER 参照がゼロ**（`llm_bridge.py`/`scheduler.py` に `robotics_planning_core`/`propose_plan`/`gemini_er` の import・呼び出しなし）。図解版は [implementation-status.html](implementation-status.html) の「つながり(connectivity)」帯（node の**枠＝ring**で表現。塗り＝STATUS とは別軸）。

| hop | seam | 到達状態（connectivity） | 根拠（file:line） |
|---|---|---|---|
| ⓪ | mic → audio capture / operator input lane を driving | **NOT-IMPLEMENTED**（呼び出し元 node なし） | `perception_lanes.py:48-125`（lane はあるが caller なし） |
| ① | text/image → lean Hermes ER gateway(8643) の「200 OK」 | **CONSTRUCTIBLE-NOT-CALLED**（curl / pytest test-client のみ・稼働 adapter は未呼び出し） | `run-er-hermes.sh:2-8`・live probe は `tests/live/*` の human gate のみ |
| ② | adapter LIVE-SEND（`propose_plan`/`HttpErTransportSender`） | **CONSTRUCTIBLE-NOT-CALLED**（#389 で機構は main・唯一の caller は pytest） | `gemini_er.py:39-75`・`build_provider_request`/`_live_send`（`WAREHOUSE_LIVE_ER` gate） |
| ③ | adapter → `pipeline.compile_raw_output`（RawModelOutput 受け渡し） | **OFFLINE-ONLY**（稼働 Bridge cycle が渡さない・pytest 内でのみ） | `pipeline.py:90-187`・`tests/unit/test_l3_pipeline.py:197-317` |
| ④ | full L3 chain → 凍結 `Command`（handoff→validator→resolver→executor→compiler） | **OFFLINE-ONLY**（実装済み done だが running から不達） | `handoff.py:114-164`・`validator.py:90`・`compiler.py:95-134`・`pipeline.py:187` |
| ⑤ | 凍結 `Command` → L2 action_map/MCP/Policy Gate → L1 Nav2 → L0 | **NOT-IMPLEMENTED**（X-ER 経路は main に無い＝XER6）／ **RUNNING-WIRED-for-ModeA**（同型 seam は Mode A で稼働） | Mode A: `scheduler.py:368-374`(`_dispatch_command`)・`bringup.launch.py:240-243`(`llm_bridge` Node) |

**verdict**: 上から running で走る pipeline は**ゼロ hop**（部品は揃うが背骨＝X-ER commander node が無い）。同型の seam が Mode A では稼働 node から連結して届く（`llm_bridge.py:308` main → `scheduler.py:368-374` dispatch → Nav2 `bringup.launch.py:240-243`）ので、**X-ER に欠けているのは配線であって部品ではない**。

**next_wire（背骨を 1 本通す）**: 新規 `warehouse_llm_bridge/x_er_bridge.py`（`main()` + `rclpy.Node`）を作り、`setup.py` の `console_scripts`（`setup.py:29-30` の `llm_bridge` と同型）に登録、`bringup.launch.py`（`:240-243` と同型）に追加。その cycle が `build_er_adapter` → `propose_plan` → `compile_raw_output` → dispatch を鎖にすれば、**hops ⓪③④⑤ を一括で閉じる**（L3 帯の OFFLINE-ONLY が RUNNING-WIRED へ変わる）。※ 2026-07-07 更新: 起動 gate と node 契約の正本は [08-x-er-bridge-node-spec](08-x-er-bridge-node-spec.md)＝launch 条件は `traffic_mode=='x-er'`（本行の旧スケッチ・不採用）ではなく **`mode_x_er.enabled`**（[06 §3 追補](06-unfrozen-contract-resolutions.md)・traffic_mode に `x-er` 値は発明しない）。

## ER inside Hermes（図の要）

「ER が Hermes 内にいる」は **text/image では真**（専用 lean Hermes 8643 の OpenAI 互換 `/v1/chat/completions` 経由。`run-er-hermes.sh`＋`config.lean.yaml`）。**audio では偽**（vanilla Hermes が input_audio に 400＝PROBE-2 `doc06:159`。shipped は **direct**＝恒久 fail-safe）。audio-through-Hermes は fork gateway **8644**（`deploy/hermes/er-audio-fork/`）だが**現状は productization target（probe）**であり shipped wire ではない。個人 `~/.hermes` は触らない（`HERMES_HOME` 隔離）。

## 残作業（完成形へ）

- ✅ **PR #389 merge 完了（2026-07-02）**＝live-send 機構（build_provider_request + HttpErTransportSender + `_live_send` + HERMES→DIRECT fallback + `WAREHOUSE_LIVE_ER` gate）が main に着地。残るは次項の「稼働 cycle 配線」（[doc06:277-279](06-unfrozen-contract-resolutions.md) / [dev/07:189,202](../dev/07-mode-x-er-live-e2e-runbook.md)・#344/#389）。
- **L4 アダプタ出力を稼働 Bridge サイクルへ配線**＝`RawModelOutput` が実際に `pipeline.compile_raw_output` に届くようにする（offline テスト以外に caller が無い。owner: [01:169-182,245-251](01-architecture-and-flow.md)）。
- **XER6 sanctioned live e2e**＝frozen Command を L2 MCP/Policy Gate/L1 Nav2 Bridge へ sim（X-lite）で運ぶ（owner: [README:91](README.md) / [dev/07:188](../dev/07-mode-x-er-live-e2e-runbook.md)・main に該当なし）。
- **audio-through-Hermes fork(8644) の本採用**（audio 観測統合が要る場合。それまで audio=direct。owner: [doc06 §5 補遺:269](06-unfrozen-contract-resolutions.md) / [environments.md:26](../../.claude/rules/environments.md)・#357）。
- **Visual Resolver の object-class snap**（doc02:150「距離 かつ object class」の defer 分。`Detection.color` proxy 未使用。owner: [02:150](02-l3-planning-core.md)・`# TODO resolver.py:192-194`）。
- **x_rmf ExecutionProfile**＝`RmfTaskCompiler` plugin 実装（Mode X-rmf backend。owner: [02:234,240](02-l3-planning-core.md)・#346）。
- **実 Langfuse trace の着地**（ER/STT 観測レッグ・既定 OFF・main に未着地。human gate #88/HLF-G0..G5。owner: [productization/02:177-199](../productization/02-l4-robotics-bridge-box.md) / `observability.py:16`）。
- **offline カバレッジ追補**: `HermesTranscriber` 実 HTTP/fail-soft・`JsonlTranscriptSink` 書込 path（現状 live-gated のみ。owner: [04](04-er-input-modalities-and-stt.md) / doc06:164）。
- **doc-hygiene**: XER6 で wire 済みなのに「pipeline に wire しない」と残る stale 注記（`visual_resolver/resolver.py:9-10`・`__init__.py`・`task_graph_executor/executor.py:9-11`・`command_compiler/__init__.py:6`・`CLAUDE.md:6`）を [docs-first](../../.claude/rules/docs-first.md) 同期で是正。

## 鮮度と更新（freshness・開発が進んだら更新する仕組み）

本 doc + [図](implementation-status.html) は **main の 1 時点スナップショット**（pin=`1609515`）。`STATUS.md` と同じく放置すると開発で腐るので、鮮度ゲートを持つ:

- **pin**: 冒頭の `<!-- impl-status-pin: SHA -->` が assess 対象の main。
- **checker**: **`python3 scripts/check_impl_status.py`**（read-only・stdlib）が (1) pin vs `origin/main` の staleness、(2) pin 以降で **ER/L3 パイプラインを触った commit 数**（0 なら snapshot はほぼ有効／>0 なら要再確認）、(3) tripwire（`build_provider_request` 等の presence が **pin と `origin/main` で異なれば** flag＝assess 後に flip した claim を検出。re-pin で自己解消）を報告し、drift/stale で exit 1。
- **更新規律**: XER ステージが land した／checker が drift を出したら **該当行 + 図 + pin を更新**する（大きく変われば 9-stage assessment を再実行）。`STATUS.md` と同じ **round 境界 refresh**（[status-maintenance.md](../../.claude/rules/status-maintenance.md)）。checker の CI 配線は governance follow-up（人間が `.github`/pre-commit に）。

## References

- 図解: [implementation-status.html](implementation-status.html)（本 doc の視覚版）/ [mode-x-er-explainer.html](mode-x-er-explainer.html)（全体像）
- 設計正本: [01-architecture-and-flow](01-architecture-and-flow.md) / [02-l3-planning-core](02-l3-planning-core.md) / [03-er-adapter-skeleton](03-er-adapter-skeleton.md) / [04-er-input-modalities-and-stt](04-er-input-modalities-and-stt.md) / [06-unfrozen-contract-resolutions](06-unfrozen-contract-resolutions.md)
- live 手順: [dev/07-mode-x-er-live-e2e-runbook](../dev/07-mode-x-er-live-e2e-runbook.md) / gateway: `deploy/dev/run-er-hermes.sh`(8643) / `deploy/hermes/er-audio-fork/`(8644)
- 実装: `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/{robotics,robotics_planning_core}/` / tests: `tests/unit/test_l3_*` `tests/live/test_xer_*`
