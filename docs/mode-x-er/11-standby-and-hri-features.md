# standby モードと HRI 機能群 — persona「はっちゃん」・ウェイクワード/拍手・エア描画・ついてこい

作成日: 2026-08-21

> **Status**: 設計提案（2026-08-21 オペレーター決定の書き起こし）。本書は ROS topic / REST API / config key / `warehouse_interfaces` frozen contract を**追加しない**。確定した決定と、未確定の OQ を分けて記録するだけである。

## 0. 本書の位置づけと作法

[ADR-0009](../adr/0009-m1-room-scale-operation.md) により M1 単騎フェーズは**実際の部屋**を走り、デモの形は「倉庫設定を薄め、ジェスチャ召喚を主役にする」と確定した（[adr/0009-m1-room-scale-operation.md:21](../adr/0009-m1-room-scale-operation.md) Decision 2）。その主役デモそのものの設計正本は [09-hand-raise-summon](09-hand-raise-summon.md) である。

**本書が扱うのは、その主役デモの前後にある HRI（human-robot interaction）機能群**である——「呼ぶ**前**」（standby から起き上がる入口と、起きたことを人に返す合図）と、「呼んだ**後**」（persona の音声返答・追従・記号による命令）。09 の召喚／指差しの判定ロジックそのものは本書の射程外であり、09 を正本とする。

作法:

| 語 | 意味 |
|---|---|
| **確定** | 2026-08-21 のオペレーター決定。日付を必ず併記する |
| **OQ** | 未確定。§7 の表に採番して集約する |
| **layer 注記** | コード・コンポーネントに言及するときは L0–L4 の帰属を併記する（[.claude/rules/layer-annotation.md](../../.claude/rules/layer-annotation.md)） |

**発明しない**: しきい値・トピック名・型・API 名は、docs か一次情報で裏取りできるものだけを書く。裏取りできないものは OQ に落とす（[.claude/rules/docs-first.md](../../.claude/rules/docs-first.md)）。

---

## 1. persona「はっちゃん」

### 1-1. 名前（確定・2026-08-21）

ロボットの呼称を **8号「はっちゃん」**（ドラゴンボールの人造人間8号に由来）とする。ウェイクワード（§2-3）・音声返答（§1-2）・standby 合図（§3）はすべてこの persona 名を使う。

### 1-2. 音声返答は「事前生成 wav を `aplay` で再生」する（確定・2026-08-21）

**毎回 TTS を生成しない。** 発話は事前に合成した wav ファイル群を持ち、OS 標準再生（`aplay`）で鳴らす。

根拠（ハードウェア側は実物確認済み）:

- M1 には **AI 音声モジュール基板とスピーカーが同梱**されている（[shared/02-hardware-design.md:573](../shared/02-hardware-design.md)。Standard/Superior 共通同梱・公式ページの "optional" 表記は誤解を招くが unboxing リストと一致）。
- **スピーカーは OS 標準再生（`aplay`）で任意 wav を再生できる**（[shared/02-hardware-design.md:576](../shared/02-hardware-design.md)）。同行が「到着発話（[09 §11](09-hand-raise-summon.md)）に流用可」と既に述べており、本書はその流用先を persona 返答へ広げるだけである。
- 基板シリアル側（CH340）の**中国語ウェイクワード "你好小雅" は本プロジェクトでは未使用**（同 :576）。§2-3 の日本語ウェイクワードはホスト側で持つ。

事前生成を採る理由:

| 観点 | 事前生成 wav + `aplay` | 都度 TTS 合成 |
|---|---|---|
| レイテンシ | ファイル再生のみ | **数百ms〜秒**（[05-operator-feedback-and-voice-response.md:182](05-operator-feedback-and-voice-response.md) が「支配項は TTS 合成」と明記） |
| 課金 | ゼロ | provider 課金が発生 |
| 決定論性 | 完全（同じ音が必ず鳴る） | provider・model 版に従属 |
| 適用条件 | **発話文面が有限集合であること** | 任意文面を喋れる |

**doc05 との関係（矛盾ではない）**: [05](05-operator-feedback-and-voice-response.md) の L4 Operator Feedback Box は TTS provider（Hermes Voice/TTS または direct）を前提に書かれている（[05:281](05-operator-feedback-and-voice-response.md)）。しかし doc05 自身が「**文面生成も LLM ではなく deterministic テンプレート**で、TTS provider に渡すのは確定した文字列だけ」と定めている（[05:109](05-operator-feedback-and-voice-response.md)）。**文面が決定論テンプレートで有限集合なら、合成そのものを事前化するのは同じ原則の極限**である。したがって本書の事前生成 wav は doc05 の設計方針の下位互換な実装選択であって、doc05 を否定しない。

- ただし **doc05 の reject 応答（`(box, reason_code)` → 現場語テンプレート）まで全部を事前生成 wav で賄えるかは別問題**（reason_code の組合せが有限でも文面に可変部が入りうる）。本書が事前生成に倒すのは **persona 返答と standby 合図（§3）という、文面が完全固定の小集合**に限る。doc05 の box 全体の合成方式は doc05 所有者判断。

### 1-3. キャラ資産は Mode A キャラLLM から流用する（確定・2026-08-21）

persona の人格設定・発話の器は、既存の Mode A キャラLLM 設計（[architecture/14-character-llm-negotiation.md](../architecture/14-character-llm-negotiation.md)）から流用する。

流用するもの / しないもの:

| 項目 | 扱い | 根拠 |
|---|---|---|
| キャラ人格（性格パラメータ・口調） | **流用する** | [14:144-156](../architecture/14-character-llm-negotiation.md) システムプロンプト方針（`性格: {personality}`） |
| **0 actuation の書き込み権限境界** | **流用する（必須）** | [14:136](../architecture/14-character-llm-negotiation.md)「キャラLLMは `/character/speech` と `/negotiation/proposal` のみ publish。Nav2/MCP/cmd_vel には触れない」 |
| 既存トピック `/character/speech`（`std_msgs/String` JSON・**L4**） | 流用候補 | [14:203](../architecture/14-character-llm-negotiation.md) トピック表 |
| bot 間交渉プロトコル（バトンパス・8ターン上限・稟議制） | **流用しない** | M1 は**単騎**（[ADR-0006](../adr/0006-single-bot-first.md)）＝相手 bot が存在しない。ただし「合意→司令官承認→実行」の稟議制の**形**は §4-2-2 の ✓ 記号（実行承認）で再利用する |

> **layer 注記**: persona 音声返答層は **L4**（operator I/O・出力）であり、**0 actuation**（motion を一切持たない）。これは doc05 の L4 Operator Feedback Box の不変条件（[05:257](05-operator-feedback-and-voice-response.md) XER-OF2「reject fixture を流して motion 0 件」）と同型である。

---

## 2. standby モード（デフォルト）

### 2-1. boot → standby がデフォルト（確定・2026-08-21）

起動直後の既定状態を **standby** とする。**ジェスチャ・音声コマンドは standby→active 遷移後にのみ armed（有効）**になる。

理由は 2 つある。

1. **誤発火抑制**。ジェスチャの false positive 率は**未実測**であり、[10 §11 G-m](10-room-scale-safety-review.md) が「ジェスチャ誤検出率の実測と hold window しきい値の確定」を部屋運用開始の前提条件（未実施・実装も未着手）として挙げている。常時 armed は、この未実測のリスクを常時抱えることを意味する。standby は「人が明示的に起こすまで一切のジェスチャ解釈をしない」という**構造的な誤発火ゼロ区間**を作る。
2. **計算資源の節約**。Jetson Orin Nano は CPU と GPU が **8GB を共有する**ユニファイドメモリであり（[architecture/06-implementation-phases.md:100](../architecture/06-implementation-phases.md)）、[23 §7 S1](../architecture/23-perception-and-localization.md) の合格ラインは「残 RAM ≥ 500MB」（[23:216](../architecture/23-perception-and-localization.md)）。骨格 NN は既に「S1 の第 3 の競合者」と名指しされている（[09:143](09-hand-raise-summon.md)）。standby 中に骨格 NN・カメラ・Nav2 を載せない構成が取れれば、この予算問題が緩む。

### 2-2. 切替の 2 軸を混同しない（重要）

「standby にする」「モードを切り替える」という言葉は、**性質のまったく違う 2 つの操作**を指しうる。本書はこれを明示的に分ける。

| 軸 | 何が変わるか | 粒度 | 切替時間 | 本書での呼び方 |
|---|---|---|---|---|
| **(a) 起動プロファイル** | **launch 構成＝何をメモリに載せるか**（プロセス集合そのもの） | ノード群 | **数秒〜十数秒**（起動・終了を伴う） | **プロファイル**（§5） |
| **(b) ランタイム状態** | 同一プロファイル内の **standby⇄active** 状態機械（armed フラグ） | フラグ | **ms オーダー** | **standby / active**（本節） |

この 2 つを混ぜると、「standby にすればメモリが空くはずだ」（実際は (a) を切らないと空かない）と「プロファイルを切れば即応するはずだ」（実際は (a) は十数秒かかる）という**逆向きの誤解が同時に生まれる**。§3 の合図は (b) の遷移に付き、§5 の演出（切替中の発話つなぎ）は (a) の所要時間に付く。

### 2-3. 入口は二つ（確定・2026-08-21）

standby→active の遷移トリガは 2 つあり、**どちらも同一の遷移に入る**（別状態を作らない）。

| # | 入口 | 検出方式 | 主な用途 |
|---|---|---|---|
| ① | **ウェイクワード「はっちゃん」** | 小型ウェイクワードエンジン（下表） | 正面・近距離・自然な呼びかけ |
| ② | **拍手** | **onset / energy の決定論検出**（NN 不要） | **カメラ死角・遠距離**（声が届きにくい / 顔が見えない状況） |

②を併置する理由は「①の代替」ではなく「①が効かない物理条件（距離・向き・騒音）を埋める」ことである。拍手は広帯域の急峻な立ち上がりを持つため、閾値と時間窓の決定論検出で足り、**追加の NN を 8GB 予算に持ち込まない**。

**ウェイクワードエンジン候補（確定した選定方針・2026-08-21）**:

| エンジン | ライセンス | 実行 | 規模 | 判定 |
|---|---|---|---|---|
| **openWakeWord** | Apache-2.0 | CPU | 数十MB級 | **候補** |
| **Vosk 小型日本語モデル** | Apache-2.0 | CPU | 数十MB級 | **候補** |
| Porcupine | **商用ライセンス** | — | — | **不採用** |

Porcupine の不採用はライセンス理由であり、これは [09:13](09-hand-raise-summon.md) が YOLO-pose を AGPL-3.0 ゆえ fallback へ降格した（MediaPipe = Apache-2.0 を第1候補にした）のと同じ規律である。

- **カスタム語「はっちゃん」の認識精度は未知**＝ spike で測る（**OQ-H2**）。日本語の 4 モーラ・撥音入りの語が、汎用小型モデルでどの程度の false accept / false reject を示すかは、モデルと語の組で実測しないと分からない。

**音声入力源**: [shared/02-hardware-design.md:575](../shared/02-hardware-design.md) が確定したとおり、同梱モジュールは**通常の USB オーディオデバイスとして見え、`arecord` で生 wav が録れる**（ドライバ追加なし）。ウェイクワード / 拍手リスナーはこのストリームを読む。Yahboom の ASR 層（zh/en のみ）は使わない。

> **layer 注記**: ウェイクワード / 拍手リスナーは **L4 知覚・publish-only・0 actuation**。[09 §4-1](09-hand-raise-summon.md) の `gesture_detector`（L4 知覚・publish-only）と同格に置き、**司令ノードから分離**する。トピック名・型は本書では決めない（doc03 契約カタログ追加を伴うため。§7 参照）。

---

## 3. standby 入り確認合図（三層）

**問題**: 人が「はっちゃん」と呼んだり拍手したりしたとき、**受け付けたかどうかが分からない**と、人は 2 回 3 回と繰り返す。繰り返しは誤発火の温床でもある。

**参考にする確立パターン**: スマートスピーカー製品群が収束した合図は **チャイム → 光 → 音声**の多層である（即時性・継続表示・意味伝達をそれぞれ別チャネルで担う）。本書もこの三層を採る。

| 層 | 合図 | 実装 | 何を担うか |
|---|---|---|---|
| **①** | **チャイム（< 0.5s）** | 事前生成 wav を `aplay`（[02:576](../shared/02-hardware-design.md)） | **即時性**——「今この瞬間に受け付けた」 |
| **②** | **音声「はい、はっちゃんです」** | 事前生成 wav を**複数パターン持ちランダム再生** | **キャラ性**——毎回同一文だと機械的で、動画の絵として持たない |
| **③** | **LED** | Rosmaster 拡張ボードのライトバー制御 API（`set_colorful_lamps` 系） | **継続状態の表示**——「今 active である」を鳴り続けずに示す |

### 3-1. ③LED は搭載有無が未確認（OQ-H1）

Rosmaster 系の拡張ボードにライトバー制御 API（`set_colorful_lamps` 系）が存在することは知られているが、**M1 実機に実際にライトバーが載っているかは本 repo の docs に記述が無い**（`docs/` および `ws/` を grep して不在を確認した）。**開梱確認で決める**（**OQ-H1**）。

これは [shared/02-hardware-design.md:578](../shared/02-hardware-design.md) の「要実機確認（6点）」と同じクラスの確認項目であり、実機は 2026-08-18 に到着済みである（同 :532 注記）。搭載が無ければ ③ は落とし、①②の二層で運用する（三層は望ましいが必須ではない）。

### 3-2. ④「前後の身震い」は保留（採用見送りではなく設計待ち・OQ-H3）

合図の 4 つ目として「微小な前後動でロボットが身震いする」案が出た。**却下ではなく、経路設計が無いために保留**する。

理由を正確に書く:

1. **これは actuation である**。①②③はすべて 0 actuation（音とライト）だが、④は車輪が回る。
2. **既存の plan 経路（L3→L2→Nav2）に合わない**。[09 INV-2](09-hand-raise-summon.md):57 が定める経路は `to_robotics_plan_draft` → L3 Validator → Visual Resolver → Task Graph Executor → Command Compiler → action_map → MCP → **L2 Policy Gate** → Nav2 Bridge REST → Nav2 である。この経路が運ぶのは「**どの known location へ行くか**」であり、「その場で 5cm 前後に揺れる」を表す語彙が `KNOWN_LOCATIONS`（9 キー・`schemas.py:159` の語彙 gate）にも `Command` にも存在しない。
3. **かといって `cmd_vel` へ直接書いてはならない**。`twist_mux` の入力は **`emergency`（priority 100）と `nav2`（priority 10）の 2 つに凍結**されている（[ws/src/warehouse_bringup/config/twist_mux.yaml:41-49](../../ws/src/warehouse_bringup/config/twist_mux.yaml)。同ファイル :5-8 が「Values are the FROZEN safety contract」と明記）。3 本目の入力を足すのは contract 変更であり、合図のために踏み込む重さではない。

→ したがって ④ が要求しているのは、**「表現動作（expressive motion）プリミティブ」という新しい経路の設計**である。目的地を持たない・短時間で終わる・安全境界の内側で完結する motion を、どの層が発行しどの gate を通すのか。これは **Phase 2 / OQ-H3** とする。

---

## 4. 採用機能ロードマップ（tier）

2026-08-21 の決定を、着手可能性で 3 tier に分ける。

| tier | 位置づけ | 内容 |
|---|---|---|
| **A** | **採用・即着手圏** | persona 音声返答（§1-2）／ standby ＋ ウェイクワード（§2-3 ①）／ 拍手入口（§2-3 ②） |
| **B** | **採用・後続スライス** | ついてこいモード（§4-2-1）／ エア描画コマンド（§4-2-2） |
| **C** | **Phase 2 裁定待ち・未採用** | ボディミラー操縦（§4-3-1）／ 両手 X ポーズ緊急停止（§4-3-2） |

**tier A は全て 0 actuation**（音・状態フラグのみ）である。したがって [10 §11](10-room-scale-safety-review.md) の部屋運用開始 gate G-a〜G-m（全て未充足）が閉じる前でも着手できる。**tier B / C は走行を伴う**ため、これらの gate に従属する（§6）。

### 4-1. tier A（採用・即着手圏）

§1-2（persona 音声返答）・§2（standby ＋ 2 入口）・§3（合図①②、③は OQ-H1 次第）。追加の凍結契約を要さない範囲で構成する。

### 4-2. tier B（採用・後続スライス）

#### 4-2-1. ついてこいモード

**決定（2026-08-21）**: **Nav2 の dynamic object following（goal updater）パターン**を採る。**目標が連続更新されるだけで plan 経路は生存する**——すなわち INV-2（L3→L2 の各段を 1 ステップも迂回しない）は保たれる、というのがオペレーターの判断である。

エア描画（§4-2-2）やボディミラー（§4-3-1）と違い、ロボットに送られるのは**依然として「目標地点」**であって速度指令ではない。この点で、後述するボディミラーの非両立性とは性質が異なる。

**ただし本書は次の 2 点を閉じていない（正直に明記する）**:

1. **[09 R-3 柱1](09-hand-raise-summon.md):261 との緊張**。部屋スケールの安全論証は多層防御の第 1 の柱として「到達点は `KNOWN_LOCATIONS` の 9 キーのみ・**人を追尾する経路が生まれるわけではない**」を挙げている。**ついてこいモードは、まさに人を追尾する経路である。** 柱1 の文言をそのまま維持したまま追従機能を足すことはできない。R-3 の安全論証は**再評価が要る**（[10](10-room-scale-safety-review.md) の安全レビューと同枠・オペレーターゲート）。
2. **goal 更新が L2 Policy Gate を何回通るか**。goal updater が **Nav2 の Behavior Tree 内**で目標を差し替えるなら、更新後の goal は L2 Policy Gate を**通らない**（1 回目の dispatch だけが gate を通る）。**L3 側で再 dispatch する**なら毎回通る。どちらを採るかで INV-2 の成否が実際に変わる。これは実装方式の選択ではなく**安全境界の選択**である（**OQ-H8**）。

> Nav2 upstream の該当 BT ノード名・挙動の一次情報は、**実装時に Nav2 公式 docs で裏取りする**。本書ではパターン名以上の API を発明しない。

#### 4-2-2. エア描画コマンド

**決定（2026-08-21）**: 空中に手で描いた記号を命令として受け取る。認識は **$1 recognizer（単ストローク記号認識・決定論アルゴリズム）** を手首軌跡に適用する。**NN の追加は不要**——[09 P1](09-hand-raise-summon.md):13 の骨格 NN（第1候補 MediaPipe Pose Landmarker）が既に出している**手首キーポイントの時系列を再利用**するだけである。8GB 予算（§2-1）に新しい常駐モデルを足さない。

**初期語彙 4 種（確定・2026-08-21）**:

| 記号 | 意味 | 変換先 |
|---|---|---|
| **○** | パトロール開始 | 既知タスクテンプレート |
| **横一直線** | 2 点間往復 | 既知タスクテンプレート |
| **×** | 現行タスクのキャンセル | 既知タスクテンプレート |
| **✓** | **保留プランの実行承認** | 既知タスクテンプレート（§1-3 の**稟議制と連結**——人が承認の一票を身振りで入れる） |

**経路（INV-1 / INV-2 が生存する）**: 記号 → **既知タスクテンプレート** → 通常の plan draft → L3 → L2。テンプレートが吐くのは `TaskNode.target = <known location 名>` であって座標ではないため、[09 INV-1](09-hand-raise-summon.md):25（幾何は plan draft の外・座標は監査用 `evidence` のみ）はそのまま成立する。**手首軌跡そのものは draft に載せない**（載せた瞬間に INV-1 が壊れる）。

**語彙設計の原則**: **互いに形が離れた少数から始める**。○ と ✓ と × と横棒は、単ストロークの特徴量空間で十分に離れている。語彙を増やすほど相互誤認が増えるため、初期は 4 種で固定する。

- **数字 1〜9 → `KNOWN_LOCATIONS` の waypoint 指定**は**ストレッチ扱い**（**OQ-H4**）。9 キーと 1:1 で対応する魅力はあるが、手書き数字は相互に似た形（1/7、3/8、5/6）を含み、**誤認率が上がる**。語彙の最終確定と併せて裁定する。

> **layer 注記**: 軌跡取得と記号認識は **L4 知覚**（publish-only・0 actuation）、記号→タスクテンプレート変換は **L3**（plan draft 生成・実行権限なし）、実行許可は従来どおり **L2** Policy Gate。これは [09 §4](09-hand-raise-summon.md) の (iii) bridge-local 決定論変換と同じ形——**ER をバイパスする決定論ローカル producer は「もう一つの provider」にすぎない**（[09:55](09-hand-raise-summon.md)）。

### 4-3. tier C（Phase 2 裁定待ち・未採用）

#### 4-3-1. ボディミラー操縦（**ADR による裁定が要る**・OQ-H5）

人の体の動きにロボットが追従する「鏡」操縦。**本フェーズでは採用しない。** 理由は好みではなく**構造的な非両立**である。

1. **plan 経路と原理的に非両立**。ボディミラーは連続 pose → velocity のサーボであり、**毎フレーム速度指令**を出す。これは「目標地点を 1 個渡す」経路ではなく、[09 INV-2](09-hand-raise-summon.md):57 の L3→L2 チェーンを通しようがない（Policy Gate を 30fps で叩く設計にはなっていない）。
2. **`twist_mux` 凍結契約に teleop 入力を足すことになる**。現行入力は `emergency`(prio 100) / `nav2`(prio 10) の 2 本のみで、これは FROZEN safety contract と明記されている（[ws/src/warehouse_bringup/config/twist_mux.yaml:5-8, :41-49](../../ws/src/warehouse_bringup/config/twist_mux.yaml) / 設計側は [architecture/15-mcp-platform.md:389-395](../architecture/15-mcp-platform.md)）。3 本目の追加は **`contract` ラベル付き PR ＋ 依存トラック合意**（[.claude/rules/parallel-workflow.md](../../.claude/rules/parallel-workflow.md) §4）の重さになる。
3. **dead-man switch が必須**。手が画角から消えたら**即ゼロ速度**にする機構が無ければ、検出欠落＝最後の速度指令のラッチ＝暴走になる。

**エア描画との違い（この対比が採否を分ける）**:

| | **エア描画コマンド**（tier B・採用） | **ボディミラー操縦**（tier C・未採用） |
|---|---|---|
| 人は何をしているか | **離散記号を認識させて 1 タスクを発行する**（＝**命令を書く**） | **体が連続ジョイスティックになる**（＝**直接操縦する**） |
| ロボットが受け取るもの | known location を含む plan draft | 毎フレームの速度ベクトル |
| 通る gate | L3 Validator → L2 Policy Gate → Nav2 → L1 → L0'（全通過） | L2 を迂回して `cmd_vel` へ直行 |
| 主な失敗モード | 誤認識 → **誤ったタスクが発行される**（gate が拾える・reject できる） | 追従遅延 / 検出欠落 → **意図しない motion が継続**（拾う gate が無い） |

→ hard-to-reverse（安全境界の変更）∧ surprising（「ジェスチャ機能の一種」に見えて実は別クラス）∧ real-trade-off（絵の強さ vs 安全網の迂回）の 3 条件が揃うため、**採否は ADR で裁定する**（[.claude/rules/docs-authoring-and-glossary.md](../../.claude/rules/docs-authoring-and-glossary.md) の ADR 3 条件）。

- **[ADR-0010](../adr/0010-raise-speed-cap-to-platform-max.md) との複合リスク**: 速度上限はミニチュア安全値 0.3 m/s からプラットフォーム上限（実機 car_type により 1.0 または 0.7 m/s）へ再定義された。**直接操縦 × 上限引き上げ**の組合せは、危険度が線形以上に上がる。ボディミラーの裁定は、運用速度が S-SPEED で確定した後に行うのが順序として正しい。

#### 4-3-2. 両手 X ポーズ緊急停止（未裁定）

両手を胸前で交差させる姿勢を緊急停止トリガとし、**既存の prio-100 emergency 経路**（`twist_mux` の `emergency` 入力）へ入れる案。**未裁定。**

⚠️ **重大な設計上の緊張を先に書いておく**: [09:143](09-hand-raise-summon.md) は「**骨格 NN の人物位置を反射安全の入力に使うことは明示的に不採用**（GPU を反射経路に持ち込む＝[23 §1 P1](../architecture/23-perception-and-localization.md) 違反）」と定め、さらに「**この誘惑は必ず出るので書いておく**」と注記している。両手 X ポーズ e-stop はまさにその誘惑である。骨格 NN が落ちたら止まらない e-stop は e-stop ではない。

→ 検討するとしても「**既存の物理 E-stop（[10 S-8](10-room-scale-safety-review.md)）を置換せず、その上に足す補助トリガ**」という前提でのみ成立しうる。単独の停止手段として設計してはならない。

---

## 5. 起動プロファイル（8GB 制約・doc23 S1 と接続）

§2-2 の軸 (a)。**何をメモリに載せるか**の構成を 4 つに切る。

| プロファイル | 構成 | 備考 |
|---|---|---|
| **P0 standby（デフォルト）** | `m1_driver` ＋ ウェイク/拍手リスナー ＋ 合図（音・LED） | **常駐極小。全プロファイルの共通土台** |
| **P1 召喚 ＋ 速度セレクタ** | P0 ＋ カメラ ＋ Pose/Hands ＋ Nav2/AMCL ＋ bridge | **主役デモ** |
| **P2 ついてこい** | P0 ＋ カメラ ＋ Pose ＋ follow goal updater ＋ Nav2 | tier B（§4-2-1） |
| **P3 エア描画** | P0 ＋ カメラ ＋ Hands 軌跡 ＋ Nav2 | tier B（§4-2-2） |

### 5-1. どの組合せが 8GB に同居できるかは実測で決める（発明しない）

本書は**メモリ数値を一切書かない**。判定は [23 §7 S1](../architecture/23-perception-and-localization.md) の測定ラダーに従う:

- **合格ライン**: 「残 RAM ≥ 500MB **かつ** Open-RMF（Mode C）分の余地が残ること。`tegrastats` 10分負荷で throttle 無し」（[23:216](../architecture/23-perception-and-localization.md)）。
- **測定順**: `idle → nvblox 単体 → +Nav2 → +Hermes/MCP`（各段の**差分**を記録）（[23:217](../architecture/23-perception-and-localization.md)）。[23:279](../architecture/23-perception-and-localization.md) が既に `+gesture NN` を 1 段追加している。
- **本書からの申し送り**: 上記ラダーに **`+wake/clap listener`**（P0 の常駐分）と **`+Hands 軌跡`**（P3 分）の段を足して測ることを、知覚トラック（doc23 所有）へ申し送る。**本書は doc23 を編集しない**（単一所有・[.claude/rules/parallel-workflow.md](../../.claude/rules/parallel-workflow.md) §7.1）。
- なお [23 OQ-23](../architecture/23-perception-and-localization.md):752 が「**部屋体積での TSDF/ESDF メモリ再試算**」を未決として挙げており、S1 の残 RAM 判定そのものがまだ動きうる点に注意する。

### 5-2. プロファイル切替は launch 構成の再起動である（確定・2026-08-21）

- 切替の実体は **launch 構成の再起動**であり、**数秒〜十数秒かかる**（§2-2 の軸 (a)）。
- **音声コマンドで切り替えられる**（例:「はっちゃん、ついてこいモード」）。
- **切替中の十数秒は発話でつなぐ**——「ついてこいモードに切り替えます、少々お待ちください」といった事前生成 wav を鳴らす。**これは言い訳ではなく演出として成立する**（人が待つ理由を理解できる沈黙は不快でない）。動画の絵としても、ロボットが応答してから動き出すまでの間が埋まる。
- **切替の実装機構（launch manager）は未定**（**OQ-H6**）。ROS 2 のライフサイクルノード／launch の動的起動・停止／複数 launch のスーパーバイザ、いずれを採るかは決めていない。**本書では機構を発明しない。**

---

## 6. 安全・不変条件との関係

### 6-1. 本書が壊さないもの

| 不変条件 | 本書での扱い |
|---|---|
| **INV-1**（幾何は plan draft の外・[09:25](09-hand-raise-summon.md)） | エア描画の手首軌跡は draft に載せない（§4-2-2） |
| **INV-2**（L3→L2 を 1 ステップも迂回しない・[09:57](09-hand-raise-summon.md)） | tier A は 0 actuation・tier B のエア描画は全通過。**ついてこいの goal 更新のみ OQ-H8** |
| **`twist_mux` 2 入力の凍結**（[twist_mux.yaml:41-49](../../ws/src/warehouse_bringup/config/twist_mux.yaml)） | tier A/B は触れない。tier C ボディミラーが触るため未採用（§4-3-1） |
| **`KNOWN_LOCATIONS` 9 キーの語彙 gate**（`schemas.py:159`） | エア描画テンプレートの出力先は 9 キー内。数字ストレッチも 9 キーを超えない（OQ-H4） |
| **反射経路に GPU/NN を入れない**（[09:143](09-hand-raise-summon.md) / [23 §1 P1](../architecture/23-perception-and-localization.md)） | 両手 X ポーズ e-stop がこれに抵触しうるため未裁定（§4-3-2） |

### 6-2. 本書が触れるもの（＝安全レビュー対象）

- **ついてこいモード** → [09 R-3 柱1](09-hand-raise-summon.md):261 の安全論証の再評価（§4-2-1 ①）。
- **ボディミラー操縦** → `twist_mux` 凍結契約 ＋ L2 迂回（§4-3-1）。**ADR 裁定待ち。**
- **両手 X ポーズ e-stop** → 反射経路の GPU 依存（§4-3-2）。

### 6-3. doc10 の部屋運用開始 gate との関係

[10 §11](10-room-scale-safety-review.md) の G-a〜G-m は**全て未充足**であり、これは本書の機能群にもそのまま掛かる。特に:

- **G-m**（ジェスチャ誤検出率の実測と hold window しきい値の確定・未実施）は、**standby の存在理由の一つ**（§2-1 ①）であると同時に、**standby だけでは代替できない**。armed 中の誤検出率は依然として測る必要がある。本書は §7 に **OQ-H7**（ウェイクワード・拍手の誤検出率実測）を追加し、G-m と同じクラスの実測項目として並べる。
- **G-b**（C-3 collision_monitor 改訂）は tier B/C の走行機能すべての前提である。現行 `radius: 0.09` は M1 内接 0.1157 未満で車体内部でしか発火しない（[10 §11 G-b](10-room-scale-safety-review.md) 出所欄）。
- **本書は運転許可を与えない。** [10](10-room-scale-safety-review.md) と同じく、最終受け入れはオペレーターゲートである。

---

## 7. OPEN QUESTIONS

> **⚠️ 採番 scoping**: 本表の **`OQ-H*`** は**本書独自の採番**であり、[09 §14](09-hand-raise-summon.md) の `OQ-N` および [23 §8](../architecture/23-perception-and-localization.md) の `OQ-N` とは**別物**である（[09 R-5](09-hand-raise-summon.md):273 が同種の採番衝突を注記しており、本書は最初から接頭辞で分ける）。

| # | 未決事項 | 決め方 | 優先度 |
|---|---|---|---|
| **OQ-H1** | **LED（ライトバー）の M1 実機搭載有無** — `set_colorful_lamps` 系 API の存在は知られるが、実機に載っているかは repo docs に記述なし（grep 済）。無ければ §3 の合図は①②の二層で運用 | **開梱確認**（[02:578](../shared/02-hardware-design.md) の「要実機確認」と同クラス） | 高（§3 の設計に直結） |
| **OQ-H2** | **ウェイクワード「はっちゃん」の認識精度** — openWakeWord / Vosk 小型日本語モデルでカスタム語がどの程度の false accept / false reject を示すか | **spike**（実機マイク・実環境騒音下） | 高（tier A の成立性） |
| **OQ-H3** | **「身震い」＝表現動作（expressive motion）プリミティブの経路設計** — 目的地を持たない短時間 motion をどの層が発行しどの gate を通すか。`cmd_vel` 直書きは `twist_mux` 契約違反ゆえ不可（§3-2） | **Phase 2 設計**（安全レビュー対象） | 中（合図は①②③で成立するため急がない） |
| **OQ-H4** | **エア描画語彙の最終確定 ＋ 数字ストレッチの採否** — 初期 4 種（○/横棒/×/✓）で固定するか拡張するか。数字 1〜9 は相互に似た形を含み誤認率が上がる | **実測**（記号ごとの誤認率）→ オペレーター裁定 | 中（tier B 着手時） |
| **OQ-H5** | **ボディミラー操縦の採否裁定** — `twist_mux` 3 本目入力 ＋ L2 迂回 ＋ dead-man switch。ADR-0010 の速度上限引き上げとの複合リスクあり | **ADR**（hard-to-reverse ∧ surprising ∧ real-trade-off の 3 条件を満たす）。S-SPEED で運用速度が確定した後 | 中（未採用のまま進める） |
| **OQ-H6** | **プロファイル切替機構（launch manager）** — ライフサイクルノード / launch の動的起動停止 / スーパーバイザのいずれか。本書では発明しない | 実装設計（所有トラック調整） | 中（§5-2） |
| **OQ-H7** | **ウェイクワード・拍手の誤検出率実測** — armed 前の入口の false positive。[10 G-m](10-room-scale-safety-review.md)（ジェスチャ誤検出率）と同クラスの実測項目 | **PHASE-1-GATE**（実機・実環境） | 高（standby の効果を測る唯一の手段） |
| **OQ-H8** | **【本書由来・追加】ついてこいモードの goal 更新が L2 Policy Gate を通るか** — Nav2 BT 内で更新すれば通らない / L3 側で再 dispatch すれば通る。**実装方式ではなく安全境界の選択**。併せて [09 R-3 柱1](09-hand-raise-summon.md):261「人を追尾する経路が生まれるわけではない」の**再評価**が要る | **安全レビュー ＋ オペレーターゲート**（[10](10-room-scale-safety-review.md) と同枠） | **高**（tier B の前提・既存安全論証に触れる） |

---

## 8. References（双方向）

**上位の決定（ADR）**:

- [adr/0009-m1-room-scale-operation.md](../adr/0009-m1-room-scale-operation.md) — 部屋スケール運用・**ジェスチャ召喚を主役に**（Decision 2 :21）。本書の HRI 機能群はこの主役デモの周辺装置である
- [adr/0007-no-overhead-camera-gesture-via-onboard-nn.md](../adr/0007-no-overhead-camera-gesture-via-onboard-nn.md) — 搭載 HP60C ＋ ローカル骨格 NN。エア描画が再利用する手首キーポイントの出所
- [adr/0010-raise-speed-cap-to-platform-max.md](../adr/0010-raise-speed-cap-to-platform-max.md) — 速度上限のプラットフォーム上限化。§4-3-1 のボディミラー裁定の順序制約（S-SPEED 後）
- [adr/0006-single-bot-first.md](../adr/0006-single-bot-first.md) — 単騎構成。§1-3 で bot 間交渉プロトコルを流用しない根拠

**同ディレクトリの正本**:

- [09-hand-raise-summon.md](09-hand-raise-summon.md) — **ジェスチャ司令の正本**。INV-1(:25) / INV-2(:57) / 反射経路に NN を入れない(:143) / R-3 安全論証(:255-267) / 部屋での再検証(:211-)。**本書は 09 の判定ロジックを再定義しない**
  - §5 の P1 プロファイル名「召喚 ＋ 速度セレクタ」が指すのは 09 の **【2026-08-21 追補④】ジェスチャ③ 速度セレクタ（右手指カウント3帯）**（同日の並行レーンが執筆）。**本書執筆時点で当該追補は同一ラウンドの未コミット変更ゆえ、行番号ではなく見出し文字列で指す**（land 後に file:line で re-pin すること）。速度帯の値そのものは [ADR-0010](../adr/0010-raise-speed-cap-to-platform-max.md) の運用値 ＋ S-SPEED 実測に従属し、**本書は速度値を持たない**
- [10-room-scale-safety-review.md](10-room-scale-safety-review.md) — 部屋運用開始 gate G-a〜G-m（§11・全て未充足）。§6-3 で本書の機能群との関係を整理
- [04-er-input-modalities-and-stt.md](04-er-input-modalities-and-stt.md) — ER の音声直入力（§2-3 の音声入力源と対）。ウェイクワードで起きた**後**の高情報量指示は ER 経路へ渡す（[09 §12](09-hand-raise-summon.md) の 2 段構想）
- [05-operator-feedback-and-voice-response.md](05-operator-feedback-and-voice-response.md) — L4 Operator Feedback Box。決定論テンプレート原則(:109)・TTS が支配項(:182)・発話スコープ(:196)・0 dispatch(:257)。§1-2 の事前生成 wav はこの設計方針の下位互換な実装選択

**ハードウェア・知覚・キャラ**:

- [shared/02-hardware-design.md](../shared/02-hardware-design.md) — **V-5 AI large model voice module**(:571-578)。同梱実物(:573) / 生 wav が録れる(:575) / **`aplay` で任意 wav 再生可**(:576) / 要実機確認 6 点(:578)
- [architecture/23-perception-and-localization.md](../architecture/23-perception-and-localization.md) — **S1 測定ラダー**（合格ライン :216 / 測定順 :217 / `+gesture NN` 追加 :279 / 部屋体積でのメモリ再試算 OQ-23 :752）
- [architecture/06-implementation-phases.md](../architecture/06-implementation-phases.md) — Jetson 8GB ユニファイドメモリ(:100)
- [architecture/14-character-llm-negotiation.md](../architecture/14-character-llm-negotiation.md) — **キャラLLM 資産の流用元**。0 actuation 境界(:136) / システムプロンプト方針(:144-156) / `/character/speech`(:203) / 稟議制フロー(:57-63)
- [architecture/15-mcp-platform.md](../architecture/15-mcp-platform.md) — `twist_mux` 優先度契約(:389-395)
- [ws/src/warehouse_bringup/config/twist_mux.yaml](../../ws/src/warehouse_bringup/config/twist_mux.yaml) — **凍結された 2 入力の実体**（FROZEN 明記 :5-8 / `emergency` prio 100・`nav2` prio 10 :41-49）

**用語**: [GLOSSARY.md §11](../GLOSSARY.md) — スタンバイモード / ウェイクワード / はっちゃん(persona) / エア描画コマンド / ついてこいモード の正準定義

**索引（backlink）**: [mode-x-er/README.md](README.md) 末尾「standby と HRI 機能群 (index)」 / [docs/README.md](../README.md) の `mode-x-er/` マップ表
