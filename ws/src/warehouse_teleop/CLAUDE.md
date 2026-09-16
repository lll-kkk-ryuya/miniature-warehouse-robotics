# warehouse_teleop — キーボード teleop（動作確認の足場）

- **担当トラック / ブランチ**: track:skeleton（安全隣接の変更は track:safety-state 併記） / `feat/teleop`（#158）
- **Phase**: 1（実機不要・bring-up / sim 手動ドライブ utility）
- **ビルド**: ament_python
- **ノード / モジュール**:
  - `teleop_keyboard`（rclpy ノード・entry point）
  - `keymap`（rclpy 非依存の **pure 写像モジュール**。`warehouse_safety/guard_logic`・`warehouse_nav2_bridge/core` と同 idiom → unit はここを叩く）
- **編集境界**: このパッケージ配下のみ。共有契約 `warehouse_interfaces` は変更不可（`.claude/rules/parallel-workflow.md` §4）。

## 提供 (produce)
- topic: `/<bot>/cmd_vel`（`geometry_msgs/Twist`・bot は ros param、既定 `bot1`・doc03:87）
  - ⚠️ **standalone bring-up utility**：`/<bot>/cmd_vel` を**直接** publish する（sim `ros_gz_bridge` / 実機 base が consume する topic）。**Nav2 + twist_mux を立てずに**使う。フルスタック稼働時は Nav2 path-follower と `/cmd_vel` を奪い合う（Emergency Guardian が prio-100 `/cmd_vel/emergency` 経由にする理由＝doc15）。`/cmd_vel/teleop` mux 入力追加は bringup 所有の別変更＝本レーン scope 外。

## 消費 (consume)
- 契約: `warehouse_interfaces.safety.clamp_velocity` / `MAX_LINEAR_VELOCITY`（safety.py:18,25・**0.3 をハードコードしない**単一ソース）
- 契約: `warehouse_interfaces.config.load_config`（`safety.max_linear_velocity` ≤ ハードキャップで運用上限を下げられる・doc19）
- キー入力（termios raw / no-TTY フォールバック）

## 速度上限 (R-26)
- リニアは `clamp_velocity(v, max_speed)`、`max_speed = min(_nonneg(param), MAX_LINEAR_VELOCITY)`（コード強制で 0.3 m/s を超えない）。非有限要求（NaN/±inf）は 0.0 stop。
- **param ハードニング**: `max_linear/max_angular/linear/angular_step` は `_nonneg`（非有限・負 → 0.0 fail-stop）で正規化。負の cap は対称クランプを反転させ runaway（`clamp_velocity(v,-m)→+m`）、負の step は走行方向を反転させるため、node の param 防御に加え **pure `key_to_twist` 側でも負/非有限の cap・step を 0.0 に潰す**（caller 不問の単一ソース防御・符号反転なし・unit 検証あり）。`publish_rate`/`stop_timeout` は `_positive`（非有限/≤0 → 既定値。NaN stop_timeout が dead-man を無効化するのを防ぐ）。
- アングラに凍結契約は無い（safety.py は LINEAR のみ）→ teleop-local の `max_angular` を同 `clamp_velocity` で bound（非有限→stop の保証だけ流用）。
- **終了**: `q`/`Ctrl-D` は callback で `shutdown_requested` フラグを立て、`main()` の `spin_once` ループが抜けて shutdown（callback 内 `rclpy.shutdown()` 禁止＝executor にマスクされ exit しない・repo idiom）。`Ctrl-C` は SIGINT→`KeyboardInterrupt`→`main()` finally。

## 依存
- `warehouse_interfaces` のみ（他トラック内部を import しない）＋ rclpy / geometry_msgs（exec_depend は package.xml）。

## テスト
- `tests/unit/test_teleop_keymap.py`：pure `key_to_twist` / `decode_key`（ROS spin 不要・headless 安全）。クランプ境界（0.3 / 0.31→0.3 / NaN→0.0 / stop→(0,0)）を `@pytest.mark.safety` で検証（R-26）。Ruff(py312/line100) + pytest 緑を維持。
- no-TTY（`stdin.isatty()` False）でノードは raw 入力を無効化し warn のみ＝CI/headless で落ちない。

## 設計ドキュメント
- `docs/architecture/03-software-architecture.md`（`/bot{n}/cmd_vel` 契約・read-only）/ `15`（twist_mux・doc15）/ `16`・`17`
- `.claude/rules/safety.md`（ミニチュア最大 0.3 m/s 強制）

> #1 契約凍結の雛形 stub を #158 で実装に置換（リポジトリ最後の skeleton stub 解消）。
> 申し送り: doc16 §9 branch 表（16-...:72）は warehouse_teleop を `ros2/hw` 表記＝`feat/teleop`（#158）追記は governance/docs PR（doc16 は skeleton 所有・本レーン read-only）。

## 【2026-08-26 追記】joystick teleop（`teleop_joy`）追加

設計正本: [docs/mode-m1/03-joystick-teleop-bringup.md](../../../docs/mode-m1/03-joystick-teleop-bringup.md) §3（joy 経路・Yahboom 公式 joy node 不採用の理由）。

- **提供 (produce)**: console_script **`teleop_joy`**（`teleop_joy.py`）— `/joy`（`sensor_msgs/Joy`・stock `joy_node` 出力）購読 → pure **`joymap.joy_to_twist`** → `/<bot>/cmd_vel`。keyboard 版と同じ **standalone bring-up utility**（mux 入力追加は bringup 所有の別変更のまま）。固定レート republish（デッドマン保持中=指令・解放=明示ゼロ。m1_driver W-1 との整合）。
- **`joymap`（pure・rclpy 非依存）**: **ベクトルキャップ**（`hypot(vx,vy) ≤ min(param, MAX_LINEAR_VELOCITY)`・方向保存＝**C-8**。keymap のスカラー clamp では `vy` を扱えないための新設）・デッドマン必須・非有限→0 寄与・cap 負/非有限→fail-stop（keymap `_nonneg` idiom）。軸/ボタン index は公式レイアウト既定（x=axes[1], y=axes[0], yaw=axes[2], deadman=button **6**〔← 4 から訂正・下記 2026-09-09 節〕）で **ros param 上書き可**（実機 `jstest` で確定＝mode-m1/03 §2-3）。
- **消費 (consume)**: `warehouse_interfaces.safety.MAX_LINEAR_VELOCITY` / `clamp_velocity`（単一ソース）・sensor_msgs / joy（package.xml exec_depend 追加）。
- **テスト (R-26)**: `tests/unit/test_teleop_joymap.py`（15 ケース・spec 由来オラクル）。**mutation 2 本 KILLED**（2026-08-26 実測）: per-axis クランプすり替え→1 fail（C-8 本命）・デッドマン disarm→1 fail。クリーン 0 fail。
- 注意: ここは**第一防御**にすぎない。最終防衛は `warehouse_m1_driver` の L0'（`clamp_body_velocity`）＝別トラック。

## 【2026-08-31 追記】/joy 鮮度タイムアウト（teleop_joy の freshness dead-man）

設計正本: [docs/mode-m1/03-joystick-teleop-bringup.md](../../../docs/mode-m1/03-joystick-teleop-bringup.md) §3（/joy 鮮度タイムアウトの行）。

- **動機**: Humble joy_node はデバイス喪失時に /joy の publish を止めるだけで**ゼロ Joy を出さない**（joystick_drivers ros2 branch `joy.cpp` handleJoyDeviceRemoved）。旧 `_on_timer` は `self._latest` を無条件 20Hz 再送するため、デッドマン保持中に joy_node/receiver が死ぬと最後の非ゼロ twist を永久送出し、cmd_vel が fresh のまま m1_driver **W-1（0.5s）も発火しない**穴があった。
- **提供 (produce)**: ros param **`joy_timeout_s`**（既定 `DEFAULT_JOY_TIMEOUT_S = 0.6`＝teleop_keyboard `stop_timeout` と同値）。最後の /joy 受信からの経過が超過（strictly greater・keyboard 版と同境界）で republish がゼロ twist に落ちる。初回 /joy 受信前は stale 扱い（fail-closed・driver_core W-1 と同姿勢）。
- **pure 実装**: `joymap.apply_joy_freshness(latest, elapsed_s, timeout_s)`（rclpy 非依存）。**非有限 elapsed は stale 扱い**（`NaN > t` が False で永久ラッチする穴そのものを塞ぐ）・**param の非有限/非正は既定へ fail-safe**（`_positive_or_default`・`driver_core.py:37-41` / keyboard `_positive` と同型。inf timeout は dead-man を無効化するため raw を honor しない）。
- **テスト (R-26)**: `tests/unit/test_teleop_joymap.py` 末尾に 11 ケース append（fresh 素通し / stale→ゼロ / 境界＝ちょうどは素通し / 非有限 elapsed 3 種→ゼロ / 退化 timeout 4 種→既定で裁定 / 既定値 0.6 の keyboard パリティ）。**mutation 4 本 KILLED**（2026-08-31 実測・個別適用）: ①ガード disarm ②非有限 elapsed を fresh 扱い ③timeout ハードニング除去 ④比較反転。クリーン 0 fail・全 suite 2545 passed。
- ⚠️ **mutation ハーネスの追加教訓（pyc エイリアス）**: 実ファイル差替方式でも **`__pycache__` を毎回 purge しないと stale bytecode が復元後も import され続ける**（pyc 有効判定は mtime 秒粒度＋サイズ。同一秒内の書き戻しでエイリアスし、「復元済みソース・変異体の挙動」という偽状態になる—2026-08-31 実測）。ハーネスは `PYTHONDONTWRITEBYTECODE=1` ＋ 各 run 前に `__pycache__` 削除を必須とする（`warehouse_m1_driver/CLAUDE.md` の PYTHONPATH 落とし穴と対の注意）。

## 【2026-09-09 追記】操作者非常停止 latch と再アーム（`teleop_joy`）

設計正本: [docs/mode-m1/05-operation-state-and-stop-authority.md](../../../docs/mode-m1/05-operation-state-and-stop-authority.md) §5（latch 意味論・解除≠即走行）/ §6（操作条件表・再アーム 2 条件）。ボタン既定・sentinel・`joy_node` 前提は [docs/mode-m1/03:52](../../../docs/mode-m1/03-joystick-teleop-bringup.md)。wire payload は [doc03:112](../../../docs/architecture/03-software-architecture.md)。
**layer 注記**（`.claude/rules/layer-annotation.md`）: teleop は **velocity producer** であり `warehouse_teleop` は正準対応表の単一 layer 帰属の対象外（[doc05:4](../../../docs/mode-m1/05-operation-state-and-stop-authority.md)）。本スライスは joy→L0' 手前の**第一防御**のみで、**L0'（m1_driver clamp）/ L1（Guardian・twist_mux prio100）/ L2（Policy Gate）は不変更**・`warehouse_interfaces` 凍結契約も無変更（doc03 は既存 `/operator/stop_request` 行の producer 記述更新のみ＝additive でない in-place）。

- **提供 (produce)**: topic **`/operator/stop_request`**（`std_msgs/String` JSON・単一 global topic＝fleet 全体）。**QoS = RELIABLE / KEEP_LAST(10) を `QoSProfile` で明示**（`.claude/rules/ros2.md:17`「既定に頼らず明示」。consumer 側 `warehouse_safety/CLAUDE.md:22` と一致し、Guardian 自身の `reliable_qos` と同形＝落ちた engage は落ちた停止）。payload は doc03:112 のリテラルを `joymap.OPERATOR_STOP_PAYLOAD` に保持（`{"action": "engage"}` / `{"action": "clear"}`）——node は JSON を手組みしない。**publish は rising edge のみ**（level 再送しない）。
  - ros param: **`estop_button`（既定 1 = B・右親指＝狙って押す意図的停止）** / **`estop_button_alt`（既定 7 = R1・右人差し指＝驚いて握り込む反射停止・`-1` で alt だけ無効）** / **`estop_clear_buttons`（既定 [10, 11] = SELECT+START 同時）** / **`publish_operator_stop`（既定 true）**＝経路 B（topic publish）だけを切る構成別 param（[doc05:92](../../../docs/mode-m1/05-operation-state-and-stop-authority.md)）/ `operator_stop_topic`（既定 `/operator/stop_request`）。**`deadman_button` 既定を 4 → 6（L1）に訂正**（4 は X-BOX モードの L1＝PC/PCS モードでは Y。一次表 = Yahboom「Handle control」 <http://www.yahboom.net/public/upload/upload-html/1690197586/Handle%20control.html> 参照日 2026-09-09）。**非常停止ボタンは実機握りテスト（2026-09-09）で 2 個に確定**し、**どちらの rising edge でも engage** する（親指は狙って押す・人差し指は驚いて握り込む＝失敗モードが逆なので一方が他方を代替しない）。機能そのものの有効/無効は **primary `estop_button` だけ**で決まり、alt は任意の追加。**array param（`int[]` 1 本）は使わない**——実機での型推論が未確認のため int 2 個に分けた（残件 S-3）。
  - **sentinel は `estop_button = -1` だけ**（＝機能無効・従来どおり deadman のみ。`m1_driver` の `car_type = -1` と同 idiom = `driver_node.py:46`）。**`estop_clear_buttons = []` は sentinel ではない**——「解除手段の無い停止機能」を「off」と読むと**最も危険な typo が無言になる**ため、**設定ミス**として扱う（`estop_button < -1` も同様。`estop_button_alt` は任意の第 2 ボタンであって sentinel ではない）。設定ミス（chord 空・範囲外 index・**estop と deadman の同一化**・alt と primary/deadman/chord の重複）は **`motion_allowed=False` 固定・publish なし**（typo が fleet 全体を止めないため）。**error ログは one-shot**（`_cfg_error_logged`）: 静的検査は起動時、**index 範囲は live サンプルでしか判らない**ため `_on_joy` でも同じ判定を回し、初回だけ「motion disabled / nothing published」を出す。
- **提供 (produce・経路 A＝standalone の唯一の停止経路)**: latch 中は `/<bot>/cmd_vel` へゼロを送り続ける。**`publish_operator_stop=false` でも無効化されない**（M0-M2 bring-up に Guardian は居ない = [mode-m1/03:50](../../../docs/mode-m1/03-joystick-teleop-bringup.md)）。
- **消費 (consume)**: `/joy`（`sensor_msgs/Joy`）の **buttons index 4 種**（`deadman_button` / `estop_button` / `estop_button_alt` / `estop_clear_buttons`）と axes 3 種（中立判定）。**`joy_node` 側の前提は 4 param**——**`launch/m1_teleop.launch.py` が機械で固定する**（下記 2026-09-10 追記・残件⑧ close）: `autorepeat_rate` **20.0**（**0 にしない**。0 だと押しっぱなしで /joy が止まり `joy_timeout_s` 0.6 s が正常操作を stale と誤判定）・`sticky_buttons` **false**（トグル化は rising edge と latch 意味論を壊す）・`deadzone` **0.0**（中立判定の正本を teleop 側 0.1 へ一本化）・`device_id` **0**（受信機 1 台前提）。
- **pure 実装**: `joymap.OperatorEstopConfig` / `OperatorEstopState` / `operator_estop_step` / `operator_estop_disarm` / `operator_estop_config_error`（rclpy 非依存・**時計を引数に取らない**＝Joy サンプルだけが状態を動かす）。`motion_allowed = (not latched) and armed`。**中立判定は専用 `_is_neutral`**（既存 `deadzone` 既定 0.1 を再利用し 3 軸すべて `isfinite(v) and abs(v) < deadzone`）——`_axis` は非有限/範囲外を 0.0 に潰すため**流用禁止**（NaN を「中立に戻した」と誤読して再アームする）。`apply_joy_freshness` は共有述語 `joy_is_stale` に切り出し（挙動不変）、node は同一判定で `operator_estop_disarm` を呼ぶ。
  - **起動直後（`prev_buttons = None` ＝履歴なし）は非対称**: **arming 側 `_rising` は履歴が無ければ False**（deadman 保持のまま起動しても armed にならない＝離して押し直す＝doc05:91 の再アーム動作そのもの）／**stopping 側 `_estop_rising` は履歴が無くても押下＝edge**（起動時に握られていれば engage）。2 つの未知を**逆方向に倒す**のは、誤停止のコスト（押し直し）と見逃しのコスト（動く車体）が非対称だから。`_chord_rising`（解除＝arming 側）は履歴なしで False。
  - **CLEAR の条件は 3 つ**: chord の rising ＋ deadman 非押下 ＋ **非常停止ボタン（primary・alt の両方）が離れている**（握ったままの「解除」は解除ではない＝doc05:89 の「解除≠走行開始」と同じ趣旨を入力側にも課す）。
- **3 ゲートの合成**: deadman/クランプ（`joy_to_twist`）・鮮度（`apply_joy_freshness`）・latch（`motion_allowed`）はすべてゼロ強制 mask なので出力は AND。エッジ検出は `_on_joy`（1 Joy = 1 step）だけで行い、timer レートが押しっぱなしをエッジ化しない。
- **テスト (R-26)**: `tests/unit/test_teleop_joymap.py` 末尾に **49 ケース**（初版 26 → 敵対レビュー追補 **+24**・sentinel ケースの param 1 本削減 −1。**ファイル全体 75 ケース**）（独立オラクル = doc05:87/:88/:89/:91 の日本語要件文＋mode-m1/03:52 のボタン/sentinel/cold-start 規定＋doc03:112 リテラル）。**ケース (19) は挙動 oracle ではなく不変条件の pin**（下記 M3 が equivalent である理由の記録。テスト側にも同旨のコメントを置いた）。payload は自前パーサでなく**実 consumer** `warehouse_safety.guard_logic.parse_operator_stop_action` で round-trip 検証する（**テストからの import のみ**。package コードは `warehouse_interfaces` だけに依存＝`.claude/rules/implementation-and-dependencies.md` §1）。
- **mutation 9/9 KILLED**（2026-09-09 敵対レビュー後の再実測・実ファイル差替＋`__pycache__` purge＋`PYTHONDONTWRITEBYTECODE=1`）。初版でレビューが **SURVIVED** と指摘した 5 体＋alt 用 1 体を追加し、全滅を確認:
  | 変異 | red | 殺したケース（代表） |
  |---|---|---|
  | MA **estop の `_rising` を `_pressed` に**（level 判定＝押しっぱなしで再 engage） | 4 | `test_held_estop_publishes_exactly_one_engage[1/7]` |
  | MB `_chord_rising` の完了判定撤去（`return True`） | 1 | `test_chord_held_across_the_engage_does_not_clear` |
  | MC `_is_neutral` を x 軸のみに | 2 | `test_neutral_requires_every_mapped_axis[deflected0/1]` |
  | MD `dz = cfg.deadzone`（`_nonneg` 撤去） | 1 | `test_non_finite_deadzone_does_not_make_a_deflected_stick_neutral` |
  | ME 中立境界 `>=` → `>` | 1 | `test_deadzone_boundary_is_exclusive` |
  | MF **alt の rising 判定を落とす** | 4 | `test_alt_estop_button_engages` / `test_cold_start_held_estop_engages[7]` |
  | M1 `motion_allowed` 常 True（再確認） | 26 | 全 latch 系 |
  | M4 中立判定に `_axis` 流用（再確認） | 4 | `test_non_finite_axis_is_not_neutral[nan]` |
  | M5 deadman を level で armed（再確認） | 4 | `test_cold_start_held_deadman_does_not_arm` |
  クリーン 0 fail・復元後 `git diff` 空。**M3（CLEAR の `neutral_seen=False` だけを外す）は依然 EQUIVALENT**（`if not latched` ガードにより「latched ⇒ neutral_seen False」が不変条件で、CLEAR 時の値は必ず False）——冗長だが明示的な構築として残し、不変条件自体をケース (19) で pin した（挙動 oracle ではない）。
- **未決・残件**: ①**実機 `jstest` で index 確定**（M1 ゲート・[mode-m1/03 §2](../../../docs/mode-m1/03-joystick-teleop-bringup.md)）——現既定は Yahboom 一次表からの導出であり実測ではない。②**doc05 OQ-OP3（解除時の残 goal 確認）は本スライスの範囲外**（teleop は goal を持たない。担保は Guardian/L2 側）。③統合構成の mux 入力（`/cmd_vel/teleop`）追加は bringup 所有のまま未着手＝doc05 §8 順序 4。④運転モード購読（§6 fail-closed・OQ-OP6）は未実装。⑤node 配線（param/topic/QoS/publish 条件）の AST pin unit は無い（doc05:173 が #593 で挙げた同型の残件・先例 `tests/unit/test_speed_band_bringup_wiring.py`）。⑥**S-3: array param（`int[]` 1 本で estop 群を渡す形）は実機で型推論を確認していない**——本スライスは `estop_button` / `estop_button_alt` の **int 2 個**で回避した。統合するなら実機 param 型の確認が先。⑦**SP-6: param epoch が非対称**——`_estop_cfg`（ボタン/chord/deadzone）は**起動時スナップショット**、`joy_to_twist` に渡す軸・cap は**毎サンプル live 読み**。`read_only` 宣言で epoch を揃えるのは次スライス。⑧**CLOSED（2026-09-10・下記「M1 standalone launch」節）**——`autorepeat_rate` / `sticky_buttons` / `deadzone` / `device_id` は `launch/m1_teleop.launch.py` が固定する（残るのは実機で `ros2 launch` を通す確認）。⑨**Guardian 再起動で latch が乖離する窓**——teleop は engage を rising edge でしか publish せず level 再送しないため、latch 中に Guardian が落ちて上がると Guardian 側だけ停止理由が抜ける（経路 A＝teleop 自身のゼロ送出は効き続ける）。裁定は doc05 §10 に記載。

### 終了経路（2026-09-10・実機 SIGTERM/SIGINT で発見した #622 の回帰 → package 共通 `node_runtime` に一般化）

**実測（Jetson・ROS 2 Humble・rclpy 3.3.21・2026-09-10・タイマー無しの裸ノードで 4 パターン×2 シグナル）**: rclpy のシグナルハンドラは `main()` の `finally` より**先に** context を破棄する（SIGINT/SIGTERM とも `finally` 時点で `rclpy.ok()` は False）。その前提で:

| `main()` の書き方 | SIGINT（Ctrl-C） | SIGTERM（`systemctl stop` / `timeout`） |
|---|---|---|
| `suppress(KeyboardInterrupt)` ＋ 素の `rclpy.shutdown()`（教科書パターン・repo の **13 ノード**） | **exit 1**（`RCLError: failed to shutdown: rcl_shutdown already called`） | **exit 1**（`ExternalShutdownException` 未捕捉） |
| `except (KeyboardInterrupt, ExternalShutdownException)` ＋ `rclpy.try_shutdown()`（本 package） | exit 0 | exit 0 |
| `except KeyboardInterrupt` ＋ 自前 SIGTERM handler ＋ `if rclpy.ok()` ガード（`m1_driver`） | exit 0 | exit 0・**ただしタイマー保有が前提**（Python 側 handler は `rcl_wait` を起こせず、タイマーの無いノードは次イベントまで固まる＝プローブで実測） |

exit 1 の実害: `deploy/jetson/systemd/*.service` は `Restart=on-failure` なので**通常停止が失敗として journal に残り、本物のクラッシュが同じ Traceback ノイズに埋もれる**（[docs/setup/jetson-deploy.md](../../../docs/setup/jetson-deploy.md) systemd unit 一覧）。

- **提供 (produce・package 内 API)**: `node_runtime.run_node(factory, args=, spin=, on_exit=)` / `best_effort(action)` / `NORMAL_STOP_EXCEPTIONS` / `context_is_live()`。両 entry point（`teleop_joy` / `teleop_keyboard`）は `main()` を `run_node` に委譲する。**3 規則**: ①正常停止 = `KeyboardInterrupt` または `ExternalShutdownException`（exit 0）②終了後に context を触るもの（最後のゼロ twist 等）は `best_effort`＝context 消失時はスキップ・途中死の `RuntimeError`（`RCLError` / `InvalidHandle` の公開基底）だけ握る（それより広く握らない＝本物のバグは表に出す）③`rclpy.try_shutdown()`（冪等）。**安全はこのゼロに依存しない**（W-1 = [mode-m1/02:62](../../../docs/mode-m1/02-m1-driver-and-watchdog.md) が最終受信から 0.5 s でブレーキ）。keyboard 版は `on_exit` で「ゼロ（best-effort）→ **端末復元（必ず）**」の順を固定し、`q` の quit フラグ loop は `spin=` に渡す。
- **置き場所が package-local な理由**: 依存してよい共有 package は `warehouse_interfaces` / `warehouse_description` の 2 つだけ（[parallel-workflow.md:71](../../../.claude/rules/parallel-workflow.md)）で、`warehouse_interfaces` は rclpy を持たない純 Python 契約 package（[doc16:89](../../../docs/architecture/16-repository-and-conventions.md)）。共通化先（新 shared package か interfaces への lazy-import 追加か）は docs 裁定＝追補 Issue。他 package は当面 3 規則を写す。
- **テスト**: `tests/unit/test_node_shutdown_lifecycle.py`（AST pin・rclpy 非導入ホストで動く・`@safety`）— **A)** `node_runtime` の 3 規則（例外 tuple・`try_shutdown` のみ・`best_effort` の「ガード→action→`RuntimeError` だけ」・`on_exit`→`destroy_node`→`try_shutdown` の順と入れ子 finally）**B)** 両 entry point の委譲・`on_exit` の中身・keyboard の stop→restore 順 **C) repo 全体のラチェット**: `rclpy.init` を呼ぶか `run_node` に委譲する全 top-level `main()` を分類し、危険パターンの集合が baseline `KNOWN_UNSAFE_STOP_ON_HUMBLE`（13 ノード）と**完全一致**することを要求（直したら baseline から消す・新規ノードが危険なら CI 赤）。分類器自体は実測表の 3 行＋反例 2 行の合成ソースで自己検証。**mutation 6/6 KILLED**（2026-09-10・下表）。
  | 変異 | red |
  |---|---|
  | `try_shutdown` → 素の `shutdown` | A |
  | tuple から `ExternalShutdownException` を落とす | A |
  | `best_effort` の `rclpy.ok()` ガード撤去 | A |
  | `except RuntimeError` → `except Exception` | A |
  | keyboard `_stop_and_restore` の順序反転 | B |
  | 教科書パターンの新ノードを ws/src に追加 | C（regressed として名指し） |
- **残件（追補 Issue・本 PR では触らない＝編集境界）**: ①baseline 13 ノードの修正（優先順 = Humble ボードで systemd 常駐する `emergency_guardian`〔`warehouse-safety.service` は `Restart=on-failure`〕・`state_cache`・`llm_bridge`、次に M1 経路の `speed_band_node`）②`m1_driver` の SIGTERM 経路がタイマー依存である点の明文化（`driver_node.py` のコメント）③`nav2_bridge` / `web_bridge` は uvicorn が signal handler を差し替えるため実害は**未確認**（baseline に含めたまま実測で裁定）④共通化先の docs 裁定（doc16 §11 or doc20）と [docs/setup/jetson-deploy.md](../../../docs/setup/jetson-deploy.md) への「正常停止 = exit 0」の明記。

## 【2026-09-10 追記】M1 standalone launch（`launch/m1_teleop.launch.py`）

設計正本: [docs/mode-m1/03-joystick-teleop-bringup.md](../../../docs/mode-m1/03-joystick-teleop-bringup.md) **§5-1**（`joy_node` の正本コマンドと各 param の理由・3 プロセスの起動順）/ [§5-2](../../../docs/mode-m1/03-joystick-teleop-bringup.md)（`jstest` モード判定・index の正は `/joy`）——**両節は PR #641 で land**（本 package は #635 の code スライス）。standalone 構成は [03:50](../../../docs/mode-m1/03-joystick-teleop-bringup.md)、前提 2 つの宣言元は [03:52](../../../docs/mode-m1/03-joystick-teleop-bringup.md)、W-4（車輪を浮かせる・主電源カットオフに手を掛ける）は [mode-m1/02:65](../../../docs/mode-m1/02-m1-driver-and-watchdog.md) / [mode-m1/02:76](../../../docs/mode-m1/02-m1-driver-and-watchdog.md)。
**layer 注記**（`.claude/rules/layer-annotation.md`）: 本 launch は teleop（velocity producer）の**起動配線**のみ。L0'（m1_driver clamp）/ L1（Guardian・twist_mux prio100）/ L2（Policy Gate）は不変更、`warehouse_interfaces` 凍結契約も無変更。

- **提供 (produce)**: launch **`m1_teleop.launch.py`**（`ros2 launch warehouse_teleop m1_teleop.launch.py`。`setup.py` の `data_files` で `share/warehouse_teleop/launch` へ install＝先例 [warehouse_bringup/setup.py:14](../warehouse_bringup/setup.py)）。起動するのは **`joy_node` と `teleop_joy` の 2 プロセスだけ**。
  - **launch 引数**: `bot`（既定 `bot1`）/ `device_id`（既定 `0`）/ `deadman_button` / `estop_button` / `estop_button_alt`（既定は **`joymap.DEFAULT_*` を `str()` した値**＝launch にボタン番号のリテラルを書かない。実測 index はここで上書きする＝[03 §5-2 手順 6](../../../docs/mode-m1/03-joystick-teleop-bringup.md)「実測が正」）。`estop_clear_buttons`（int 配列）は**引数化しない**——array param の型推論が実機未確認（残件⑥ S-3）ゆえ node 既定 `[10, 11]` のまま。
  - **機械で固定する `joy_node` param 4 つ**（値と理由は 03 §5-1 の表）: `autorepeat_rate` **20.0**（0 だと押しっぱなしで `/joy` が止まり `joy_timeout_s` 0.6 s が**正常操作を stale と誤判定**）・`sticky_buttons` **false**（トグル化は rising edge を壊す＝engage / 解除 chord / 再アームが**すべて edge 依存**）・`deadzone` **0.0**（中立判定の正本を teleop 側 `deadzone` 0.1 の 1 か所に寄せる）・`device_id`（launch 引数・既定 0）。substitution 由来の param は `ParameterValue(..., value_type=int)` で型を付ける（無指定は **str で届いて declare 時に落ちる**＝bringup の `operating_vx_max` と同じ罠）。
  - **`m1_driver` を含めない**（W-4）: driver は**車輪に通電する**ため、§2 プローブと G-g 抜線試験を通した後、**車輪を完全に浮かせ主電源カットオフに手を掛けた状態で別端末から**上げる（[03 §5-1 ③](../../../docs/mode-m1/03-joystick-teleop-bringup.md)）。launch がここに driver を足すと「teleop を上げる」が「車輪へ通電する」を意味してしまい、W-3 不在下で**必須層**の W-4 が構造的に迂回される。Nav2 / twist_mux も M0-M2 では立てない（03:50）。
- **消費 (consume)**: `warehouse_teleop.joymap` の `DEFAULT_DEADMAN_BUTTON` / `DEFAULT_ESTOP_BUTTON` / `DEFAULT_ESTOP_BUTTON_ALT`（rclpy 非依存ゆえ launch から import 可・単一ソース）・`joy` / `launch` / `launch_ros`（`package.xml` に `exec_depend` 追加）。
- **テスト (R-26)**: `tests/unit/test_teleop_m1_launch_wiring.py`（**AST pin**・`launch_ros` 非導入ホストで動く・`@pytest.mark.unit, safety`。先例 `tests/unit/test_speed_band_bringup_wiring.py`）— 独立オラクル = 03 §5-1 の値リテラル（20.0 / false / 0.0）と :50（standalone）/ 02:65 / 02:76（W-4）。8 ケース＋ROS 環境限定 1（`importorskip` で host は skip）: ①`joy_node` の 4 param 完全一致・`autorepeat_rate` は float 20.0・`sticky_buttons is False`・`deadzone` 0.0・`device_id` は launch arg 由来 ②`teleop_joy` の 4 param が全て launch arg 由来 ③起動 Node が `(joy, joy_node)` と `(warehouse_teleop, teleop_joy)` の**2 組だけ**・コード側に `m1_driver` / `twist_mux` / `nav2` / `Include` が出ない ④index 既定が `str(DEFAULT_*)` でその名前が `joymap.py` に実在 ⑤`setup.py` が `launch/*.launch.py` を share へ install。
- **mutation 4/4 KILLED**（2026-09-10 実測・1 体ずつ適用→復元・`__pycache__` purge ＋ `PYTHONDONTWRITEBYTECODE=1`）:
  | 変異 | red |
  |---|---|
  | `autorepeat_rate` 20.0 → 0.0 | ① |
  | `sticky_buttons` False → True | ① |
  | `m1_driver` の `Node` を launch に追加 | ③（2 テストとも） |
  | `deadman_button` の既定を `str(DEFAULT_DEADMAN_BUTTON)` → リテラル `'6'` | ④ |
- **残件**: ①**実機で `ros2 launch` を通していない**（M1 ゲートで `ros2 param get /joy_node autorepeat_rate` → `20.0` / 押しっぱなし `ros2 topic hz /joy` → ≈20 Hz を確認する＝[03 §5-1](../../../docs/mode-m1/03-joystick-teleop-bringup.md)）。②`estop_clear_buttons` は引数化していない（S-3 の実機型確認が先）。③**複数ゲームパッド接続時の `device_id` 選択挙動が未検証**（`device_id` は `joy_node` の列挙 index であり `jsN` の N と一致する保証が無い＝03 §5-1 `# TODO(実機)`）。④統合構成（Nav2 同時稼働）の launch は本ファイルの範囲外＝bringup 所有のまま（残件③と同じ）。

## 【2026-09-16 追記】Mode Outdoor 遠隔操作リンクの純ロジック（`operator_link_logic`）

設計正本: [docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md:42](../../../docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md)（遠隔操作面は `web_bridge` と**別プロセス別 port**・actuation あり・**運ぶ意味は閉集合**・package 境界は未凍結＝`OQ-OD23`）/ [:53](../../../docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md)（既存 teleop の **deadman / `/joy` 鮮度 / latch の純ロジックを再利用**し入力源だけ差し替える＝`OQ-OD27`）/ [:76](../../../docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md)（制御接続が運ぶ ①heartbeat ②teleop ③`stop_request` ④横断承認 ⑤重要状態・映像は別接続）/ [:85](../../../docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md)・[:89](../../../docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md)・[:90](../../../docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md)（周期 200 ms・途絶 1.0 s は**候補**）/ [:91](../../../docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md)（実装単一ソースは将来 `warehouse_interfaces.safety` 定数）/ [05:51](../../../docs/mode-outdoor/05-safety-envelope-and-intervention.md)（リンク断 → engage・`reason_code = link_loss`・**自動 clear しない**）/ [05:95](../../../docs/mode-outdoor/05-safety-envelope-and-intervention.md)（R-26 行）/ [09:61](../../../docs/mode-outdoor/09-external-review-v3-response.md)・[:62](../../../docs/mode-outdoor/09-external-review-v3-response.md)・[:67](../../../docs/mode-outdoor/09-external-review-v3-response.md)（`session_epoch` / 単調 `sequence` / 送信時刻＋許容時計誤差・滞留と過去セッション破棄・計測時計と watchdog 単調時計は役割別）/ [09:104](../../../docs/mode-outdoor/09-external-review-v3-response.md)・[:105](../../../docs/mode-outdoor/09-external-review-v3-response.md)・[:108](../../../docs/mode-outdoor/09-external-review-v3-response.md)（有界キュー・再接続で駆動指令破棄・古いフレームを捨てる・**映像鮮度は heartbeat と別**）/ [09:131](../../../docs/mode-outdoor/09-external-review-v3-response.md)（承認トークンは**一回限り・その横断限り**）/ [doc03:112](../../../docs/architecture/03-software-architecture.md)（`/operator/stop_request` の凍結 payload）/ [doc22:24](../../../docs/architecture/22-web-observability.md)（`web_bridge` 非ゴール＝本モジュールが触れない相手）。

**layer 注記**（`.claude/rules/layer-annotation.md`）: `operator_link_logic.py` は **L1 stop_request producer の判定**＋**既存 teleop（velocity producer）の前段 admission filter**。**本ファイル自身に actuation は無い**（socket を持たず publisher を作らず何も publish せず actuation topic 名を持たない）。最終防壁は不変＝L0'（`m1_driver` clamp）/ L1（Guardian・twist_mux prio100）/ `joymap` の deadman・鮮度・latch。`warehouse_interfaces` 凍結契約は**無変更**、`web_bridge` の R-26 unit も**無改変で緑のまま**（02:42）。

- **提供 (produce)**: module **`warehouse_teleop.operator_link_logic`**（純 stdlib・`rclpy` / `websockets` / `fastapi` / `uvicorn` / `asyncio` を import しない。**node・port・接続分離は本スライスに含まない**）。
  - `ControlKind`（Enum・**閉集合**）= `HEARTBEAT` / `TELEOP` / `STOP_REQUEST` / `CROSSING_APPROVAL` / `AUTHORITY`。02:76 の ①〜④ ＋ 操作権（09:108）。⑤重要状態は **outbound** なので inbound kind に含めない。
  - `parse_control_message(raw: str | bytes) -> ControlMessage | None` — 厳密 JSON。未知 kind / キー過不足 / 型違い / 非有限数 / 負のカウンタ は **`None`（無視・例外を投げない）**（05:40）。envelope = `session_epoch` / `sequence` / `sent_at_s` ＋ kind 固有 payload。
  - payload 型: `HeartbeatPayload`（フィールド無し）/ `TeleopPayload(axes, buttons)`（`/joy` 相当）/ `StopRequestPayload(action)`（`STOP_ACTIONS` = joymap の `OPERATOR_STOP_PAYLOAD` から**導出**）/ `CrossingApprovalToken(token_id, crossing_id, route_version, datum_version, entry_heading, expires_at_s)` / `AuthorityPayload(claim)`。
  - `SessionGuard(session_epoch, max_clock_skew_s, valid_for_s)` — `accept(msg, now_wall_s, now_mono_s)`（epoch 不一致・`sequence` 非増加・`sent_at_s` が `[now_wall − valid_for − skew, now_wall + skew]` 外 を拒否。**baseline `sequence` は全チェック通過後にだけ進む**＝拒否された message が後続の番号を焼かない）/ `take_pending_teleop()`（depth-1 newest-wins・消費は一度きり）/ `reconnect(new_epoch)` → **破棄した駆動指令数を返す**（累計は `discarded_teleop_count`）。
  - `LinkWatchdog(timeout_s)` — `observe_heartbeat` / `evaluate -> LinkVerdict(engaged, reason_code, age_s)` / `is_fresh` / `rearm`（**heartbeat が今新しいときのみ**成功）。未観測＝engaged・途絶で latch・**復帰で自動 clear しない**・負 age / 非有限は engaged（fail-closed）。
  - `stop_request_payload(verdict: LinkVerdict | None) -> str | None` — engaged のとき joymap の `OPERATOR_STOP_PAYLOAD[ESTOP_ACTION_ENGAGE]` を**そのまま**返す（JSON を二度書かない）。**publish はしない**。clear は返さない（fleet 共有 latch を他 producer 分まで解除しないため）。**`None` 入力は `None` 出力**（停止経路の入口は例外を投げない）。
  - `VideoFreshness(stale_after_s, max_stamp_skew_s)` — `observe_frame(frame_stamp_s, now_wall_s, now_mono_s)`（**stamp は順序判定のみ**・古い/重複フレームは捨てる・**壁時計から `max_stamp_skew_s` を超えて離れた stamp は両方向とも拒否**＝単調順序だけでは `1e308` 1 枚で「永遠に最新」になり channel が STALE で固着する）/ `verdict -> VideoState.FRESH|STALE|ABSENT`（**単調到着時刻で age**）/ **`reset()`**（新セッション・送信側再起動＝0 から振り直された stamp を受け直せる）。heartbeat と状態を共有しない。**`VideoState.is_fresh`** は `FRESH` のみ True＝`ABSENT` は「まだ新しくない」側（`is VideoState.STALE` と書くと起動時 False ＝ fail-open）。
  - `replay_teleop(msg, now_mono_s, received_mono_s, joy_timeout_s, *, current_session_epoch, expected_axes=None, expected_buttons=None) -> tuple[list[float], list[int]] | None` — 既存 teleop 経路が消費する **Joy 相当サンプル**を返す（**twist は返さない**＝deadman / ベクタキャップ / 再アームは `joymap` のまま）。**`current_session_epoch` は必須**（既定値を持たせると忘れた caller に fail-open が既定で付く）: `take_pending_teleop()` 済みのサンプルは `reconnect()` の射程外なので、epoch 照合が無いと 09:104 の破棄が「キュー内だけ成立・取り出し済みには不成立」になり、**前セッション最後の指令で走り続ける**。
  - `OPERATOR_STOP_TOPIC`（joymap の `DEFAULT_OPERATOR_STOP_TOPIC` の再 export）/ `REASON_LINK_LOSS = "link_loss"` / `MAX_JOY_ARRAY_LEN = 64`（**parser の資源上限であって契約値でも controller layout でもない**。実レイアウトの固定は `replay_teleop(expected_axes=..., expected_buttons=...)`）。
  - `CrossingApprovalRegistry.consume(token, now_wall_s, expected_crossing_id, expected_route_version, expected_datum_version) -> bool` — **`token_id` ごとにちょうど一度だけ True**。再利用・期限切れ・束縛不一致は False。**不一致/期限切れの提示では token を burn しない**（提示ミス 1 回で有効な承認を失わせないため）。burn 済み集合は**有界**（`consume` 毎に期限切れエントリを掃く。期限切れ token は期限チェック自体が拒否するので覚えておく意味が無い）。
- **セッション終了は 3 か所で効かせる**: `SessionGuard.reconnect()`（キュー内の駆動指令を破棄）＋ `replay_teleop(current_session_epoch=...)`（取り出し済みサンプルを拒否）＋ `VideoFreshness.reset()`（送信側再起動後も復帰できる）。**どれか 1 つでも欠けると 09:104 の破棄が穴になる**（reconnect だけでは 1 tick 前に take したサンプルが生き残る＝レビューが再現）。
- **消費 (consume)**: `warehouse_teleop.joymap` の `joy_is_stale`（鮮度判定の**単一ソース**・02:53 の再利用要件）/ `OPERATOR_STOP_PAYLOAD`・`ESTOP_ACTION_ENGAGE`（doc03:112 の凍結 JSON）/ `DEFAULT_OPERATOR_STOP_TOPIC`。**他トラックの内部モジュールは import しない**。
- **既定値を持たない設計（重要）**: `timeout_s` / `max_clock_skew_s` / `valid_for_s` / `stale_after_s` / `joy_timeout_s` は**すべて注入必須**で、**module 側に既定値を置かない**。02:85/:90 が候補値を未凍結とし 02:91 が実装単一ソースを `warehouse_interfaces.safety` に置くと定めているため、ここに既定を書くと :91 が禁じる**第 2 のソース**になる。退化値（非有限・負・0）は**黙って既定へフォールバックせず `ValueError`**（`inf` は窓を無効化する＝`joy_is_stale` が警告する罠）。`replay_teleop` は退化 `joy_timeout_s` で joymap の屋内既定 0.6 s に落ちず **`None`（再生しない）**を返す。
- **テスト (R-26)**: `tests/unit/test_operator_link_logic.py`（**182 ケース**・うち 66 が `@pytest.mark.safety`〔watchdog / replay / registry〕・独立オラクルはテスト側リテラル）＋ `tests/unit/test_operator_link_boundary.py`（**9 ケース**・全件 safety・AST pin。先例 `tests/unit/test_web_bridge_noactuation.py`）。boundary が固定するのは ①**import の allowlist**（`__future__` / `json` / `math` / `dataclasses` / `enum` / `warehouse_teleop.joymap` のみ。**deny リスト形では `socket`・`threading`+`http.client`・`sensor_msgs.msg` を取りこぼす**＝レビュー指摘で allowlist へ変更）②publisher / subscription / service / action client を作らない ③docstring 以外の文字列定数に `/` 始まりの topic 名を持たない（`/operator/stop_request` は joymap からの import のみ）④`cmd_vel` / `goal_pose` / `navigate_to_pose` をソース中どこにも書かない ⑤`joy_is_stale` 等を joymap から import している ⑥`ControlKind` が docs の閉集合と**完全一致**（実行時と AST の両方）。
- **mutation 16/16 KILLED**（2026-09-16 実測・1 体ずつ実ファイル置換 → sentinel 確認 → 復元・`__pycache__` purge ＋ `PYTHONDONTWRITEBYTECODE=1`。初版 9 体＋レビュー指摘由来 7 体）:
  | 変異 | 最初に赤くなった unit |
  |---|---|
  | heartbeat 復帰で latch を自動 clear | `test_watchdog_boot_latch_is_not_cleared_by_heartbeats_alone` |
  | `sequence` 同値を accept（replay 許容） | `test_guard_requires_a_strictly_increasing_sequence` |
  | 送信時刻窓の**下限**を撤去（滞留指令が通る） | `test_guard_send_time_window_is_bounded_on_both_sides` |
  | 承認 token を burn しない（再利用可） | `test_token_is_consumed_exactly_once` |
  | replay が鮮度判定を無視 | `test_replay_applies_the_joystick_freshness_rule` |
  | `reconnect` が pending 駆動指令を保持 | `test_reconnect_discards_the_pending_drive_command` |
  | 未観測 watchdog を非 engaged で開始 | `test_watchdog_boot_latch_is_not_cleared_by_heartbeats_alone` |
  | 古い / 重複フレームを accept | `test_video_drops_an_older_or_duplicated_frame` |
  | 健全時も stop payload を出す | `test_cleared_verdict_publishes_nothing_rather_than_a_clear` |
  | **replay が session epoch を見ない（再接続漏れ）** | `test_a_taken_teleop_sample_does_not_survive_a_reconnect` |
  | **拒否 message が `sequence` baseline を進める** | `test_a_rejected_message_does_not_advance_the_sequence_baseline` |
  | 映像 stamp の skew 上限を撤去（`1e308` で wedge） | `test_a_bogus_future_stamp_is_refused_and_does_not_wedge_the_video_channel` |
  | module に `import socket` / `threading` を追加 | `test_imports_are_confined_to_the_allowlist` |
  | `stop_request_payload(None)` が例外を投げる | `test_stop_request_payload_of_nothing_is_nothing` |
  | 期限前 token まで evict する | `test_token_is_consumed_exactly_once` |
  | Joy 配列長の上限を撤去 | `test_parser_refuses_an_oversized_joy_array` |
- **TODO(契約)**: ①**閾値の単一ソース**＝`timeout_s`（02:90 候補 1.0 s）/ heartbeat 周期（02:89 候補 200 ms）/ 時計誤差 / 指令期限 / 映像 stale / 承認期限 は未凍結（`OQ-OD25` / `OQ-OD20` / `OQ-OD22`）→ 確定時に `warehouse_interfaces.safety` の定数へ昇格し（02:91・`IDLE_SPEED_EPS` の先例）本 module は import するだけにする。②**node の package 帰属**（`operator_link_node` の別 package / 別 port を契約カタログに載せるか）＝`OQ-OD23`、AST pin の課し方＝`OQ-OD24`。③**接続分離**（制御 / 映像 / 記録の 3 本・映像 age → 遠隔速度上限の対応表）＝`OQ-OD90`・**本スライスでは表を発明しない**。④**wire JSON（kind 名・キー名・payload 形）は [提案・未凍結]**＝doc03 契約カタログにも `warehouse_interfaces` にも未登録。凍結時は additive-first（`.claude/rules/parallel-workflow.md` §7.2）で contract-PR。⑤`reason_code = link_loss`（05:51）は **doc03:112 の凍結 payload に載る場所が無い**ため in-process の `LinkVerdict` に留め、合流先 topic（05:57 の `/safety/stop_request` 案）は `OQ-OD53` 裁定待ち。⑥`entry_heading`（09:131）は token に載せるが**照合しない**（車体方位と角度許容の正本が無い）＝`OQ-OD92`。⑦MANUAL / REMOTE の切替順序（09:108「減速停止 → 旧操作権無効化 → 新操作権確認 → 中立確認 → 再開」）は本 module の外（`AUTHORITY` kind を運ぶところまで）＝`OQ-OD91`。⑧**`sequence` の wire 意味論が未凍結 [提案・未凍結]**: 現実装は**セッション単位の単一カウンタを全 kind で共有**する前提。09:62 は「単調増加」としか言わず、**per-kind か共有か**も**跳躍幅の上限**も定めていない。帰結は 2 つ——(a) peer が kind ごとに別カウンタを振ると heartbeat が番号を進めるたび teleop が replay 扱いで**飢える**、(b) 巨大な値（例 `2**62`）を 1 通 accept するとそのセッションは **reconnect まで実質ロック**される。contract 凍結時に「共有カウンタか・跳躍上限を置くか」を決める。⑨token eviction は**壁時計が burn 済み token の期限より手前へ巻き戻る**と当該 token を再消費させうる（壁時計は操作卓と共有）。margin を発明せず残件として記録する。
