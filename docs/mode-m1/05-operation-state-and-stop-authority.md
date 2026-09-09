# 05 — 運転状態と停止権限の担当分離（operation state & stop authority）

> **Status**: 設計確定（**2026-09-07 オペレーター採用の設計戦略の書き起こし**。3 レーン検証で既存裁定・実コードと突合済）。実装は §8 の順で行い、**ROS topic 名・型・周期・しきい値は本書では決めない**（doc03 契約カタログへの additive 追加は実装スライスで＝§9 OQ-OP1/OP2）。
> **layer 注記**（[.claude/rules/layer-annotation.md](../../.claude/rules/layer-annotation.md)）: 本書が扱うのは **L2**（実行許可 = Policy Gate）・**L1**（Emergency Guardian・twist_mux）・**L0'**（m1_driver 内クランプ＋停止上乗せ）・**L4**（運転モード発信元 = publish-only・0 actuation）。**teleop は velocity producer であり、`warehouse_teleop` は正準表が単一 layer 帰属の対象外とする package**（[productization/01:195](../productization/01-commercial-box-map.md)）＝本書では layer を断定しない。速度経路に新ノードは足さない（§1）。
> **本書が再定義しないもの**: standby⇄active（armed）の 2 軸は [mode-x-er/11 §2-2](../mode-x-er/11-standby-and-hri-features.md) が正本。本書は**第 3 の軸（運転モード）と停止理由の集合**だけを新設し、doc11 の軸と直交させる（§2）。

## 0. 位置づけ

停止・再開まわりの設計は従来 5 箇所に分散していた（doc11 の standby・doc12 の Guardian・mode-m1/02 §3 の W-1〜W-4・ADR-0004 の L2 restrict-only・ADR-0012 の最小安全方針）。本書は **「誰の速度指令を採用するか」（調停）と「そもそも走行してよいか」（停止権限）を別の問いとして分離**し、既存の各担当を拡張する形で確定する。**新しい許可判断ノードは作らない**（第 2 の許可判断点の新設は [productization/11 の L2 定義](../productization/11-l2-contract-governance-traffic-box.md)と衝突するため）。

## 1. 担当分離（確定・2026-09-07）

同じ許可を二重に判断するのではなく、**異なる対象**を各担当に固定する:

| 担当 | layer | 判断する対象 | 既存実体 |
|---|---|---|---|
| **L2 Policy Gate** | L2 | **新しい自律タスクを実行してよいか**（per-dispatch・restrict-only・合成は AND） | `policy_gate.py`（[ADR-0004](../adr/0004-l2-restrict-only-policy-profile.md)） |
| **teleop（発生源ゲート）** | velocity producer（単一帰属対象外 = [productization/01:195](../productization/01-commercial-box-map.md)） | **今のモード・deadman・/joy 鮮度で手動指令を出してよいか**（発生源で自己判定） | `warehouse_teleop`（§6） |
| **Emergency Guardian** | L1 | **停止条件が成立しているか**。成立中は prio100 へゼロ Twist を level 送出＋goal cancel | `emergency_guardian.py`（[doc12:181](../architecture/12-infrastructure-common.md)） |
| **m1_driver** | L0' | **届いた速度が有効か**（clamp 必経・W-1 鮮度）＋**停止上乗せ**（§4・新設） | `driver_core.py` |
| **運転モード発信元** | L4 | 現在の運転モード（§2 軸(c)）を publish する。**許可判断は持たない** | 新設（実装形 = OQ-OP6） |

**確定裁定 — twist_mux 手前の「許可チェック」relay は不採用**。理由は 2 つ検証済み: (a) twist_mux の lock は「lock priority より厳密に低い入力を全遮断」する閾値カットであり、「AUTO 状態で teleop だけ遮断して nav2 を通す」は**原理的に表現不能**（一次情報: ros-teleop/twist_mux `humble` ブランチ 4.3.0 の `src/twist_mux.cpp` `getLockPriority`/`hasPriority` と `include/twist_mux/topic_handle.hpp` `isMasked` = `getPriority() < lock_priority`・参照日 2026-08-31）。(b) 速度経路上の relay ノードは L2-G8（L2 の出口は position goal のみ・[productization/11:214](../productization/11-l2-contract-governance-traffic-box.md)）と [ADR-0012](../adr/0012-speed-band-no-l2-best-effort.md) の確定先例（velocity path に L2 を載せる案の却下）に抵触する。**代わりに「発生源で止める」**: teleop の publish 自粛＋L2 の dispatch 拒否＋Guardian の goal cancel の組合せで同じ状態表を実現する。

**新規タスクの拒否だけでは走行中のタスクは止まらない**。停止時は必ず ①L2 で新規拒否 ②実行中 goal の cancel ③ゼロ指令の維持 を組み合わせる（③の主担当は Guardian・最終床は driver W-1）。

## 2. 状態軸の整理 — 6 状態機械は採用しない（確定・2026-09-07）

| 軸 | 内容 | 正本 |
|---|---|---|
| (a) 起動プロファイル | launch 構成（P0-P3・切替は十数秒） | [mode-x-er/11 §5](../mode-x-er/11-standby-and-hri-features.md) |
| (b) standby⇄active | 召喚・音声を**受け付けてよいか**（armed フラグ・ms） | [mode-x-er/11 §2-2](../mode-x-er/11-standby-and-hri-features.md)（[11:88-89](../mode-x-er/11-standby-and-hri-features.md) の 2 軸表） |
| **(c) 運転モード（本書新設）** | **どの速度源が意図されているか**: `AUTO`（Nav2）/ `MANUAL`（teleop） | 本書 |
| **停止理由の集合（本書新設）** | 状態ではなく**理由ごとに保持する集合**。1 つでも残っていれば走行禁止 | 本書 §5 |

- 「召喚を受け付けてよいか」（軸 b）と「車体が走行してよいか」（停止理由集合）は**別の情報**であり、単一の `STANDBY` 状態へ押し込まない（doc11 正準語 standby の再定義を避ける）。
- 外部提案にあった `STANDBY/AUTO/MANUAL/STOPPING/PAUSED/ESTOP` の 6 状態機械は**不採用**。PAUSED（目的地保持・再開）は §7 の別項目へ、STOPPING（停止完了確認）は将来 §7 の一時停止実装時に検討する。
- 停止理由の初期集合: **①既存 Guardian estop 条件**（near_collision / battery_critical / pose_stale — 自動解除のまま不変。blocked_timeout は recovery＝estop 側ではない）**②操作者非常停止要求**（§5・latch・明示解除のみ）**③手動走行中の /joy 途絶**（teleop 発生源ゲートが担当・§6。未使用のジョイスティックを抜いただけで自律走行を止めることはしない — /joy は自律経路に接続されていないことを検証済み）。

## 3. Emergency の「イベント」と「現在状態」の分離（確定・2026-09-07）

### 3-1. 確定した現状の事実（2026-08-31〜09-07 検証）

1. `/emergency/event` は **rising edge のみ**発行される（`guard_logic.py:385-416` EdgeLatch・[doc12:185](../architecture/12-infrastructure-common.md)）。**解消（falling edge）は通知されない**。
2. state.json の `emergency.active` は**解除経路の無い受信ログ**（`aggregator.py:165-182`・clear プロトコルは [doc12:342](../architecture/12-infrastructure-common.md) が Phase-2 TODO と自認）。
3. L2 の `PolicyGate.set_emergency` は長らく**呼び出し元ゼロ＝未配線**で、emergency 中でも L2 は新規 dispatch を拒否できなかった（`check_emergency` never-fire）。**この穴は #593（2026-09-07 land）の level mirror が閉じた** — L4 `llm_bridge` が `/bot{n}/cmd_vel/emergency` を購読し、`WarehouseTools.policy_gate`（read-only property）経由で `set_emergency` を反映、無信号が `policy_gate.emergency_clear_after_s`（既定 1.0s・厳密 `>`・tighten-only floor）を超えたら clear する（正本 = [doc12:616](../architecture/12-infrastructure-common.md) 追補②。実装側 anchor は行 pin せず契約の形で指す = §References）。
4. **平常時の Guardian 生存を運ぶチャネルは存在しない**。state.json の timestamp は State Cache 由来（Guardian 死でも更新され続ける）。Guardian 単独死は「0.5 秒後に走行が再開する」既知の部分故障モード（[mode-x-er/10:459-461](../mode-x-er/10-room-scale-safety-review.md)）。#593 以降、**estop 継続中に限り** level 信号の継続受信が「Guardian がこの bot を停止させ続けている」の ground truth になる（[doc12:622](../architecture/12-infrastructure-common.md)）が、平常時の生存証明が無い点は不変。

### 3-2. 決定（CURRENT = #593 level mirror ／ 残る論点 = 平常時の生存証明）

- **イベント（発生の記録・通知）と現在状態（いま停止中か・理由は何か）を分ける**。`/emergency/event` は記録・通知用として現行のまま維持する。
- **現在状態の L2 供給は #593 で land 済み（CURRENT・正本 = [doc12:616](../architecture/12-infrastructure-common.md) 追補②）**: 新 topic を作らず、Guardian が estop 継続中 50ms 毎に再アサートする既存 `/bot{n}/cmd_vel/emergency`（level 信号）をミラーする。イベント駆動案・state.json `emergency.active` 参照案は falsification 付きで却下済み（[doc12:628-631](../architecture/12-infrastructure-common.md)。後者は clear protocol 不在で永続 reject になる = §3-1 事実 2）。
- **mirror の意味論として受容した暫定（隠さない）**: この信号は estop 中しか流れない**条件付き信号**であり、「無信号 = 非常時でない」が定義。したがって**無信号 1.0s 超は「解消」と「Guardian 死」を区別しない**。Guardian 単独死では物理層（twist_mux prio100・0.5s 失効）→ L2（1.0s）の順で開く fail-active 窓が残るが、これは §3-1 事実 4 の既知故障モードに包絡され、**L2 は常に物理より後に開く**。最小安全方針の下で暫定受容し、**平常時も流れる「Guardian 生存証明チャネル」（周期 publish の現在状態）を導入するか恒久受容するかは OQ-OP1 として保持**する。bridge 再起動〜DDS discovery 完了までの短い窓（estop 保持中の dispatch が phantom 受理されうる・motion は物理層が阻止し Guardian が毎 tick cancel）も同クラスの残余（§10）。
- **受信側の鮮度監視の原則は「常時流れる設計のチャネル」に適用する**: `transient_local` は後着購読者への保持配信であって生存確認にならないため、§4 停止上乗せ・§6 運転モードが購読するチャネルでは**不明・stale → 統合構成で新しい走行を開始しない／MANUAL 指令を出さない**（fail-closed）。※条件付き信号である mirror にこの規則をそのまま適用すると平常時に全 dispatch が塞がるため適用外 — これが上記暫定受容の理由である。
- **【2026-09-09 追記で限定】** 以下の「発明しない」は **L2 feed 用の生存証明チャネル**（本節が論じている対象）に限る。**§4 停止上乗せ（L0'）が購読する入力チャネルは §4-1 で確定済**（`/bot{n}/stop_state`・topic 名/型/鮮度規律/期限規律。**publish 周期は未定**）。両者を 1 本に統合するかは OQ-OP1 のまま未決。— 生存証明チャネルを新設する場合の topic 名・型・周期・鮮度閾値は**本書で発明しない** → OQ-OP1。**凍結契約 `warehouse_interfaces` は無変更**（doc03 カタログへの additive 追加のみ・[doc12:512](../architecture/12-infrastructure-common.md) の pose_stale 先例と同型）。なお doc03 への既存 `/bot{n}/cmd_vel/emergency` 行の追記は**解消済（2026-09-09・#597）**: doc03:113（[doc12:638](../architecture/12-infrastructure-common.md) の解消マーク参照）。

## 4. m1_driver の停止上乗せ（stop overlay）（確定・2026-09-07）

driver に「走行を許可する権限」を新設するのではなく、**既存の速度指令を必要に応じてゼロにする追加機能**として定義する。名称は **停止上乗せ（stop overlay）**とし、「safe-OFF」という表現は使わない — **機能無効時はこの追加保護が働かない（既存動作を維持する）**だけである。

| 停止上乗せの状態 | driver の動作 |
|---|---|
| 機能無効（既定） | **既存動作を維持**（clamp 必経＋W-1 鮮度監視。現行と bit 等価） |
| 有効・停止要求なし | 既存の判定を通った速度を送る（**L2 が拒否した仕事を許可する意味ではない**） |
| 有効・停止要求あり | ゼロを送る |
| 有効・状態更新が途絶 | ゼロを送り、走行再開を保留する（§3-2 の鮮度監視） |

- **既定は機能無効**。有効化しないと M0-M2 standalone bring-up（[mode-m1/03:50](03-joystick-teleop-bringup.md)・発信元ノード不在の構成）が実行不能になるため。単体動作確認は無効・統合構成では明示有効、と起動設定で分ける。
- **clamp 必経（G-l 条件）は不変**。停止上乗せは `clamp_body_velocity` を迂回する経路を作らない。W-1 とは「守る故障が違う」（W-1=指令途絶／上乗せ=停止要求・状態途絶）ため timeout を単一 param に混ぜない。**W-3（MCU watchdog 不在）の代替と説明しない**（ホスト死には効かない）。
- R-26 unit（独立オラクル＋mutation）: ①停止要求→ゼロ ②非有限/非正/逆行する期限→無許可扱い ③初期状態=停止側（有効時）④**無効時は現行と bit 等価**（negative oracle — これが無いと「安全機構がある」名目だけが残る）⑤clamp 必経の回帰 ⑥W-1 との AND 合成（どちらか stale なら brake）⑦W-2 との整合 ⑧注入時計のみ使用。

### 4-1. 停止上乗せの入力契約（producer → overlay）（確定・2026-09-09）

§4 の「停止要求」「状態更新」を運ぶチャネルを確定する。これは §3-2 が言う**「常時流れる設計のチャネル」**（`transient_local` は生存確認にならない）に該当し、fail-closed の鮮度監視をそのまま適用する。**doc03 カタログへの追加は additive 1 行のみ**（topic 名・型・一行責務。詳細の正本は本節＝doc03 の委譲規約に従う）。**凍結契約 `warehouse_interfaces` は無変更**。

| 項目 | 確定値 |
|---|---|
| topic | `/bot{n}/stop_state`（per-bot。driver が per-bot に立つため） |
| 型 | `std_msgs/String`（JSON）。Phase 4 で `.msg` 化（[doc16 §3](../architecture/16-repository-and-conventions.md)） |
| producer | **Emergency Guardian（L1）** — 停止理由の集合を持つ担当（§1 / §5）。**周期 publish**（停止要求が無い間も流し続ける＝§3-2） |
| consumer | **m1_driver（L0'）の停止上乗せ**。`stop_overlay_enabled: true` のときだけ購読する（既定無効＝:68 を壊さない） |
| QoS | RELIABLE / KEEP_LAST depth **1** / **VOLATILE**。`transient_local` は使わない（後着購読者へ古い許可を再配信＝fail-open になるため＝§3-2） |
| payload | `{"stop_requested": <bool>, "valid_until": <float>}`。未知キーは consumer が無視する（additive-first・前方互換） |

**期限規律（watermark）**

- `valid_until` は**絶対期限（秒）**。基準時計は **producer と consumer が同一ホスト・同一 boot で共有する単調時計**（POSIX `CLOCK_MONOTONIC`）。**壁時計は使わない**（NTP のステップで期限が伸びる＝fail-open になるため）。
- **同一ホスト・同一 boot は本節が置く前提であって、他 doc から導かれたものではない**（doc03「Jetson 内部」は「Jetson 内部のアプリ契約トピックを網羅する」と言うだけで、ホストや時計ドメインを規定していない）。producer を別ホスト／別コンテナに置く場合、および `use_sim_time`（`/clock`）下で走らせる場合は**この期限規律が成立しない**（consumer は恒久的に fail-closed 側へ落ちる）。sim での扱いは本節では決めない → producer 実装スライス。
- producer は `valid_until` を**単調非減少**で発行する。consumer は受理済みの最大値を **watermark** として保持し、**それを下回る値（逆行）を拒否**する。目的は「replay・順序逆転・producer 再起動によって、古くて長い許可が復活しない」こと。
- **逆行・非有限・非正はいずれも「無視」ではなく即時失権**（成立中の許可を落とす）＝ :70 ②。
- consumer は許可長を **`stop_state_max_validity_s`** で上限クリップする（producer の誤値・暴走が長時間の許可へ化けるのを防ぐ）。**W-1 の `cmd_vel_timeout_s` とは別 param**（守る故障が違う＝:69）。既定値は **凍結 twist_mux 入力 timeout と同じ 0.5s を暫定的に借りる**（導出ではない。W-1 既定 `DEFAULT_CMD_TIMEOUT_S` と同じ借り方で、実運用値は `# TODO(Phase 1 実測)`）。
- この param は **operator が設定する信頼済みノブ**として扱う: consumer が硬化するのは**退化値（非有限・非正）→ 既定へフォールバック**のみで、大きな有限値はそのまま天井になる（＝上乗せは実質無効化されうる）。これは W-1 の `cmd_vel_timeout_s` と**同じ信頼クラス**（既存 idiom）。**ハード上限を設けるか否かは未決**（値を発明せず開いたままにする）→ §10。
- **壁時計 producer は上限クリップに吸収されて静かに縮退する**: 期限が常に天井へ丸められ、絶対期限契約が事実上「窓ぶんのハートビート」になる（安全側だが契約の保証は失われる）。検出手段（clip 発生の log / counter）は producer 実装スライスで足す。

**鮮度規律・不正 payload の扱い**

- `now > valid_until` → 停止側（:66 の表 4 行目）。未受信 → 初期状態＝停止側（:70 ③）。
- **JSON パース不能・キー欠落・型不一致は即時失権**（fail-closed）。これは `/operator/stop_request` の「不明・不正 payload は**無視**」（§5・OQ-OP2）と**逆向き**だが矛盾ではない: あちらは停止を**掛ける**方向の入力なので無視が安全側、こちらは走行を**許す**方向の入力なので無視は危険側になる。**fail-closed の向きは入力の意味で決まる**。
- **キー重複も失権**（JSON の last-wins をそのまま採ると `{"stop_requested": true, …, "stop_requested": false}` が停止要求を許可へ上書きできてしまう）。同様に、**decode 中に生じた例外を consumer 外へ漏らさない**（漏らすと「失権」ではなく driver プロセス死になる。表現不能な巨大整数・過度に深いネストが実例）。

**本節が裁定しないこと（隠さない）**

- OQ-OP1 の本題（**L2 feed** を #593 の level mirror のまま恒久受容するか、生存証明チャネルへ移すか）は**本節では裁定しない**。本節は §4 停止上乗せ（L0'）の入力契約のみを確定する。両者を 1 本のチャネルへ統合するかは fault injection（§8）後のオペレーター裁定に残す。
- **publish 周期（rate）は確定しない**。本節が固定するのは「常時流れる設計であること」と consumer 側の期限・鮮度規律だけで、実値は producer 実装スライス（＋ Phase 1 実測）へ残す。
- **`stop_state_max_validity_s` のハード上限**（大きな有限値による実質無効化を封じるか、W-1 と同じ信頼クラスのまま置くか）はオペレーター裁定に残す。
- producer 実装（Guardian 側の publisher）は `warehouse_safety` 所有の後続スライス。**本契約が先に land しても、producer 不在の間は overlay を有効化すると常時停止側**（fail-closed・設計どおり）。
- producer 再起動で `valid_until` が watermark を下回り続ける場合、consumer は停止側で保持し続ける（fail-closed）。復帰手段（driver 再起動 か producer 側の watermark 継承か）は producer 実装スライスで裁定する。

**R-26 追加（:70 の ①〜⑧ に対する additive）**

⑨ 契約 payload の decode: 正常形 → `(stop_requested, valid_until)`、パース不能・キー欠落・型不一致・**キー重複**・**decode 中の例外**（表現不能な巨大整数・深いネスト）→ 即時失権（例外を consumer 外へ漏らさない） ⑩ 上限クリップが効く（過大な `valid_until` が許可窓を延ばさない）。退化した窓 param は既定へフォールバックする一方、**大きな有限値は信頼して通す**ことも pin する（開いている点を suite 上で可視にするため） ⑪ 配線層を AST で pin する（§10 の #593 残余 follow-up ① と同じ `test_speed_band_bringup_wiring.py` 先例）。CI に rclpy が無く**配線層は実行されない**ため、pin は部分一致でなく**厳密一致**にする: topic 式・型・QoS 4 値・**guard の極性**（`if not …` / `… or True` を通さない）・**callback が decode 結果を改変せず core へ渡すこと**（`on_stop_state(False, …)` のようなハードコードや core 呼び出しの欠落が緑のまま通らないように）。

## 5. 操作者非常停止要求（operator stop request）（確定・2026-09-07）

- Guardian に**新しい停止理由として追加**する。実装 idiom は既存どおり: `BotState` への既定値付きフィールド追加＋`evaluate` の追加ブロック＋純ロジック側の latch dataclass（`pose_stale`/`PoseGateTracker` が先例）。**既存の異常条件（near_collision / battery_critical / pose_stale）は自動解除のまま変えない**（非回帰を R-26 で pin）。
- **latch 意味論**: 要求ボタンを離しても保持し、**明示解除でのみ**落ちる。いずれかの停止理由が残っていれば Guardian は既存 Emergency 経路（prio100）からゼロを level 送出し続ける。解除後は走行禁止のまま待機へ戻し、**走行の開始は別操作**とする。
- **「解除≠即走行」は Guardian 単体では担保できない**（検証済み）: `_cancel_all_goals` は fire-and-forget で成功保証が無く、解除 0.5 秒後に Nav2 goal が残っていれば `cmd_vel/nav2` が通る（[mode-x-er/10:459-461](../mode-x-er/10-room-scale-safety-review.md)）。担保は「latch 期間中の cancel 反復＋解除時の残 goal 確認＋L2 の現在状態参照（§3）」の合成で行う（確認手段の詳細 = OQ-OP3）。
- **入力源の裁定**: 第 1 候補 = **ゲームパッドの空きボタン**（15 ボタン中 deadman の 1 個のみ使用済・実機 index は M1 ゲートの jstest で確定）。**web は不可**（observe-only の R-26 unit が機械的に禁止 = [architecture/22](../architecture/22-web-observability.md)）。**骨格 NN を単独の停止手段にしない**（[mode-x-er/11:235](../mode-x-er/11-standby-and-hri-features.md)）。**音声も単独手段にしない**（**本書判断** — 誤検出率未実測という骨格 NN と同型の非決定論性を理由とする外挿。[mode-x-er/11:79](../mode-x-er/11-standby-and-hri-features.md) の未実測宣言）。物理 E-stop（doc10 P-1・未裁定）は独立の層であり本要求で置換しない。
- 停止理由は 1 個の Bool でなく**理由ごとに保持**する（§2）。操作者要求だけ解除しても他の異常が残っていれば走行禁止。
- 付随挙動の記録: 新理由は `action="estop"` のため `/emergency/event`（type 新値・コアキー不変＝additive）と `/negotiation/abort` が発火する。下流 consumer は全て未知 type を無視/素通しすることを確認済み。M1 単騎では交渉系は非起動。

## 6. teleop 側の操作条件（確定・2026-09-07）

| 操作・異常 | 動作 |
|---|---|
| MANUAL モードで deadman を押す | 有効な操作入力があれば publish |
| deadman を離す | 停止。**自律には戻らない**（AUTO への遷移は別操作） |
| `/joy` が途絶える | 停止し、**再接続だけでは走行を再開しない** |
| 非常停止ボタンを離す | 非常停止を保持（§5 latch） |
| 非常停止を解除する | 待機へ。**スティックを倒したままでも動かさない** |

- **再アーム条件**: 再接続・停止解除の後は、**スティックを一度中立に戻し、deadman を押し直す**ことを walk 再開の条件にする（押しっぱなしの古い操作による再発進の防止）。
- `/joy` 鮮度タイムアウトは実装スライス進行中（別セッション）。mux 統合時の publish 意味論（release 後の沈黙）は standalone 構成（連続ゼロ維持）と要件が逆転するため**構成別 param** とする。正本の joy 経路は [mode-m1/03](03-joystick-teleop-bringup.md)。
- **運転モード参照の既定は「無効」**（§4 の停止上乗せと同じ構造）: standalone 構成（発信元不在 = [mode-m1/03:50](03-joystick-teleop-bringup.md)）では従来どおり deadman が主ゲートで動く。モード購読を**明示有効化**した統合構成では、**モード不明・stale → MANUAL 指令を出さない**（fail-closed）。既定値・鮮度閾値の具体は OQ-OP6。

## 7. 別項目（本書では実装対象にしない）

1. **一時停止・目的地保持・再開**: 再開は必ず **L4 → MCP → L2 再判定 → Nav2 Bridge → Nav2** の既存経路で行う（保存目的地の直接送信は INV-2 = [mode-x-er/09:57](../mode-x-er/09-hand-raise-summon.md) 違反）。再開時に Emergency 継続中なら拒否・条件が変わっていれば現在の条件で再判定（「以前許可された仕事だから無条件に許可」はしない）。召喚デモに必須ではないため後回しにできるが、ユーザー機能としては削除しない。
2. **通常手動走行の障害物保護（手動用 CM）**: 前提連鎖が長い — 既存 CM の Humble param 突合（現行 yaml は Jazzy schema）→ G-a（footprint contract）→ G-b（C-3 radius 改訂・安全レビュー）→ G-f 実測 → sim/実機 config 分離裁定 → `/cmd_vel/teleop` mux 入力スライス（[doc10:480-492](../mode-x-er/10-room-scale-safety-review.md) の G 表）。**既存 CM の位置（nav2 枝の上流）は動かさない**。
3. **退避（CM 停止からの脱出）**: 新モードを発明する前に、凍結契約の既存 `yield` / `retreat_to` の意味・実行条件が一致するかを判定する（一致しない場合のみ別途設計）。

## 8. 実装順序（確定・2026-09-07）

| 順 | 作業 | 完了条件 |
|---|---|---|
| 1 | 本書 land（状態・停止理由・担当の文書確定） | check_consistency 0 ERROR |
| 2 | **Emergency 現在状態の L2 共有**（§3）— **充足済み（#593 land・level mirror・R-26 32 本 main で green）**。残余 follow-up は §10（get_fleet_status 併修=**#600 で解消**・配線層 AST pin・起動窓） | ✅ emergency 中の dispatch reject R-26 |
| 3 | **joy 鮮度監視・中立確認・deadman 再操作**（§6・一部進行中） | 前進押し続けでも Emergency が勝つ実機確認 |
| 4 | **teleop の mux 追加**（Emergency prio100 不変の additive 3 入力・contract 級 PR） | 手動解除・通信断で自律へ戻らない |
| 5 | **運転モード発信元・driver 停止上乗せ・操作者停止 latch**（§2/§4/§5） | 解除しただけでは動かない実機確認 |
| 6 | **手動用 CM**（§7-2 の前提連鎖の後） | 壁への接近で手動指令が制限される |
| 別 | 一時停止・目的地保持・L2 経由の再開（§7-1） | — |

fault injection（Guardian kill・driver kill・USB 抜線・joy 切断・process kill）は 5 の完了時と G-g（[doc10:486](../mode-x-er/10-room-scale-safety-review.md)）で実施。**STM32 comm watchdog（G-g）は本書と独立の裁定**（ADR 起草対象）。

## 9. OPEN QUESTIONS（接頭辞 `OQ-OP*`）

> 採番 scoping: `OQ-OP*` は本書独自。採用前に `grep -rn "OQ-OP" docs/` で 0 件（2026-09-07 確認）＝ [09 の OQ-T*](../mode-x-er/09-hand-raise-summon.md)・[11 の OQ-H*](../mode-x-er/11-standby-and-hri-features.md)・[04 の OQ-R*](04-runtime-speed-limiter.md) と衝突なし。

| # | 未決事項 | 決め方 | 優先度 |
|---|---|---|---|
| **OQ-OP1** | **平常時も流れる Guardian 生存証明チャネル**（周期 publish の現在状態）を導入するか、#593 の level mirror を恒久受容するか（§3-2）。導入時の topic 名・型・周期・鮮度閾値は doc03 additive 追記と同一 PR。先行条件だった **doc03 への既存 `/bot{n}/cmd_vel/emergency` 追記は解消済**（doc03:113・#597＝[doc12:638](../architecture/12-infrastructure-common.md) 解消マーク）。**【2026-09-09 で範囲縮小】** §4-1 が **L0' 停止上乗せ用の常時 publish チャネル `/bot{n}/stop_state`**（topic 名・型・鮮度/期限規律・doc03 additive 行）を確定したため、残る問いは「**そのチャネルを L2 feed にも流用するか、level mirror を恒久受容するか**」＋**publish 周期**。「常時チャネルを作るか否か」自体はもはや論点ではない | fault injection（§8）で Guardian 死の実害を実測 → オペレーター裁定 | 中（CURRENT は暫定受容済み） |
| **OQ-OP2** | 操作者停止要求・明示解除の入力 topic の形（別 topic か同 topic payload か）→【2026-09-08 解消】単一 global topic `/operator/stop_request`（`std_msgs/String` JSON・同 topic payload 方式: `{"action": "engage"}`／`{"action": "clear"}`。不明・不正 payload は無視＝clear 扱いにしない。M1 単騎ゆえ per-bot 選択性は安全要件でない。PR #602） | doc03 additive 追記と同一 PR（済） | 高（順序 5 の前提） |
| **OQ-OP3** | 解除時の残 goal 確認手段（cancel 反復の完了確認・nav_status 参照の形） | 実装設計＋実機確認 | 高（§5「解除≠走行」の担保） |
| **OQ-OP4** | standalone stdio MCP（`server.py`）での現在状態参照。[doc12:636](../architecture/12-infrastructure-common.md) が「ROS 文脈が無くミラー不能・現用外」と登録済み — 残るのは state.json fallback にするか非対応と割り切るかの裁定のみ | 実装スライスで裁定 | 低 |
| **OQ-OP5** | state.json `emergency.active` の clear プロトコル実装（[doc12:342](../architecture/12-infrastructure-common.md) Phase-2 TODO）。**L2 feed は level mirror のまま独立系統と裁定済み**（[doc12:631](../architecture/12-infrastructure-common.md)）のため、残る実害は `self_action_gate` の sticky reject と LLM 観測面 | doc12 所有トラックと調整 | 中 |
| **OQ-OP6** | 運転モード発信元の実装形（どの package が持つか。軸(b) standby manager との同居可否）＋ **モード topic の既定値と stale 時挙動**（§6 の fail-closed の具体形・発信元不在構成での既定無効） | 実装スライスで裁定（doc11 OQ-H9 と同時が望ましい） | 中 |
| **OQ-OP7** | 操作者停止要求の GLOSSARY 分類（operational stop か protective stop か。搬送経路は protective の prio100 チャネル） | GLOSSARY 追補時に裁定 | 低 |

## 10. 残件・既知ドリフト（隠さない）

- `get_fleet_status` が `emergency` を返していない（`tools.py` ↔ [doc12:389-409](../architecture/12-infrastructure-common.md) のフロー記述）。**#593 後は「L2 が enforce する emergency（mirror）を司令官が観測できない」**——reject 理由が見えず再試行ループになりうるため優先度が上がった。mirror の hold/clear ログ追加とセットで follow-up スライス。→ **【2026-09-08 解消】** #600 が `emergency{active,history}`（State Cache ring 素通し）と `l2_emergency_holds`（L2 enforce 実体 = `PolicyGate.emergency_holds()`）を**別キー**で返し（[doc12:631](../architecture/12-infrastructure-common.md) の独立系統裁定＝混ぜない）、hold/clear の遷移エッジ 1 行ログとセットで実装。
- **#593 の残余 follow-up（レビュー確定・2026-09-08）**: ①配線層（topic 名・QoS・timer）が unit/CI のどちらにも乗っていない → AST pin を追加（`test_speed_band_bringup_wiring.py` 先例）②L4 側 `_BOTS` ハードコードと config `robots:` の突合アサート ③bridge 起動〜DDS discovery 完了までの phantom 受理窓（§3-2）。
- **#582（joy 鮮度）は §6「再接続だけでは走行を再開しない」をまだ満たさない**（ガードは mask であって latch でない・再アーム未実装）— #582 本文の残件に明示済・後続スライスで latch/再アームを実装。あわせて mode-m1/03 中段挿入で割れる [jetson/02](../jetson/02-remote-access-and-dev-link.md) の `mode-m1/03` pin は **#582 内で :55 へ再 pin 済**（この形の pin は check_consistency の検査対象外＝CI では検出されない）。
- doc12 内部の行 pin ドリフト（Guardian 詳細節を「:95-151」と自己参照するが実体は :181。**同型 stale がコード側 `ws/src/warehouse_bringup/launch/bringup.launch.py:221` のコメントにも現存**）・[mode-x-er/10:457](../mode-x-er/10-room-scale-safety-review.md) の GLOSSARY 行参照ドリフト — 所有トラックへ申し送り（本 PR では触らない）。
- doc12 / doc10 への backlink 追記は所有境界を尊重し本 PR では行わない（張り残しとして明示）。
- **【2026-09-09 追記】停止上乗せの入力契約は §4-1 に確定**（topic `/bot{n}/stop_state`・doc03「Jetson 内部」表へ additive 1 行・consumer 側配線と R-26 のみ land）。**producer（Guardian 側 publisher）は `warehouse_safety` 所有の後続スライスで未実装**＝現状 `stop_overlay_enabled:=true` は feed 不在で常時停止側（fail-closed）。**OQ-OP1 の本題（L2 feed を level mirror のまま恒久受容するか）は §4-1 では裁定していない**（§9 の表は未決のまま）。

## References（双方向）

- [mode-x-er/11-standby-and-hri-features.md](../mode-x-er/11-standby-and-hri-features.md) — 軸(a)(b) の正本（§2-2 の 2 軸表 = [11:88-89](../mode-x-er/11-standby-and-hri-features.md)）・入力源裁定の根拠（[11:235](../mode-x-er/11-standby-and-hri-features.md)）・OQ-H9（[11:310](../mode-x-er/11-standby-and-hri-features.md)）
- [architecture/12-infrastructure-common.md](../architecture/12-infrastructure-common.md) — Guardian 正本（詳細節 :181・clear TODO :342・level 自動解除 :511・状態同期フロー :389-409・**【2026-09-07 追補②】L2 feed = level mirror :616**） 
- [mode-x-er/10-room-scale-safety-review.md](../mode-x-er/10-room-scale-safety-review.md) — Guardian 単独死 :459-461・P-1 :376・G 表 :480-492
- [02-m1-driver-and-watchdog.md](02-m1-driver-and-watchdog.md) — W-1〜W-4（§3 = :58-68）・fail-active :25 ／ [03-joystick-teleop-bringup.md](03-joystick-teleop-bringup.md) — standalone 構成 :50・M0-M2 ゲート
- [adr/0004-l2-restrict-only-policy-profile.md](../adr/0004-l2-restrict-only-policy-profile.md)（restrict-only・AND 合成）／ [adr/0012-speed-band-no-l2-best-effort.md](../adr/0012-speed-band-no-l2-best-effort.md)（最小安全方針 §Context :9・velocity path に L2 を載せない先例）
- [productization/11-l2-contract-governance-traffic-box.md](../productization/11-l2-contract-governance-traffic-box.md)（L2 定義・L2-G8 :214）／ [architecture/22-web-observability.md](../architecture/22-web-observability.md)（web observe-only）
- 実装側 anchor（行 pin しない＝churn 前提・契約の形で指す）: `policy_gate.py` の `set_emergency`/`check_emergency`・`emergency_guardian.py` の level 送出と `_cancel_all_goals`・`guard_logic.py` の `evaluate`/`BotState`/latch 群・`aggregator.py` の `emergency.active`・`driver_core.py` の clamp 必経と W-1・`twist_mux.yaml` の凍結 2 入力（emergency 100 / nav2 10）
- [architecture/03-software-architecture.md](../architecture/03-software-architecture.md) — トピック契約カタログ。**§4-1 の `/bot{n}/stop_state` 行 = doc03:114**（名前・型・一行責務のみ／詳細は §4-1 が正本＝doc03:116 の委譲規約）。`/operator/stop_request` = doc03:112（§5）・`/bot{n}/cmd_vel/emergency` = doc03:113（§3-2）
- **用語**: [GLOSSARY.md §11](../GLOSSARY.md) — 停止上乗せ / 操作者非常停止要求 / 運転モード（本書と双方向）。**§4-1 が導入した語（`/bot{n}/stop_state`・期限 watermark・許可窓上限）は未登録＝張り残し**（GLOSSARY は本 PR の編集境界外）
- **索引（backlink）**: [mode-m1/README.md](README.md) ファイル表 / [docs/README.md](../README.md) mode-m1 表
