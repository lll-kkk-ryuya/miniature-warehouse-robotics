# Mode X-ER Operator Feedback / 音声リジェクト応答（拒否理由を人へ返す）

作成日: 2026-06-24

> **状態**: 設計提案。本書が定義する **L4 Operator Feedback Box** と `OperatorNotice` は未凍結の内部案であり、本 doc では ROS topic / REST API / config key / `warehouse_interfaces` frozen contract を**凍結しない**（別ノード event の運び方は §5.2 で案A＝専用 `/operator/notice` topic を採用するが、topic 名/型の凍結は別 contract PR）。実装前に offline fixture と契約 PR で確定する。`reason_code` / decision_event の語彙は既存 proposal catalog（`docs/productization/05-decision-observability-and-tooling.md:48-65`）を**消費**するだけで、新語彙を発明しない。

## 結論（先に要点）

1. 人の音声指示がパイプラインのどこか（schema / target / confidence / DAG / policy / battery / emergency / nav / clamp …）で **reject / needs_clarification / emergency_stop** になったとき、「**どの箱（この部分）の・どの理由（この箇所）でおかしいから動作できない**」を**人へ音声で返す**機能を追加する。
2. これは新しい判断ロジックではなく、**operator 向け出力チャネル**である。各 gate は既に人間可読の理由（`decision` / `reason_code` / `reason_detail` / L3 の `message_for_operator`）を**出している**（`docs/productization/05-decision-observability-and-tooling.md:48-69`・`docs/mode-x-er/02-l3-planning-core.md:95`）。欠けているのは**それを音声化して人へ戻す箱**だけ。
3. 置き場所は **L4 Robotics Bridge Super-Box 内の新 sub-box「Operator Feedback Box」**。既存の **Input Context Box（operator→system の入力 sub-box・`docs/productization/01-commercial-box-map.md:13`）の鏡像**（system→operator の出力）として対称に置く。理由とほかの候補を却下した根拠は §2。
4. **安全のため reject 理由の文面は deterministic（テンプレート lookup）で作り、LLM に作文させない**。fallible な model の周りに deterministic な安全 gate と説明可能な reason_code を置く本プロジェクトの思想（`docs/productization/01-commercial-box-map.md:53`・`docs/mode-x-er/04-er-input-modalities-and-stt.md:80`）に合わせる。LLM は**安全に無関係な言い回し整形**にのみ optional で使い、reason_code を ground truth として必ず添える（§4）。
5. まず **Mode X-ER** に組み込む。Mode X-ER は既に **operator voice 入力**と **Hermes Voice/TTS transport** を持つ（`docs/mode-x-er/01-architecture-and-flow.md:23`・`:219`）ので、対称な**音声出力**を足すのが最小増分になる。組み込み方は §5。
6. **喋るのは「operator が音声で出した命令に紐づく失敗・節目」だけ**（`gen_id` 相関 + lifecycle filter）。命令外の自律停止・50ms tick まで喋ると**鳴り続けて使えない**ので黙らせる（web/log のみ）。発話スコープは §5.3。

---

## 1. 位置づけ — なぜ必要か（ギャップ）

Mode X-ER の標準フローは「人の音声 → ER → L3 → L2 → L1/L0」と**下り（指令）方向**で、戻りは `bot odom / scan / battery` の**状態**だけである（`docs/mode-x-er/01-architecture-and-flow.md:85-94`）。

一方、人の指示が実行されない理由は**至る所**で生まれる:

| 層 | reject を出す箱 | 例（`reason_code`） | 根拠 file:line |
|---|---|---|---|
| L4 | Input Context | `missing_image, stale_state, calibration_missing, stt_failed` | `docs/productization/06-oss-reuse-and-box-small-designs.md:98` |
| L4 | Model Adapter | `timeout, provider_error, malformed_response, empty_output` | `docs/productization/06-oss-reuse-and-box-small-designs.md:117` |
| L4 | Fusion (optional) | `target_mismatch, action_mismatch, confidence_gap, needs_operator` | `docs/productization/06-oss-reuse-and-box-small-designs.md:141` |
| L3 | Handoff (seam) | `forbidden_endpoint, low_level_action_present, coordinate_goal_unfrozen` | `docs/productization/06-oss-reuse-and-box-small-designs.md:158` |
| L3 | Validator | `UNKNOWN_ROBOT, UNKNOWN_TARGET, LOW_CONFIDENCE_TARGET, TASK_GRAPH_CYCLE, EMERGENCY_ACTIVE` | `docs/mode-x-er/02-l3-planning-core.md:96` |
| L3 | Visual Resolver | `unresolved`（map 外 / polygon 外 / reprojection 過大） | `docs/mode-x-er/02-l3-planning-core.md:151` |
| L2 | Governance / Policy Gate | `stale_generation, duplicate_command, battery_low, emergency_active, unknown_location` | `docs/productization/05-decision-observability-and-tooling.md:254-259` |
| L2 | Traffic | `route_conflict, no_route, rmf_unavailable` | `docs/productization/06-oss-reuse-and-box-small-designs.md:177` |
| L1 | Navigation | `no_path, recovery_exhausted, localization_unhealthy` | `docs/productization/06-oss-reuse-and-box-small-designs.md:196` |
| L1 | Safety | `emergency`（near_collision / pose_stale） | `docs/productization/05-decision-observability-and-tooling.md:84` |
| L0 | Hardware | `clamped_velocity, nonfinite_cmd, heartbeat_lost` | `docs/productization/06-oss-reuse-and-box-small-designs.md:228` |

これらは**既に**人間可読フィールドを持って emit されている:

- `decision` は固定語彙 `accepted / rejected / warning / needs_clarification / emergency_stop`（`docs/productization/05-decision-observability-and-tooling.md:69`）。
- `reason_detail` は**人間向け補足**（集計軸にしない、と明記。`docs/productization/05-decision-observability-and-tooling.md:71`）。
- L3 Validator の rule result は `code, severity, field_path, message_for_operator, debug_detail, dispatch_effect` を持つ（`docs/mode-x-er/02-l3-planning-core.md:95`）。**`message_for_operator` は人へ返すための文面そのもの**。
- ER output は `operator_clarification_required` を持ち、true なら 0 dispatch（`docs/mode-x-er/01-architecture-and-flow.md:149`・`docs/mode-x-er/03-er-adapter-skeleton.md:71`）。

**つまり「人へ返す中身」は設計上すでに存在し、それを音声で operator に届ける箱だけが無い。** 既存の grep でも operator 向け音声出力 / TTS リジェクト応答の box は不在（Hermes の TTS は transport 候補、キャラLLM の TTS は Mode A 演出用の出力先未決事項であって reject 応答ではない: `docs/architecture/06-implementation-phases.md:206`）。本書はこの 1 箱を定義する。

---

## 2. どの box に入れるか（**最重要**）

### 2.1 決定

**新 sub-box「L4 Operator Feedback Box」を、L4 Robotics Bridge Super-Box の配下に置く。** Input Context Box（operator→system 入力）の**鏡像**（system→operator 出力）。

```
L4 Robotics Bridge Super-Box（親 box）
  ├─ Input Context Box        [sub-box] operator/voice/image/state → model input   ← 既存
  ├─ Model Adapter Box        [sub-box] ER/VLA/STT transport
  ├─ Fusion Box               [sub-box・optional]
  ├─ Operator Feedback Box    [sub-box・optional] reject/clarification → 音声で人へ  ← 本書で新設
  └─ (L3 Handoff)             [seam → L3 配下]
```

> **所有層 ≠ event source 層（重要・誤読しやすい点）**: この箱の**所有/実行の home は L4**（operator I/O ＋ Hermes TTS transport ＝ Input Context の対称）。ただし**消費する reject decision_event は L2/L1/L0 を含む全層から来る**。これは横断 Eval/Observability Box が全 box(L4–L0) を横断 consume するのと同じ**共有 decision_event バス**を読むということ（`run_id/gen_id` で join・`docs/productization/05-decision-observability-and-tooling.md:88`）。layer（RT クラス／event source）と 種別（box/sub-box）は**直交軸**（`docs/productization/01-commercial-box-map.md:73`）なので、「**L4-owned な sub-box**」かつ「**横断 decision_event の consumer**」は両立する。横断 Eval/Observability（`docs/productization/01-commercial-box-map.md:35,79`）との違いは**所有層ではなく責務**——observe-only の集計/KPI/report sink（`docs/productization/05-decision-observability-and-tooling.md:88-106`・`docs/productization/01-commercial-box-map.md:71`）か、リアルタイムで人へ出す一次チャネルか。

### 2.2 種別判定（taxonomy 正本 `docs/productization/01-commercial-box-map.md:42-48` に照らす）

| 問い | 判定 | 根拠 |
|---|---|---|
| 独立した produces/consumes を持つか | **持つ**（consumes: gate の decision_event。produces: `OperatorNotice`（音声出力＋`operator_notice_ref`）＋ 自身の `box=l4_operator_feedback` event） | seam ではない（seam は新 schema を産まない・`01:46`） |
| 親 interface を越えて単独 consume されるか | 描画/transport は Super-Box 内に閉じ単一 consume（operator I/O ＋ TTS）。ただし **event 入力は横断 decision_event バス**（Eval/Obs と共有・`05:88`）から取る＝**所有層は L4・event source 層は横断**（直交軸・`01:73`） | → **L4-owned sub-box（＝横断 consumer）**（`01:45`） |
| 丸ごと省略/縮退できるか | できる（speaker 無し site では log / web sink のみに縮退） | optional sub-box（Fusion と同じ扱い・`01:63`） |

→ **box でも seam でもなく sub-box（L4 が所有・home）。** ただし上表のとおり**event source は横断**（Input Context は Super-Box 下りパイプラインの純粋な内部ステージ `06:89-97` だが、本箱は下りパイプライン上には乗らず、横断 decision_event バスを読み speaker/web へ出す）。「Input Context の鏡像」は**所有・transport の対称性**を指し、event 経路は横断である点を明示する。Model Adapter / Fusion と同じ成熟度区分（**proposal・ws/src に実体なし**）。

### 2.3 なぜ L4 か / ほかの候補を却下する理由

| 候補 | 採否 | 理由（file:line） |
|---|---|---|
| **L4 Robotics Bridge Super-Box 内 sub-box** | ✅**採用** | L4 が operator I/O を所有する。map の入力境界が `[入力] Operator / WMS / API / Voice`（`docs/productization/01-commercial-box-map.md:10`）。TTS は Super-Box の Hermes-managed transport（Plugin Extension `STT/TTS`・`docs/productization/01-commercial-box-map.md:106`・`docs/productization/02-l4-robotics-bridge-box.md:132`）。voice **入力**（Input Context）の対称な voice **出力**は同じ箱に置くのが自然（`docs/productization/06-oss-reuse-and-box-small-designs.md:85-104`）。 |
| Eval / Observability Box（横断） | ❌（同じバス・別責務） | **同じ横断 decision_event バスを consume する兄弟**だが、Eval/Obs は **observe-only の join / KPI / report 集計 sink**（`docs/productization/05-decision-observability-and-tooling.md:88-106`・`docs/productization/01-commercial-box-map.md:71` decision event aggregation）。**リアルタイムに人へ出す一次チャネルは別責務**なので別箱にし、home を L4（operator I/O ＋ TTS）に置く。E-G2「観測 producer と別 node」・観測 sink fail-open（`docs/productization/06-oss-reuse-and-box-small-designs.md:249`・`05:279`）は、**この箱を別 node・fail-open にする**根拠（§6 L4OF-G2）であって、Eval/Obs 却下そのものの根拠ではない。 |
| L3 Planning Core | ❌ | L3 は **command 候補を作る**箱で、実行許可も operator I/O も持たない（`docs/productization/01-commercial-box-map.md:85`）。reject の音声化は planning ではない。 |
| Governance / Policy Gate | ❌ | Governance は **enforcement**（accepted motion だけ通す・`01:86`）。reject 理由を**生む**側であり、人へ**喋る**側ではない。喋らせると enforcement と I/O が密結合する。 |
| ER / LLM に作文させる | ❌（safety 文面） | fallible な model に**安全 reject の理由文を生成**させると hallucination リスク。deterministic gate の思想に反する（§4・`01:53`）。 |

### 2.4 taxonomy 正本（`01`）への登録は follow-up（単一所有を守る）

box taxonomy / Box 一覧の正本は `docs/productization/01-commercial-box-map.md`（所有: productization box-map トラック・現在 `docs/productization-box-map-layers` worktree が編集中）。**本書では分類を確定するに留め、`01` の Box 一覧表への行追加は別 PR（`contract` 調整・owner = box-map トラック）に分ける**（`.claude/rules/parallel-workflow.md` §7.1 共有ファイル単一所有 / §4 契約変更プロトコル）。観測 funnel への `l4_operator_feedback` 追加も同様に Eval/Obs owner と調整する。**この「L4 所有 sub-box・横断 consumer」分類は、§2.1 注の cross-layer event-source 論点（本箱を横断 bus の consumer として Eval/Obs の兄弟に置くか、L4 sub-box の所有のまま置くか）を box-map owner が裁定するまで暫定**とする。

---

## 3. Operator Feedback Box の小設計（`06` テンプレに合わせる）

`docs/productization/06-oss-reuse-and-box-small-designs.md:74-83` の 1-box 1-表に従う。

| 項目 | 設計案 |
|---|---|
| 目的 | gate が出した reject / clarification / emergency の decision を**人間可読・現場言語の通知**にし、音声（第一）＋ web/overlay（併走）で operator に返す。motion は一切持たない。 |
| 再利用する OSS / 標準 tool | **TTS（専用エンジン・LLM ではない）**: Hermes Voice/TTS transport（`transport: hermes`・`docs/mode-x-er/01-architecture-and-flow.md:219`）が **Gemini TTS / OpenAI TTS / Edge / Piper 等をネイティブ proxy**、または direct adapter（Gemini API TTS / Google Cloud TTS）。provider 詳細・出典は §7。**STT 入力との対称**: Whisper（`06:39`）。**テンプレート/locale**: Pydantic で `OperatorNotice` shape 検証（`06:36`）。**web sink**: 既存 `/character/speech` 同型の event→Web 経路（`docs/architecture/22-web-observability.md:36,112`）が precedent。 |
| 自作で残す境界 | `(box, reason_code)` → **現場言語テンプレート**の写像（deterministic）、locale（JA/EN）、優先度（emergency は即時・割り込み）、抑制/まとめ（同一 reason の連投を間引く）、TTS 失敗時の fail-open、**0 dispatch 保証**（音声箱が motion を絶対に出さない）。 |
| 入力 artifact | `decision_event`（`box, stage, decision, reason_code, reason_detail, robot, gen_id, run_id, message_for_operator?`）、`operator_locale`、`sink_profile` |
| 出力 artifact | `operator_notice_ref`（喋った文面 + 音声 ref）、自身の `decision_event`（`box=l4_operator_feedback`） |
| decision event | `box=l4_operator_feedback`、`stage=render / speak`、`reason_code=template_missing, tts_failed, sink_unavailable, message_too_long, locale_missing` |
| fixture | 各 gate の reject fixture（§1 表の代表）→ 期待文面 golden、emergency 割り込み、TTS 失敗（fail-open）、locale 切替、unknown reason_code（fallback 文面） |
| acceptance gate | §6 の L4OF-G0〜G5 |

> **0 actuation の対称性**: Input Context は「model quality に効くが実行許可は持たない」と明記されている（`docs/productization/06-oss-reuse-and-box-small-designs.md:102`）。Operator Feedback も同様に**operator 体験に効くが実行許可を持たない**。両者は L4 の operator I/O ペアとして対称。

> **「入力の理由」と「箱自身の失敗」を混同しない（重要）**: 人向け文へ変換する**入力**は「止まった gate の理由」（`reason_detail` / L3 `message_for_operator`）。一方 `tts_failed` / `template_missing` などの **`box=l4_operator_feedback` の decision_event は箱**自身**の失敗**（喋れなかった・テンプレが無い）であり、**audit と fail-open fallback（web/overlay へ落とし run は止めない）用**。データを LLM に分からせるためのものではない。文面生成も LLM ではなく deterministic テンプレート（§4）で、TTS provider に渡すのは**確定した文字列**だけ（provider は text→audio するだけ）。

### 3.1 再利用性 — mode 非依存の商用 box か（YES）

**Operator Feedback は単一 mode 専用ではなく、mode 非依存で再利用できる商用 box として設計する**（再利用境界は productization に写す・`docs/productization/04-box-storage-and-reuse-guidelines.md:160`）。

- **mode 非依存**: consume するのは generic な `decision_event`（全 gate 共通形・§1）。Mode A/B/C/X-ER/X-ER-VLA いずれでも reject を emit する pipeline ならそのまま接続できる。Input Context（operator 入力）の鏡像＝operator 出力を担う再利用ペア（`docs/productization/01-commercial-box-map.md:13`）。
- **保管単位（04 の box manifest）**: `status: proposal`（実装ゼロの sub-box＝`docs/productization/04-box-storage-and-reuse-guidelines.md:61`）。design=本書 / interfaces=`decision_event` consume・`OperatorNotice` produce / fixtures=各 gate reject golden（§5.5）/ acceptance-gates=L4OF-G0〜G5（§6）/ decision-events=`box=l4_operator_feedback` / audit-eval=自身の decision_event。
- **差し替え点（案件差分は site profile に寄せる・`docs/productization/04-box-storage-and-reuse-guidelines.md:80`）＝コア（filter→template→sink）は再利用し profile だけ swap**:

| 差し替えるもの | site profile に置く |
|---|---|
| 現場言語テンプレート・locale | `templates_<locale>`（safety 文面は固定） |
| TTS provider / voice | `tts.provider` / `tts.<p>.voice`（Gemini TTS / OpenAI / Edge / Piper・§7） |
| sink 構成 | speaker / web / OBS の有無（fail-open） |
| 発話スコープ・抑制 policy | speakable decision 集合・milestone・suppression（§5.3） |
| 別ノード transport | 案A `/operator/notice`（topic 名・型は doc03 契約） |

- **再利用の接続例**: ① Mode A 演出（キャラLLM の停止理由を音声化）② Mode C（Open-RMF reject を音声化）③ 別現場・別 fleet（templates＋TTS＋known locations を site profile で差替）。いずれもコアを再利用し profile だけ替える。
- **正準登録**: 商用 Box 一覧（`docs/productization/01-commercial-box-map.md`）と L4 詳細図（`docs/productization/layer-l4-detail.html`）への正準登録は box-map / diagrams owner と調整（§2.4）。L4 詳細図には **④ Operator Feedback（出力 sub-box・proposal）** として追記済み。本書は mode 非依存の再利用境界を定義する。

---

## 4. 文面は deterministic、LLM 作文は optional（安全設計）

reject 理由の音声文面は **2 段**にする。

1. **deterministic 層（必須・既定）**: `(box, reason_code)` をキーに**固定テンプレート**を引く。安全・拒否の文面は**ここで完結**し、model を通さない。L3 reject では gate が出した `message_for_operator`（`docs/mode-x-er/02-l3-planning-core.md:95`）をそのまま使える。
   - 例: `("l3_validator","UNKNOWN_TARGET")` → 「**bot1 に出した『赤い箱』が地図上の登録位置に見つからないので、動かせません。**」（"この部分=L3 Validator・この箇所=target 参照"）。
2. **LLM 整形層（任意・安全に無関係な範囲のみ）**: トーンや言い回しを会話的にしたい場合のみ ER/LLM で整形してよい。ただし **reason_code と deterministic 文面を ground truth として必ず添付**し、model 出力が reason を**変えていない**ことを検査してから喋る（変えていれば deterministic 文面に fallback）。安全 reject（emergency / battery / collision）は**整形層を通さない**。

根拠: 「fallible な model 群の周りに deterministic な安全 gate と説明可能な reason_code を置く」のが本プロジェクトの中核思想（`docs/productization/01-commercial-box-map.md:53`・`docs/mode-x-er/04-er-input-modalities-and-stt.md:80`）。reject の**説明可能性**を model の作文に委ねると、その思想を operator 出口で崩す。

---

## 5. Mode X-ER への組み込み方（実装の差し込み点）

### 5.1 data flow（戻りに operator notice を足す）

既存の戻りは状態のみ（`docs/mode-x-er/01-architecture-and-flow.md:85-94`）。ここに**拒否/要確認の戻り**を 1 本足す:

```
（下り・既存）
operator voice → Input Context → ER Adapter → RoboticsPlan draft
  → L3(Validator/Resolver/Executor/Compiler) → L2(action_map/MCP/Policy Gate)
  → X-lite Nav2 Bridge / X-rmf → L1 Nav2 → L0 firmware

（戻り・本書で追加）
任意の gate が decision ∈ {rejected, needs_clarification, emergency_stop}
  → decision_event（reason_code + reason_detail / message_for_operator）
  → ★ L4 Operator Feedback Box（reason_code → 現場言語テンプレート, deterministic）
  → Hermes Voice/TTS transport（or direct）
  → speaker（人へ「この部分のこの箇所がおかしいので動作できません」）
  ＋ web console / OBS overlay（併走 sink）
```

各 gate は既に decision_event を emit している（§1）。Operator Feedback Box は**それを subscribe するだけ**で、各箱の内部を import しない（疎結合・`.claude/rules/parallel-workflow.md` §2.1）。emergency は**割り込み**で最優先に喋る。

### 5.2 起動方式（"叩き方"）— 止まった瞬間に emit、L4 が即 render

> 「止まった箇所から直接 L4 の音声説明を叩く方が速い」という直感は**おおむね正しいが、1点だけ安全上の例外**がある。両者を分けて設計する。

> **"emit" とは**: ある node が**小さな event レコード（decision_event）を channel に publish して即座に戻る**こと。誰かが consume するのを待たない fire-and-forget（ROS なら `publisher.publish(msg)`、in-process なら callback への非同期 dispatch）。「関数を呼んで戻り値を待つ（＝同期 call）」の対義。**gate は emit するだけで TTS を待たない**のが安全の肝。

**不変条件（最優先・安全）**: **gate は reject を decision_event として emit したら即座に処理を続け、TTS の完了を絶対に待たない（non-blocking）。** 理由 — Safety / Hardware は上位 model に依存せず、上位が止まっても下位が独立に止める（`docs/productization/01-commercial-box-map.md:89`・`docs/productization/05-decision-observability-and-tooling.md:304`）。enforcement と観測 producer を同一 node にせず観測は fail-open（`docs/productization/06-oss-reuse-and-box-small-designs.md:249`・`docs/productization/05-decision-observability-and-tooling.md:279`）。L1 は Hard-RT（50ms tick）、L0 は MCU 即時なので、外部 API の TTS（数百ms〜秒）を gate の critical path に乗せない。

**起点の層で "叩き方" を分ける**（どちらも「止まった瞬間に L4 render が走る」点は同じ。違いは transport だけ）:

| reject の起点 | プロセス位置 | 叩き方 | 理由 |
|---|---|---|---|
| L4 Input Context / Model Adapter / Fusion ・ **L3 Validator / Resolver**（Bridge プロセス内） | L4 と同一プロセス | **直接 in-process の非同期 dispatch**（≒ 関数呼び出し・transport ゼロ） | ここは「止まった箇所から直接 L4 render を叩く」＝直感どおり。hub を経由しない |
| L2 Governance / Traffic ・ L1 Navigation / Safety ・ L0 Hardware | 別ノード/別プロセス（Jetson Nav2 / ESP32） | **non-blocking な event**（ROS topic / State Cache フラグ）を emit → L4 が購読して即 render | 物理的に別マシンで「直接関数呼び出し」が不可。かつ Safety → L4 の**同期呼び出しは上位依存＝禁止**（`docs/productization/01-commercial-box-map.md:89`） |

> **「経由＝遅い」は誤解**: decision_event は audit / Eval funnel のために**どのみち emit される**（`docs/productization/05-decision-observability-and-tooling.md:88`・§1）。Operator Feedback はその**同じ emission を購読するだけ**で hop を新設しない。人が聞くまでの遅延 = detect + emit + transport + template + **TTS 合成** + 再生で、支配項は **TTS 合成（数百ms〜秒）**。emit/transport は in-process で μs、LAN topic でも数 ms ＝ TTS に対して無視できる。**直接同期呼び出しは速くならないどころか、その TTS 秒を gate の critical path に乗せて危険**（L1 50ms tick / L0 MCU が TTS を待つ）。よって「**直接だが非同期**（emit→即 render）」が最速かつ安全で、in-process reject ではそれが**そのまま直接 render**になる。emergency / collision の停止動作は L0/L1 が独立に実施済みで、音声は事後通知（gate 側は emit のみ）。

**別ノード event の運び方（A 専用 topic / B 既存経路に相乗り）— 影響比較**:

| 観点 | A. 専用 ROS topic（例 `/operator/notice`） | B. 既存経路に相乗り（decision_event stream / Eval bus） |
|---|---|---|
| 契約コスト | **新 frozen 契約**（doc03 topic ＋ `contract` PR ＋ 全トラック予告・`.claude/rules/parallel-workflow.md` §4）。additive で既存購読者は無視可（§7.2） | **新契約ゼロ**（最小ガバナンス・最速）。decision_event は監査用に**どのみち emit される**（`docs/productization/05-decision-observability-and-tooling.md:88`）のを購読するだけ |
| 配信意味論 | **event stream＝lossless・順序保証**。deadlock / 到着など離散イベントを取りこぼさない | decision_event の **event stream なら可**。ただし **State Cache フラグ（latest-value）は transition を取りこぼす**＝右折/到着の節目に不向き |
| 結合 | 1 本の明確な operator チャネル。各箱は topic schema にだけ依存 | 観測（observe-only・fail-open・`docs/productization/05-decision-observability-and-tooling.md:279`）経路に相乗り＝**負荷時に event drop すると音声も静かに落ちる**。粒度/latency が観測都合に引っ張られる |
| テスト/再現 | rosbag で record/replay 容易 | 既存 Eval fixture に相乗り |
| 向く場面 | 別ノード reject が多い / lossless 必須 / 2 件目 site で安定契約が要る | MVP・2台 PoC・大半が L4-local reject |

> **採用方針（A で確定・将来拡張性優先）**: **専用 topic `/operator/notice`（案A）** を採る。別ノード reject はこの専用チャネルへ publish し、★ が購読する。**lossless・順序保証**で deadlock / 到着など離散イベントを取りこぼさず、別 site / 別 mode への拡張も 1 チャネルで安定する。**代償は新 frozen 契約**＝doc03 トピック契約 ＋ `contract` PR ＋ 全トラック予告（`.claude/rules/parallel-workflow.md` §4）。ただし **additive**（新トピックは既存購読者が無視できる・§7.2）なので破壊的でない。**L4-local reject は同一プロセスゆえ topic 不要＝直接 in-process render**（topic は別ノード分だけ）。**State Cache の boolean フラグは transition 取りこぼしで不可**（節目を喋れない）。不変条件（emit only・非ブロッキング・上位非依存）は A でも不変。

### 5.3 発話スコープ（何を喋り、何を黙るか）— operator 命令に紐づく lifecycle だけ

**問題（鳴り続けない工夫）**: L2〜L0 は operator の音声命令と**無関係にも**止まりうる（自律 collision_monitor stop、battery 自律帰還、routine recovery、相手 bot への一時 yield、50ms 安全 tick…）。これを全部喋ると**鳴り続けて使えない**。

**原則**: 喋るのは「**operator が音声で出した命令（task）の lifecycle に紐づく notable event**」だけ。それ以外（命令外の自律停止・高頻度 tick・無関係 reject）は**黙る（web/log のみ）**。

**どう紐づけるか（attribution）**: 命令は L3 で `Command`(gen_id=N) に compile され、action_map が `gen_id=N` を注入して dispatch する（既存・B-3）。その命令が下流 L2/L1/L0 で reject/fail/complete すると、対応する decision_event は**同じ `gen_id` / `run_id` / `robot` を持つ**（funnel は L4→L0 を `run_id`/`gen_id` で join・`docs/productization/05-decision-observability-and-tooling.md:88`・`docs/productization/06-oss-reuse-and-box-small-designs.md:249` E-G0）。Operator Feedback はこの**相関キーで filter**する:

```
speak ⟺  event.gen_id ∈ {現在 live な operator 命令}
        ∧ event.decision ∈ {rejected, needs_clarification, emergency_stop,（任意で arrived/completed）}
        ∧ event が lifecycle transition（高頻度 sample/tick ではない）
```

- **喋る例（operator 命令に起因・実アクションの節目）**: deadlock で解消不能 / no_path / recovery_exhausted / target 未解決 / battery 不足で命令不可 / 到着（completed）/ 右折など task の milestone。
- **黙る例**: 命令外の自律 collision stop、相手への一時 yield（命令は継続中）、50ms tick、Eval 集計用 routine sample。

これは既存の記録粒度方針とも整合する（Safety は通常 tick=metrics、状態変化/emergency だけ event・`docs/productization/05-decision-observability-and-tooling.md:118`）。lifecycle は L3 Task Graph Executor の `pending→ready→running→succeeded/failed/cancelled`（`docs/mode-x-er/02-l3-planning-core.md:178-182`）に対応づく。

**「では私の命令が L2〜L0 で止まったら？」の処理フロー**:

```
1. voice 命令 → L4 → L3 が Command(gen_id=N) に compile → action_map が gen_id=N で dispatch
2. L2/L1/L0 でその命令(gen_id=N)が reject/fail
   → 止めた箱が decision_event(gen_id=N, box, reason_code, reason_detail) を emit
     （非ブロッキング・監査用にどのみち出る・gate は待たない）
3. ★ Operator Feedback が購読 → gen_id=N は live 命令か? terminal/notable か? → yes なら
   → (box,reason_code)→現場語テンプレ→TTS「bot1 の搬送が <理由> で止まりました」
4. 命令と無関係な自律停止（gen_id 無 or autonomous 印 or 高頻度 tick）→ filter で除外 → 無音（web/log のみ）
```

filter で落とした event は**喋らないが audit には残す**（`box=l4_operator_feedback` の `decision=suppressed` 相当）。これにより「なぜ黙ったか」も後から説明できる。

### 5.4 コード上の保管場所（productization の module 案に追加）

`docs/productization/02-l4-robotics-bridge-box.md:240-266` の `robotics_bridge/` module 案に、operator 出力を 1 ディレクトリ足す形が自然（**現時点ではコード追加しない・proposal**）:

```text
robotics_bridge/
  context/                 # Input Context（実装あり: situation.py）
  adapters/                # ER/VLA/STT（proposal）
  feedback/                # ★ Operator Feedback Box（proposal・本書）
    notice_builder.py      #   (box,reason_code) → OperatorNotice（deterministic template）
    templates_ja.py        #   現場言語テンプレート（locale 別・safety 文面は固定）
    sinks/
      tts_sink.py          #   Hermes Voice/TTS or direct
      web_sink.py          #   /character/speech 同型 event（既存 web 経路 precedent）
  tracing/                 # Bridge-owned trace root（実装あり: tracing.py）
  orchestration/           # Super-Box cycle / 0 dispatch（実装あり: scheduler.py）
```

transport は box interface 裏の `transport: hermes|direct` 選択であって box を割らない（`docs/productization/01-commercial-box-map.md:52`）。speaker 無しの site は `tts_sink` を外し web_sink だけにする（optional 縮退）。

### 5.5 実装フェーズ案（Mode X-ER の XER フェーズに追補）

既存 XER0〜XER7（`docs/mode-x-er/README.md:85-92`）に、reject 観測が出揃う **XER2 (Validator)** 以降で並走させる:

| Phase | 内容 | 完了条件 |
|---|---|---|
| XER-OF0 | docs 設計（本書） | box 分類・data flow・テンプレ方針・gate を docs 化 |
| XER-OF1 | offline notice builder | §1 各 gate の reject fixture → 期待文面 golden（deterministic・model 不要） |
| XER-OF2 | 0 dispatch / fail-open test | reject fixture を流して **motion 0 件**、TTS 失敗で run 継続を unit 固定（R-26 と同枠の安全 unit） |
| XER-OF2.5 | attribution / scope filter | `gen_id`/`run_id` 相関 ＋ lifecycle filter で**命令外の自律停止・高頻度 tick を黙らせる**（§5.3）。命令起因 reject だけ通す fixture を golden 化 |
| XER-OF3 | TTS sink 配線 | Hermes Voice/TTS または direct で実際に喋る。emergency 割り込み・抑制を検証 |
| XER-OF4 | web 併走 sink | `/character/speech` 同型 event を web console / overlay へ（観測 fail-open） |

---

## 6. Acceptance Gates

| Gate | 内容 |
|---|---|
| L4OF-G0 | §1 の各 gate `reason_code` が locale テンプレートに写像できる（既知 code で `template_missing` を出さない。未知 code は安全 fallback 文面） |
| L4OF-G1 | **0 actuation**: Operator Feedback Box は motion command / tool dispatch を一切 emit しない（reject fixture を流して assert） |
| L4OF-G2 | **fail-open**: TTS / sink 失敗は log に残して run を止めない（観測 sink fail-open 原則・`docs/productization/05-decision-observability-and-tooling.md:279`） |
| L4OF-G3 | 安全 reject（emergency / battery / collision）の文面は **deterministic テンプレート固定**で、LLM 整形層を通らない（§4） |
| L4OF-G4 | 喋る文面が「**どの box（この部分）**・**どの reason_code / field（この箇所）**で動けないか」を含む（operator が原因箇所を特定できる） |
| L4OF-G5 | **発話スコープ**: 命令外の自律停止・高頻度 tick・無関係 reject は**喋らない**（`gen_id`/`run_id` 相関＋lifecycle filter・§5.3）。命令起因の terminal/notable event だけ発話する fixture で固定（鳴り続けないことを assert） |

---

## 7. 未凍結事項 / TODO

- `OperatorNotice` schema（`box, reason_code, locale, text, severity, source_decision_ref`）を product contract に昇格するか。`warehouse_interfaces` には**まだ追加しない**。
- `decision_event` の transport は §5.2 で方針確定（**L4-local reject = 直接 in-process render / 別ノード reject = non-blocking event・gate は TTS を待たない**）。**採用は A（専用 `/operator/notice` topic）＝将来拡張性・lossless 優先**。**next step = doc03 トピック契約 ＋ `contract` PR**（additive・既存購読者は無視可・§7.2）。**State Cache の boolean フラグは transition 取りこぼしのため不可**。**契約ドラフト（型 `std_msgs/String`(JSON)・QoS RELIABLE/KEEP_LAST/VOLATILE・payload・pub/sub）は §8**、残る未凍結は §8.8。
- **TTS provider（speaker の中身）は LLM ではなく専用 TTS エンジン**（text→audio）。経路は2つ: **(a) Hermes 経由**（`transport: hermes`）— Hermes が TTS エンジンを proxy し、**Google Gemini TTS / OpenAI TTS / Edge / Piper(ローカル) など 10 provider をネイティブ対応**（config `tts.provider` / `tts.<provider>.voice`）。**(b) direct adapter**（`transport: direct`）— Gemini API TTS（`gemini-2.5-flash-preview-tts` / `gemini-2.5-pro-preview-tts`・text→audio・日本語対応）や Google Cloud TTS（Chirp 3 HD・SSML）。**Mode X-ER は司令塔が Gemini Robotics-ER ゆえ TTS も Gemini 系で揃えるのが自然**（Hermes ネイティブなので `transport:hermes` でそのまま使える）。preview model のため実装時に再確認。出典（確認 2026-06-24）: `https://ai.google.dev/gemini-api/docs/speech-generation`・`https://hermes-agent.nousresearch.com/docs/user-guide/features/tts`。
- **発話スコープの確定値**: speakable な `decision` 集合（`rejected`/`needs_clarification`/`emergency_stop` に加え `arrived`/`completed` を喋るか）、milestone（右折・到着）を喋る範囲、同一 reason の抑制間隔は現場依存（§5.3）。実装時に config 化する。
- taxonomy 正本 `docs/productization/01-commercial-box-map.md` の Box 一覧 / 観測 funnel への登録（owner = box-map / Eval-Obs トラックと調整・§2.4）。
- 現場言語テンプレート・locale・抑制 policy・emergency 割り込み policy の値（現場依存。`docs/productization/05-decision-observability-and-tooling.md:165` の「既存 tool に含まれない自作領域」）。
- Hermes Voice/TTS が日本語・低 latency・割り込みを満たすかの offline probe（満たさなければ direct TTS adapter）。STT 入力（`04`）と同じく**実証前は併走**で持つ。
- ER/LLM 整形層（§4 layer 2）を実装するか、deterministic のみで足りるかの判断。

---

## 8. doc03 トピック契約ドラフト（`/operator/notice`）— contract PR 用

> **状態**: 契約 PR 用ドラフト。§5.2 で採用した**案A（専用 topic）**の実体。doc03（トピック契約カタログ）には **topic 名・型・一行責務**のみを足し、**payload schema / QoS / publisher / subscriber の正本は本節**に置く（doc03 の慣習: `docs/architecture/03-software-architecture.md:112`「doc03 は topic 名・型・一行責務のみ」）。**doc03 への行追加は別 contract PR**（owner = skeleton/governance・`.claude/rules/parallel-workflow.md` §4）で行い、本書では凍結しない。

### 8.1 doc03 へ追加する行（提案）

doc03 §ROS 2 トピック設計「Jetson 内部」表（`docs/architecture/03-software-architecture.md:92-110`）に1行追加:

```
| `/operator/notice` | `std_msgs/String`（JSON） | 別ノード(L2/L1/L0)の operator 起因 reject/clarification/emergency 通知。L4 Operator Feedback Box が購読し音声化（契約正本: mode-x-er/05 §8。Phase 4 で `.msg` 化, doc16 §3） |
```

### 8.2 役割（何を運ぶか）

- 別ノード（L2/L1/L0＝別プロセス: Jetson Nav2 / MCP / ESP32）で **operator 命令が止まったとき**、その reject/clarification/emergency を **L4 Operator Feedback Box**（`warehouse_llm_bridge` の feedback sub-box）へ運ぶ非ブロッキング event チャネル。
- **L4-local reject（Input Context / Model Adapter / Fusion / L3）は本 topic を使わない**＝同一プロセスで直接 in-process render（§5.2）。本 topic は**別ノード分だけ**。
- gate は本 topic へ **publish して即継続**（TTS を待たない・非ブロッキング・上位非依存。§5.2 不変条件）。

### 8.3 型

- `std_msgs/String`（JSON 文字列）。doc16 §3 の凍結方針に従い **Phase 4 まで JSON 文字列**で運用（`.msg` 再ビルド回避）。Phase 4 で `.msg` 化を検討。

### 8.4 payload JSON schema（`operator_notice.v0`）

既存の **decision_event 形**（`docs/productization/05-decision-observability-and-tooling.md:48-65`）を**そのまま消費**する（新語彙を発明しない・§0）。operator-relevant な reject のみが流れる:

```json
{
  "schema_version": "operator_notice.v0",
  "timestamp": "2026-06-24T12:00:00.000Z",
  "run_id": "run_x_er_...",
  "gen_id": 42,
  "robot": "bot1",
  "box": "navigation",
  "stage": "result",
  "decision": "rejected",
  "reason_code": "no_path",
  "reason_detail": "no valid path to shelf_1",
  "message_for_operator": "（optional・L3 が出す場合）"
}
```

- `decision` は固定語彙のうち **`rejected` / `needs_clarification` / `emergency_stop`** のみ（`accepted` / `warning` は本 topic に流さない＝喋らない・`docs/productization/05-decision-observability-and-tooling.md:69`）。**v0 は reject 級 event のみ**＝`arrived`/`completed` 等の milestone は decision 固定語彙外で本 v0 契約の対象外（扱いは §5.3/§7 の発話スコープ確定値・§8.8 参照）。
- `reason_code` は box ごとの catalog から（自由文にしない・同 `:70`）。`reason_detail` は人間向け補足（集計軸でない・同 `:71`）。`message_for_operator` は L3 が出す確定文面（optional・`docs/mode-x-er/02-l3-planning-core.md:95`）。
- `gen_id` / `run_id` / `robot` は **attribution の鍵**（box が「自分の命令か・節目か」を filter・§5.3）。大きな raw data は埋めず参照（同 `:72`）。

### 8.5 QoS

| QoS policy | 値 | 根拠 |
|---|---|---|
| Reliability | **RELIABLE** | 案A 採用の理由＝**lossless**（reject の取りこぼし不可・State Cache フラグを却下した理由・§5.2） |
| History | **KEEP_LAST, depth=20**（暫定） | バースト緩衝。単一購読者（box）が捌ける前提。depth は実装時に調整 |
| Durability | **VOLATILE** | event は瞬間値。**late-join / box 再起動後に古い reject を再生しない**（古い拒否を後から喋らない）。`transient_local`（latch）は不可 |
| Liveliness | AUTOMATIC（default） | 特別要件なし |

> RELIABLE ＋ KEEP_LAST で **live session 内は lossless・順序保証**（節目を取りこぼさない）。VOLATILE で **再起動跨ぎの stale 再生を防ぐ**。これが「State Cache の boolean フラグ（latest-value・transition 取りこぼし）」を不可とした理由（§5.2）。

### 8.6 publisher / subscriber

**publisher（別ノードの gate・operator 起因 reject を出す側）**:

| 層 | node（候補） | 出す reason_code 例 |
|---|---|---|
| L2 | `warehouse_mcp_server`（Governance / Policy Gate） | `battery_low` / `emergency_active` / `stale_generation` / `duplicate_command` / `unknown_location` |
| L2 | `warehouse_traffic` / `warehouse_rmf_adapter` | `route_conflict` / `no_route` / `rmf_unavailable` |
| L1 | `warehouse_nav2_bridge`（Navigation） | `no_path` / `recovery_exhausted` / `localization_unhealthy` |
| L1 | `warehouse_safety`（Emergency Guardian） | `emergency`（near_collision / pose_stale）※既存 `/emergency/event` と棲み分け＝§8.7 |
| L0 | firmware / micro-ROS Agent | `nonfinite_cmd` / `clamped_velocity` / `heartbeat_lost`（Phase 1+・bridge 経由） |

**subscriber**:

- **L4 Operator Feedback Box**（`warehouse_llm_bridge` の feedback sub-box）— 主購読者（単一）。filter→template→TTS。**emergency は §8.7 のとおり既存 `/emergency/event` も直接購読**（本 topic の emergency 行は候補・MVP は /emergency/event 優先）。
- （任意）**Eval / Observability**（audit・observe-only）、**web_bridge**（doc22・observe-only）。

### 8.7 既存トピックとの関係（重複回避）

- **`/emergency/event`**（既存・`docs/architecture/03-software-architecture.md:98`）: Emergency Guardian の estop 構造化イベント。emergency reject は**既にここに流れている**。**MVP 推奨: emergency は box が既存 `/emergency/event` を直接購読、それ以外の reject を `/operator/notice` で受ける**（emergency を二重 publish させない）。確定は配線時。
- **`/state_cache/snapshot`**（`docs/architecture/03-software-architecture.md:103`）: 最新値 state（latest-value）。本 topic は **event**（transition）で別物（§5.2 で State Cache フラグを却下した理由）。

### 8.8 残る未凍結（contract PR で確定）

- topic 名の最終確定（本 topic は box の**入力** decision_event を運ぶ。box の**出力** `OperatorNotice`（§7）とは別物。名前で誤読しないか・`/operator/reject_event` 等の候補）。
- KEEP_LAST depth 値・QoS 微調整。
- どの node が実際に publish するか（§8.6 候補のうち MVP 配線）・L0 Hardware の bridge 方法。
- emergency の `/emergency/event` 相乗りか `/operator/notice` 二重化か（§8.7）。
- `schema_version` 値の凍結・Phase 4 `.msg` 化の型定義。
- **milestone 連動**: §7 の発話スコープ確定値で `arrived`/`completed`（到着・右折等）を喋ると決めた場合、それらは現 `decision` 固定語彙（`docs/productization/05-decision-observability-and-tooling.md:69`）に**無い**ため v0 payload（reject 級 decision のみ）では運べない＝**milestone 用 event 語彙の追加 or 別チャネル**を要する。v0 は reject 級に scope する。

### 8.9 contract PR チェックリスト

- [ ] **additive 確認**: 新 topic は既存購読者が無視できる（破壊的でない・§7.2）。
- [ ] doc03 §トピック設計「Jetson 内部」表に §8.1 の行を追加（`contract` ラベル）。
- [ ] 依存トラック（safety-state / nav-traffic / wo / web）へ予告し合意（§4）。
- [ ] 本 §8 を payload / QoS / pub-sub の正本としてリンク（doc03 は一行のみ）。
- [ ] `OperatorNotice`（box の出力・§7）と本 topic（box の入力 decision_event）の別物性を明記。

---

## 9. 参照

- 正本（mode-x-er）: `docs/mode-x-er/01-architecture-and-flow.md`（data flow・戻り `:85-94`・Voice/TTS `:219`）/ `docs/mode-x-er/02-l3-planning-core.md`（`message_for_operator` `:95`・validation code `:96`・unresolved `:151`）/ `docs/mode-x-er/04-er-input-modalities-and-stt.md`（STT 任意・deterministic 思想 `:80`）/ `docs/mode-x-er/README.md`（標準フロー `:42-63`）
- box taxonomy 正本: `docs/productization/01-commercial-box-map.md`（種別 `:42-48`・operator 入力境界 `:10`・Input Context sub-box `:13`・Plugin TTS `:106`・安全境界 `:53`）
- L4 box / Hermes transport: `docs/productization/02-l4-robotics-bridge-box.md`（責務 `:56-87`・Plugin TTS `:132`・module 案 `:240-266`）
- decision / reject 集計: `docs/productization/05-decision-observability-and-tooling.md`（decision_event `:48-69`・reason_detail `:71`・dispatch gate `:254-259`・fail-open `:279`）
- box 小設計テンプレ / Input Context 対称: `docs/productization/06-oss-reuse-and-box-small-designs.md`（テンプレ `:74-83`・Input Context `:85-104`・E-G2 `:249`）
- web sink precedent: `docs/architecture/22-web-observability.md`（`/character/speech` `:36,112`）
- 図解（本書の補足）: [`operator-feedback-flow.html`](operator-feedback-flow.html)
</invoke>
