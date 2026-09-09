# warehouse_teleop — キーボード teleop（動作確認の足場）

- **担当トラック / ブランチ**: track:teleop / `feat/teleop`（#158）
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

- **提供 (produce)**: topic **`/operator/stop_request`**（`std_msgs/String` JSON・単一 global topic＝fleet 全体）。**QoS = RELIABLE / KEEP_LAST(10)**（rclpy 既定プロファイル＝consumer 側 `warehouse_safety/CLAUDE.md:22` と一致）。payload は doc03:112 のリテラルを `joymap.OPERATOR_STOP_PAYLOAD` に保持（`{"action": "engage"}` / `{"action": "clear"}`）——node は JSON を手組みしない。**publish は rising edge のみ**（level 再送しない）。
  - ros param: **`estop_button`（既定 1 = B）** / **`estop_clear_buttons`（既定 [10, 11] = SELECT+START 同時）** / **`publish_operator_stop`（既定 true）**＝経路 B（topic publish）だけを切る構成別 param（[doc05:92](../../../docs/mode-m1/05-operation-state-and-stop-authority.md)）/ `operator_stop_topic`（既定 `/operator/stop_request`）。**`deadman_button` 既定を 4 → 6（L1）に訂正**（4 は X-BOX モードの L1＝PC/PCS モードでは Y。一次表 = Yahboom「Handle control」 <http://www.yahboom.net/public/upload/upload-html/1690197586/Handle%20control.html> 参照日 2026-09-09）。
  - **sentinel**: `estop_button = -1` **または** `estop_clear_buttons = []` で本機能を無効化＝従来どおり deadman のみ（`m1_driver` の `car_type = -1` と同 idiom = `driver_node.py:46`）。engage だけ or clear だけの半端な有効化を作らないため、どちらの sentinel も**機能全体**を切る。**設定済みで範囲外の index・chord に deadman/estop が混在**は設定ミス扱いで **`motion_allowed=False` 固定・publish なし**（typo が fleet 全体を止めないため）。起動時に 1 回 error ログ。
- **提供 (produce・経路 A＝standalone の唯一の停止経路)**: latch 中は `/<bot>/cmd_vel` へゼロを送り続ける。**`publish_operator_stop=false` でも無効化されない**（M0-M2 bring-up に Guardian は居ない = [mode-m1/03:50](../../../docs/mode-m1/03-joystick-teleop-bringup.md)）。
- **消費 (consume)**: `/joy`（`sensor_msgs/Joy`）の **buttons index 3 種**（`deadman_button` / `estop_button` / `estop_clear_buttons`）と axes 3 種（中立判定）。**`joy_node` 側の前提 2 つ**: `autorepeat_rate` を **0 にしない**（既定 20 Hz。0 だと押しっぱなしで /joy が止まり `joy_timeout_s` 0.6 s が正常操作を stale と誤判定）・`sticky_buttons` は **false**（トグル化は rising edge と latch 意味論を壊す）。
- **pure 実装**: `joymap.OperatorEstopConfig` / `OperatorEstopState` / `operator_estop_step` / `operator_estop_disarm` / `operator_estop_config_error`（rclpy 非依存・**時計を引数に取らない**＝Joy サンプルだけが状態を動かす）。`motion_allowed = (not latched) and armed`。**中立判定は専用 `_is_neutral`**（既存 `deadzone` 既定 0.1 を再利用し 3 軸すべて `isfinite(v) and abs(v) < deadzone`）——`_axis` は非有限/範囲外を 0.0 に潰すため**流用禁止**（NaN を「中立に戻した」と誤読して再アームする）。`apply_joy_freshness` は共有述語 `joy_is_stale` に切り出し（挙動不変）、node は同一判定で `operator_estop_disarm` を呼ぶ。
- **3 ゲートの合成**: deadman/クランプ（`joy_to_twist`）・鮮度（`apply_joy_freshness`）・latch（`motion_allowed`）はすべてゼロ強制 mask なので出力は AND。エッジ検出は `_on_joy`（1 Joy = 1 step）だけで行い、timer レートが押しっぱなしをエッジ化しない。
- **テスト (R-26)**: `tests/unit/test_teleop_joymap.py` 末尾に **26 ケース append**（独立オラクル = doc05:87/:88/:89/:91 の日本語要件文＋doc03:112 リテラル）。payload は自前パーサでなく**実 consumer** `warehouse_safety.guard_logic.parse_operator_stop_action` で round-trip 検証する（**テストからの import のみ**。package コードは `warehouse_interfaces` だけに依存＝`.claude/rules/implementation-and-dependencies.md` §1）。
- **mutation 5/6 KILLED**（2026-09-09 実測・実ファイル差替＋`__pycache__` purge＋`PYTHONDONTWRITEBYTECODE=1`）: M1 `motion_allowed` 常 True → 15 red・M2 latch を level 判定に → 8 red・M3′ ENGAGE/CLEAR の disarm 撤去 → 3 red・M4 中立判定に `_axis` 流用 → 2 red・M5 deadman を level で armed → 3 red。**M3（CLEAR の `neutral_seen=False` だけを外す）は EQUIVALENT**（`if not latched` ガードにより「latched ⇒ neutral_seen False」が不変条件で、CLEAR 時の値は必ず False）——冗長だが明示的な構築として残し、不変条件自体をケース (19) で pin した。
- **未決・残件**: ①**実機 `jstest` で index 確定**（M1 ゲート・[mode-m1/03 §2](../../../docs/mode-m1/03-joystick-teleop-bringup.md)）——現既定は Yahboom 一次表からの導出であり実測ではない。②**doc05 OQ-OP3（解除時の残 goal 確認）は本スライスの範囲外**（teleop は goal を持たない。担保は Guardian/L2 側）。③統合構成の mux 入力（`/cmd_vel/teleop`）追加は bringup 所有のまま未着手＝doc05 §8 順序 4。④運転モード購読（§6 fail-closed・OQ-OP6）は未実装。⑤node 配線（param/topic/QoS/publish 条件）の AST pin unit は無い（doc05:132 が #593 で挙げた同型の残件）。
